"""Spec §18: refund routing — auto-refunds, dispute refunds, anti-double-dip."""
import pytest
from cryptography.fernet import Fernet

from bot.config import Settings
from bot.core import dealops
from bot.core.timers import make_scheduler
from bot.db.repo import Credentials, Deals, Ledger, Listings, Users
from bot.utils.vault import Vault


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return None


@pytest.fixture
def fbot():
    return FakeBot()


@pytest.fixture
def vault():
    return Vault(Fernet.generate_key().decode())


@pytest.fixture
def sched():
    return make_scheduler()


@pytest.fixture
def cfg():
    return Settings()


async def _live_listing(db, vault, price=10000, seller=11):
    users, listings = Users(db), Listings(db)
    await users.ensure(seller, "", "Seller")
    await users.ensure(22, "", "Buyer")
    lid = await listings.create(seller, "TikTok", "TikTok Shop")
    await listings.update(lid, title="T", price_cents=price, status="LIVE")
    await Credentials(db).save(lid, vault.encrypt("user: x\npass: y"))
    return lid


async def _to_paid(db, fbot, sched, vault, cfg, price=10000, method="manual"):
    lid = await _live_listing(db, vault, price)
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.choose_payment(db, fbot, sched, vault, did, method, cfg)
    await dealops.submit_payment_proof(db, fbot, did, 22, "shot123")
    await dealops.admin_confirm_payment(db, fbot, sched, vault, did, 99)
    return did


@pytest.mark.asyncio
async def test_buyer_cancel_before_payment_no_refund_needed(db, fbot, sched, vault, cfg):
    lid = await _live_listing(db, vault)
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.cancel_deal(db, fbot, did, 22)
    assert (await Deals(db).get(did))["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_dispute_then_admin_refund(db, fbot, sched, vault, cfg):
    did = await _to_paid(db, fbot, sched, vault, cfg)
    await dealops.open_dispute(db, fbot, did, 22, "wrong account")
    assert (await Deals(db).get(did))["status"] == "DISPUTED"
    await dealops.admin_resolve_dispute(db, fbot, sched, did, 99, "refund", "proof shows mismatch")
    d = await Deals(db).get(did)
    assert d["status"] == "REFUNDED"
    assert d["refund_actor"] == "admin:99"
    kinds = [r["kind"] for r in await Ledger(db).all()]
    assert "refund" in kinds
    assert any(c == 22 and "refunded" in t for c, t in fbot.sent)


@pytest.mark.asyncio
async def test_dispute_then_admin_release(db, fbot, sched, vault, cfg):
    did = await _to_paid(db, fbot, sched, vault, cfg)
    await dealops.open_dispute(db, fbot, did, 22, "not as described")
    await dealops.admin_resolve_dispute(db, fbot, sched, did, 99, "release", "proofs check out")
    assert (await Deals(db).get(did))["status"] == "RELEASED"


@pytest.mark.asyncio
async def test_no_refund_after_release_anti_double_dip(db, fbot, sched, vault, cfg):
    did = await _to_paid(db, fbot, sched, vault, cfg)
    await dealops.release_funds(db, fbot, did, "bot")
    with pytest.raises(Exception):
        await dealops.refund_deal(db, fbot, did, "admin:99", "buyer changed mind")
    assert (await Deals(db).get(did))["status"] == "RELEASED"


@pytest.mark.asyncio
async def test_price_lte_commission_goes_to_admin_review(db, fbot, sched, vault, cfg):
    # $2 price, $2 USDT commission -> net $0: must NOT auto-release
    did = await _to_paid(db, fbot, sched, vault, cfg, price=200, method="binance")
    await dealops.release_funds(db, fbot, did, "bot")
    d = await Deals(db).get(did)
    assert d["status"] == "PAID_HELD"  # held for admin review, not released


@pytest.mark.asyncio
async def test_competing_deal_cancelled_on_payment(db, fbot, sched, vault, cfg):
    lid = await _live_listing(db, vault)
    d1 = await dealops.open_deal(db, fbot, lid, 22)
    await Users(db).ensure(33, "", "Buyer2")
    d2 = await dealops.open_deal(db, fbot, lid, 33)  # competing negotiation
    await dealops.choose_payment(db, fbot, sched, vault, d1, "manual", cfg)
    await dealops.submit_payment_proof(db, fbot, d1, 22, "shot")
    await dealops.admin_confirm_payment(db, fbot, sched, vault, d1, 99)
    assert (await Deals(db).get(d1))["status"] == "PAID_HELD"
    assert (await Deals(db).get(d2))["status"] == "CANCELLED"
    assert (await Listings(db).get(lid))["status"] == "SOLD"


@pytest.mark.asyncio
async def test_delivery_deadline_miss_refunds(db, fbot):
    # deal stuck in AWAITING_PAYMENT with a past delivery_due_at -> auto refund path
    users, listings, deals = Users(db), Listings(db), Deals(db)
    await users.ensure(11)
    await users.ensure(22)
    lid = await listings.create(11, "TikTok", "TikTok Shop")
    await listings.update(lid, title="T", price_cents=10000, status="LIVE")
    did = await deals.create(lid, 22, 11, 10000, 200)
    await deals.update(did, status="AWAITING_PAYMENT",
                       delivery_due_at="2020-01-01T00:00:00.000000Z")
    await dealops.on_delivery_deadline(db, fbot, did)
    d = await deals.get(did)
    assert d["status"] in ("REFUNDED", "CANCELLED")

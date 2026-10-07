"""Differentiated commission per payment method + PKR conversion."""
import pytest
from cryptography.fernet import Fernet

from bot.config import Settings
from bot.core import dealops
from bot.core.timers import make_scheduler
from bot.db.repo import Credentials, Deals, Listings, Users
from bot.utils.money import (
    fmt_cents,
    fmt_pkr,
    release_amount,
    release_amount_pkr,
    usd_cents_to_pkr,
)
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
    return Settings()  # defaults: 200c USDT, 50 PKR, rate 280


async def _live_listing(db, vault, price=10000, seller=11):
    users, listings = Users(db), Listings(db)
    await users.ensure(seller, "", "Seller")
    await users.ensure(22, "", "Buyer")
    lid = await listings.create(seller, "TikTok", "TikTok Shop")
    await listings.update(lid, title="T", price_cents=price, status="LIVE")
    await Credentials(db).save(lid, vault.encrypt("user: x\npass: y"))
    return lid


async def _to_paid(db, fbot, sched, vault, cfg, price, method):
    lid = await _live_listing(db, vault, price)
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.choose_payment(db, fbot, sched, vault, did, method, cfg)
    await dealops.admin_confirm_payment(db, fbot, sched, vault, did, 99)
    return did


# ------------------------------------------------------------ money helpers

def test_fmt_pkr():
    assert fmt_pkr(28000) == "Rs 28,000"
    assert fmt_pkr(50) == "Rs 50"
    assert fmt_pkr(0) == "Rs 0"


def test_usd_cents_to_pkr_rounding():
    assert usd_cents_to_pkr(10000, 280) == 28000
    assert usd_cents_to_pkr(199, 280) == 557      # 1.99 * 280 = 557.2
    assert usd_cents_to_pkr(150, 278.5) == 418    # 1.50 * 278.5 = 417.75
    assert usd_cents_to_pkr(1, 280) == 3          # $0.01 -> Rs 3


def test_release_amount_pkr_edges():
    assert release_amount_pkr(28000, 50) == 27950
    assert release_amount_pkr(51, 50) == 1
    assert release_amount_pkr(50, 50) is None   # equal -> admin review
    assert release_amount_pkr(49, 50) is None   # less -> admin review
    assert release_amount_pkr(0, 50) is None


# ------------------------------------------------- per-method snapshots

@pytest.mark.asyncio
async def test_choose_payment_sets_usdt_commission(db, fbot, sched, vault,
                                                   cfg):
    lid = await _live_listing(db, vault, price=10000)
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.choose_payment(db, fbot, sched, vault, did, "binance", cfg)
    d = await Deals(db).get(did)
    assert d["commission_cents"] == 200
    assert d["commission_pkr"] == 0
    assert d["payable_pkr"] == 0


@pytest.mark.asyncio
async def test_choose_payment_sets_pkr_amounts(db, fbot, sched, vault, cfg):
    lid = await _live_listing(db, vault, price=10000)
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.choose_payment(db, fbot, sched, vault, did, "manual", cfg)
    d = await Deals(db).get(did)
    assert d["payable_pkr"] == 28000
    assert d["commission_pkr"] == 50
    assert d["commission_cents"] == 0
    # buyer instructions show the PKR amount
    assert any("Rs 28,000" in t for _, t in fbot.sent)


# ------------------------------------------------------- release per method

@pytest.mark.asyncio
async def test_usdt_release_math(db, fbot, sched, vault, cfg):
    did = await _to_paid(db, fbot, sched, vault, cfg, price=10000,
                         method="binance")
    await dealops.release_funds(db, fbot, did, "bot")
    d = await Deals(db).get(did)
    assert d["status"] == "RELEASED"
    seller_msgs = [t for cid, t in fbot.sent if cid == 11]
    assert any("$98.00" in t and "$2.00" in t for t in seller_msgs)


@pytest.mark.asyncio
async def test_local_release_math_pkr(db, fbot, sched, vault, cfg):
    did = await _to_paid(db, fbot, sched, vault, cfg, price=10000,
                         method="manual")
    await dealops.release_funds(db, fbot, did, "bot")
    d = await Deals(db).get(did)
    assert d["status"] == "RELEASED"
    seller_msgs = [t for cid, t in fbot.sent if cid == 11]
    assert any("Rs 27,950" in t and "Rs 50" in t for t in seller_msgs)


@pytest.mark.asyncio
async def test_local_tiny_price_goes_to_admin_review(db, fbot, sched, vault,
                                                      cfg):
    # $0.10 -> Rs 28 payable vs Rs 50 commission -> admin review, stays HELD
    did = await _to_paid(db, fbot, sched, vault, cfg, price=10,
                         method="manual")
    d = await Deals(db).get(did)
    assert d["payable_pkr"] == 28
    await dealops.release_funds(db, fbot, did, "bot")
    d = await Deals(db).get(did)
    assert d["status"] == "PAID_HELD"  # held for admin review


@pytest.mark.asyncio
async def test_local_refund_shows_pkr(db, fbot, sched, vault, cfg):
    did = await _to_paid(db, fbot, sched, vault, cfg, price=10000,
                         method="manual")
    await dealops.refund_deal(db, fbot, did, "admin:99", "test")
    buyer_msgs = [t for cid, t in fbot.sent if cid == 22]
    assert any("Rs 28,000" in t for t in buyer_msgs)

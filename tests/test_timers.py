"""Spec §18: timer logic (24h/8h/12h) using short fake durations —
the on_* timer callbacks are invoked directly, plus schedule_job wiring."""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from bot.config import Settings
from bot.core import dealops
from bot.core.timers import cancel_job, make_scheduler, schedule_job
from bot.db.repo import Credentials, Deals, Listings, Users
from bot.utils.vault import Vault

HOLD = "hold_release"
HAND = "handover_lock"


class FakeBot:
    def __init__(self):
        self.sent = []  # (chat_id, text)

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return None


def _past(hours=1):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%fZ")


def _future(hours=1):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%fZ")


@pytest.fixture
def fbot():
    return FakeBot()


@pytest.fixture
def vault():
    return Vault(Fernet.generate_key().decode())


@pytest.fixture
def sched():
    s = make_scheduler()
    return s  # not started: add_job/remove_job still work


@pytest.fixture
def cfg():
    return Settings()


async def _paid_deal(db, fbot, sched, vault, cfg, price=10000):
    """Build a deal all the way to PAID_HELD (delivered)."""
    users, listings, deals = Users(db), Listings(db), Deals(db)
    await users.ensure(11, "", "Seller")
    await users.ensure(22, "", "Buyer")
    lid = await listings.create(11, "TikTok", "TikTok Shop")
    await listings.update(lid, title="T", price_cents=price, status="LIVE")
    await Credentials(db).save(lid, vault.encrypt("user: x\npass: y"))
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.choose_payment(db, fbot, sched, vault, did, "manual", cfg)
    await dealops.submit_payment_proof(db, fbot, did, 22, "screenshot_file_id")
    await dealops.admin_confirm_payment(db, fbot, sched, vault, did, 99)
    return did


@pytest.mark.asyncio
async def test_hold_release_fires_when_expired(db, fbot, sched, vault, cfg):
    did = await _paid_deal(db, fbot, sched, vault, cfg)
    await Deals(db).update(did, hold_until=_past(1))  # pretend 24h passed
    await dealops.on_hold_release(db, fbot, did)
    d = await Deals(db).get(did)
    assert d["status"] == "RELEASED"
    # seller got a payout instruction
    assert any(c == 11 and "Funds released" in t for c, t in fbot.sent)


@pytest.mark.asyncio
async def test_hold_release_skipped_when_not_expired(db, fbot, sched, vault, cfg):
    did = await _paid_deal(db, fbot, sched, vault, cfg)
    await Deals(db).update(did, hold_until=_future(23))  # 1h into the hold
    await dealops.on_hold_release(db, fbot, did)
    d = await Deals(db).get(did)
    assert d["status"] == "PAID_HELD"  # untouched


@pytest.mark.asyncio
async def test_hold_release_skipped_when_disputed(db, fbot, sched, vault, cfg):
    did = await _paid_deal(db, fbot, sched, vault, cfg)
    await dealops.open_dispute(db, fbot, did, 22, "login does not work")
    await Deals(db).update(did, hold_until=_past(1))
    await dealops.on_hold_release(db, fbot, did)  # orphaned timer firing late
    d = await Deals(db).get(did)
    assert d["status"] == "DISPUTED"  # timer paused — no release


@pytest.mark.asyncio
async def test_handover_lock_closes_chat(db, fbot, sched, vault, cfg):
    did = await _paid_deal(db, fbot, sched, vault, cfg)
    assert dealops.handover_active(await Deals(db).get(did)) is True
    await dealops.on_handover_lock(db, fbot, did)
    d = await Deals(db).get(did)
    assert d["chat_open"] == 0
    assert dealops.handover_active(d) is False
    assert any("read-only" in t for _, t in fbot.sent)


@pytest.mark.asyncio
async def test_schedule_and_cancel_job(db, fbot, sched):
    await schedule_job(db, sched, fbot, 123, HOLD)
    assert sched.get_job(f"{HOLD}:123") is not None
    await cancel_job(db, sched, 123, HOLD)
    assert sched.get_job(f"{HOLD}:123") is None


@pytest.mark.asyncio
async def test_confirm_receipt_releases_instantly(db, fbot, sched, vault, cfg):
    did = await _paid_deal(db, fbot, sched, vault, cfg)
    await dealops.confirm_receipt(db, fbot, sched, did, 22)
    assert (await Deals(db).get(did))["status"] == "RELEASED"


@pytest.mark.asyncio
async def test_confirm_receipt_by_non_buyer_rejected(db, fbot, sched, vault, cfg):
    did = await _paid_deal(db, fbot, sched, vault, cfg)
    with pytest.raises(Exception):
        await dealops.confirm_receipt(db, fbot, sched, did, 11)  # seller
    assert (await Deals(db).get(did))["status"] == "PAID_HELD"

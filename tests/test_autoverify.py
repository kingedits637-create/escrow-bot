"""Auto payment verification: unique amounts, matchers, polling, fallback."""
import time

import pytest
from cryptography.fernet import Fernet

from bot.config import Settings, mask_secret
from bot.core import dealops
from bot.core.timers import make_scheduler
from bot.db.repo import Credentials, Deals, Listings, Users
from bot.payments import autoverify
from bot.payments.autoverify import (
    bybit_signature,
    binance_signature,
    fetch_binance_deposits,
    fetch_bybit_deposits,
    generate_expected_usdt,
    match_deposit,
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
    return Settings()


@pytest.fixture
def auto_cfg():
    c = Settings()
    c.binance_api_key = "testkey123456"
    c.binance_api_secret = "testsecret123456"
    c.binance_usdt_address = "TXYZescrow123"
    c.binance_network = "BEP20"
    c.bybit_api_key = "bybitkey123456"
    c.bybit_api_secret = "bybitsecret123456"
    c.bybit_usdt_address = "bybitaddr123"
    return c


async def _live_listing(db, vault, price=10000, seller=11):
    users, listings = Users(db), Listings(db)
    await users.ensure(seller, "", "Seller")
    await users.ensure(22, "", "Buyer")
    lid = await listings.create(seller, "TikTok", "TikTok Shop")
    await listings.update(lid, title="T", price_cents=price, status="LIVE")
    await Credentials(db).save(lid, vault.encrypt("user: x\npass: y"))
    return lid


# ------------------------------------------------------- unique-amount trick

def test_expected_amount_shape():
    for _ in range(200):
        e = generate_expected_usdt(10000)
        assert 100.01 <= e <= 100.99
        assert round(e, 2) == e
        assert e != 100.00  # never .00


def test_expected_amount_no_collisions_1000_deals():
    taken: set[float] = set()
    prices = [5000 + (i * 137) % 95000 for i in range(1000)]  # varied prices
    for p in prices:
        e = generate_expected_usdt(p, taken)
        assert e not in taken
        taken.add(e)
    assert len(taken) == 1000


def test_expected_amount_rerolls_on_collision():
    taken = {100.37}
    seen = {generate_expected_usdt(10000, taken) for _ in range(50)}
    assert 100.37 not in seen  # collided value is always avoided


# ------------------------------------------------------------------ signing

def test_binance_signature_is_hmac_sha256():
    import hashlib
    import hmac
    qs = "coin=USDT&timestamp=123"
    expected = hmac.new(b"s", qs.encode(), hashlib.sha256).hexdigest()
    assert binance_signature("s", qs) == expected
    assert len(binance_signature("s", qs)) == 64


def test_bybit_signature_shape():
    sig = bybit_signature("s", "1700000000000", "k", "5000", "coin=USDT")
    assert len(sig) == 64
    assert sig != bybit_signature("s", "1700000000001", "k", "5000", "coin=USDT")


def test_mask_secret_never_leaks():
    assert mask_secret("abcdef123456") == "****3456"
    assert mask_secret("") == "****"
    assert "abcdef123456" not in mask_secret("abcdef123456")


# ------------------------------------------------------------------ matching

def _dep(amount, age_sec, confirmed=True, network="BEP20"):
    return {
        "amount": amount,
        "time_ms": int(time.time() * 1000) - age_sec * 1000,
        "confirmed": confirmed,
        "network": network,
        "txid": "tx1",
    }


def test_match_deposit_hit():
    since = int(time.time() * 1000) - 600_000
    dep = _dep(100.37, 120)
    assert match_deposit([dep], 100.37, since, 0.5, network="BEP20") is dep


def test_match_deposit_tolerance_edge():
    since = int(time.time() * 1000) - 600_000
    assert match_deposit([_dep(100.87, 60)], 100.37, since, 0.5) is not None
    assert match_deposit([_dep(100.88, 60)], 100.37, since, 0.5) is None


def test_match_deposit_rejects():
    since = int(time.time() * 1000) - 600_000
    assert match_deposit([_dep(100.37, 120, confirmed=False)], 100.37, since, 0.5) is None
    assert match_deposit([_dep(100.37, 3600)], 100.37, since, 0.5) is None  # too old
    assert match_deposit([_dep(50.00, 60)], 100.37, since, 0.5) is None  # wrong amount
    assert match_deposit([_dep(100.37, 60, network="TRC20")], 100.37, since, 0.5,
                         network="BEP20") is None  # wrong network
    assert match_deposit([], 100.37, since, 0.5) is None


# ------------------------------------------------------------ fetch parsing

@pytest.mark.asyncio
async def test_fetch_binance_parses():
    async def fake_get(url, headers=None, params=None):
        assert "X-MBX-APIKEY" in headers
        assert "signature=" in url
        now = int(time.time() * 1000)
        return [
            {"amount": "100.37", "coin": "USDT", "network": "BEP20",
             "status": 1, "insertTime": now - 60000, "txId": "abc"},
            {"amount": "5", "coin": "USDT", "network": "BEP20",
             "status": 0, "insertTime": now - 60000, "txId": "pend"},
        ]

    deps = await fetch_binance_deposits("k", "s", http_get=fake_get)
    assert len(deps) == 2
    assert deps[0]["confirmed"] is True and deps[0]["amount"] == 100.37
    assert deps[1]["confirmed"] is False


@pytest.mark.asyncio
async def test_fetch_bybit_parses():
    async def fake_get(url, headers=None, params=None):
        assert headers["X-BAPI-SIGN"]
        now = int(time.time() * 1000)
        return {"retCode": 0, "result": {"rows": [
            {"coin": "USDT", "chain": "BEP20", "amount": "100.37",
             "status": "3", "createdTime": str(now - 60000), "txID": "t1"},
            {"coin": "USDT", "chain": "BEP20", "amount": "9",
             "status": "1", "createdTime": str(now - 60000), "txID": "t2"},
        ]}}

    deps = await fetch_bybit_deposits("k", "s", http_get=fake_get)
    assert len(deps) == 2
    assert deps[0]["confirmed"] is True
    assert deps[1]["confirmed"] is False  # status 1 = confirming


# ------------------------------------------------------ fallback when no keys

@pytest.mark.asyncio
async def test_fallback_to_manual_when_keys_missing(db, fbot, sched, vault, cfg):
    lid = await _live_listing(db, vault)
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.choose_payment(db, fbot, sched, vault, did, "binance", cfg)
    d = await Deals(db).get(did)
    assert d["status"] == "AWAITING_PAYMENT"
    assert float(d["expected_usdt"] or 0) == 0  # no auto amount assigned
    assert sched.get_job(f"pay_poll:{did}") is None  # no poll job
    # buyer got the manual Pay-ID instructions instead
    assert any("Pay ID" in t for _, t in fbot.sent)


# ------------------------------------------------- auto flow end-to-end (mocked)

@pytest.mark.asyncio
async def test_auto_confirm_on_matching_deposit(db, fbot, sched, vault,
                                                auto_cfg, monkeypatch):
    lid = await _live_listing(db, vault)
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.choose_payment(db, fbot, sched, vault, did, "binance",
                                 auto_cfg)
    d = await Deals(db).get(did)
    expected = float(d["expected_usdt"])
    assert expected > 100  # unique amount assigned
    assert sched.get_job(f"pay_poll:{did}") is not None  # polling started

    async def fake_fetch(api_key, api_secret, http_get=None):
        now = int(time.time() * 1000)
        # NOTE: use "now" (not now-30s): the codebase timestamp format
        # truncates seconds (rounds down to the minute), so now is always
        # >= the parsed poll-start time.
        return [{"amount": expected, "time_ms": now,
                 "confirmed": True, "network": "BEP20", "txid": "txmatch"}]

    monkeypatch.setattr(autoverify, "fetch_binance_deposits", fake_fetch)
    await autoverify.poll_payment_job(db, fbot, sched, vault, auto_cfg, did)

    d = await Deals(db).get(did)
    assert d["status"] == "PAID_HELD"
    assert d["auto_verified"] == 1
    assert sched.get_job(f"pay_poll:{did}") is None  # poll stopped
    assert (await Listings(db).get(lid))["status"] == "SOLD"
    # buyer got auto-verified notice + credentials
    assert any("auto-verified" in t for _, t in fbot.sent)


@pytest.mark.asyncio
async def test_poll_timeout_notifies_and_keeps_deal(db, fbot, sched, vault,
                                                     auto_cfg, monkeypatch):
    from datetime import datetime, timedelta, timezone

    lid = await _live_listing(db, vault)
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.choose_payment(db, fbot, sched, vault, did, "binance",
                                 auto_cfg)
    # backdate the poll start beyond the timeout window (codebase timestamp
    # convention: "%Y-%m-%dT%H:%M:%fZ" — no seconds field)
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%fZ")
    await Deals(db).update(did, pay_poll_started_at=past)

    async def fake_fetch(api_key, api_secret, http_get=None):
        return []  # no deposits

    monkeypatch.setattr(autoverify, "fetch_binance_deposits", fake_fetch)
    await autoverify.poll_payment_job(db, fbot, sched, vault, auto_cfg, did)

    d = await Deals(db).get(did)
    assert d["status"] == "AWAITING_PAYMENT"  # stays for manual handling
    assert sched.get_job(f"pay_poll:{did}") is None
    buyer_msgs = [t for cid, t in fbot.sent if cid == 22]
    assert any("not detected" in t for t in buyer_msgs)


@pytest.mark.asyncio
async def test_poll_ignores_non_awaiting_deal(db, fbot, sched, vault,
                                               auto_cfg, monkeypatch):
    lid = await _live_listing(db, vault)
    did = await dealops.open_deal(db, fbot, lid, 22)
    await dealops.choose_payment(db, fbot, sched, vault, did, "binance",
                                 auto_cfg)
    # deal got cancelled meanwhile
    await dealops.cancel_deal(db, fbot, did, 22)

    called = []

    async def fake_fetch(api_key, api_secret, http_get=None):
        called.append(True)
        return []

    monkeypatch.setattr(autoverify, "fetch_binance_deposits", fake_fetch)
    await autoverify.poll_payment_job(db, fbot, sched, vault, auto_cfg, did)
    assert not called  # never even fetched
    assert sched.get_job(f"pay_poll:{did}") is None

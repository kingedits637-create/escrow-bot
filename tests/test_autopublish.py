"""Auto-approve listings: flag on/off, flagged admin notification."""
import pytest

from bot.config import Settings
from bot.core import dealops
from bot.db.repo import Listings, Users


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return None


@pytest.fixture
def fbot():
    return FakeBot()


def _draft(db, sdata):
    return sdata


async def _make_draft(db, followers=5000, avg_views=0):
    users, listings = Users(db), Listings(db)
    await users.ensure(11, "", "Seller")
    lid = await listings.create(11, "TikTok", "TikTok Shop")
    s = {
        "listing_id": lid,
        "title": "Test account",
        "category": "TikTok",
        "subcategory": "TikTok Shop",
        "price_cents": 10000,
        "followers": followers,
        "avg_views": avg_views,
        "country": "USA",
        "account_age": "2 years",
        "description": "nice account",
        "ver_email_access": "yes",
        "ver_violations": "no",
        "ver_violations_dtl": "",
        "ver_monetized": "yes",
        "ver_proof_video": "vid123",
        "ver_proof_shots": ["shot1", "shot2"],
    }
    return lid, s


@pytest.mark.asyncio
async def test_auto_approve_on_goes_live(db, fbot):
    cfg = Settings()
    cfg.auto_approve_listings = True
    lid, s = await _make_draft(db)
    status, flagged = await dealops.submit_listing(db, fbot, 11, s, cfg)
    assert status == "LIVE"
    assert flagged is False
    assert (await Listings(db).get(lid))["status"] == "LIVE"
    seller_msgs = [t for cid, t in fbot.sent if cid == 11]
    assert any("LIVE" in t for t in seller_msgs)


@pytest.mark.asyncio
async def test_auto_approve_flagged_still_live_but_admin_notified(db, fbot,
                                                                   monkeypatch):
    cfg = Settings()
    cfg.auto_approve_listings = True
    admin_notes = []

    async def fake_notify(bot, text):
        admin_notes.append(text)

    monkeypatch.setattr(dealops, "notify_admins", fake_notify)
    # 100k followers but only 10 avg views -> heuristic flags it
    lid, s = await _make_draft(db, followers=100000, avg_views=10)
    status, flagged = await dealops.submit_listing(db, fbot, 11, s, cfg)
    assert status == "LIVE"
    assert flagged is True
    assert (await Listings(db).get(lid))["engagement_flag"] == 1
    assert any("auto-approved but flagged" in t for t in admin_notes)


@pytest.mark.asyncio
async def test_auto_approve_off_goes_pending(db, fbot, monkeypatch):
    cfg = Settings()
    cfg.auto_approve_listings = False
    admin_notes = []

    async def fake_notify(bot, text):
        admin_notes.append(text)

    monkeypatch.setattr(dealops, "notify_admins", fake_notify)
    lid, s = await _make_draft(db)
    status, flagged = await dealops.submit_listing(db, fbot, 11, s, cfg)
    assert status == "PENDING"
    assert (await Listings(db).get(lid))["status"] == "PENDING"
    assert any("pending review" in t for t in admin_notes)
    # seller is NOT told it's live
    seller_msgs = [t for cid, t in fbot.sent if cid == 11]
    assert not any("LIVE" in t for t in seller_msgs)


@pytest.mark.asyncio
async def test_engagement_heuristic():
    assert dealops.engagement_flagged(100000, 10) is True
    assert dealops.engagement_flagged(5000, 0) is False   # no avg views -> skip
    assert dealops.engagement_flagged(500, 1) is False    # too small to judge
    assert dealops.engagement_flagged(100000, 50000) is False

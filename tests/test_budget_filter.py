"""Spec §18: budget filter queries."""
import pytest

from bot.db.repo import Listings, Users
from bot.utils.keyboards import BUDGET_RANGES


@pytest.mark.asyncio
async def test_budget_filter_queries(db):
    users, listings = Users(db), Listings(db)
    await users.ensure(1)
    prices = [3000, 15000, 30000, 60000]  # $30, $150, $300, $600
    for p in prices:
        lid = await listings.create(1, "TikTok", "TikTok Shop")
        await listings.update(lid, title=f"acc {p}", price_cents=p, status="LIVE")

    cases = {
        "0": [3000],
        "1": [15000],
        "2": [30000],
        "3": [60000],
        "any": [3000, 15000, 30000, 60000],
    }
    for key, expected in cases.items():
        lo, hi = BUDGET_RANGES[key]
        got = sorted(l["price_cents"] for l in await listings.live(min_cents=lo, max_cents=hi))
        assert got == expected, f"budget {key}: {got} != {expected}"


@pytest.mark.asyncio
async def test_non_live_excluded(db):
    users, listings = Users(db), Listings(db)
    await users.ensure(1)
    lid = await listings.create(1, "TikTok", "TikTok Shop")
    await listings.update(lid, title="pending acc", price_cents=10000, status="PENDING")
    assert await listings.live() == []
    assert await listings.count_live() == 0

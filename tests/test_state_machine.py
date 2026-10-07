"""Spec §18: escrow state machine — legal transitions pass, illegal ones rejected."""
import pytest

from bot.core.escrow import (
    DealStatus as S,
    IllegalTransition,
    can_transition,
    can_transition_listing,
    transition,
    transition_listing,
)
from bot.db.repo import Deals, Listings, Users


def _mk_deal_status_matrix():
    legal = [
        (S.NEGOTIATING, S.AWAITING_PAYMENT),
        (S.NEGOTIATING, S.CANCELLED),
        (S.AWAITING_PAYMENT, S.PAID_HELD),
        (S.AWAITING_PAYMENT, S.CANCELLED),
        (S.PAID_HELD, S.RELEASED),
        (S.PAID_HELD, S.REFUNDED),
        (S.PAID_HELD, S.DISPUTED),
        (S.DISPUTED, S.RELEASED),
        (S.DISPUTED, S.REFUNDED),
    ]
    illegal = [
        (S.NEGOTIATING, S.PAID_HELD),      # skip payment
        (S.NEGOTIATING, S.RELEASED),       # release without payment
        (S.AWAITING_PAYMENT, S.RELEASED),  # release before paid_held
        (S.PAID_HELD, S.AWAITING_PAYMENT), # backwards
        (S.PAID_HELD, S.CANCELLED),        # cancel after paid -> must refund
        (S.DISPUTED, S.PAID_HELD),         # backwards out of dispute
        (S.RELEASED, S.REFUNDED),          # double-dip: refund after release
        (S.RELEASED, S.DISPUTED),
        (S.REFUNDED, S.RELEASED),          # release after refund
        (S.CANCELLED, S.NEGOTIATING),      # revive cancelled
    ]
    return legal, illegal


LEGAL, ILLEGAL = _mk_deal_status_matrix()


@pytest.mark.parametrize("frm,to", LEGAL)
def test_legal_transitions(frm, to):
    assert can_transition(frm, to), f"{frm} -> {to} should be legal"


@pytest.mark.parametrize("frm,to", ILLEGAL)
def test_illegal_transitions_rejected(frm, to):
    assert not can_transition(frm, to), f"{frm} -> {to} must be rejected"


@pytest.mark.asyncio
async def test_transition_writes_and_audits(db):
    users, deals, listings = Users(db), Deals(db), Listings(db)
    await users.ensure(1)
    await users.ensure(2)
    lid = await listings.create(1, "TikTok", "TikTok Shop")
    did = await deals.create(lid, 1, 2, 10000, 200)
    d = await transition(db, did, S.AWAITING_PAYMENT, "user:1", "test")
    assert d["status"] == S.AWAITING_PAYMENT
    d = await transition(db, did, S.PAID_HELD, "admin:9")
    assert d["status"] == S.PAID_HELD
    row = await db.fetchone(
        "SELECT * FROM audit WHERE entity='deal' AND entity_id=? ORDER BY id DESC", (did,))
    assert row is not None and "awaiting_payment_to_paid_held" in row["action"]


@pytest.mark.asyncio
async def test_illegal_transition_raises_and_state_unchanged(db):
    users, deals, listings = Users(db), Deals(db), Listings(db)
    await users.ensure(1)
    await users.ensure(2)
    lid = await listings.create(1, "TikTok", "TikTok Shop")
    did = await deals.create(lid, 1, 2, 10000, 200)
    with pytest.raises(IllegalTransition):
        await transition(db, did, S.RELEASED, "user:1")  # skip straight to release
    d = await deals.get(did)
    assert d["status"] == S.NEGOTIATING  # unchanged


@pytest.mark.asyncio
async def test_anti_double_dip_no_refund_after_release(db):
    users, deals, listings = Users(db), Deals(db), Listings(db)
    await users.ensure(1)
    await users.ensure(2)
    lid = await listings.create(1, "TikTok", "TikTok Shop")
    did = await deals.create(lid, 1, 2, 10000, 200)
    for to, actor in [(S.AWAITING_PAYMENT, "user:1"), (S.PAID_HELD, "admin:9"),
                      (S.RELEASED, "user:1")]:
        await transition(db, did, to, actor)
    with pytest.raises(IllegalTransition):
        await transition(db, did, S.REFUNDED, "admin:9")


@pytest.mark.asyncio
async def test_listing_lifecycle(db):
    users, listings = Users(db), Listings(db)
    await users.ensure(5)
    lid = await listings.create(5, "TikTok", "TikTok Shop")
    await transition_listing(db, lid, "PENDING", "user:5")
    await transition_listing(db, lid, "LIVE", "admin:9")
    assert (await listings.get(lid))["status"] == "LIVE"
    with pytest.raises(IllegalTransition):
        await transition_listing(db, lid, "PENDING", "admin:9")  # no going back
    assert can_transition_listing("PENDING", "LIVE")
    assert not can_transition_listing("LIVE", "PENDING")
    assert not can_transition_listing("SOLD", "LIVE")

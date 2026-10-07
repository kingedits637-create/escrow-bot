"""Escrow state machine (spec §12). Every transition is validated;
illegal transitions raise IllegalTransition and are rejected. Every
successful transition is audit-logged with a timestamp.
"""
from __future__ import annotations

from typing import Optional

from bot.db.base import Database
from bot.db.repo import Audit, Deals, utcnow


class IllegalTransition(Exception):
    pass


class DealStatus:
    NEGOTIATING = "NEGOTIATING"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    PAID_HELD = "PAID_HELD"
    DISPUTED = "DISPUTED"
    RELEASED = "RELEASED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


TRANSITIONS: dict[str, set[str]] = {
    DealStatus.NEGOTIATING: {DealStatus.AWAITING_PAYMENT, DealStatus.CANCELLED},
    DealStatus.AWAITING_PAYMENT: {DealStatus.PAID_HELD, DealStatus.CANCELLED},
    DealStatus.PAID_HELD: {
        DealStatus.RELEASED,
        DealStatus.REFUNDED,
        DealStatus.DISPUTED,
    },
    DealStatus.DISPUTED: {DealStatus.RELEASED, DealStatus.REFUNDED},
    # Terminal states: no outgoing transitions.
    DealStatus.RELEASED: set(),
    DealStatus.REFUNDED: set(),
    DealStatus.CANCELLED: set(),
}


def can_transition(frm: str, to: str) -> bool:
    return to in TRANSITIONS.get(frm, set())


async def transition(db: Database, deal_id: int, to: str, actor: str,
                     note: str = "") -> dict:
    """Move a deal to a new status. Raises IllegalTransition on misuse."""
    deals, audit = Deals(db), Audit(db)
    deal = await deals.get(deal_id)
    if deal is None:
        raise IllegalTransition(f"deal {deal_id} does not exist")
    frm = deal["status"]
    if not can_transition(frm, to):
        raise IllegalTransition(f"illegal transition {frm} -> {to} (deal {deal_id})")
    await deals.update(deal_id, status=to)
    await audit.log(actor, f"deal_{frm.lower()}_to_{to.lower()}", "deal", deal_id, note)
    deal["status"] = to
    return deal


class ListingStatus:
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    LIVE = "LIVE"
    REJECTED = "REJECTED"
    SOLD = "SOLD"
    BANNED = "BANNED"


LISTING_TRANSITIONS: dict[str, set[str]] = {
    # DRAFT -> LIVE is the auto-approve path (owner-ordered AUTO_APPROVE_LISTINGS).
    ListingStatus.DRAFT: {ListingStatus.PENDING, ListingStatus.LIVE},
    ListingStatus.PENDING: {ListingStatus.LIVE, ListingStatus.REJECTED},
    ListingStatus.LIVE: {ListingStatus.SOLD, ListingStatus.BANNED},
    ListingStatus.REJECTED: {ListingStatus.PENDING},  # seller may resubmit
    ListingStatus.SOLD: set(),
    ListingStatus.BANNED: set(),
}


def can_transition_listing(frm: str, to: str) -> bool:
    return to in LISTING_TRANSITIONS.get(frm, set())


async def transition_listing(db: Database, listing_id: int, to: str,
                             actor: str, note: str = "") -> None:
    from bot.db.repo import Listings

    listings, audit = Listings(db), Audit(db)
    listing = await listings.get(listing_id)
    if listing is None:
        raise IllegalTransition(f"listing {listing_id} does not exist")
    frm = listing["status"]
    if not can_transition_listing(frm, to):
        raise IllegalTransition(f"illegal listing transition {frm} -> {to}")
    await listings.set_status(listing_id, to, note)
    await audit.log(actor, f"listing_{frm.lower()}_to_{to.lower()}", "listing",
                    listing_id, note)

"""Deal engine — ALL escrow business logic lives here (WORKER B, spec §§7-13).

Handlers in bot/handlers/deal.py are thin: they verify caller roles from
the DB, catch DealError, and delegate here. Every deal/listing status
change goes through bot.core.escrow.transition / transition_listing;
timers are scheduled only via bot.core.timers helpers.

Note on imports: bot.core.timers imports this module lazily, so timer
helpers are imported *inside* functions (deferred) to avoid a cycle.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bot.config import settings
from bot.core.escrow import (
    DealStatus,
    IllegalTransition,
    ListingStatus,
    transition,
    transition_listing,
)
from bot.db.base import Database
from bot.db.repo import (
    Audit,
    Credentials,
    Deals,
    Jobs,
    Ledger,
    Listings,
    Messages,
    Payouts,
    Users,
    utcnow,
)
from bot.payments import autoverify
from bot.payments.binance import BinanceAdapter
from bot.payments.bybit import BybitAdapter
from bot.payments.manual import ManualAdapter
from bot.utils import keyboards, money, texts
from bot.utils.masking import mask_text
from bot.utils.money import fmt_cents, fmt_pkr, usd_cents_to_pkr
from bot.utils.notify import notify_admins
from bot.utils.vault import VaultError

_TS_FMT = "%Y-%m-%dT%H:%M:%fZ"

# Deal statuses in which the chat relay is allowed to operate.
_ACTIVE_CHAT_STATUSES = {
    DealStatus.NEGOTIATING,
    DealStatus.AWAITING_PAYMENT,
    DealStatus.PAID_HELD,
    DealStatus.DISPUTED,
}


class DealError(Exception):
    """Business-rule violation. Handlers catch this and show it to the user."""


# ---------------------------------------------------------------- helpers

def _actor_user(tg_id: int) -> str:
    return f"user:{tg_id}"


def _actor_admin(tg_id: int) -> str:
    return f"admin:{tg_id}"


def _fill(template: str, **kw: Any) -> str:
    """Brace-safe placeholder fill: replaces {name} literally, so user text
    containing { or } can never break str.format()."""
    out = template
    for key, value in kw.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime(_TS_FMT)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, _TS_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


async def _get_deal(db: Database, deal_id: int) -> dict:
    deal = await Deals(db).get(deal_id)
    if deal is None:
        raise DealError("Deal not found.")
    return deal


def _role(deal: dict, user_id: int) -> Optional[str]:
    if user_id == deal["buyer_id"]:
        return "buyer"
    if user_id == deal["seller_id"]:
        return "seller"
    return None


async def _other_active_deals(db: Database, deal: dict) -> list[dict]:
    """Other still-active deals on the same listing (competing negotiations)."""
    return [
        d
        for d in await Deals(db).active(500)
        if d["listing_id"] == deal["listing_id"] and d["id"] != deal["id"]
    ]


async def _send(bot: Any, tg_id: int, text: str, **kw: Any) -> None:
    """Best-effort send (user may have blocked the bot)."""
    try:
        await bot.send_message(tg_id, text, parse_mode="HTML", **kw)
    except Exception:
        pass


async def _notify_admins_kb(bot: Any, text: str, reply_markup: Any) -> None:
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML",
                                  reply_markup=reply_markup)
        except Exception:
            pass


async def _drop_job_rows(db: Database, deal_id: int) -> None:
    """Remove persisted timer rows so a restart never re-registers them.
    (The live APS jobs are removed separately wherever a scheduler is
    available; any orphaned firing is a safe no-op because every timer
    callback re-checks deal status.)"""
    jobs = Jobs(db)
    for kind in ("delivery_deadline", "handover_lock", "hold_release"):
        await jobs.delete(deal_id, kind)


# ------------------------------------------------------- listing submission

def engagement_flagged(followers: int, avg_views: int) -> bool:
    """Heuristic from the spec: absurd follower/view mismatch → flagged
    for admin spot-check. Never blocks by itself."""
    return followers > 1000 and avg_views > 0 and avg_views < followers * 0.001


async def submit_listing(db: Database, bot: Any, seller_tg_id: int,
                         s: dict, cfg: Any) -> tuple[str, bool]:
    """Persist a finished sell-flow draft and publish it.

    Returns (new_status, flagged). When cfg.auto_approve_listings is true
    the listing goes LIVE immediately (DRAFT -> LIVE); flagged listings go
    live AND trigger an admin review/remove notification. Otherwise the
    listing goes PENDING for manual admin approval (old behavior).
    """
    from bot.db.repo import Listings as _Listings

    listings = _Listings(db)
    listing_id = s["listing_id"]
    flagged = engagement_flagged(s.get("followers", 0), s.get("avg_views", 0))

    checklist = {
        "age": "Seller declared",
        "email_access": "Seller declared",
        "violations": "Seller declared",
        "monetization": "Seller declared",
        "proof_video": "Proof attached",
        "proof_shots": "Proof attached",
    }
    await listings.update(
        listing_id,
        title=s["title"],
        price_cents=s["price_cents"],
        followers=s["followers"],
        country=s["country"],
        account_age=s["account_age"],
        description=s["description"],
        ver_email_access=s["ver_email_access"],
        ver_violations=s["ver_violations"],
        ver_violations_dtl=s.get("ver_violations_dtl", ""),
        ver_monetized=s["ver_monetized"],
        ver_proof_video=s["ver_proof_video"],
        ver_proof_shots=json.dumps(s["ver_proof_shots"]),
        engagement_flag=1 if flagged else 0,
    )
    await listings.set_checklist(listing_id, checklist)

    title = html.escape(s.get("title", ""))
    cat = html.escape(s.get("category", ""))
    sub = html.escape(s.get("subcategory", ""))
    meta = (f"#{listing_id} — {title}\n{cat} → {sub} · "
            f"{fmt_cents(s['price_cents'])} · {s['followers']:,} followers")

    if cfg.auto_approve_listings:
        await transition_listing(db, listing_id, ListingStatus.LIVE,
                                 actor=f"user:{seller_tg_id}",
                                 note="auto-approved")
        await listings.update(listing_id, approved_at=utcnow())
        await _send(bot, seller_tg_id, texts.LISTING_AUTO_LIVE)
        if flagged:
            await notify_admins(
                bot,
                f"⚠️ <b>Listing auto-approved but flagged</b>\n{meta}\n"
                f"<i>Engagement heuristic flagged it — review/remove in "
                f"🛠️ Admin Panel if it's a scam.</i>",
            )
        return ListingStatus.LIVE, flagged

    await transition_listing(db, listing_id, ListingStatus.PENDING,
                             actor=f"user:{seller_tg_id}",
                             note="submitted via sell flow")
    await notify_admins(
        bot,
        f"📋 <b>New listing pending review</b>\n{meta}"
        + (" ⚠️ <i>engagement auto-flagged</i>" if flagged else "")
        + "\nOpen 🛠️ Admin Panel → Pending listings.",
    )
    return ListingStatus.PENDING, flagged


def _payout_label(payout: Optional[dict]) -> str:
    """Human-readable, masked payout description — never full secrets."""
    if not payout or not payout.get("method"):
        return "not set — an admin will contact you"
    method = html.escape(str(payout["method"]))
    details = payout.get("details") or {}
    masked_bits = []
    for key, val in details.items():
        val = str(val)
        masked_bits.append(
            f"{html.escape(str(key))}: {val[:2]}***{val[-2:]}" if len(val) > 4 else "***"
        )
    detail_txt = ", ".join(masked_bits[:2])
    return f"{method} ({detail_txt})" if detail_txt else method


# ---------------------------------------------------------------- open / pay

async def open_deal(db: Database, bot: Any, listing_id: int, buyer_id: int) -> int:
    """Open (or re-enter) a deal on a listing. Returns the deal id.

    The listing stays LIVE until a payment is confirmed — competing
    negotiations are allowed; the first PAID deal wins and the others
    are auto-cancelled (see admin_confirm_payment).
    """
    listing = await Listings(db).get(listing_id)
    if listing is None:
        raise DealError("Listing not found.")
    if listing["status"] != ListingStatus.LIVE:
        raise DealError("This listing is no longer available.")
    if listing["seller_id"] == buyer_id:
        raise DealError("You can't buy your own listing.")

    existing = await Deals(db).active_for_listing(listing_id)
    if existing is not None:
        if existing["buyer_id"] == buyer_id:
            return existing["id"]  # idempotent re-entry into own open deal
        # Different buyer: competing negotiation — allowed, first PAID wins.

    deal_id = await Deals(db).create(
        listing_id,
        buyer_id,
        listing["seller_id"],
        listing["price_cents"],
        0,  # commission is set when the buyer picks a payment method
    )
    await Audit(db).log(_actor_user(buyer_id), "deal_opened", "deal", deal_id,
                        f"listing {listing_id}")
    await _send(
        bot,
        listing["seller_id"],
        _fill(
            texts.DEAL_OPENED_SELLER,
            title=html.escape(listing["title"] or "your listing"),
            deal_id=deal_id,
        ),
    )
    return deal_id


async def choose_payment(db: Database, bot: Any, scheduler: Any,
                         vault: Any, deal_id: int, method: str,
                         cfg: Any) -> None:
    """Buyer picks a payment method: NEGOTIATING -> AWAITING_PAYMENT.

    Sets the per-method commission snapshot on the deal:
      - binance/bybit: commission_cents = COMMISSION_USDT_CENTS (USD)
      - manual:        payable_pkr + commission_pkr (PKR via USD_PKR_RATE)
    For auto-verifiable crypto methods a unique expected USDT amount is
    assigned and deposit polling starts.
    """
    if method not in ("binance", "bybit", "manual"):
        raise DealError("Unknown payment method.")
    deal = await _get_deal(db, deal_id)
    if deal["status"] != DealStatus.NEGOTIATING:
        raise DealError("Payment can't be started in this deal state.")
    adapters = {
        "binance": BinanceAdapter(cfg),
        "bybit": BybitAdapter(cfg),
        "manual": ManualAdapter(),
    }
    adapter = adapters[method]

    if method == "manual":
        commission_cents, commission_pkr = 0, int(cfg.commission_local_pkr)
        payable_pkr = usd_cents_to_pkr(deal["price_cents"], cfg.usd_pkr_rate)
    else:
        commission_cents, commission_pkr = int(cfg.commission_usdt_cents), 0
        payable_pkr = 0
    await Deals(db).update(
        deal_id,
        payment_method=method,
        commission_cents=commission_cents,
        commission_pkr=commission_pkr,
        payable_pkr=payable_pkr,
    )
    deal.update(payment_method=method, commission_cents=commission_cents,
                commission_pkr=commission_pkr, payable_pkr=payable_pkr)

    auto = method in ("binance", "bybit") and adapter.auto_verify_possible
    if auto:
        # Unique-amount trick: avoid clashes with other awaiting deals.
        taken = {
            float(d.get("expected_usdt") or 0)
            for d in await Deals(db).active(500)
            if d.get("payment_method") == method and d.get("expected_usdt")
        }
        expected = autoverify.generate_expected_usdt(deal["price_cents"], taken)
        await Deals(db).update(
            deal_id, expected_usdt=expected,
            pay_poll_started_at=utcnow(), auto_verified=0,
        )
        deal["expected_usdt"] = expected
        await autoverify.start_pay_poll(db, scheduler, bot, vault, cfg, deal_id)

    await transition(db, deal_id, DealStatus.AWAITING_PAYMENT,
                     _actor_user(deal["buyer_id"]), f"method: {method}")
    if auto:
        await _send(bot, deal["buyer_id"], adapter.instructions(deal, cfg))
    else:
        await _send(
            bot,
            deal["buyer_id"],
            adapter.instructions(deal, cfg)
            + "\n\n🧾 Once you've paid, send your proof <b>here</b> "
              "(Pay ID as text, or a screenshot photo).",
        )


async def submit_payment_proof(db: Database, bot: Any, deal_id: int,
                               user_id: int, proof: str) -> None:
    """Buyer submits a Pay ID (text) or screenshot (photo file_id)."""
    deal = await _get_deal(db, deal_id)
    if user_id != deal["buyer_id"]:
        raise DealError("Only the buyer can submit payment proof.")
    if deal["status"] != DealStatus.AWAITING_PAYMENT:
        raise DealError("This deal is not waiting for payment.")
    if not proof or not proof.strip():
        raise DealError("Empty proof — send your Pay ID or a screenshot.")
    proof = proof.strip()[:500]
    await Deals(db).update(deal_id, payment_ref=proof)
    await Audit(db).log(_actor_user(user_id), "payment_proof_submitted",
                        "deal", deal_id, deal["payment_method"])

    method_label = "Binance USDT" if deal["payment_method"] == "binance" \
        else "Easypaisa / JazzCash / Bank"
    admin_text = (
        f"💳 <b>Payment proof</b> — Deal #{deal_id}\n"
        f"Amount: <b>{fmt_cents(deal['price_cents'])}</b> via {method_label}\n"
    )
    kb = keyboards.approve_reject("pay", deal_id)
    if proof.startswith("photo:"):
        file_id = proof[len("photo:"):]
        admin_text += "Screenshot attached — review it, then confirm or reject."
        for admin_id in settings.admin_ids:
            try:
                await bot.send_photo(admin_id, file_id, caption=admin_text,
                                    parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
    else:
        admin_text += f"Reference: <code>{html.escape(proof[:200])}</code>"
        await _notify_admins_kb(bot, admin_text, kb)

    await _send(
        bot,
        deal["buyer_id"],
        "🧾 <b>Proof received.</b> An admin will confirm your payment shortly. "
        "You'll be notified the moment it's confirmed.",
    )


# ------------------------------------------------- payment confirm / reject

async def _confirm_payment_core(db: Database, bot: Any, scheduler: Any,
                                vault: Any, deal_id: int, actor: str,
                                via: str) -> dict:
    """Shared confirm logic: AWAITING_PAYMENT -> PAID_HELD, listing SOLD,
    competing deals auto-cancelled, credentials delivered instantly."""
    deal = await _get_deal(db, deal_id)
    if deal["status"] != DealStatus.AWAITING_PAYMENT:
        raise DealError("This deal is not awaiting payment.")

    await transition(db, deal_id, DealStatus.PAID_HELD, actor,
                     f"payment confirmed via {via}")
    await Deals(db).update(deal_id, paid_at=utcnow())
    await Ledger(db).add("hold", deal["price_cents"], actor, deal_id,
                         f"escrow hold via {via}")

    # First PAID wins: listing goes SOLD, competing negotiations die.
    listing = await Listings(db).get(deal["listing_id"])
    if listing is not None and listing["status"] == ListingStatus.LIVE:
        await transition_listing(db, deal["listing_id"], ListingStatus.SOLD,
                                 actor, f"deal {deal_id} paid")
    for other in await _other_active_deals(db, deal):
        try:
            await transition(db, other["id"], DealStatus.CANCELLED, "bot",
                             f"listing sold via deal {deal_id}")
        except IllegalTransition:
            continue
        await _send(
            bot,
            other["buyer_id"],
            f"❌ Deal #{other['id']} was closed — this listing just sold "
            f"to another buyer. Your money was never held, nothing is owed.",
        )
        await _drop_job_rows(db, other["id"])

    await _drop_job_rows(db, deal_id)
    autoverify.stop_pay_poll(scheduler, deal_id)
    await deliver_credentials(db, bot, scheduler, vault, deal_id)
    return await _get_deal(db, deal_id)


async def admin_confirm_payment(db: Database, bot: Any, scheduler: Any,
                                vault: Any, deal_id: int, admin_id: int) -> dict:
    """Admin taps Confirm: AWAITING_PAYMENT -> PAID_HELD, listing SOLD,
    competing deals auto-cancelled, credentials delivered instantly."""
    deal = await _get_deal(db, deal_id)
    return await _confirm_payment_core(
        db, bot, scheduler, vault, deal_id, _actor_admin(admin_id),
        deal.get("payment_method") or "unknown")


async def auto_confirm_payment(db: Database, bot: Any, scheduler: Any,
                               vault: Any, deal_id: int,
                               txid: str = "") -> dict:
    """Bot auto-confirmed a detected on-chain deposit: same delivery flow
    as an admin tap, actor 'bot'."""
    deal = await _get_deal(db, deal_id)
    await _send(bot, deal["buyer_id"], texts.PAY_AUTO_VERIFIED)
    tx_note = f", tx {txid[:24]}" if txid else ""
    return await _confirm_payment_core(
        db, bot, scheduler, vault, deal_id, "bot",
        f"auto-verified {deal.get('payment_method') or 'crypto'}{tx_note}")


async def admin_reject_payment(db: Database, bot: Any, deal_id: int,
                              admin_id: int, reason: str) -> dict:
    """Admin taps Reject: back to NEGOTIATING so the buyer can retry."""
    deal = await _get_deal(db, deal_id)
    if deal["status"] != DealStatus.AWAITING_PAYMENT:
        raise DealError("This deal is not awaiting payment.")
    reason = (reason or "not specified").strip()[:500]
    await transition(db, deal_id, DealStatus.NEGOTIATING,
                     _actor_admin(admin_id), f"payment rejected: {reason}")
    await Deals(db).update(deal_id, payment_ref="")
    await _send(
        bot,
        deal["buyer_id"],
        f"❌ <b>Payment proof rejected:</b> {html.escape(reason)}\n\n"
        f"No money was taken into escrow. Tap <b>💰 Buy Now / Pay</b> on the "
        f"deal to pay again and resubmit.",
    )
    return await _get_deal(db, deal_id)


# ------------------------------------------------------------------ delivery

async def deliver_credentials(db: Database, bot: Any, scheduler: Any,
                              vault: Any, deal_id: int) -> None:
    """Decrypt the vault blob and deliver it to the buyer (one-time full
    view, then a masked notice). Sets delivered_at / handover_until /
    hold_until and schedules the handover_lock + hold_release timers."""
    from bot.core import timers  # deferred: timers imports dealops lazily

    deal = await _get_deal(db, deal_id)
    blob = await Credentials(db).get_blob(deal["listing_id"])
    if not blob:
        await notify_admins(
            bot,
            f"🚨 <b>Vault empty</b> — Deal #{deal_id} is PAID_HELD but the "
            f"listing has no credentials. Manual intervention needed.",
        )
        raise DealError("No credentials in the vault for this listing.")
    try:
        creds = vault.decrypt(blob)
    except VaultError:
        await notify_admins(
            bot,
            f"🚨 <b>Vault decrypt failed</b> — Deal #{deal_id}. "
            f"Check FERNET_KEY immediately.",
        )
        raise DealError("Could not decrypt credentials — admin notified.")

    now = _now()
    handover_until = _iso(now + timedelta(hours=settings.handover_hours))
    hold_until = _iso(now + timedelta(hours=settings.hold_hours))
    await Deals(db).update(
        deal_id,
        delivered_at=_iso(now),
        handover_until=handover_until,
        hold_until=hold_until,
    )
    await Audit(db).log("bot", "credentials_delivered", "deal", deal_id, "")

    # One-time full view...
    await _send(
        bot,
        deal["buyer_id"],
        _fill(texts.DELIVERED_BUYER, creds=html.escape(creds)),
    )
    # ...then the masked notice (the creds are never re-sent).
    await _send(
        bot,
        deal["buyer_id"],
        "🔐 <i>One-time view: the credentials above won't be shown again. "
        "Save them now.</i>",
    )
    await _send(bot, deal["seller_id"], texts.DELIVERED_SELLER)

    await timers.schedule_job(db, scheduler, bot, deal_id, "handover_lock")
    await timers.schedule_job(db, scheduler, bot, deal_id, "hold_release")


async def on_delivery_deadline(db: Database, bot: Any, deal_id: int) -> None:
    """Safety net for listings that require seller action post-payment.
    (Standard listings deliver instantly, so delivery_due_at is never set
    and this is a no-op — but the guard stays for spec §9 compliance.)"""
    deal = await Deals(db).get(deal_id)
    if deal is None or deal.get("delivered_at"):
        return
    if not deal.get("delivery_due_at"):
        return
    if deal["status"] == DealStatus.PAID_HELD:
        await refund_deal(db, bot, deal_id, "bot",
                          "seller missed the 12h delivery deadline")
    elif deal["status"] == DealStatus.AWAITING_PAYMENT:
        await transition(db, deal_id, DealStatus.CANCELLED, "bot",
                         "delivery deadline missed before payment")
        await _send(bot, deal["buyer_id"],
                    f"❌ Deal #{deal_id} expired before delivery could happen.")
    await _drop_job_rows(db, deal_id)


# ------------------------------------------------------------- timers (jobs)

def handover_active(deal: dict) -> bool:
    """True while the buyer may exchange emails/OTPs with the seller."""
    if not deal.get("delivered_at") or not deal.get("handover_until"):
        return False
    if not deal.get("chat_open"):
        return False
    until = _parse_iso(deal["handover_until"])
    return until is not None and _now() < until


def _hold_expired(deal: dict) -> bool:
    until = _parse_iso(deal.get("hold_until"))
    return until is not None and _now() >= until


async def on_handover_lock(db: Database, bot: Any, deal_id: int) -> None:
    """8h window ended: chat becomes read-only, the 24h hold continues."""
    deal = await Deals(db).get(deal_id)
    if deal is None or not deal.get("chat_open"):
        return
    await Deals(db).update(deal_id, chat_open=0)
    await Audit(db).log("bot", "handover_locked", "deal", deal_id, "")
    await _send(bot, deal["buyer_id"], texts.HANDOVER_LOCKED)
    await _send(bot, deal["seller_id"], texts.HANDOVER_LOCKED)


async def on_hold_release(db: Database, bot: Any, deal_id: int,
                          scheduler: Any = None) -> None:
    """24h hold expired with no dispute: auto-release to the seller.

    (timers.py invokes this as func(db, bot, deal_id); the scheduler
    parameter is kept optional for direct callers.)
    """
    deal = await Deals(db).get(deal_id)
    if deal is None:
        return
    if deal["status"] != DealStatus.PAID_HELD:
        return  # disputed / released / refunded / cancelled — nothing to do
    if not _hold_expired(deal):
        return  # defensive: job fired early
    await release_funds(db, bot, deal_id, "bot")
    if scheduler is not None:
        from bot.core import timers  # deferred
        await timers.cancel_all_jobs(db, scheduler, deal_id)


# ------------------------------------------------------- release / refund

async def release_funds(db: Database, bot: Any, deal_id: int,
                        actor: str) -> dict:
    """Release escrow to the seller (minus the per-method commission).

    - Binance/Bybit: net = price_usd - COMMISSION_USDT_CENTS (USD).
    - Easypaisa/JazzCash/bank: net = payable_pkr - COMMISSION_LOCAL_PKR.
    price <= commission (either currency) is NEVER auto-released — the
    deal stays HELD and the admins get an 'admin review needed' alert.
    """
    from bot.handlers.rating import prompt_ratings  # deferred: handlers import dealops

    deal = await _get_deal(db, deal_id)
    if deal["status"] not in (DealStatus.PAID_HELD, DealStatus.DISPUTED):
        raise DealError("This deal can't be released from its current state.")

    local = (deal.get("payment_method") or "") == "manual"
    if local:
        gross_pkr = int(deal.get("payable_pkr") or 0)
        fee_pkr = int(deal.get("commission_pkr") or 0)
        net = money.release_amount_pkr(gross_pkr, fee_pkr)
        cur = "PKR"
        gross_txt, fee_txt = fmt_pkr(gross_pkr), fmt_pkr(fee_pkr)
    else:
        net = money.release_amount(deal["price_cents"],
                                   deal["commission_cents"])
        cur = "USDT"
        gross_txt = fee_txt = ""
    if net is None:
        # Admin-review edge: releasing $0/negative would be wrong — hold.
        if local:
            detail = (f"payable {fmt_pkr(gross_pkr)} ≤ commission "
                      f"{fmt_pkr(fee_pkr)}")
        else:
            detail = (f"price {fmt_cents(deal['price_cents'])} ≤ commission "
                      f"{fmt_cents(deal['commission_cents'])}")
        await notify_admins(
            bot,
            f"⚠️ <b>Admin review needed</b> — Deal #{deal_id}: {detail}. "
            f"Funds stay HELD; resolve manually.",
        )
        return deal

    await transition(db, deal_id, DealStatus.RELEASED, actor,
                     f"net {net} {cur} to seller")
    await Deals(db).update(deal_id, released_at=utcnow())
    await Ledger(db).add("release", net, actor, deal_id,
                         f"funds released to seller ({cur})")
    if local:
        await Ledger(db).add("commission", fee_pkr, actor, deal_id,
                             "platform commission (PKR)")
    else:
        await Ledger(db).add("commission", deal["commission_cents"], actor,
                             deal_id, "platform commission (USDT)")
    await Users(db).bump_deals(deal["buyer_id"])
    await Users(db).bump_deals(deal["seller_id"])
    await _drop_job_rows(db, deal_id)

    payout = await Payouts(db).get(deal["seller_id"])
    if local:
        amount_txt, fee_txt = fmt_pkr(net), fmt_pkr(fee_pkr)
    else:
        amount_txt, fee_txt = fmt_cents(net), fmt_cents(deal["commission_cents"])
    await _send(
        bot,
        deal["seller_id"],
        _fill(
            texts.RELEASED_SELLER,
            deal_id=deal_id,
            amount=amount_txt,
            fee=fee_txt,
            method=_payout_label(payout),
        ),
    )
    await _send(
        bot,
        deal["buyer_id"],
        _fill(texts.RELEASED_BUYER, deal_id=deal_id),
    )
    await prompt_ratings(db, bot, deal_id)
    return await _get_deal(db, deal_id)


async def refund_deal(db: Database, bot: Any, deal_id: int,
                      actor: str, reason: str) -> dict:
    """Full refund to the buyer via the original payment method.

    ANTI-DOUBLE-DIP: only from PAID_HELD / DISPUTED / AWAITING_PAYMENT —
    NEVER from RELEASED (buyer can't keep the account AND get a refund).
    """
    from bot.handlers.rating import prompt_ratings  # deferred: handlers import dealops

    deal = await _get_deal(db, deal_id)
    if deal["status"] == DealStatus.RELEASED:
        raise DealError("No refunds after release — except proven fraud, "
                        "which an admin handles manually.")
    if deal["status"] not in (DealStatus.PAID_HELD, DealStatus.DISPUTED,
                              DealStatus.AWAITING_PAYMENT):
        raise DealError("There's nothing to refund in this deal state.")

    reason = (reason or "no reason given").strip()[:500]
    await transition(db, deal_id, DealStatus.REFUNDED, actor, reason)
    await Deals(db).update(deal_id, refunded_at=utcnow(),
                           refund_reason=reason, refund_actor=actor)
    await Ledger(db).add("refund", deal["price_cents"], actor, deal_id, reason)
    await _drop_job_rows(db, deal_id)

    method_note = {
        "binance": "Binance USDT (same address)",
        "bybit": "Bybit USDT (same address)",
        "manual": "Easypaisa / JazzCash / bank (same number/account)",
    }.get(deal["payment_method"], "your original payment method")
    if (deal.get("payment_method") or "") == "manual":
        amount_txt = fmt_pkr(deal.get("payable_pkr") or 0)
    else:
        amount_txt = fmt_cents(deal["price_cents"])
    await _send(
        bot,
        deal["buyer_id"],
        _fill(
            texts.REFUNDED_BUYER,
            deal_id=deal_id,
            amount=amount_txt,
            reason=html.escape(reason),
        ) + f"\n\nIt will return via {html.escape(method_note)}.",
    )
    await _send(
        bot,
        deal["seller_id"],
        _fill(texts.REFUNDED_SELLER, deal_id=deal_id,
              reason=html.escape(reason)),
    )
    await prompt_ratings(db, bot, deal_id)
    return await _get_deal(db, deal_id)


# ------------------------------------------------------------------ cancel

async def cancel_deal(db: Database, bot: Any, deal_id: int,
                      user_id: int) -> dict:
    """Buyer: only in NEGOTIATING / AWAITING_PAYMENT.
    Seller: NEGOTIATING / AWAITING_PAYMENT / PAID_HELD-before-delivery.
    Money held (PAID_HELD, undelivered) -> refund. Listing stays LIVE
    (it only goes SOLD on a confirmed payment)."""
    deal = await _get_deal(db, deal_id)
    role = _role(deal, user_id)
    if role is None:
        raise DealError("You're not part of this deal.")
    actor = _actor_user(user_id)
    status = deal["status"]

    if role == "buyer":
        if status not in (DealStatus.NEGOTIATING, DealStatus.AWAITING_PAYMENT):
            raise DealError("Too late to cancel — open a dispute instead.")
    else:  # seller
        if status == DealStatus.PAID_HELD and deal.get("delivered_at"):
            raise DealError("Credentials already delivered — contact an admin.")
        if status not in (DealStatus.NEGOTIATING, DealStatus.AWAITING_PAYMENT,
                          DealStatus.PAID_HELD):
            raise DealError("This deal can't be cancelled now.")

    if status == DealStatus.PAID_HELD and not deal.get("delivered_at"):
        # Money was held but never delivered -> instant refund.
        return await refund_deal(db, bot, deal_id, actor,
                                 f"cancelled by {role} before delivery")

    await transition(db, deal_id, DealStatus.CANCELLED, actor,
                     f"cancelled by {role}")
    await _drop_job_rows(db, deal_id)
    other_id = deal["seller_id"] if role == "buyer" else deal["buyer_id"]
    await _send(
        bot,
        other_id,
        f"❌ Deal #{deal_id} was cancelled by the {role}. "
        f"No money changed hands.",
    )
    return await _get_deal(db, deal_id)


# ----------------------------------------------------------------- disputes

async def open_dispute(db: Database, bot: Any, deal_id: int,
                       buyer_id: int, reason: str) -> dict:
    """Buyer opens a dispute from PAID_HELD. The 24h hold timer is paused
    (job row removed; any orphaned firing is a safe no-op since the deal
    is no longer PAID_HELD)."""
    deal = await _get_deal(db, deal_id)
    if buyer_id != deal["buyer_id"]:
        raise DealError("Only the buyer can open a dispute.")
    if deal["status"] != DealStatus.PAID_HELD:
        raise DealError("Disputes can only be opened on a paid deal.")
    reason = (reason or "").strip()[:1000]
    if not reason:
        raise DealError("Please describe the problem.")

    await transition(db, deal_id, DealStatus.DISPUTED,
                     _actor_user(buyer_id), reason[:200])
    await Deals(db).update(deal_id, disputed_at=utcnow(),
                           dispute_reason=reason)
    await Jobs(db).delete(deal_id, "hold_release")  # pause the 24h timer

    await _send(bot, buyer_id, _fill(texts.DISPUTE_OPENED, deal_id=deal_id))
    await _notify_admins_kb(
        bot,
        _fill(texts.DISPUTE_ADMIN, deal_id=deal_id,
              reason=html.escape(reason)),
        keyboards.dispute_decide(deal_id),
    )
    return await _get_deal(db, deal_id)


async def admin_resolve_dispute(db: Database, bot: Any, scheduler: Any,
                                deal_id: int, admin_id: int,
                                decision: str, reason: str) -> dict:
    """Admin resolves: 'refund' -> buyer refunded, 'release' -> seller paid."""
    from bot.core import timers  # deferred

    deal = await _get_deal(db, deal_id)
    if deal["status"] != DealStatus.DISPUTED:
        raise DealError("This deal is not under dispute.")
    if decision not in ("refund", "release"):
        raise DealError("Unknown dispute decision.")
    reason = (reason or "").strip()[:500] or "dispute resolved by admin"

    # Defensive: make sure the paused hold timer can never fire later.
    await timers.cancel_job(db, scheduler, deal_id, "hold_release")

    if decision == "refund":
        return await refund_deal(db, bot, deal_id, _actor_admin(admin_id),
                                 f"dispute: {reason}")
    return await release_funds(db, bot, deal_id, _actor_admin(admin_id))


async def confirm_receipt(db: Database, bot: Any, scheduler: Any,
                          deal_id: int, buyer_id: int) -> dict:
    """Buyer confirms receipt any time during PAID_HELD -> instant release."""
    from bot.core import timers  # deferred

    deal = await _get_deal(db, deal_id)
    if buyer_id != deal["buyer_id"]:
        raise DealError("Only the buyer can confirm receipt.")
    if deal["status"] != DealStatus.PAID_HELD:
        raise DealError("There's nothing to confirm in this deal state.")
    await Deals(db).update(deal_id, confirm_receipt=1)
    await timers.cancel_job(db, scheduler, deal_id, "hold_release")
    return await release_funds(db, bot, deal_id, _actor_user(buyer_id))


# --------------------------------------------------------------------- chat

async def relay_message(db: Database, bot: Any, deal_id: int,
                        sender_id: int, text: str) -> None:
    """Anonymous relay with smart masking + offense counting.

    deal chat -> "🗣 Buyer: ..." / "🏷 Seller: ...". Neither side ever
    sees the other's identity.
    """
    deal = await _get_deal(db, deal_id)
    role = _role(deal, sender_id)
    if role is None:
        raise DealError("You're not part of this deal.")
    if deal["status"] not in _ACTIVE_CHAT_STATUSES:
        raise DealError("This deal is closed — chat is read-only.")
    if not deal.get("chat_open"):
        raise DealError("This deal chat is locked.")
    text = (text or "").strip()
    if not text:
        return

    handover = handover_active(deal)
    masked, offenses = mask_text(text, handover=handover)
    await Messages(db).add(deal_id, role, text, masked)

    label = "🗣 <b>Buyer:</b>" if role == "buyer" else "🏷 <b>Seller:</b>"
    other_id = deal["seller_id"] if role == "buyer" else deal["buyer_id"]
    await _send(bot, other_id, f"{label} {html.escape(masked)}")

    if offenses:
        kinds = ", ".join(offenses)
        await _send(
            bot, sender_id,
            _fill(texts.MASK_WARNING, kinds=html.escape(kinds)),
        )
        new_count = (deal.get("offense_count") or 0) + 1
        await Deals(db).update(deal_id, offense_count=new_count)
        if new_count >= 2:
            await notify_admins(
                bot,
                _fill(
                    texts.MASK_ADMIN_ALERT,
                    deal_id=deal_id,
                    user_id=sender_id,
                    role=role,
                    kinds=html.escape(kinds),
                    original=html.escape(text[:1500]),
                ),
            )


async def end_chat(db: Database, bot: Any, deal_id: int,
                   user_id: int) -> dict:
    """Either party ends the chat (confirmed in the handler). Money logic
    is untouched — ending chat never affects escrow."""
    deal = await _get_deal(db, deal_id)
    role = _role(deal, user_id)
    if role is None:
        raise DealError("You're not part of this deal.")
    if deal.get("chat_open"):
        await Deals(db).update(deal_id, chat_open=0)
        await Audit(db).log(_actor_user(user_id), "chat_ended", "deal",
                            deal_id, "")
        notice = (f"🔚 The {role} ended the deal chat. "
                  f"Escrow money is unaffected.")
        await _send(bot, deal["buyer_id"], notice)
        await _send(bot, deal["seller_id"], notice)
    return await _get_deal(db, deal_id)

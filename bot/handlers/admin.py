"""Admin panel handlers (spec §14).

Every handler here is guarded by ``settings.is_admin`` — non-admins get
``texts.NOT_ADMIN``. Listing/payment status changes go ONLY through
``bot.core.escrow`` (actor ``'admin:<tg_id>'``); deal payment/dispute
decisions go through ``bot.core.dealops`` (Worker B's module — imported
lazily; if it is not present yet the action is refused with a clear
message instead of duplicating business logic).
"""
from __future__ import annotations

import csv
import html
import inspect
import io
import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import settings
from bot.core import escrow
from bot.core.states import BroadcastFlow, WarnFlow
from bot.db.repo import Audit, Deals, Ledger, Listings, Messages, Users, utcnow
from bot.utils import keyboards, texts
from bot.utils.money import fmt_cents, fmt_pkr
from bot.utils.notify import notify_admins

router = Router()

# ---------------------------------------------------------------------------
# Local FSM states (reason prompts) — kept here so we don't touch foundation.
# ---------------------------------------------------------------------------


class ListingReject(StatesGroup):
    reason = State()


class PaymentReject(StatesGroup):
    reason = State()


class DisputeDecide(StatesGroup):
    reason = State()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


async def _deny(event: Message | CallbackQuery) -> bool:
    """True when the caller is NOT an admin (a denial was already sent)."""
    if settings.is_admin(event.from_user.id):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer(texts.NOT_ADMIN, show_alert=True)
    else:
        await event.answer(texts.NOT_ADMIN)
    return True


def _cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin:menu")
    return b.as_markup()


def _back_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Admin panel", callback_data="admin:menu")
    return b.as_markup()


def _shots(l: dict) -> list:
    try:
        shots = json.loads(l.get("ver_proof_shots") or "[]")
        return shots if isinstance(shots, list) else []
    except (TypeError, ValueError):
        return []


def _fmt_listing(l: dict, checklist: dict, pending_total: int = 0) -> str:
    shots = _shots(l)
    lines = [
        f"📋 <b>Listing #{l['id']}</b> — <i>{html.escape(l['status'])}</i>",
    ]
    if pending_total:
        lines.append(f"⏳ {pending_total} pending review (showing the oldest first)")
    lines += [
        f"📝 <b>{html.escape(l['title'] or '')}</b>",
        f"📂 {html.escape(l['category'] or '')} / {html.escape(l['subcategory'] or '')}",
        f"💵 Price: <b>{fmt_cents(l['price_cents'])}</b>",
        f"👥 Followers: {l['followers']:,}",
        f"🌍 Country: {html.escape(l['country'] or '-')}",
        f"📅 Account age: {html.escape(l['account_age'] or '-')}",
        f"🧾 {html.escape(l['description'] or '-')}",
        "",
        "<b>Verification (seller-declared — never auto-verified):</b>",
        f"✉️ Original email access: {html.escape(l['ver_email_access'] or '-')}",
        f"🚩 Violations declared: {html.escape(l['ver_violations'] or '-')}",
    ]
    if (l.get("ver_violations") or "").lower() == "yes":
        lines.append(f"   ↳ {html.escape(l.get('ver_violations_dtl') or '')}")
    lines.append(f"💲 Monetized: {html.escape(l['ver_monetized'] or '-')}")
    lines.append("📎 <b>Checklist:</b>")
    labels = {
        "age": "Account age",
        "email_access": "Original email access",
        "violations": "Violations declared",
        "monetization": "Monetization status",
        "proof_video": "Proof video",
        "proof_shots": "Proof screenshots",
    }
    for key, label in labels.items():
        state = checklist.get(key, "-")
        lines.append(f"   • {label}: <i>{html.escape(str(state))}</i>")
    lines.append(f"🎥 Proof video: {'✅ attached' if l['ver_proof_video'] else '❌ missing'}")
    lines.append(f"🖼️ Screenshots: {len(shots)} attached")
    if l.get("engagement_flag"):
        lines.append("⚠️ <b>Engagement heuristic flagged</b> — spot-check carefully.")
    lines.append(f"👤 Seller tg_id: <code>{l['seller_id']}</code>")
    return "\n".join(lines)


_STATUS_EMOJI = {
    "NEGOTIATING": "💬",
    "AWAITING_PAYMENT": "💳",
    "PAID_HELD": "🔒",
    "DISPUTED": "🚩",
    "RELEASED": "✅",
    "REFUNDED": "↩️",
    "CANCELLED": "❌",
}


def _fmt_deal_line(d: dict) -> str:
    emo = _STATUS_EMOJI.get(d["status"], "•")
    title = html.escape((d.get("title") or "")[:40])
    return (
        f"{emo} <b>#{d['id']}</b> {title} — {d['status']} "
        f"({fmt_cents(d['price_cents'])})"
    )


# ---------------------------------------------------------------------------
# dealops bridge — Worker B's module; never duplicate its business logic.
# ---------------------------------------------------------------------------


def _dealops():
    """Return the dealops module, or None when Worker B hasn't delivered it."""
    try:
        from bot.core import dealops

        return dealops
    except ImportError:
        return None


async def _call_dealops(name: str, **kwargs):
    """Call a dealops function, passing only the kwargs it actually accepts.

    Adapts to small signature differences without duplicating business logic.
    Raises RuntimeError when dealops is not available yet.
    """
    mod = _dealops()
    if mod is None:
        raise RuntimeError(
            "Deal engine (bot.core.dealops) is not installed yet — "
            "this action will work once Worker B's module is delivered."
        )
    fn = getattr(mod, name)
    accepted = set(inspect.signature(fn).parameters)
    return await fn(**{k: v for k, v in kwargs.items() if k in accepted})


# ---------------------------------------------------------------------------
# Admin menu
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:menu")
async def admin_menu_cb(cb: CallbackQuery, state: FSMContext):
    if await _deny(cb):
        return
    await state.clear()
    await cb.answer()
    try:
        await cb.message.edit_text(texts.ADMIN_MENU, reply_markup=keyboards.admin_menu())
    except Exception:
        await cb.message.answer(texts.ADMIN_MENU, reply_markup=keyboards.admin_menu())


# ---------------------------------------------------------------------------
# Pending listings
# ---------------------------------------------------------------------------


async def _show_next_pending(message: Message, db) -> None:
    pending = await Listings(db).pending()
    if not pending:
        await message.answer("📋 No pending listings — all reviewed ✅", reply_markup=_back_kb())
        return
    l = pending[0]
    checklist = await Listings(db).checklist(l["id"])
    await message.answer(_fmt_listing(l, checklist, pending_total=len(pending)))
    # Proof media: video first, then screenshots.
    if l.get("ver_proof_video"):
        try:
            await message.answer_video(video=l["ver_proof_video"], caption="🎥 Proof video")
        except Exception:
            await message.answer("🎥 (proof video could not be loaded)")
    for fid in _shots(l)[:6]:
        try:
            await message.answer_photo(photo=fid)
        except Exception:
            break
    await message.answer(
        f"Decision for listing #{l['id']}?", reply_markup=keyboards.approve_reject("listing", l["id"])
    )


@router.callback_query(F.data == "admin:listings")
async def admin_listings(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    await cb.answer()
    await _show_next_pending(cb.message, db)


@router.callback_query(F.data.startswith("admin:listing_approve:"))
async def admin_listing_approve(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    listing_id = int(cb.data.split(":")[-1])
    l = await Listings(db).get(listing_id)
    if l is None or l["status"] != "PENDING":
        await cb.answer("Listing is no longer pending.", show_alert=True)
        return
    # Mark proof checklist items as admin spot-checked (contract requirement).
    checklist = await Listings(db).checklist(listing_id)
    for key in ("proof_video", "proof_shots"):
        if key in checklist:
            checklist[key] = "Admin spot-checked"
    await Listings(db).set_checklist(listing_id, checklist)
    try:
        await escrow.transition_listing(
            db, listing_id, escrow.ListingStatus.LIVE, actor=f"admin:{cb.from_user.id}"
        )
    except escrow.IllegalTransition as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    await Listings(db).update(listing_id, approved_at=utcnow())
    await cb.answer("✅ Listing is now LIVE")
    try:
        await cb.bot.send_message(
            l["seller_id"],
            f"✅ Your listing <b>{html.escape(l['title'])}</b> was approved and is now LIVE!",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _show_next_pending(cb.message, db)


@router.callback_query(F.data.startswith("admin:listing_reject:"))
async def admin_listing_reject(cb: CallbackQuery, state: FSMContext, db):
    if await _deny(cb):
        return
    listing_id = int(cb.data.split(":")[-1])
    l = await Listings(db).get(listing_id)
    if l is None or l["status"] != "PENDING":
        await cb.answer("Listing is no longer pending.", show_alert=True)
        return
    await state.update_data(listing_id=listing_id)
    await state.set_state(ListingReject.reason)
    await cb.answer()
    await cb.message.answer(
        f"❌ Reject listing #{listing_id} — send the reason for the seller:",
        reply_markup=_cancel_kb(),
    )


@router.message(ListingReject.reason)
async def admin_listing_reject_reason(msg: Message, state: FSMContext, db):
    if await _deny(msg):
        await state.clear()
        return
    reason = (msg.text or "").strip()
    data = await state.get_data()
    listing_id = data.get("listing_id")
    l = await Listings(db).get(listing_id) if listing_id else None
    if l is None or l["status"] != "PENDING":
        await msg.answer("Listing is no longer pending.", reply_markup=_back_kb())
        await state.clear()
        return
    if not reason:
        await msg.answer("Please send a reason (or tap ❌ Cancel).")
        return
    try:
        await escrow.transition_listing(
            db, listing_id, escrow.ListingStatus.REJECTED,
            actor=f"admin:{msg.from_user.id}", note=reason,
        )
    except escrow.IllegalTransition as exc:
        await msg.answer(str(exc), reply_markup=_back_kb())
        await state.clear()
        return
    await state.clear()
    try:
        await msg.bot.send_message(
            l["seller_id"],
            "❌ Your listing <b>"
            + html.escape(l["title"])
            + "</b> was rejected.\nReason: "
            + html.escape(reason),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await msg.answer(f"❌ Listing #{listing_id} rejected.", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Payments to confirm (manual / Binance-manual fallback)
# ---------------------------------------------------------------------------


async def _show_next_payment(message: Message, db) -> None:
    deals = await Deals(db).active(limit=200)
    awaiting = [d for d in deals if d["status"] == "AWAITING_PAYMENT"]
    if not awaiting:
        await message.answer("💳 No payments waiting for confirmation.", reply_markup=_back_kb())
        return
    d = awaiting[0]
    method = d.get("payment_method") or "manual"
    lines = [
        "💳 <b>Payment to confirm</b>",
        f"Deal <b>#{d['id']}</b> — {html.escape(d.get('title') or '')}",
        f"💵 Amount: <b>{fmt_cents(d['price_cents'])}</b>",
        f"🧾 Method: {html.escape(method)}",
        f"🛒 Buyer: <code>{d['buyer_id']}</code> · 🏷 Seller: <code>{d['seller_id']}</code>",
    ]
    if awaiting and len(awaiting) > 1:
        lines.append(f"⏳ {len(awaiting)} payments waiting (oldest first)")
    proof = d.get("payment_ref") or ""
    if method == "binance":
        lines.append(f"🔑 Buyer Pay ID: <code>{html.escape(proof) or '—'}</code>")
    await message.answer("\n".join(lines))
    if method != "binance" and proof:
        # Manual payment → proof should be a screenshot (photo file_id).
        try:
            await message.answer_photo(photo=proof, caption="🧾 Payment screenshot")
        except Exception:
            await message.answer(
                "🧾 Payment proof (text):\n" + html.escape(proof)
            )
    await message.answer(
        f"Confirm payment for deal #{d['id']}?",
        reply_markup=keyboards.approve_reject("payment", d["id"]),
    )


@router.callback_query(F.data == "admin:payments")
async def admin_payments(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    await cb.answer()
    await _show_next_payment(cb.message, db)


async def _confirm_payment(cb: CallbackQuery, db, scheduler, vault, deal_id: int) -> None:
    """Shared logic for admin:pay_approve:* and admin:payment_approve:*."""
    d = await Deals(db).get(deal_id)
    if d is None or d["status"] != "AWAITING_PAYMENT":
        await cb.answer("Deal is no longer awaiting payment.", show_alert=True)
        return
    try:
        await _call_dealops(
            "admin_confirm_payment",
            db=db, bot=cb.bot, scheduler=scheduler, vault=vault,
            deal_id=deal_id, admin_id=cb.from_user.id,
        )
    except RuntimeError as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    except Exception as exc:  # e.g. IllegalTransition surfaced by dealops
        await cb.answer(f"Failed: {exc}", show_alert=True)
        return
    await cb.answer("✅ Payment confirmed — credentials delivered")
    await _show_next_payment(cb.message, db)


# Buttons emitted by dealops notifications (Worker B contract):
@router.callback_query(F.data.startswith("admin:pay_approve:"))
async def admin_pay_approve(cb: CallbackQuery, db, scheduler, vault):
    if await _deny(cb):
        return
    await cb.answer()
    await _confirm_payment(cb, db, scheduler, vault, int(cb.data.split(":")[-1]))


# Buttons from this panel's own approve_reject('payment', …) keyboard:
@router.callback_query(F.data.startswith("admin:payment_approve:"))
async def admin_payment_approve(cb: CallbackQuery, db, scheduler, vault):
    if await _deny(cb):
        return
    await cb.answer()
    await _confirm_payment(cb, db, scheduler, vault, int(cb.data.split(":")[-1]))


async def _start_payment_reject(cb: CallbackQuery, state: FSMContext, db, deal_id: int) -> None:
    d = await Deals(db).get(deal_id)
    if d is None or d["status"] != "AWAITING_PAYMENT":
        await cb.answer("Deal is no longer awaiting payment.", show_alert=True)
        return
    await state.update_data(deal_id=deal_id)
    await state.set_state(PaymentReject.reason)
    await cb.answer()
    await cb.message.answer(
        f"❌ Reject payment for deal #{deal_id} — send the reason for the buyer:",
        reply_markup=_cancel_kb(),
    )


@router.callback_query(F.data.startswith("admin:pay_reject:"))
async def admin_pay_reject(cb: CallbackQuery, state: FSMContext, db):
    if await _deny(cb):
        return
    await _start_payment_reject(cb, state, db, int(cb.data.split(":")[-1]))


@router.callback_query(F.data.startswith("admin:payment_reject:"))
async def admin_payment_reject(cb: CallbackQuery, state: FSMContext, db):
    if await _deny(cb):
        return
    await _start_payment_reject(cb, state, db, int(cb.data.split(":")[-1]))


@router.message(PaymentReject.reason)
async def admin_payment_reject_reason(msg: Message, state: FSMContext, db):
    if await _deny(msg):
        await state.clear()
        return
    reason = (msg.text or "").strip()
    if not reason:
        await msg.answer("Please send a reason (or tap ❌ Cancel).")
        return
    data = await state.get_data()
    deal_id = data.get("deal_id")
    await state.clear()
    d = await Deals(db).get(deal_id) if deal_id else None
    if d is None or d["status"] != "AWAITING_PAYMENT":
        await msg.answer("Deal is no longer awaiting payment.", reply_markup=_back_kb())
        return
    try:
        await _call_dealops(
            "admin_reject_payment",
            db=db, bot=msg.bot, deal_id=deal_id,
            admin_id=msg.from_user.id, reason=reason,
        )
    except RuntimeError as exc:
        await msg.answer(str(exc), reply_markup=_back_kb())
        return
    except Exception as exc:
        await msg.answer(f"Failed: {exc}", reply_markup=_back_kb())
        return
    await msg.answer(f"❌ Payment for deal #{deal_id} rejected — buyer notified.", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------


def _fmt_transcript(msgs: list[dict], limit_chars: int = 3500) -> str:
    role_name = {"buyer": "🛒 Buyer", "seller": "🏷 Seller", "system": "⚙️ System"}
    parts: list[str] = []
    total = 0
    for m in msgs:
        line = f"<b>{role_name.get(m['sender_role'], m['sender_role'])}:</b> {html.escape(m['text'])}"
        if total + len(line) > limit_chars:
            parts.append("<i>… (truncated)</i>")
            break
        parts.append(line)
        total += len(line)
    return "\n".join(parts) if parts else "<i>No messages yet.</i>"


async def _show_next_dispute(message: Message, db) -> None:
    disputes = await Deals(db).disputed()
    if not disputes:
        await message.answer("🚩 No open disputes.", reply_markup=_back_kb())
        return
    d = disputes[0]
    head = [
        "🚩 <b>Dispute</b> — deal <b>#" + str(d["id"]) + "</b>",
        f"📝 {html.escape(d.get('title') or '')} — {fmt_cents(d['price_cents'])}",
        f"🛒 Buyer: <code>{d['buyer_id']}</code> · 🏷 Seller: <code>{d['seller_id']}</code>",
        f"❓ Reason: {html.escape(d.get('dispute_reason') or '—')}",
        f"⏸ Disputed at: {html.escape(d.get('disputed_at') or '—')}",
    ]
    if len(disputes) > 1:
        head.append(f"⏳ {len(disputes)} open disputes (oldest first)")
    head += ["", "<b>Transcript (last 30):</b>"]
    msgs = await Messages(db).transcript(d["id"], 30)
    await message.answer("\n".join(head) + "\n" + _fmt_transcript(msgs))
    await message.answer(
        f"Decision for dispute on deal #{d['id']}?",
        reply_markup=keyboards.dispute_decide(d["id"]),
    )


@router.callback_query(F.data == "admin:disputes")
async def admin_disputes(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    await cb.answer()
    await _show_next_dispute(cb.message, db)


@router.callback_query(F.data.startswith("admin:dispute_refund:"))
async def admin_dispute_refund(cb: CallbackQuery, state: FSMContext, db):
    if await _deny(cb):
        return
    await _start_dispute_decide(cb, state, db, "refund")


@router.callback_query(F.data.startswith("admin:dispute_release:"))
async def admin_dispute_release(cb: CallbackQuery, state: FSMContext, db):
    if await _deny(cb):
        return
    await _start_dispute_decide(cb, state, db, "release")


async def _start_dispute_decide(cb: CallbackQuery, state: FSMContext, db, decision: str) -> None:
    deal_id = int(cb.data.split(":")[-1])
    d = await Deals(db).get(deal_id)
    if d is None or d["status"] != "DISPUTED":
        await cb.answer("Deal is no longer disputed.", show_alert=True)
        return
    await state.update_data(deal_id=deal_id, decision=decision)
    await state.set_state(DisputeDecide.reason)
    await cb.answer()
    what = "refund the buyer" if decision == "refund" else "release funds to the seller"
    await cb.message.answer(
        f"Decision: <b>{what}</b> (deal #{deal_id}).\nSend the reason (logged for both sides):",
        reply_markup=_cancel_kb(),
    )


@router.message(DisputeDecide.reason)
async def admin_dispute_reason(msg: Message, state: FSMContext, db, scheduler):
    if await _deny(msg):
        await state.clear()
        return
    reason = (msg.text or "").strip()
    if not reason:
        await msg.answer("Please send a reason (or tap ❌ Cancel).")
        return
    data = await state.get_data()
    deal_id, decision = data.get("deal_id"), data.get("decision")
    await state.clear()
    d = await Deals(db).get(deal_id) if deal_id else None
    if d is None or d["status"] != "DISPUTED":
        await msg.answer("Deal is no longer disputed.", reply_markup=_back_kb())
        return
    try:
        await _call_dealops(
            "admin_resolve_dispute",
            db=db, bot=msg.bot, scheduler=scheduler, deal_id=deal_id,
            admin_id=msg.from_user.id, decision=decision, reason=reason,
        )
    except RuntimeError as exc:
        await msg.answer(str(exc), reply_markup=_back_kb())
        return
    except Exception as exc:
        await msg.answer(f"Failed: {exc}", reply_markup=_back_kb())
        return
    what = "refunded to the buyer" if decision == "refund" else "released to the seller"
    await msg.answer(f"✅ Dispute on deal #{deal_id} resolved: {what}.", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Active deals
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:deals")
async def admin_deals(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    await cb.answer()
    deals = await Deals(db).active(limit=50)
    if not deals:
        text = "💰 No active deals right now."
    else:
        lines = ["💰 <b>Active deals</b> (newest first):", ""]
        lines += [_fmt_deal_line(d) for d in deals]
        text = "\n".join(lines)
    try:
        await cb.message.edit_text(text, reply_markup=_back_kb())
    except Exception:
        await cb.message.answer(text, reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Users: list, detail, warn, ban/unban
# ---------------------------------------------------------------------------


_USERS_PER_PAGE = 10


@router.callback_query(F.data == "admin:users")
async def admin_users_first(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    await cb.answer()
    await _show_users(cb.message, db, offset=0)


@router.callback_query(F.data.startswith("admin:users:"))
async def admin_users_page(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    await cb.answer()
    try:
        offset = max(0, int(cb.data.split(":")[-1]))
    except ValueError:
        offset = 0
    await _show_users(cb.message, db, offset=offset)


async def _show_users(message: Message, db, offset: int = 0) -> None:
    users = await Users(db).list_all(limit=_USERS_PER_PAGE, offset=offset)
    total = await Users(db).count()
    if not users:
        await message.answer("👥 No users yet.", reply_markup=_back_kb())
        return
    lines = [f"👥 <b>Users</b> ({offset + 1}–{offset + len(users)} of {total}):", ""]
    b = InlineKeyboardBuilder()
    for u in users:
        flag = "🚫" if u["is_banned"] else "•"
        warns = f" ⚠️{u['warn_count']}" if u["warn_count"] else ""
        name = html.escape(u["first_name"] or u["username"] or str(u["tg_id"]))
        lines.append(f"{flag} {name} — <code>{u['tg_id']}</code>{warns}")
        b.button(text=f"👤 {u['tg_id']}", callback_data=f"admin:user:{u['tg_id']}")
    if offset > 0:
        b.button(text="◀ Prev", callback_data=f"admin:users:{offset - _USERS_PER_PAGE}")
    if offset + len(users) < total:
        b.button(text="Next ▶", callback_data=f"admin:users:{offset + _USERS_PER_PAGE}")
    b.button(text="🔙 Admin panel", callback_data="admin:menu")
    b.adjust(2)
    await message.answer("\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("admin:user:"))
async def admin_user_detail(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    tg_id = int(cb.data.split(":")[-1])
    u = await Users(db).get(tg_id)
    if u is None:
        await cb.answer("User not found.", show_alert=True)
        return
    name = html.escape(f"{u['first_name'] or ''}".strip() or "-")
    username = f"@{html.escape(u['username'])}" if u["username"] else "—"
    lines = [
        f"👤 <b>User</b> <code>{u['tg_id']}</code>",
        f"Name: {name} · {username}",
        f"Status: {'🚫 BANNED' if u['is_banned'] else '✅ active'}",
        f"⚠️ Warnings: {u['warn_count']}",
        f"💰 Deals: {u['deals_count']}",
        f"⭐ Rating: {html.escape(Users.rating_text(u))}",
        f"📅 Joined: {html.escape(u['created_at'] or '-')}",
    ]
    await cb.answer()
    try:
        await cb.message.edit_text("\n".join(lines), reply_markup=keyboards.user_moderate(tg_id))
    except Exception:
        await cb.message.answer("\n".join(lines), reply_markup=keyboards.user_moderate(tg_id))


@router.callback_query(F.data.startswith("admin:warn:"))
async def admin_warn(cb: CallbackQuery, state: FSMContext, db):
    if await _deny(cb):
        return
    tg_id = int(cb.data.split(":")[-1])
    u = await Users(db).get(tg_id)
    if u is None:
        await cb.answer("User not found.", show_alert=True)
        return
    await state.update_data(warn_tg_id=tg_id)
    await state.set_state(WarnFlow.reason)
    await cb.answer()
    await cb.message.answer(
        f"⚠️ Warn user <code>{tg_id}</code> — send the warning reason:",
        reply_markup=_cancel_kb(),
    )


@router.message(WarnFlow.reason)
async def admin_warn_reason(msg: Message, state: FSMContext, db):
    if await _deny(msg):
        await state.clear()
        return
    reason = (msg.text or "").strip()
    if not reason:
        await msg.answer("Please send a reason (or tap ❌ Cancel).")
        return
    data = await state.get_data()
    tg_id = data.get("warn_tg_id")
    await state.clear()
    if not tg_id:
        await msg.answer("Session expired — open the user again.", reply_markup=_back_kb())
        return
    count = await Users(db).warn(tg_id)
    await Audit(db).log(f"admin:{msg.from_user.id}", "warn_user", "user", tg_id, reason)
    try:
        await msg.bot.send_message(
            tg_id,
            "⚠️ <b>Warning from the marketplace admin:</b>\n"
            + html.escape(reason)
            + f"\n\nThis is warning #{count}. Repeated violations may lead to a ban.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    note = f"⚠️ User <code>{tg_id}</code> warned (#{count})."
    if count >= 3:
        # Spec: sustained offenders are bannable by the admin — alert, don't auto-ban.
        await notify_admins(
            msg.bot,
            f"🚨 User <code>{tg_id}</code> now has <b>{count} warnings</b> — consider banning.",
        )
        note += "\n🚨 3+ warnings — consider banning from the user panel."
    await msg.answer(note, reply_markup=_back_kb())


@router.callback_query(F.data.startswith("admin:ban:"))
async def admin_ban(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    tg_id = int(cb.data.split(":")[-1])
    await Users(db).set_banned(tg_id, True)
    await Audit(db).log(f"admin:{cb.from_user.id}", "ban_user", "user", tg_id, "")
    try:
        await cb.bot.send_message(tg_id, "🚫 You have been banned from the marketplace. Contact support if you think this is a mistake.")
    except Exception:
        pass
    await cb.answer("🚫 User banned")


@router.callback_query(F.data.startswith("admin:unban:"))
async def admin_unban(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    tg_id = int(cb.data.split(":")[-1])
    await Users(db).set_banned(tg_id, False)
    await Audit(db).log(f"admin:{cb.from_user.id}", "unban_user", "user", tg_id, "")
    try:
        await cb.bot.send_message(tg_id, "✅ Your ban has been lifted. Welcome back!")
    except Exception:
        pass
    await cb.answer("✅ User unbanned")


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast(cb: CallbackQuery, state: FSMContext):
    if await _deny(cb):
        return
    await state.set_state(BroadcastFlow.text)
    await cb.answer()
    await cb.message.answer(
        "📣 Send the broadcast message text (or tap ❌ Cancel):",
        reply_markup=_cancel_kb(),
    )


@router.message(BroadcastFlow.text)
async def admin_broadcast_text(msg: Message, state: FSMContext, db):
    if await _deny(msg):
        await state.clear()
        return
    text = (msg.text or "").strip()
    if not text:
        await msg.answer("Send some text first (or tap ❌ Cancel).")
        return
    await state.update_data(broadcast_text=text)
    await state.set_state(BroadcastFlow.confirm)
    count = await Users(db).count()
    b = InlineKeyboardBuilder()
    b.button(text=f"✅ Send to {count} users", callback_data="admin:broadcast_yes")
    b.button(text="❌ Cancel", callback_data="admin:broadcast_no")
    b.adjust(1)
    preview = text[:500] + ("…" if len(text) > 500 else "")
    await msg.answer(
        "📣 <b>Preview:</b>\n" + html.escape(preview) + f"\n\nSend to <b>{count}</b> users?",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data == "admin:broadcast_yes")
async def admin_broadcast_yes(cb: CallbackQuery, state: FSMContext, db):
    if await _deny(cb):
        return
    data = await state.get_data()
    text = data.get("broadcast_text") or ""
    await state.clear()
    if not text:
        await cb.answer("Nothing to send.", show_alert=True)
        return
    await cb.answer("Sending…")
    sent = failed = skipped = 0
    # Plain text (no parse mode) so admin formatting can't break delivery.
    users = await Users(db).list_all(limit=100000, offset=0)
    for u in users:
        if u["is_banned"]:
            skipped += 1
            continue
        try:
            await cb.bot.send_message(u["tg_id"], text)
            sent += 1
        except Exception:
            failed += 1
    await cb.message.answer(
        f"📣 Broadcast done: ✅ {sent} sent · ❌ {failed} failed · ⏭ {skipped} banned skipped.",
        reply_markup=_back_kb(),
    )


@router.callback_query(F.data == "admin:broadcast_no")
async def admin_broadcast_no(cb: CallbackQuery, state: FSMContext):
    if await _deny(cb):
        return
    await state.clear()
    await cb.answer("Cancelled")
    await cb.message.answer("📣 Broadcast cancelled.", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:stats")
async def admin_stats(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    await cb.answer()
    user_count = await Users(db).count()
    by_status = await Deals(db).count_by_status()
    totals = await Ledger(db).totals()
    lines = ["📊 <b>Marketplace stats</b>", "", f"👥 Users: <b>{user_count}</b>", "", "<b>Deals by status:</b>"]
    order = ["NEGOTIATING", "AWAITING_PAYMENT", "PAID_HELD", "DISPUTED",
             "RELEASED", "REFUNDED", "CANCELLED"]
    for s in order:
        lines.append(f"   {_STATUS_EMOJI.get(s, '•')} {s}: {by_status.get(s, 0)}")
    lines += [
        "",
        "<b>Ledger totals:</b>",
        f"   🔒 Held: {fmt_cents(totals.get('hold', 0))}",
        f"   ✅ Released: {fmt_cents(totals.get('release', 0))}",
        f"   ↩️ Refunded: {fmt_cents(totals.get('refund', 0))}",
        f"   💰 Commission earned: <b>{fmt_cents(totals.get('commission', 0))}</b>",
    ]
    try:
        await cb.message.edit_text("\n".join(lines), reply_markup=_back_kb())
    except Exception:
        await cb.message.answer("\n".join(lines), reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Ledger + CSV export
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:ledger")
async def admin_ledger(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    await cb.answer()
    entries = await Ledger(db).all(limit=20)
    if not entries:
        text = "🧾 Ledger is empty."
    else:
        lines = ["🧾 <b>Ledger</b> (last 20):", ""]
        for e in entries:
            deal = f" · deal #{e['deal_id']}" if e["deal_id"] else ""
            note = f" — {html.escape(e['note'])}" if e["note"] else ""
            lines.append(
                f"#{e['id']} <b>{html.escape(e['kind'])}</b> {fmt_cents(e['amount_cents'])}"
                f"{deal} · <i>{html.escape(e['actor'])}</i>{note}"
            )
        text = "\n".join(lines)
    b = InlineKeyboardBuilder()
    b.button(text="📥 Export CSV", callback_data="admin:ledger_csv")
    b.button(text="🔙 Admin panel", callback_data="admin:menu")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await cb.message.answer(text, reply_markup=b.as_markup())


@router.callback_query(F.data == "admin:ledger_csv")
async def admin_ledger_csv(cb: CallbackQuery, db):
    if await _deny(cb):
        return
    await cb.answer("Preparing CSV…")
    entries = await Ledger(db).all(limit=5000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "created_at", "kind", "amount_usd", "actor", "deal_id", "note"])
    for e in entries:
        writer.writerow([
            e["id"], e["created_at"], e["kind"],
            f"{e['amount_cents'] / 100:.2f}", e["actor"], e["deal_id"], e["note"],
        ])
    doc = BufferedInputFile(buf.getvalue().encode("utf-8"), filename="ledger.csv")
    await cb.message.answer_document(doc, caption="🧾 Ledger export (CSV)")
    await cb.message.answer("Export sent.", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Settings (display-only — honest note about .env + restart)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:settings")
async def admin_settings(cb: CallbackQuery):
    if await _deny(cb):
        return
    await cb.answer()
    binance_auto = "✅ auto" if settings.binance_auto_configured() else "manual"
    bybit_auto = "✅ auto" if settings.bybit_auto_configured() else "manual"
    text = (
        "⚙️ <b>Settings</b> (loaded from <code>.env</code> at startup)\n\n"
        f"💵 Commission (USDT deals): <b>{fmt_cents(settings.commission_usdt_cents)}</b>\n"
        f"💵 Commission (local PKR deals): <b>{fmt_pkr(settings.commission_local_pkr)}</b>\n"
        f"💱 USD→PKR rate: <b>{settings.usd_pkr_rate}</b>\n"
        f"📋 Auto-approve listings: <b>{'ON' if settings.auto_approve_listings else 'OFF'}</b>\n"
        f"⏳ Hold: <b>{settings.hold_hours}h</b> · "
        f"🤝 Handover: <b>{settings.handover_hours}h</b> · "
        f"📦 Delivery deadline: <b>{settings.delivery_deadline_hours}h</b>\n"
        f"🟡 Binance pay: <b>{binance_auto}</b> · "
        f"🔵 Bybit pay: <b>{bybit_auto}</b>\n"
        f"⭐ Rating window: <b>{settings.rating_window_days} days</b>\n\n"
        "⚠️ <i>Honest note: these values are read from .env when the bot starts. "
        "To change them, edit .env on the server and restart the bot — "
        "the panel does not hot-edit configuration.</i>"
    )
    try:
        await cb.message.edit_text(text, reply_markup=_back_kb())
    except Exception:
        await cb.message.answer(text, reply_markup=_back_kb())

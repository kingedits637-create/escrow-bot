"""Deal flow handlers (WORKER B) — thin wrappers over bot.core.dealops.

Callbacks handled here: deal:buy|open|pay|confirm|dispute|cancel|endchat
(+ _yes confirmations) and pay:method:<m>. Plain-text relay and payment
proof intake run through the DealChat FSM. Dispute reasons use
DisputeFlow.

Every callback re-reads the deal from the DB and verifies the caller's
role before acting. Shared objects come only from the handler `data`
dict — never constructed here.
"""
from __future__ import annotations

import html
from typing import Any, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import Settings
from bot.core import dealops
from bot.core.escrow import DealStatus, IllegalTransition
from bot.core.states import DealChat, DisputeFlow
from bot.db.base import Database
from bot.db.repo import Deals, Listings
from bot.utils import keyboards, texts

router = Router()


# ------------------------------------------------------------------ helpers

async def _deal_or_alert(cb: CallbackQuery, db: Database,
                         deal_id: int) -> Optional[dict]:
    deal = await Deals(db).get(deal_id)
    if deal is None:
        await cb.answer("Deal not found.", show_alert=True)
        return None
    return deal


def _is_buyer(deal: dict, tg_id: int) -> bool:
    return tg_id == deal["buyer_id"]


def _is_party(deal: dict, tg_id: int) -> bool:
    return tg_id in (deal["buyer_id"], deal["seller_id"])


def _deal_id_from(data: str, prefix: str) -> Optional[int]:
    try:
        return int(data.split(":")[2]) if data.startswith(prefix) else None
    except (ValueError, IndexError):
        return None


async def _deal_card(db: Database, deal: dict) -> str:
    """Anonymous deal summary — never any seller identity."""
    from bot.utils.money import fmt_cents

    listing = await Listings(db).get(deal["listing_id"])
    title = html.escape(listing["title"]) if listing else f"listing #{deal['listing_id']}"
    lines = [
        f"🧾 <b>Deal #{deal['id']}</b> — {title}",
        f"💵 Price: <b>{fmt_cents(deal['price_cents'])}</b> (escrow)",
        f"📊 Status: <b>{deal['status']}</b>",
    ]
    if deal["status"] == DealStatus.PAID_HELD:
        if deal.get("delivered_at"):
            lines.append("✅ Credentials delivered — verify the account.")
            lines.append("⏳ 8h handover + 24h hold are running.")
        else:
            lines.append("⏳ Payment confirmed — delivering credentials…")
    elif deal["status"] == DealStatus.AWAITING_PAYMENT:
        lines.append("⏳ Waiting for your payment inside the bot.")
    elif deal["status"] == DealStatus.DISPUTED:
        lines.append("🚩 Under dispute — the 24h timer is paused.")
    if not deal.get("chat_open"):
        lines.append("🔒 Chat is locked.")
    return "\n".join(lines)


# ------------------------------------------------------------- deal:buy/open

@router.callback_query(F.data.startswith("deal:buy:"))
async def deal_buy(cb: CallbackQuery, state: FSMContext, db: Database,
                   bot: Any) -> None:
    """Buyer taps 'Buy Now' on a listing detail screen (Worker A links here)."""
    listing_id = _deal_id_from(cb.data, "deal:buy:")
    uid = cb.from_user.id
    if listing_id is None:
        await cb.answer("Bad request.", show_alert=True)
        return
    listing = await Listings(db).get(listing_id)
    if listing is None or listing["status"] != "LIVE":
        await cb.answer("This listing is no longer available.", show_alert=True)
        return
    if listing["seller_id"] == uid:
        await cb.answer("You can't buy your own listing.", show_alert=True)
        return
    try:
        deal_id = await dealops.open_deal(db, bot, listing_id, uid)
    except dealops.DealError as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    await state.set_state(DealChat.chatting)
    await state.update_data(deal_id=deal_id)
    await cb.message.answer(
        texts.DEAL_OPENED_BUYER.replace("{deal_id}", str(deal_id)),
        reply_markup=keyboards.deal_menu(deal_id, True),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("deal:open:"))
async def deal_open(cb: CallbackQuery, state: FSMContext,
                    db: Database) -> None:
    """Open the deal chat: sets the DealChat FSM so plain text relays."""
    deal_id = _deal_id_from(cb.data, "deal:open:")
    deal = await _deal_or_alert(cb, db, deal_id) if deal_id else None
    if deal is None:
        return
    if not _is_party(deal, cb.from_user.id):
        await cb.answer("This isn't your deal.", show_alert=True)
        return
    await state.set_state(DealChat.chatting)
    await state.update_data(deal_id=deal["id"])
    await cb.message.answer(
        await _deal_card(db, deal),
        reply_markup=keyboards.deal_menu(deal["id"], _is_buyer(deal, cb.from_user.id)),
    )
    await cb.answer()


# ------------------------------------------------------------------ payment

@router.callback_query(F.data.startswith("deal:pay:"))
async def deal_pay(cb: CallbackQuery, state: FSMContext,
                   db: Database, settings: Settings) -> None:
    """Buyer taps 'Buy Now / Pay' -> choose a payment method."""
    deal_id = _deal_id_from(cb.data, "deal:pay:")
    deal = await _deal_or_alert(cb, db, deal_id) if deal_id else None
    if deal is None:
        return
    if not _is_buyer(deal, cb.from_user.id):
        await cb.answer("Only the buyer can pay.", show_alert=True)
        return
    if deal["status"] != DealStatus.NEGOTIATING:
        await cb.answer(f"Can't pay in status {deal['status']}.", show_alert=True)
        return
    await state.set_state(DealChat.pay_select)
    await state.update_data(deal_id=deal["id"])
    await cb.message.answer(texts.PAY_CHOOSE,
                            reply_markup=keyboards.pay_methods(settings.pay_methods_list))
    await cb.answer()


@router.callback_query(F.data.startswith("pay:method:"))
async def pay_method(cb: CallbackQuery, state: FSMContext, db: Database,
                     bot: Any, scheduler: Any, vault: Any,
                     settings: Settings) -> None:
    """Buyer picked a payment method -> NEGOTIATING becomes AWAITING_PAYMENT."""
    method = cb.data.split(":")[2] if cb.data.count(":") >= 2 else ""
    if method not in settings.pay_methods_list:
        await cb.answer("This payment method is not available.", show_alert=True)
        return
    data = await state.get_data()
    deal_id = data.get("deal_id")
    deal = await Deals(db).get(deal_id) if deal_id else None
    if deal is None or not _is_buyer(deal, cb.from_user.id):
        await cb.answer("This payment session expired — tap Pay again.",
                        show_alert=True)
        await state.clear()
        return
    try:
        await dealops.choose_payment(db, bot, scheduler, vault, deal["id"],
                                     method, settings)
    except (dealops.DealError, IllegalTransition) as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    # Back to chatting: the buyer's next text/photo is treated as proof
    # (manual fallback for auto methods too).
    await state.set_state(DealChat.chatting)
    await state.update_data(deal_id=deal["id"])
    await cb.answer("Payment method selected — instructions sent ✅")


# ------------------------------------------------------------------ confirm

@router.callback_query(F.data.startswith("deal:confirm:"))
async def deal_confirm(cb: CallbackQuery, db: Database, bot: Any,
                       scheduler: Any) -> None:
    """Buyer taps 'Confirm Receipt' -> instant release to the seller."""
    deal_id = _deal_id_from(cb.data, "deal:confirm:")
    deal = await _deal_or_alert(cb, db, deal_id) if deal_id else None
    if deal is None:
        return
    if not _is_buyer(deal, cb.from_user.id):
        await cb.answer("Only the buyer can confirm receipt.", show_alert=True)
        return
    try:
        await dealops.confirm_receipt(db, bot, scheduler, deal["id"],
                                      cb.from_user.id)
    except (dealops.DealError, IllegalTransition) as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    await cb.message.answer("✅ Receipt confirmed — funds released to the seller.")
    await cb.answer()


# ------------------------------------------------------------------ dispute

@router.callback_query(F.data.startswith("deal:dispute:"))
async def deal_dispute(cb: CallbackQuery, state: FSMContext,
                       db: Database) -> None:
    """Buyer taps 'Open Dispute' -> ask for the reason (DisputeFlow)."""
    deal_id = _deal_id_from(cb.data, "deal:dispute:")
    deal = await _deal_or_alert(cb, db, deal_id) if deal_id else None
    if deal is None:
        return
    if not _is_buyer(deal, cb.from_user.id):
        await cb.answer("Only the buyer can open a dispute.", show_alert=True)
        return
    if deal["status"] != DealStatus.PAID_HELD:
        await cb.answer("Disputes can only be opened on a paid deal.",
                        show_alert=True)
        return
    await state.set_state(DisputeFlow.reason)
    await state.update_data(deal_id=deal["id"])
    # ❌ Cancel = back to the deal (no new callback prefix invented).
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Cancel",
                             callback_data=f"deal:open:{deal['id']}"),
    ]])
    await cb.message.answer(
        "🚩 <b>Open a dispute</b> — describe the problem "
        "(wrong account / login doesn't work / not as described):\n\n"
        "The 24h hold timer pauses while an admin reviews.",
        reply_markup=cancel_kb,
    )
    await cb.answer()


@router.message(DisputeFlow.reason, F.text)
async def dispute_reason(msg: Message, state: FSMContext, db: Database,
                         bot: Any) -> None:
    data = await state.get_data()
    deal_id = data.get("deal_id")
    try:
        await dealops.open_dispute(db, bot, deal_id, msg.from_user.id,
                                   msg.text or "")
        await msg.answer(
            texts.DISPUTE_OPENED.replace("{deal_id}", str(deal_id)))
    except (dealops.DealError, IllegalTransition) as exc:
        await msg.answer(f"⚠️ {html.escape(str(exc))}")
    await state.clear()


# ------------------------------------------------------------------- cancel

@router.callback_query(F.data.startswith("deal:cancel_yes:"))
async def deal_cancel_yes(cb: CallbackQuery, state: FSMContext, db: Database,
                          bot: Any) -> None:
    deal_id = _deal_id_from(cb.data, "deal:cancel_yes:")
    deal = await _deal_or_alert(cb, db, deal_id) if deal_id else None
    if deal is None:
        return
    if not _is_party(deal, cb.from_user.id):
        await cb.answer("This isn't your deal.", show_alert=True)
        return
    try:
        await dealops.cancel_deal(db, bot, deal["id"], cb.from_user.id)
    except (dealops.DealError, IllegalTransition) as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await cb.message.answer(f"❌ Deal #{deal['id']} cancelled.")
    await cb.answer()


@router.callback_query(F.data.startswith("deal:cancel:"))
async def deal_cancel(cb: CallbackQuery, db: Database) -> None:
    """Ask for confirmation first — cancelling can trigger a refund."""
    deal_id = _deal_id_from(cb.data, "deal:cancel:")
    deal = await _deal_or_alert(cb, db, deal_id) if deal_id else None
    if deal is None:
        return
    if not _is_party(deal, cb.from_user.id):
        await cb.answer("This isn't your deal.", show_alert=True)
        return
    await cb.message.answer(
        f"❌ Cancel deal #{deal['id']}?\n"
        "If money is held and credentials weren't delivered, "
        "the buyer is refunded automatically.",
        reply_markup=keyboards.cancel_confirm(deal["id"]),
    )
    await cb.answer()


# ------------------------------------------------------------------ end chat

@router.callback_query(F.data.startswith("deal:endchat_yes:"))
async def deal_endchat_yes(cb: CallbackQuery, state: FSMContext, db: Database,
                           bot: Any) -> None:
    deal_id = _deal_id_from(cb.data, "deal:endchat_yes:")
    deal = await _deal_or_alert(cb, db, deal_id) if deal_id else None
    if deal is None:
        return
    if not _is_party(deal, cb.from_user.id):
        await cb.answer("This isn't your deal.", show_alert=True)
        return
    try:
        await dealops.end_chat(db, bot, deal["id"], cb.from_user.id)
    except dealops.DealError as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await cb.message.answer("🔚 Deal chat ended. Escrow money is unaffected.")
    await cb.answer()


@router.callback_query(F.data.startswith("deal:endchat:"))
async def deal_endchat(cb: CallbackQuery, db: Database) -> None:
    deal_id = _deal_id_from(cb.data, "deal:endchat:")
    deal = await _deal_or_alert(cb, db, deal_id) if deal_id else None
    if deal is None:
        return
    if not _is_party(deal, cb.from_user.id):
        await cb.answer("This isn't your deal.", show_alert=True)
        return
    await cb.message.answer(
        "🔚 End this deal chat? (Money/escrow is NOT affected.)",
        reply_markup=keyboards.endchat_confirm(deal["id"]),
    )
    await cb.answer()


# ------------------------------------------------- relay + proof intake

@router.message(DealChat.chatting, F.photo)
async def deal_chat_photo(msg: Message, state: FSMContext, db: Database,
                          bot: Any) -> None:
    """Photos: payment screenshot (buyer, AWAITING_PAYMENT) -> proof.
    Otherwise the deal chat is text-only."""
    data = await state.get_data()
    deal = await Deals(db).get(data.get("deal_id")) if data.get("deal_id") else None
    if deal is None:
        await state.clear()
        return
    uid = msg.from_user.id
    if deal["status"] == DealStatus.AWAITING_PAYMENT and _is_buyer(deal, uid):
        file_id = msg.photo[-1].file_id
        try:
            await dealops.submit_payment_proof(db, bot, deal["id"], uid,
                                               f"photo:{file_id}")
            await msg.answer("🧾 Screenshot received — an admin will confirm "
                             "your payment shortly.")
        except (dealops.DealError, IllegalTransition) as exc:
            await msg.answer(f"⚠️ {html.escape(str(exc))}")
        return
    await msg.answer("💬 Deal chat supports text messages only.")


@router.message(DealChat.chatting, F.text)
async def deal_chat_text(msg: Message, state: FSMContext, db: Database,
                         bot: Any) -> None:
    """Plain text in an open deal chat.

    Buyer text while AWAITING_PAYMENT = payment proof (Pay ID).
    Everything else = anonymous relay via dealops.relay_message.
    """
    data = await state.get_data()
    deal = await Deals(db).get(data.get("deal_id")) if data.get("deal_id") else None
    if deal is None:
        await state.clear()
        return
    uid = msg.from_user.id
    if not _is_party(deal, uid):
        await state.clear()
        await msg.answer("⚠️ This isn't your deal.")
        return

    if deal["status"] == DealStatus.AWAITING_PAYMENT and _is_buyer(deal, uid):
        try:
            await dealops.submit_payment_proof(db, bot, deal["id"], uid,
                                               msg.text or "")
            await msg.answer("🧾 Payment reference received — an admin will "
                             "confirm your payment shortly.")
        except (dealops.DealError, IllegalTransition) as exc:
            await msg.answer(f"⚠️ {html.escape(str(exc))}")
        return

    try:
        await dealops.relay_message(db, bot, deal["id"], uid, msg.text or "")
    except (dealops.DealError, IllegalTransition) as exc:
        await msg.answer(f"⚠️ {html.escape(str(exc))}")


# ------------------------------------------------------------------- /cancel

@router.message(Command("cancel"), DealChat.chatting)
@router.message(Command("cancel"), DealChat.pay_select)
@router.message(Command("cancel"), DisputeFlow.reason)
async def deal_fsm_cancel(msg: Message, state: FSMContext) -> None:
    """Leave the deal chat / abort the dispute draft (state-scoped only —
    the global /cancel lives with Worker A's start.py)."""
    await state.clear()
    await msg.answer("OK — closed. Tap 💬 Chat on a deal to talk again.")

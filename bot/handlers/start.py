"""Entry point handlers: /start menu and the top-level navigation callbacks.

Delegates to the entry functions in sell.py / buy.py / wallet.py so each
flow keeps a single entry point. Delegation targets are imported lazily
inside the handlers to keep module import order safe.
"""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import settings
from bot.db.repo import Deals
from bot.utils import keyboards, texts

router = Router()

_STATUS_LABEL = {
    "NEGOTIATING": "💬 Negotiating",
    "AWAITING_PAYMENT": "⏳ Awaiting payment",
    "PAID_HELD": "🔒 Paid — in escrow",
    "DISPUTED": "🚩 Disputed",
    "RELEASED": "✅ Completed",
    "REFUNDED": "↩️ Refunded",
    "CANCELLED": "❌ Cancelled",
}


async def _send_or_edit(target: Message | CallbackQuery, text: str, **kwargs) -> None:
    """Reply to a message or edit the callback's message, defensively."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, **kwargs)
        except Exception:
            # e.g. message not modified or already deleted — send fresh instead
            await target.message.answer(text, **kwargs)
        await target.answer()
    else:
        await target.answer(text, **kwargs)


def _menu(is_admin: bool) -> tuple[str, Any]:
    return texts.MAIN_MENU, keyboards.main_menu(is_admin)


# ---------------------------------------------------------------------------
# /start and navigation
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message, data: dict, state: FSMContext) -> None:
    await state.clear()
    text, kb = _menu(settings.is_admin(message.from_user.id))
    await message.answer(text, reply_markup=kb)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, data: dict, state: FSMContext) -> None:
    """Global cancel for any multi-step flow (main.py advertises /cancel)."""
    cur = await state.get_state()
    await state.clear()
    note = "🛑 Cancelled." if cur else ""
    text, kb = _menu(settings.is_admin(message.from_user.id))
    await message.answer(f"{note}\n\n{text}".strip() if note else text, reply_markup=kb)


@router.message(Command("help"))
async def cmd_help(message: Message, data: dict, state: FSMContext) -> None:
    await message.answer(texts.HELP_RULES, reply_markup=keyboards.back_home())


@router.callback_query(F.data == "start:home")
async def cb_home(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    await state.clear()
    text, kb = _menu(settings.is_admin(cb.from_user.id))
    await _send_or_edit(cb, text, reply_markup=kb)


@router.callback_query(F.data == "start:buy")
async def cb_buy(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    from bot.handlers.buy import enter_buy
    await enter_buy(cb, data, state)


@router.callback_query(F.data == "start:sell")
async def cb_sell(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    from bot.handlers.sell import enter_sell
    await enter_sell(cb, data, state)


@router.callback_query(F.data == "start:wallet")
async def cb_wallet(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    from bot.handlers.wallet import show_wallet
    await show_wallet(cb, data)


@router.callback_query(F.data == "start:help")
async def cb_help(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    await _send_or_edit(cb, texts.HELP_RULES, reply_markup=keyboards.back_home())


@router.callback_query(F.data == "start:orders")
async def cb_orders(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    db = data["db"]
    deals = await Deals(db).for_user(cb.from_user.id, limit=10)
    if not deals:
        await _send_or_edit(cb, "🧾 <b>My Orders</b>\n\nNo deals yet.", reply_markup=keyboards.back_home())
        return
    lines = ["🧾 <b>My Orders</b>\n"]
    b = InlineKeyboardBuilder()
    for d in deals:
        role = "🛒 Buying" if d["buyer_id"] == cb.from_user.id else "📦 Selling"
        label = _STATUS_LABEL.get(d["status"], d["status"])
        lines.append(f"{role} — <b>{d['title'][:40]}</b>\n   Deal #{d['id']} · {label}")
        b.button(text=f"#{d['id']} {label}", callback_data=f"deal:open:{d['id']}")
    b.adjust(1)
    b.button(text="🏠 Main menu", callback_data="start:home")
    await _send_or_edit(cb, "\n".join(lines), reply_markup=b.as_markup())

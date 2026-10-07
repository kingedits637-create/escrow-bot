"""WALLET view: saved payout details (masked) + PayoutSetup FSM to update.

The wallet never holds money — the escrow bot holds deal funds. This
screen only stores where the user wants to be PAID after a sale.
"""
from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.states import PayoutSetup
from bot.db.repo import Payouts
from bot.utils import keyboards

router = Router()

PAYOUT_METHODS = [
    ("binance", "🟡 Binance (USDT)"),
    ("easypaisa", "🟢 Easypaisa"),
    ("jazzcash", "🟢 JazzCash"),
    ("bank", "🏦 Bank transfer"),
]
PAYOUT_METHOD_LABEL = dict(PAYOUT_METHODS)


def _mask_value(value: str) -> str:
    """Show only the first/last couple of chars — never full payout details."""
    v = str(value)
    if len(v) <= 4:
        return "••••"
    return f"{v[:2]}••••{v[-2:]}"


def _mask_details(details: dict) -> str:
    if not details:
        return "—"
    return ", ".join(
        f"{html.escape(str(k))}: <code>{_mask_value(v)}</code>"
        for k, v in details.items()
    )


def _setup_kb():
    b = InlineKeyboardBuilder()
    for code, label in PAYOUT_METHODS:
        b.button(text=label, callback_data=f"wallet:method:{code}")
    b.button(text="❌ Cancel", callback_data="wallet:cancel")
    b.adjust(1)
    return b.as_markup()


async def _respond(target: Message | CallbackQuery, text: str, **kwargs) -> None:
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, **kwargs)
        except Exception:
            await target.message.answer(text, **kwargs)
        await target.answer()
    else:
        await target.answer(text, **kwargs)


# ---------------------------------------------------------------------------
# Wallet view
# ---------------------------------------------------------------------------

async def show_wallet(event: Message | CallbackQuery, data: dict) -> None:
    """Entry: wallet overview. Callable from start.py delegation."""
    payout = await Payouts(data["db"]).get(event.from_user.id)
    if payout and payout["method"]:
        method_line = f"💳 <b>{html.escape(PAYOUT_METHOD_LABEL.get(payout['method'], payout['method']))}</b>"
        details_line = _mask_details(payout.get("details") or {})
        saved = f"\n{method_line}\n{details_line}"
    else:
        saved = "\n<i>No payout details saved yet.</i>"
    text = (
        "👛 <b>My Wallet</b>\n"
        f"{saved}\n\n"
        "ℹ️ Money from your sales is held by the escrow bot — "
        "this wallet only stores <b>where</b> you want to be paid."
    )
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Set / Update payout details", callback_data="wallet:setup")
    b.button(text="🏠 Main menu", callback_data="start:home")
    b.adjust(1)
    await _respond(event, text, reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# PayoutSetup FSM
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "wallet:setup")
async def cb_setup(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    await cb.message.edit_text("💳 Choose where you want to be paid:", reply_markup=_setup_kb())
    await state.set_state(PayoutSetup.method)
    await cb.answer()


@router.callback_query(F.data == "wallet:cancel")
async def cb_cancel(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    await state.clear()
    await show_wallet(cb, data)


@router.callback_query(F.data.startswith("wallet:method:"), PayoutSetup.method)
async def cb_method(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    method = cb.data.rsplit(":", 1)[1]
    if method not in PAYOUT_METHOD_LABEL:
        await cb.answer("Invalid method.", show_alert=True)
        return
    await state.update_data(method=method)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="wallet:cancel")
    await cb.message.edit_text(
        f"{PAYOUT_METHOD_LABEL[method]}\n\n"
        "✍️ Send your payout details "
        "(e.g. Binance UID or email, Easypaisa/JazzCash number, IBAN):",
        reply_markup=b.as_markup(),
    )
    await state.set_state(PayoutSetup.details)
    await cb.answer()


@router.message(PayoutSetup.details, F.text)
async def msg_details(message: Message, data: dict, state: FSMContext) -> None:
    sdata = await state.get_data()
    details = message.text.strip()
    if len(details) < 3:
        await message.answer("Those details look too short — send the full payout details, or tap ❌ Cancel.")
        return
    await Payouts(data["db"]).save(
        message.from_user.id, sdata["method"], {"info": details}
    )
    await state.clear()
    await message.answer("✅ Payout details saved.", reply_markup=keyboards.back_home())
    await show_wallet(message, data)

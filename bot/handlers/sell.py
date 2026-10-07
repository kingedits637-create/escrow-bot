"""SELL flow: category → subcategory → form → verification → vault →
payout → review → PENDING. Full SellFlow FSM per spec §5.

Honesty rules built in:
- Past violations/strikes are SELLER-DECLARED — we never claim any
  automatic strike/violation verification (spec §5 note).
- The buyer-visible checklist says "Seller declared" / "Proof attached" /
  "Admin spot-checked" exactly as the spec defines.
- An engagement heuristic (optional avg_views) flags absurd
  follower/view mismatches for admin review — it does not reject.
"""
from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.states import SellFlow
from bot.db.repo import Credentials, Listings, Payouts
from bot.utils import keyboards, texts
from bot.utils.catalog import category_names, sub_names
from bot.utils.money import fmt_cents, parse_usd
from bot.utils.vault import VaultError

router = Router()

PAYOUT_METHODS = [
    ("binance", "🟡 Binance (USDT)"),
    ("easypaisa", "🟢 Easypaisa"),
    ("jazzcash", "🟢 JazzCash"),
    ("bank", "🏦 Bank transfer"),
]
PAYOUT_METHOD_LABEL = dict(PAYOUT_METHODS)


def _cancel_kb():
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="sell:cancel")
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
# Entry
# ---------------------------------------------------------------------------

async def enter_sell(event: Message | CallbackQuery, data: dict, state: FSMContext) -> None:
    """Start the sell flow. Callable from start.py delegation."""
    await state.clear()
    names = category_names()
    await _respond(
        event,
        texts.SELL_CATEGORIES,
        reply_markup=keyboards.categories(names, "sell"),
    )
    await state.set_state(SellFlow.category)


@router.callback_query(F.data == "sell:cancel")
async def cb_cancel(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("🛑 Listing draft discarded.", reply_markup=keyboards.back_home())
    await cb.answer()


# ---------------------------------------------------------------------------
# Category / subcategory
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("sell:cat:"), SellFlow.category)
async def cb_category(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    names = category_names()
    try:
        idx = int(cb.data.rsplit(":", 1)[1])
        category = names[idx]
    except (IndexError, ValueError):
        await cb.answer("Invalid category — try again.", show_alert=True)
        return
    await state.update_data(category=category)
    await cb.message.edit_text(
        texts.SUBCATEGORY_PROMPT,
        reply_markup=keyboards.subcategories(sub_names(category), "sell"),
    )
    await state.set_state(SellFlow.subcategory)
    await cb.answer()


@router.callback_query(F.data == "sell:sub:custom", SellFlow.subcategory)
async def cb_sub_custom(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    await cb.message.edit_text(texts.SUBCATEGORY_CUSTOM_PROMPT, reply_markup=_cancel_kb())
    await state.set_state(SellFlow.subcategory_custom)
    await cb.answer()


@router.message(SellFlow.subcategory_custom, F.text)
async def msg_sub_custom(message: Message, data: dict, state: FSMContext) -> None:
    custom = message.text.strip()
    if not custom:
        await message.answer("Please type the account type, or /cancel.")
        return
    await _save_subcategory(message, data, state, f"Custom: {custom[:60]}")


@router.callback_query(F.data.startswith("sell:sub:"), SellFlow.subcategory)
async def cb_subcategory(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    sdata = await state.get_data()
    subs = sub_names(sdata.get("category", ""))
    try:
        idx = int(cb.data.rsplit(":", 1)[1])
        sub = subs[idx]
    except (IndexError, ValueError):
        await cb.answer("Invalid option — try again.", show_alert=True)
        return
    await _save_subcategory(cb, data, state, sub)
    await cb.answer()


async def _save_subcategory(target, data: dict, state: FSMContext, subcategory: str) -> None:
    sdata = await state.get_data()
    await state.update_data(subcategory=subcategory)
    # Create the draft listing early so nothing is lost if the user drops off.
    listing_id = await Listings(data["db"]).create(
        data["db_user"]["tg_id"], sdata["category"], subcategory
    )
    await state.update_data(listing_id=listing_id)
    await _respond(target, texts.SELL_TITLE, reply_markup=_cancel_kb())
    await state.set_state(SellFlow.title)


# ---------------------------------------------------------------------------
# Listing form
# ---------------------------------------------------------------------------

@router.message(SellFlow.title, F.text)
async def msg_title(message: Message, data: dict, state: FSMContext) -> None:
    title = message.text.strip()
    if len(title) < 4:
        await message.answer("Title is too short — give it a bit more detail, or /cancel.")
        return
    await state.update_data(title=title[:120])
    await message.answer(texts.SELL_PRICE, reply_markup=_cancel_kb())
    await state.set_state(SellFlow.price)


@router.message(SellFlow.price, F.text)
async def msg_price(message: Message, data: dict, state: FSMContext) -> None:
    cents = parse_usd(message.text)
    commission = data["settings"].commission_usdt_cents
    if cents is None:
        await message.answer("That doesn't look like a price. Send a number like <code>150</code> or <code>150.00</code>.", reply_markup=_cancel_kb())
        return
    if cents <= commission:
        await message.answer(
            f"⚠️ Price must be above the platform fee ({fmt_cents(commission)}), "
            f"otherwise nothing would remain for you after a sale. "
            f"Send a higher price, or /cancel."
        )
        return
    await state.update_data(price_cents=cents)
    await message.answer(texts.SELL_FOLLOWERS, reply_markup=_cancel_kb())
    await state.set_state(SellFlow.followers)


@router.message(SellFlow.followers, F.text)
async def msg_followers(message: Message, data: dict, state: FSMContext) -> None:
    raw = message.text.strip().replace(",", "").replace("k", "000").replace("K", "000")
    try:
        followers = int(raw)
    except ValueError:
        followers = -1
    if followers < 0:
        await message.answer("Send the follower/subscriber count as a plain number (e.g. 120000), or /cancel.")
        return
    await state.update_data(followers=followers)
    await message.answer(
        "📊 Average views per post? <b>(optional — send 0 to skip)</b>\n"
        "This helps our automatic engagement sanity check.",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(SellFlow.avg_views)


@router.message(SellFlow.avg_views, F.text)
async def msg_avg_views(message: Message, data: dict, state: FSMContext) -> None:
    raw = message.text.strip().replace(",", "")
    try:
        avg = int(raw)
    except ValueError:
        avg = -1
    if avg < 0:
        await message.answer("Send a number (or 0 to skip), or /cancel.")
        return
    await state.update_data(avg_views=avg)
    await message.answer(texts.SELL_COUNTRY, reply_markup=_cancel_kb())
    await state.set_state(SellFlow.country)


@router.message(SellFlow.country, F.text)
async def msg_country(message: Message, data: dict, state: FSMContext) -> None:
    await state.update_data(country=message.text.strip()[:60])
    await message.answer(texts.SELL_AGE, reply_markup=_cancel_kb())
    await state.set_state(SellFlow.account_age)


@router.message(SellFlow.account_age, F.text)
async def msg_age(message: Message, data: dict, state: FSMContext) -> None:
    await state.update_data(account_age=message.text.strip()[:60])
    await message.answer(texts.SELL_DESC, reply_markup=_cancel_kb())
    await state.set_state(SellFlow.description)


@router.message(SellFlow.description, F.text)
async def msg_desc(message: Message, data: dict, state: FSMContext) -> None:
    await state.update_data(description=message.text.strip()[:2000])
    await message.answer(texts.VER_EMAIL, reply_markup=keyboards.yes_no("sell:email"))
    await state.set_state(SellFlow.ver_email_access)


# ---------------------------------------------------------------------------
# Verification (structured, seller-declared — never auto-verified)
# ---------------------------------------------------------------------------

def _yes_no_from_text(text: str) -> str | None:
    t = text.strip().lower()
    if t in ("yes", "y", "yeah", "haan", "جی"):
        return "yes"
    if t in ("no", "n", "nahi", "نہیں"):
        return "no"
    return None


async def _handle_email_access(target, data, state, value: str) -> None:
    await state.update_data(ver_email_access=value)
    await _respond(target, texts.VER_VIOL, reply_markup=keyboards.yes_no("sell:viol"))
    await state.set_state(SellFlow.ver_violations)


@router.callback_query(F.data.startswith("sell:email:"), SellFlow.ver_email_access)
async def cb_email(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    await _handle_email_access(cb, data, state, cb.data.rsplit(":", 1)[1])
    await cb.answer()


@router.message(SellFlow.ver_email_access, F.text)
async def msg_email(message: Message, data: dict, state: FSMContext) -> None:
    v = _yes_no_from_text(message.text)
    if v is None:
        await message.answer("Please tap ✅ Yes or ❌ No.")
        return
    await _handle_email_access(message, data, state, v)


async def _handle_violations(target, data, state, value: str) -> None:
    await state.update_data(ver_violations=value)
    if value == "yes":
        await _respond(target, texts.VER_VIOL_DTL, reply_markup=_cancel_kb())
        await state.set_state(SellFlow.ver_violations_details)
    else:
        await _respond(target, texts.VER_MONETIZED, reply_markup=keyboards.yes_no("sell:mon"))
        await state.set_state(SellFlow.ver_monetized)


@router.callback_query(F.data.startswith("sell:viol:"), SellFlow.ver_violations)
async def cb_viol(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    await _handle_violations(cb, data, state, cb.data.rsplit(":", 1)[1])
    await cb.answer()


@router.message(SellFlow.ver_violations, F.text)
async def msg_viol(message: Message, data: dict, state: FSMContext) -> None:
    v = _yes_no_from_text(message.text)
    if v is None:
        await message.answer("Please tap ✅ Yes or ❌ No.")
        return
    await _handle_violations(message, data, state, v)


@router.message(SellFlow.ver_violations_details, F.text)
async def msg_viol_details(message: Message, data: dict, state: FSMContext) -> None:
    await state.update_data(ver_violations_dtl=message.text.strip()[:500])
    await message.answer(texts.VER_MONETIZED, reply_markup=keyboards.yes_no("sell:mon"))
    await state.set_state(SellFlow.ver_monetized)


async def _handle_monetized(target, data, state, value: str) -> None:
    await state.update_data(ver_monetized=value)
    await _respond(target, texts.VER_VIDEO, reply_markup=_cancel_kb())
    await state.set_state(SellFlow.ver_proof_video)


@router.callback_query(F.data.startswith("sell:mon:"), SellFlow.ver_monetized)
async def cb_mon(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    await _handle_monetized(cb, data, state, cb.data.rsplit(":", 1)[1])
    await cb.answer()


@router.message(SellFlow.ver_monetized, F.text)
async def msg_mon(message: Message, data: dict, state: FSMContext) -> None:
    v = _yes_no_from_text(message.text)
    if v is None:
        await message.answer("Please tap ✅ Yes or ❌ No.")
        return
    await _handle_monetized(message, data, state, v)


@router.message(SellFlow.ver_proof_video, F.video)
async def msg_proof_video(message: Message, data: dict, state: FSMContext) -> None:
    await state.update_data(ver_proof_video=message.video.file_id, ver_proof_shots=[])
    b = InlineKeyboardBuilder()
    b.button(text="✅ Done", callback_data="sell:shots_done")
    b.button(text="❌ Cancel", callback_data="sell:cancel")
    b.adjust(1)
    await message.answer(texts.VER_SHOTS, reply_markup=b.as_markup())
    await state.set_state(SellFlow.ver_proof_shots)


@router.message(SellFlow.ver_proof_video)
async def msg_proof_video_wrong(message: Message, data: dict, state: FSMContext) -> None:
    await message.answer("🎥 Please upload a <b>video</b> screen recording (not a photo or file), or /cancel.")


@router.message(SellFlow.ver_proof_shots, F.photo)
async def msg_proof_shot(message: Message, data: dict, state: FSMContext) -> None:
    sdata = await state.get_data()
    shots = sdata.get("ver_proof_shots", [])
    shots.append(message.photo[-1].file_id)
    await state.update_data(ver_proof_shots=shots)
    await message.answer(f"🖼️ Screenshot {len(shots)} saved. Send more, or tap ✅ Done.")


@router.message(SellFlow.ver_proof_shots, F.text)
async def msg_proof_shot_wrong(message: Message, data: dict, state: FSMContext) -> None:
    if message.text == "/cancel":
        await state.clear()
        await message.answer("🛑 Listing draft discarded.", reply_markup=keyboards.back_home())
        return
    await message.answer("Send screenshots as <b>photos</b>, then tap ✅ Done.")


@router.callback_query(F.data == "sell:shots_done", SellFlow.ver_proof_shots)
async def cb_shots_done(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    sdata = await state.get_data()
    if not sdata.get("ver_proof_shots"):
        await cb.answer("At least 1 screenshot is required.", show_alert=True)
        return
    await cb.message.edit_text(texts.VER_CREDENTIALS, reply_markup=_cancel_kb())
    await state.set_state(SellFlow.credentials)
    await cb.answer()


# ---------------------------------------------------------------------------
# Credentials → encrypted vault
# ---------------------------------------------------------------------------

@router.message(SellFlow.credentials, F.text)
async def msg_credentials(message: Message, data: dict, state: FSMContext) -> None:
    creds = message.text.strip()
    if len(creds) < 4:
        await message.answer("Those credentials look too short — send the full login details, or /cancel.")
        return
    sdata = await state.get_data()
    try:
        blob = data["vault"].encrypt(creds)
    except VaultError:
        await message.answer("⚠️ Vault is not configured. Please contact support.")
        return
    await Credentials(data["db"]).save(sdata["listing_id"], blob)
    # Remove the plaintext credentials from the chat for the seller's safety.
    try:
        await message.delete()
    except Exception:
        pass
    b = InlineKeyboardBuilder()
    for code, label in PAYOUT_METHODS:
        b.button(text=label, callback_data=f"sell:payout:{code}")
    b.button(text="❌ Cancel", callback_data="sell:cancel")
    b.adjust(1)
    await message.answer(texts.PAYOUT_METHOD, reply_markup=b.as_markup())
    await state.set_state(SellFlow.payout_method)


# ---------------------------------------------------------------------------
# Payout details
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("sell:payout:"), SellFlow.payout_method)
async def cb_payout_method(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    method = cb.data.rsplit(":", 1)[1]
    if method not in PAYOUT_METHOD_LABEL:
        await cb.answer("Invalid method.", show_alert=True)
        return
    await state.update_data(payout_method=method)
    await cb.message.edit_text(
        f"{PAYOUT_METHOD_LABEL[method]}\n\n{texts.PAYOUT_DETAILS}",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(SellFlow.payout_details)
    await cb.answer()


@router.message(SellFlow.payout_details, F.text)
async def msg_payout_details(message: Message, data: dict, state: FSMContext) -> None:
    details = message.text.strip()
    if len(details) < 3:
        await message.answer("Those details look too short — send the full payout details, or /cancel.")
        return
    sdata = await state.get_data()
    await Payouts(data["db"]).save(
        message.from_user.id, sdata["payout_method"], {"info": details}
    )
    await message.answer(await _review_text(data, state), reply_markup=_review_kb(), parse_mode="HTML")
    await state.set_state(SellFlow.review)


# ---------------------------------------------------------------------------
# Review + submit
# ---------------------------------------------------------------------------

def _review_kb():
    b = InlineKeyboardBuilder()
    b.button(text="✅ Submit for review", callback_data="sell:submit")
    b.button(text="❌ Cancel", callback_data="sell:cancel")
    b.adjust(1)
    return b.as_markup()


async def _review_text(data: dict, state: FSMContext) -> str:
    s = await state.get_data()
    shots = s.get("ver_proof_shots", [])
    viol = s.get("ver_violations")
    viol_line = "✅ No" if viol == "no" else f"⚠️ Yes — {html.escape(s.get('ver_violations_dtl', ''))}"
    flagged = _engagement_flagged(s.get("followers", 0), s.get("avg_views", 0))
    lines = [
        "📋 <b>Review your listing</b>\n",
        f"<b>Title:</b> {html.escape(s.get('title', ''))}",
        f"<b>Category:</b> {html.escape(s.get('category', ''))} → {html.escape(s.get('subcategory', ''))}",
        f"<b>Price:</b> {fmt_cents(s.get('price_cents', 0))} (fee {fmt_cents(data['settings'].commission_usdt_cents)} on USDT sales)",
        f"<b>Followers:</b> {s.get('followers', 0):,}" + (f" · avg views/post: {s.get('avg_views', 0):,}" if s.get('avg_views') else ""),
        f"<b>Country:</b> {html.escape(s.get('country', ''))}",
        f"<b>Account age:</b> {html.escape(s.get('account_age', ''))}",
        f"<b>Description:</b> {html.escape(s.get('description', ''))}",
        "",
        "<b>Verification (seller-declared — not auto-verified):</b>",
        f"• Original email access: {'✅ Yes' if s.get('ver_email_access') == 'yes' else '❌ No'}",
        f"• Past violations/strikes: {viol_line}",
        f"• Monetized: {'✅ Yes' if s.get('ver_monetized') == 'yes' else '❌ No'}",
        f"• Proof video: {'✅ Attached' if s.get('ver_proof_video') else '❌ Missing'}",
        f"• Screenshots: ✅ {len(shots)} attached",
        "",
        f"<b>Payout:</b> {html.escape(PAYOUT_METHOD_LABEL.get(s.get('payout_method', ''), ''))}",
        "",
        ("<i>Tap Submit — your listing goes live immediately.</i>"
         if data["settings"].auto_approve_listings else
         "<i>Tap Submit — an admin will review this listing before it goes live.</i>"),
    ]
    if flagged:
        lines.append(texts.ENGAGEMENT_FLAG_NOTE)
    return "\n".join(lines)


def _engagement_flagged(followers: int, avg_views: int) -> bool:
    """Heuristic from the spec: absurd follower/view mismatch → admin spot-check."""
    from bot.core.dealops import engagement_flagged
    return engagement_flagged(followers, avg_views)


@router.callback_query(F.data == "sell:submit", SellFlow.review)
async def cb_submit(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    from bot.core import dealops as _dealops

    s = await state.get_data()
    status, _flagged = await _dealops.submit_listing(
        data["db"], data["bot"], cb.from_user.id, s, data["settings"])
    if status == "LIVE":
        await cb.message.edit_text(texts.LISTING_AUTO_LIVE,
                                   reply_markup=keyboards.back_home())
    else:
        await cb.message.edit_text(texts.LISTING_SUBMITTED,
                                   reply_markup=keyboards.back_home())
    await state.clear()
    await cb.answer()

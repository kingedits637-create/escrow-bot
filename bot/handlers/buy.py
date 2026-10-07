"""BUY flow: category → budget filter → paginated live listings →
detail view. Spec §6.

Privacy rule: NEVER any seller identity — no tg_id, username or name.
Only the aggregate rating (e.g. 4.8★ · 120 deals) is shown.
The [Buy Now] button points at `deal:buy:<listing_id>`, which is handled
by WORKER B's deal.py — this screen only links to it.
"""
from __future__ import annotations

import html
import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.states import BuyFlow
from bot.db.repo import Listings, Users
from bot.utils import keyboards, texts
from bot.utils.catalog import category_names
from bot.utils.money import fmt_cents

router = Router()

PAGE_SIZE = 5


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

async def enter_buy(event: Message | CallbackQuery, data: dict, state: FSMContext) -> None:
    """Start the buy flow. Callable from start.py delegation."""
    await state.clear()
    await _respond(
        event,
        texts.BUY_CATEGORIES,
        reply_markup=keyboards.categories(category_names(), "buy"),
    )
    await state.set_state(BuyFlow.category)


# ---------------------------------------------------------------------------
# Category → budget
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("buy:cat:"))
async def cb_category(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    names = category_names()
    try:
        idx = int(cb.data.rsplit(":", 1)[1])
        category = names[idx]
    except (IndexError, ValueError):
        await cb.answer("Invalid category — try again.", show_alert=True)
        return
    await state.update_data(buy_category=category)
    await cb.message.edit_text(texts.BUDGET_PROMPT, reply_markup=keyboards.budget_filter())
    await state.set_state(BuyFlow.budget)
    await cb.answer()


@router.callback_query(F.data.startswith("buy:budget:"))
async def cb_budget(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    key = cb.data.rsplit(":", 1)[1]
    if key not in keyboards.BUDGET_RANGES:
        await cb.answer("Invalid budget — try again.", show_alert=True)
        return
    await state.update_data(buy_budget=key)
    sdata = await state.get_data()
    await _show_page(cb, data, state, sdata.get("buy_category"), key, 0)
    await state.set_state(BuyFlow.browsing)


@router.callback_query(F.data.startswith("buy:page:"))
async def cb_page(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    # buy:page:<cat_idx>:<budget>:<offset>
    try:
        _, _, cat_idx, budget, offset = cb.data.split(":")
        category = category_names()[int(cat_idx)]
        offset = int(offset)
    except (ValueError, IndexError):
        await cb.answer("Something went wrong — start over.", show_alert=True)
        return
    await _show_page(cb, data, state, category, budget, offset)


# ---------------------------------------------------------------------------
# Listing cards (paginated) — no seller identity, only aggregate rating
# ---------------------------------------------------------------------------

async def _show_page(target, data: dict, state: FSMContext,
                     category: str | None, budget: str, offset: int) -> None:
    db = data["db"]
    listings = Listings(db)
    min_cents, max_cents = keyboards.BUDGET_RANGES[budget]
    total = await listings.count_live(category, min_cents, max_cents)
    items = await listings.live(category, min_cents, max_cents,
                                limit=PAGE_SIZE, offset=offset)

    if not items:
        await _respond(target, texts.NO_LISTINGS, reply_markup=keyboards.back_home())
        return

    await state.update_data(buy_category=category, buy_budget=budget)
    lines = [f"🛒 <b>{html.escape(category or 'All')}</b> — live listings ({total}):\n"]
    b = InlineKeyboardBuilder()
    users = Users(db)
    for l in items:
        seller = await users.get(l["seller_id"])
        rating = Users.rating_text(seller) if seller else "New seller"
        badge = "✅ Monetized" if l["ver_monetized"] == "yes" else "❌ Not monetized"
        lines.append(
            f"📦 <b>{html.escape(l['title'])}</b>\n"
            f"   💵 {fmt_cents(l['price_cents'])} · 👥 {l['followers']:,} · 🌍 {html.escape(l['country'])}\n"
            f"   {badge} · ⭐ {html.escape(rating)}"
        )
        short = (l["title"][:28] + "…") if len(l["title"]) > 28 else l["title"]
        b.button(text=f"👁 {short} — {fmt_cents(l['price_cents'])}",
                 callback_data=f"buy:view:{l['id']}")
    b.adjust(1)

    nav: list[tuple[str, str]] = []
    cat_idx = category_names().index(category) if category in category_names() else 0
    if offset > 0:
        nav.append(("◀ Prev", f"buy:page:{cat_idx}:{budget}:{max(0, offset - PAGE_SIZE)}"))
    if offset + PAGE_SIZE < total:
        nav.append(("Next ▶", f"buy:page:{cat_idx}:{budget}:{offset + PAGE_SIZE}"))
    for text, cb_data in nav:
        b.button(text=text, callback_data=cb_data)
    b.button(text="🏠 Main menu", callback_data="start:home")
    b.adjust(1, 2)
    await _respond(target, "\n".join(lines), reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------

def _verification_summary(listing: dict) -> str:
    """Short buyer-visible verification summary from stored checklist."""
    try:
        checklist = json.loads(listing.get("ver_checklist") or "{}")
    except (TypeError, ValueError):
        checklist = {}
    parts = []
    if listing.get("ver_proof_video"):
        parts.append("proof video")
    try:
        shots = json.loads(listing.get("ver_proof_shots") or "[]")
    except (TypeError, ValueError):
        shots = []
    if shots:
        parts.append(f"{len(shots)} screenshot{'s' if len(shots) != 1 else ''}")
    summary = "✔ " + " + ".join(parts) if parts else "⏳ awaiting admin review"
    # Seller-declared items are never presented as verified by us.
    email = checklist.get("email_access", "")
    viol = checklist.get("violations", "")
    decl = []
    if email:
        decl.append("email access: seller declared")
    if viol:
        decl.append("strikes: seller declared")
    if decl:
        summary += "\n   (" + ", ".join(decl) + " — not auto-verified)"
    return summary


@router.callback_query(F.data.startswith("buy:view:"))
async def cb_view(cb: CallbackQuery, data: dict, state: FSMContext) -> None:
    try:
        listing_id = int(cb.data.rsplit(":", 1)[1])
    except ValueError:
        await cb.answer("Invalid listing.", show_alert=True)
        return
    db = data["db"]
    listing = await Listings(db).get(listing_id)
    if listing is None or listing["status"] != "LIVE":
        await cb.answer("This listing is no longer available.", show_alert=True)
        return

    # Buyer side of the deal: buyer must not be the seller of this listing.
    if listing["seller_id"] == cb.from_user.id:
        await cb.answer("This is your own listing.", show_alert=True)
        return

    seller = await Users(db).get(listing["seller_id"])
    rating = Users.rating_text(seller) if seller else "New seller"
    badge = "✅ Monetized" if listing["ver_monetized"] == "yes" else "❌ Not monetized"
    text = (
        f"📦 <b>{html.escape(listing['title'])}</b>\n\n"
        f"💵 <b>{fmt_cents(listing['price_cents'])}</b>\n"
        f"👥 Followers: {listing['followers']:,}\n"
        f"🌍 Country: {html.escape(listing['country'])}\n"
        f"📅 Account age: {html.escape(listing['account_age'])}\n"
        f"{badge}\n\n"
        f"🧾 {html.escape(listing['description'])}\n\n"
        f"🔍 <b>Verification:</b>\n{_verification_summary(listing)}\n\n"
        f"⭐ Seller rating: {html.escape(rating)}"
    )
    if listing["engagement_flag"]:
        text += "\n⚠️ <i>Auto-check flagged unusual engagement — an admin will spot-check.</i>"
    await _respond(cb, text, reply_markup=keyboards.listing_detail(listing_id))

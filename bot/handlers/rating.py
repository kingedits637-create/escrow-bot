"""Rating handlers (WORKER B).

prompt_ratings(db, bot, deal_id) is called by bot.core.dealops after a
deal is RELEASED or REFUNDED. Callbacks: rate:stars:<deal>:<n> then an
optional comment (RateFlow.comment, skippable via rate:skip:<deal>).

Guards (re-checked from the DB on every step):
  - rater is the buyer or seller of the deal
  - deal is completed (RELEASED or REFUNDED)
  - within settings.rating_window_days of completion
  - one rating per deal per side (UNIQUE(deal_id, rater_id))
Low seller ratings trigger an admin alert (Ratings.low_sellers).
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import Settings
from bot.core.states import RateFlow
from bot.db.base import Database
from bot.db.repo import Deals, Ratings, Users
from bot.utils import keyboards, texts
from bot.utils.notify import notify_admins

router = Router()

_COMPLETED = {"RELEASED", "REFUNDED"}


async def prompt_ratings(db: Database, bot: Any, deal_id: int) -> None:
    """Ask both sides to rate the completed deal."""
    deal = await Deals(db).get(deal_id)
    if deal is None or deal["status"] not in _COMPLETED:
        return
    buyer_id, seller_id = deal["buyer_id"], deal["seller_id"]
    jobs = [
        (buyer_id, "seller", "seller"),
        (seller_id, "buyer", "buyer"),
    ]
    for tg_id, other, ratee in jobs:
        try:
            await bot.send_message(
                tg_id,
                texts.RATE_PROMPT.replace("{deal_id}", str(deal_id))
                                 .replace("{other}", other),
                parse_mode="HTML",
                reply_markup=keyboards.stars(deal_id, ratee),
            )
        except Exception:
            pass


# ------------------------------------------------------------------ guards

def _completed_at(deal: dict) -> Optional[datetime]:
    raw = deal.get("released_at") or deal.get("refunded_at")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%fZ").replace(
            tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


async def _rate_guard(db: Database, settings: Settings, deal_id: int,
                      rater_id: int) -> tuple[bool, str, Optional[dict],
                                             Optional[int]]:
    """Returns (ok, error_text, deal, ratee_id)."""
    deal = await Deals(db).get(deal_id)
    if deal is None:
        return False, "Deal not found.", None, None
    if rater_id == deal["buyer_id"]:
        ratee_id = deal["seller_id"]
    elif rater_id == deal["seller_id"]:
        ratee_id = deal["buyer_id"]
    else:
        return False, "You're not part of this deal.", None, None
    if deal["status"] not in _COMPLETED:
        return False, "You can only rate a completed deal.", None, None
    done_at = _completed_at(deal)
    if done_at is None or (datetime.now(timezone.utc) - done_at
                           > timedelta(days=settings.rating_window_days)):
        return False, "The rating window for this deal has closed.", None, None
    if await Ratings(db).exists(deal_id, rater_id):
        return False, "You've already rated this deal.", None, None
    return True, "", deal, ratee_id


async def _save_rating(db: Database, bot: Any, settings: Settings,
                       deal_id: int, rater_id: int, stars: int,
                       comment: str) -> str:
    """Persist the rating; returns user-facing confirmation text."""
    ok, err, _deal, ratee_id = await _rate_guard(db, settings, deal_id,
                                                 rater_id)
    if not ok:
        return f"⚠️ {html.escape(err)}"
    added = await Ratings(db).add(deal_id, rater_id, ratee_id, stars,
                                  comment[:500])
    if not added:
        return "⚠️ You've already rated this deal."
    await Users(db).add_rating(ratee_id, stars)

    # Sustained low seller rating -> admin alert.
    try:
        for low in await Ratings(db).low_sellers():
            if low["tg_id"] == ratee_id:
                await notify_admins(
                    bot,
                    f"🚨 <b>Low seller rating</b> — user {ratee_id}: "
                    f"{low['avg']:.1f}★ over {low['rating_count']} deals "
                    f"(after deal #{deal_id}).",
                )
                break
    except Exception:
        pass
    return texts.RATE_DONE


# ---------------------------------------------------------------- callbacks

@router.callback_query(F.data.startswith("rate:stars:"))
async def rate_stars(cb: CallbackQuery, state: FSMContext, db: Database,
                     settings: Settings) -> None:
    """rate:stars:<deal>:<n> -> ask for an optional comment."""
    try:
        _, _, deal_s, n_s = cb.data.split(":")
        deal_id, stars = int(deal_s), int(n_s)
    except (ValueError, IndexError):
        await cb.answer("Bad request.", show_alert=True)
        return
    if stars not in (1, 2, 3, 4, 5):
        await cb.answer("Pick 1–5 stars.", show_alert=True)
        return
    ok, err, _deal, _ratee = await _rate_guard(db, settings, deal_id,
                                               cb.from_user.id)
    if not ok:
        await cb.answer(err, show_alert=True)
        return
    await state.set_state(RateFlow.comment)
    await state.update_data(deal_id=deal_id, stars=stars)
    await cb.message.answer(texts.RATE_COMMENT,
                            reply_markup=keyboards.skip_comment(deal_id))
    await cb.answer()


@router.callback_query(F.data.startswith("rate:skip:"))
async def rate_skip(cb: CallbackQuery, state: FSMContext, db: Database,
                    bot: Any, settings: Settings) -> None:
    """rate:skip:<deal> -> record the stars with no comment."""
    try:
        deal_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Bad request.", show_alert=True)
        return
    data = await state.get_data()
    stars = data.get("stars")
    if data.get("deal_id") != deal_id or not stars:
        await cb.answer("Rating session expired — tap the stars again.",
                        show_alert=True)
        return
    text = await _save_rating(db, bot, settings, deal_id, cb.from_user.id,
                              stars, "")
    await state.clear()
    await cb.message.answer(text)
    await cb.answer()


@router.message(RateFlow.comment, F.text)
async def rate_comment(msg: Message, state: FSMContext, db: Database,
                       bot: Any, settings: Settings) -> None:
    data = await state.get_data()
    deal_id, stars = data.get("deal_id"), data.get("stars")
    if not deal_id or not stars:
        await state.clear()
        await msg.answer("⚠️ Rating session expired — tap the stars again.")
        return
    text = await _save_rating(db, bot, settings, deal_id, msg.from_user.id,
                              stars, (msg.text or "").strip())
    await state.clear()
    await msg.answer(text)

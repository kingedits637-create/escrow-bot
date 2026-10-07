"""Middleware: ensure user row exists on every update; block banned users."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from bot.config import settings
from bot.db.base import Database
from bot.db.repo import Users


class UserMiddleware(BaseMiddleware):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                     event: TelegramObject, data: dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None)
        if user is not None:
            repo = Users(self.db)
            urec = await repo.ensure(user.id, user.username or "", user.first_name or "")
            data["db_user"] = urec
            if urec["is_banned"] and not settings.is_admin(user.id):
                if isinstance(event, Message):
                    await event.answer("⛔ Your account is banned. Contact support.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⛔ Your account is banned.", show_alert=True)
                return None
        # Handlers declare `data: dict` expecting the full context dict, but
        # aiogram only injects kwargs by individual key name — so expose the
        # dict itself under the "data" key for name-based injection.
        data["data"] = data
        return await handler(event, data)

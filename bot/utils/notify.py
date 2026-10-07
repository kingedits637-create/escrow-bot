"""Small shared helpers."""
from __future__ import annotations

from typing import Any

from bot.config import settings


async def notify_admins(bot: Any, text: str) -> None:
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass


def escrow_address_configured(cfg=None) -> bool:
    c = cfg or settings
    return bool(c.escrow_binance_address or c.escrow_manual_number)

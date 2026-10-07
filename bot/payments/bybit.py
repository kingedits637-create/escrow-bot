"""Bybit/USDT payment adapter — auto-verify via deposit polling.

Same two modes as the Binance adapter:
- AUTO (default when read-only Bybit API keys + escrow USDT address are
  configured): buyer sends the exact unique USDT amount; the bot polls
  Bybit deposit records and auto-confirms on match. Keys are READ-ONLY —
  only signed GET requests for deposit history are ever made.
- MANUAL (fallback): buyer sends USDT and submits their Bybit UID;
  an admin taps Confirm. Used automatically when keys are absent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from bot.utils.money import fmt_cents
from .base import PaymentAdapter

if TYPE_CHECKING:
    from bot.config import Settings


class BybitAdapter(PaymentAdapter):
    code = "bybit"
    label = "Bybit USDT"

    def __init__(self, cfg: "Settings") -> None:
        self.cfg = cfg

    @property
    def auto_verify_possible(self) -> bool:
        return self.cfg.bybit_auto_configured()

    def instructions(self, deal: dict, cfg: "Settings") -> str:
        addr = cfg.bybit_usdt_address or "<i>not configured — ask admin</i>"
        if self.auto_verify_possible and deal.get("expected_usdt"):
            expected = float(deal["expected_usdt"])
            return (
                f"🔵 <b>Pay with Bybit (USDT)</b> — ⚡ auto-verified\n\n"
                f"Send exactly <b>{expected:.2f}</b> USDT to:\n"
                f"<code>{addr}</code>\n\n"
                f"⚠️ The <b>exact</b> amount matters (including the cents) — "
                f"that's how the bot recognizes your payment.\n\n"
                f"🤖 The bot will auto-detect your deposit (usually within a "
                f"few minutes) and deliver instantly. You don't need to send "
                f"anything else.\n\n"
                f"If it isn't detected within {cfg.payment_poll_timeout_min} "
                f"minutes, just send your <b>Bybit UID</b> here as a fallback."
            )
        amount = fmt_cents(deal["price_cents"])
        return (
            f"🔵 <b>Pay with Bybit (USDT)</b>\n\n"
            f"Send exactly <b>{amount}</b> USDT to the escrow wallet:\n"
            f"<code>{addr}</code>\n\n"
            f"Then send your <b>Bybit UID</b> here "
            f"(from your payment receipt)."
        )


ADAPTER = BybitAdapter

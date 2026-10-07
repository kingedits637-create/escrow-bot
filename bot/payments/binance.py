"""Binance/USDT payment adapter (spec §8a) + auto-verify via deposit polling.

Two modes:
- AUTO (default when read-only API keys + escrow USDT address are
  configured): the buyer sends the exact unique USDT amount; the bot polls
  Binance deposit history and auto-confirms on match. Keys are READ-ONLY —
  the adapter only ever performs signed GET requests for deposit history.
- MANUAL (fallback): buyer sends USDT and submits their Binance Pay ID;
  an admin taps Confirm. Used automatically when keys are absent.

The adapter NEVER claims auto-verification works when it isn't
configured (see BUILD_REPORT).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from bot.utils.money import fmt_cents
from .base import PaymentAdapter

if TYPE_CHECKING:
    from bot.config import Settings


class BinanceAdapter(PaymentAdapter):
    code = "binance"
    label = "Binance USDT"

    def __init__(self, cfg: "Settings") -> None:
        self.cfg = cfg

    @property
    def auto_verify_possible(self) -> bool:
        # Auto mode needs read-only API keys + the escrow address.
        # (Legacy BINANCE_MODE=auto is still honored when keys exist.)
        return self.cfg.binance_auto_configured()

    def _address(self, cfg: "Settings") -> str:
        return (cfg.binance_usdt_address or cfg.escrow_binance_address
                or "<i>not configured — ask admin</i>")

    def instructions(self, deal: dict, cfg: "Settings") -> str:
        addr = self._address(cfg)
        network = cfg.binance_network or "TRC20"
        if self.auto_verify_possible and deal.get("expected_usdt"):
            expected = float(deal["expected_usdt"])
            return (
                f"🟡 <b>Pay with Binance (USDT)</b> — ⚡ auto-verified\n\n"
                f"Send exactly <b>{expected:.2f}</b> USDT ({network}) to:\n"
                f"<code>{addr}</code>\n\n"
                f"⚠️ The <b>exact</b> amount matters (including the cents) — "
                f"that's how the bot recognizes your payment.\n\n"
                f"🤖 The bot will auto-detect your deposit (usually within a "
                f"few minutes) and deliver instantly. You don't need to send "
                f"anything else.\n\n"
                f"If it isn't detected within {cfg.payment_poll_timeout_min} "
                f"minutes, just send your <b>Binance Pay ID</b> here as a "
                f"fallback."
            )
        amount = fmt_cents(deal["price_cents"])
        return (
            f"🟡 <b>Pay with Binance (USDT)</b>\n\n"
            f"Send exactly <b>{amount}</b> USDT to the escrow wallet:\n"
            f"<code>{addr}</code>\n\n"
            f"Then send your <b>Binance Pay ID</b> here "
            f"(the numbers from your payment receipt)."
        )


ADAPTER = BinanceAdapter

"""Manual payment adapter: Easypaisa / JazzCash / bank (spec §8b).

Buyer sends money to the escrow number/account, uploads a screenshot,
and an admin taps [Confirm] / [Reject] inside the deal.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from bot.utils.money import fmt_pkr, usd_cents_to_pkr
from .base import PaymentAdapter

if TYPE_CHECKING:
    from bot.config import Settings


class ManualAdapter(PaymentAdapter):
    code = "manual"
    label = "Easypaisa / JazzCash / Bank"

    def instructions(self, deal: dict, cfg: "Settings") -> str:
        pkr = deal.get("payable_pkr") or usd_cents_to_pkr(
            deal["price_cents"], cfg.usd_pkr_rate)
        num = cfg.escrow_manual_number or "<i>not configured — ask admin</i>"
        title = cfg.escrow_manual_title or "Escrow"
        return (
            f"🟢 <b>Pay with Easypaisa / JazzCash / Bank</b>\n\n"
            f"Send exactly <b>{fmt_pkr(pkr)}</b> to:\n"
            f"<code>{num}</code> ({title})\n\n"
            f"Then upload a <b>screenshot</b> of your payment here. "
            f"An admin will confirm it."
        )


ADAPTER = ManualAdapter

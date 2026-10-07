"""Payment adapter interface (spec §8). New methods are added by
subclassing PaymentAdapter — no changes to the deal engine needed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.config import Settings


class PaymentAdapter(ABC):
    code: str = ""   # 'binance' | 'manual'
    label: str = ""

    @abstractmethod
    def instructions(self, deal: dict, cfg: "Settings") -> str:
        """HTML instructions shown to the buyer after choosing this method."""

    @property
    def needs_admin_confirm(self) -> bool:
        """True = a human admin must tap Confirm before delivery."""
        return True

    @property
    def auto_verify_possible(self) -> bool:
        """True only when live API verification is actually configured."""
        return False

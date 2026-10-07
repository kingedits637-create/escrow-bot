"""Environment-based configuration. No secrets are hard-coded anywhere."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int_list(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def _str_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        raw = default
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


@dataclass
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    admin_ids: list[int] = field(default_factory=lambda: _int_list("ADMIN_IDS"))
    fernet_key: str = field(default_factory=lambda: os.getenv("FERNET_KEY", ""))
    # --- differentiated commission per payment method (clean replace of the
    # old single COMMISSION_CENTS) ---
    commission_usdt_cents: int = field(
        default_factory=lambda: _int("COMMISSION_USDT_CENTS", 200)
    )
    commission_local_pkr: int = field(
        default_factory=lambda: _int("COMMISSION_LOCAL_PKR", 50)
    )
    usd_pkr_rate: float = field(
        default_factory=lambda: _float("USD_PKR_RATE", 280.0)
    )
    # --- auto-approve listings ---
    auto_approve_listings: bool = field(
        default_factory=lambda: _bool("AUTO_APPROVE_LISTINGS", True)
    )
    # --- enabled payment methods (comma-separated; valid: binance, bybit, manual) ---
    pay_methods_list: list[str] = field(
        default_factory=lambda: _str_list("PAY_METHODS", "binance,manual")
    )
    # --- auto payment verification (deposit polling, read-only keys) ---
    binance_usdt_address: str = field(
        default_factory=lambda: os.getenv("BINANCE_USDT_ADDRESS", "")
    )
    binance_network: str = field(
        default_factory=lambda: os.getenv("BINANCE_NETWORK", "TRC20")
    )
    bybit_api_key: str = field(default_factory=lambda: os.getenv("BYBIT_API_KEY", ""))
    bybit_api_secret: str = field(
        default_factory=lambda: os.getenv("BYBIT_API_SECRET", "")
    )
    bybit_usdt_address: str = field(
        default_factory=lambda: os.getenv("BYBIT_USDT_ADDRESS", "")
    )
    payment_poll_interval_sec: int = field(
        default_factory=lambda: _int("PAYMENT_POLL_INTERVAL_SEC", 60)
    )
    payment_poll_timeout_min: int = field(
        default_factory=lambda: _int("PAYMENT_POLL_TIMEOUT_MIN", 30)
    )
    payment_amount_tolerance: float = field(
        default_factory=lambda: _float("PAYMENT_AMOUNT_TOLERANCE", 0.5)
    )
    hold_hours: float = field(default_factory=lambda: _float("HOLD_HOURS", 24.0))
    handover_hours: float = field(default_factory=lambda: _float("HANDOVER_HOURS", 8.0))
    delivery_deadline_hours: float = field(
        default_factory=lambda: _float("DELIVERY_DEADLINE_HOURS", 12.0)
    )
    binance_mode: str = field(
        default_factory=lambda: os.getenv("BINANCE_MODE", "manual").lower()
    )
    binance_api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    binance_api_secret: str = field(
        default_factory=lambda: os.getenv("BINANCE_API_SECRET", "")
    )
    escrow_binance_address: str = field(
        default_factory=lambda: os.getenv("ESCROW_BINANCE_ADDRESS", "")
    )
    escrow_manual_number: str = field(
        default_factory=lambda: os.getenv("ESCROW_MANUAL_NUMBER", "")
    )
    escrow_manual_title: str = field(
        default_factory=lambda: os.getenv("ESCROW_MANUAL_TITLE", "Escrow")
    )
    database_path: str = field(
        default_factory=lambda: os.getenv("DATABASE_PATH", "./data/escrow.db")
    )
    webhook_url: str = field(default_factory=lambda: os.getenv("WEBHOOK_URL", "").rstrip("/"))
    port: int = field(default_factory=lambda: _int("PORT", 8000))
    rating_window_days: int = field(
        default_factory=lambda: _int("RATING_WINDOW_DAYS", 7)
    )

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_ids

    def validate(self) -> list[str]:
        """Return a list of configuration problems (empty = OK to boot)."""
        problems: list[str] = []
        if not self.bot_token or ":" not in self.bot_token:
            problems.append("BOT_TOKEN is missing or malformed.")
        if not self.fernet_key:
            problems.append("FERNET_KEY is missing. Run: python -m bot.utils.vault genkey")
        if not self.admin_ids:
            problems.append("ADMIN_IDS is empty — nobody can open the admin panel.")
        if self.commission_usdt_cents < 0:
            problems.append("COMMISSION_USDT_CENTS cannot be negative.")
        if self.commission_local_pkr < 0:
            problems.append("COMMISSION_LOCAL_PKR cannot be negative.")
        if self.usd_pkr_rate <= 0:
            problems.append("USD_PKR_RATE must be positive.")
        if self.payment_poll_interval_sec < 15:
            problems.append("PAYMENT_POLL_INTERVAL_SEC is too low (min 15).")
        if self.binance_mode not in ("manual", "auto"):
            problems.append("BINANCE_MODE must be 'manual' or 'auto'.")
        return problems

    # --- auto-verify helpers -------------------------------------------------
    def binance_auto_configured(self) -> bool:
        """True when Binance deposit-polling can run (read-only keys)."""
        return bool(
            self.binance_api_key and self.binance_api_secret
            and self.binance_usdt_address
        )

    def bybit_auto_configured(self) -> bool:
        """True when Bybit deposit-polling can run (read-only keys)."""
        return bool(
            self.bybit_api_key and self.bybit_api_secret
            and self.bybit_usdt_address
        )


def mask_secret(value: str) -> str:
    """Never log raw secrets: '****' + last 4 chars."""
    v = str(value or "")
    return "****" + v[-4:] if len(v) > 4 else "****"


settings = Settings()

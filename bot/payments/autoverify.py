"""Auto payment verification via exchange deposit polling (read-only keys).

How it works
------------
When a buyer picks Binance or Bybit AND the owner configured read-only API
keys + the escrow USDT address, the bot assigns the deal a UNIQUE expected
amount (price + random cents, e.g. $100 -> $100.37) and polls the exchange
deposit history every PAYMENT_POLL_INTERVAL_SEC. A deposit counts when:

  * it is confirmed on-chain (Binance status==1 / Bybit status==3),
  * abs(deposit.amount - expected) <= PAYMENT_AMOUNT_TOLERANCE,
  * deposit time >= polling start (no credit for older deposits),
  * (Binance) the network matches BINANCE_NETWORK when configured.

On match the payment is auto-confirmed (same delivery flow as an admin
tap). On PAYMENT_POLL_TIMEOUT_MIN without a match the buyer + admins are
notified and the deal stays AWAITING_PAYMENT for manual handling.

Security: only READ-ONLY API keys are ever needed. This module performs
signed GET requests for deposit history only — it can never withdraw,
trade, or move funds. Secrets are never logged (see mask_secret).

If keys are absent/misconfigured the adapters report auto_verify_possible
False and the bot silently uses the manual Pay-ID flow instead.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from bot.config import mask_secret
from bot.core.escrow import DealStatus
from bot.db.base import Database
from bot.db.repo import Deals, utcnow
from bot.utils import texts
from bot.utils.notify import notify_admins

_TS_FMT = "%Y-%m-%dT%H:%M:%fZ"


# ------------------------------------------------------- unique-amount trick

def generate_expected_usdt(price_cents: int,
                           taken: set[float] | None = None) -> float:
    """Unique USDT amount for a deal: price + random cents (never .00).

    `taken` holds expected amounts already assigned to other live deals for
    the same method; on collision we re-roll (cap 100 tries — practically
    impossible to exhaust). Deposit time is the second disambiguator, so a
    stale collision can never credit the wrong deal.
    """
    base = round(int(price_cents) / 100, 2)
    taken = taken or set()
    for _ in range(100):
        cents = random.randint(1, 99)  # never .00 — looks intentional
        expected = round(base + cents / 100, 2)
        if expected not in taken:
            return expected
    raise ValueError("could not generate a unique expected amount")


# ----------------------------------------------------------------- signing

def binance_signature(secret: str, query_string: str) -> str:
    return hmac.new(secret.encode(), query_string.encode(),
                    hashlib.sha256).hexdigest()


def bybit_signature(secret: str, timestamp_ms: str, api_key: str,
                    recv_window: str, query_string: str) -> str:
    payload = timestamp_ms + api_key + recv_window + query_string
    return hmac.new(secret.encode(), payload.encode(),
                    hashlib.sha256).hexdigest()


# ------------------------------------------------------------- http plumbing

async def _aiohttp_get(url: str, headers: dict | None = None,
                       params: dict | None = None,
                       timeout_sec: int = 25) -> Any:
    """Production HTTP GET (aiohttp). Swappable in tests via the `http_get`
    parameter of the fetch_* functions below."""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers or {},
                               params=params or {}) as resp:
            resp.raise_for_status()
            return await resp.json()


HttpGet = Callable[..., Awaitable[Any]]


async def fetch_binance_deposits(api_key: str, api_secret: str,
                                 http_get: HttpGet = _aiohttp_get) -> list[dict]:
    """Normalized Binance USDT deposits, newest first.

    Each item: {amount: float, time_ms: int, confirmed: bool, network: str,
    txid: str}. Raises on transport/API errors (caller decides retry policy).
    """
    ts = int(time.time() * 1000)
    qs = f"coin=USDT&timestamp={ts}"
    sig = binance_signature(api_secret, qs)
    url = f"https://api.binance.com/sapi/v1/capital/deposit/hisrec?{qs}&signature={sig}"
    raw = await http_get(url, headers={"X-MBX-APIKEY": api_key})
    out = []
    for d in raw if isinstance(raw, list) else []:
        try:
            out.append({
                "amount": float(d.get("amount", 0)),
                "time_ms": int(d.get("insertTime", 0)),
                "confirmed": int(d.get("status", 0)) == 1,
                "network": str(d.get("network", "")),
                "txid": str(d.get("txId", "")),
            })
        except (TypeError, ValueError):
            continue
    return out


async def fetch_bybit_deposits(api_key: str, api_secret: str,
                               http_get: HttpGet = _aiohttp_get) -> list[dict]:
    """Normalized Bybit USDT deposits, newest first. Same item shape as
    fetch_binance_deposits. Bybit v5 deposit status 3 == successful."""
    ts = str(int(time.time() * 1000))
    recv = "5000"
    qs = "coin=USDT"
    sign = bybit_signature(api_secret, ts, api_key, recv, qs)
    url = f"https://api.bybit.com/v5/asset/deposit/query-record?{qs}"
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
    }
    raw = await http_get(url, headers=headers)
    rows = (raw or {}).get("result", {}).get("rows", [])
    out = []
    for d in rows if isinstance(rows, list) else []:
        try:
            out.append({
                "amount": float(d.get("amount", 0)),
                "time_ms": int(d.get("createdTime", 0)),
                "confirmed": str(d.get("status", "")) == "3",
                "network": str(d.get("chain", "")),
                "txid": str(d.get("txID", "")),
            })
        except (TypeError, ValueError):
            continue
    return out


# ------------------------------------------------------------------ matching

def match_deposit(deposits: list[dict], expected: float, since_ms: int,
                  tolerance: float, network: str = "") -> Optional[dict]:
    """First deposit that satisfies every rule, or None."""
    for dep in deposits:
        if not dep.get("confirmed"):
            continue
        if dep.get("time_ms", 0) < since_ms:
            continue
        if abs(dep.get("amount", 0.0) - expected) > tolerance:
            continue
        if network and dep.get("network", "").upper() != network.upper():
            continue
        return dep
    return None


# ------------------------------------------------------------ polling driver

def _poll_job_id(deal_id: int) -> str:
    return f"pay_poll:{deal_id}"


def _parse_iso(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, _TS_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


async def start_pay_poll(db: Database, scheduler: Any, bot: Any,
                         vault: Any, cfg: Any, deal_id: int) -> None:
    """(Re)start the interval poll job for a deal awaiting auto payment."""
    stop_pay_poll(scheduler, deal_id)
    scheduler.add_job(
        poll_payment_job,
        "interval",
        seconds=max(15, int(cfg.payment_poll_interval_sec)),
        id=_poll_job_id(deal_id),
        args=[db, bot, scheduler, vault, cfg, deal_id],
        max_instances=1,
        coalesce=True,
    )


def stop_pay_poll(scheduler: Any, deal_id: int) -> None:
    try:
        scheduler.remove_job(_poll_job_id(deal_id))
    except Exception:
        pass


async def _send(bot: Any, tg_id: int, text: str) -> None:
    try:
        await bot.send_message(tg_id, text, parse_mode="HTML")
    except Exception:
        pass


async def poll_payment_job(db: Database, bot: Any, scheduler: Any,
                           vault: Any, cfg: Any, deal_id: int) -> None:
    """APScheduler interval callback: poll exchange deposits for one deal."""
    from bot.core import dealops  # deferred: dealops imports this module

    deal = await Deals(db).get(deal_id)
    if deal is None or deal["status"] != DealStatus.AWAITING_PAYMENT:
        stop_pay_poll(scheduler, deal_id)
        return

    method = deal.get("payment_method") or ""
    if method not in ("binance", "bybit"):
        stop_pay_poll(scheduler, deal_id)
        return

    # Timeout?
    started = _parse_iso(deal.get("pay_poll_started_at"))
    timeout_at = (started + timedelta(minutes=cfg.payment_poll_timeout_min)
                  if started else None)
    if timeout_at is not None and datetime.now(timezone.utc) >= timeout_at:
        stop_pay_poll(scheduler, deal_id)
        await on_pay_poll_timeout(db, bot, deal_id)
        return

    expected = float(deal.get("expected_usdt") or 0)
    if expected <= 0:
        stop_pay_poll(scheduler, deal_id)
        return
    since_ms = int(started.timestamp() * 1000) if started else 0

    try:
        if method == "binance":
            deposits = await fetch_binance_deposits(
                cfg.binance_api_key, cfg.binance_api_secret)
            dep = match_deposit(deposits, expected, since_ms,
                                float(cfg.payment_amount_tolerance),
                                network=cfg.binance_network or "")
        else:
            deposits = await fetch_bybit_deposits(
                cfg.bybit_api_key, cfg.bybit_api_secret)
            dep = match_deposit(deposits, expected, since_ms,
                                float(cfg.payment_amount_tolerance))
    except Exception as exc:
        # Transport/API hiccup — try again next interval; never crash.
        # Log with masked key so no secret ever hits the logs.
        key = cfg.binance_api_key if method == "binance" else cfg.bybit_api_key
        print(f"pay_poll: {method} fetch failed (key={mask_secret(key)}): "
              f"{type(exc).__name__}")
        return

    if dep is None:
        return

    stop_pay_poll(scheduler, deal_id)
    await Deals(db).update(deal_id, auto_verified=1)
    await dealops.auto_confirm_payment(db, bot, scheduler, vault, deal_id,
                                       txid=dep.get("txid", ""))


async def on_pay_poll_timeout(db: Database, bot: Any, deal_id: int) -> None:
    """No deposit detected in time: tell the buyer + admins. The deal stays
    AWAITING_PAYMENT — an admin can still confirm manually."""
    deal = await Deals(db).get(deal_id)
    if deal is None or deal["status"] != DealStatus.AWAITING_PAYMENT:
        return
    await _send(
        bot, deal["buyer_id"],
        _fill_timeout(texts.PAY_NOT_DETECTED_BUYER, deal_id=deal_id),
    )
    await notify_admins(
        bot,
        f"⏳ <b>Auto-verify timed out</b> — Deal #{deal_id} "
        f"({html.escape(deal.get('payment_method') or '?')}): no matching "
        f"deposit detected. You can still confirm manually from "
        f"🛠️ Admin Panel → Payments.",
    )


def _fill_timeout(template: str, **kw: Any) -> str:
    out = template
    for key, value in kw.items():
        out = out.replace("{" + key + "}", str(value))
    return out

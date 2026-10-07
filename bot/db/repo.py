"""Repository layer — the ONLY place with SQL. Handlers never write raw SQL."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from .base import Database


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")


def _row_to_dict(row) -> Optional[dict]:
    return dict(row) if row is not None else None


class Users:
    def __init__(self, db: Database):
        self.db = db

    async def ensure(self, tg_id: int, username: str = "", first_name: str = "") -> dict:
        await self.db.execute(
            "INSERT INTO users (tg_id, username, first_name) VALUES (?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, "
            "first_name=excluded.first_name",
            (tg_id, username or "", first_name or ""),
        )
        return await self.get(tg_id)

    async def get(self, tg_id: int) -> Optional[dict]:
        return _row_to_dict(await self.db.fetchone("SELECT * FROM users WHERE tg_id=?", (tg_id,)))

    async def list_all(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        return [dict(r) for r in rows]

    async def count(self) -> int:
        row = await self.db.fetchone("SELECT COUNT(*) AS c FROM users")
        return row["c"]

    async def set_banned(self, tg_id: int, banned: bool) -> None:
        await self.db.execute("UPDATE users SET is_banned=? WHERE tg_id=?", (1 if banned else 0, tg_id))

    async def warn(self, tg_id: int) -> int:
        await self.db.execute("UPDATE users SET warn_count=warn_count+1 WHERE tg_id=?", (tg_id,))
        row = await self.db.fetchone("SELECT warn_count FROM users WHERE tg_id=?", (tg_id,))
        return row["warn_count"]

    async def bump_deals(self, tg_id: int) -> None:
        await self.db.execute("UPDATE users SET deals_count=deals_count+1 WHERE tg_id=?", (tg_id,))

    async def add_rating(self, tg_id: int, stars: int) -> None:
        await self.db.execute(
            "UPDATE users SET rating_sum=rating_sum+?, rating_count=rating_count+1 WHERE tg_id=?",
            (stars, tg_id),
        )

    @staticmethod
    def rating_text(user: dict) -> str:
        if not user or not user["rating_count"]:
            return "New seller"
        avg = user["rating_sum"] / user["rating_count"]
        return f"{avg:.1f}★ · {user['deals_count']} deals"


class Listings:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, seller_id: int, category: str, subcategory: str) -> int:
        cur = await self.db.execute(
            "INSERT INTO listings (seller_id, category, subcategory) VALUES (?,?,?)",
            (seller_id, category, subcategory),
        )
        return cur.lastrowid

    async def get(self, listing_id: int) -> Optional[dict]:
        return _row_to_dict(await self.db.fetchone("SELECT * FROM listings WHERE id=?", (listing_id,)))

    async def update(self, listing_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        await self.db.execute(f"UPDATE listings SET {cols} WHERE id=?", (*fields.values(), listing_id))

    async def set_status(self, listing_id: int, status: str, reason: str = "") -> None:
        await self.db.execute(
            "UPDATE listings SET status=?, reject_reason=? WHERE id=?", (status, reason, listing_id)
        )

    async def live(self, category: Optional[str] = None,
                   min_cents: Optional[int] = None, max_cents: Optional[int] = None,
                   limit: int = 10, offset: int = 0) -> list[dict]:
        sql = "SELECT * FROM listings WHERE status='LIVE'"
        params: list[Any] = []
        if category:
            sql += " AND category=?"; params.append(category)
        if min_cents is not None:
            sql += " AND price_cents>=?"; params.append(min_cents)
        if max_cents is not None:
            sql += " AND price_cents<=?"; params.append(max_cents)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = await self.db.fetchall(sql, params)
        return [dict(r) for r in rows]

    async def count_live(self, category: Optional[str] = None,
                         min_cents: Optional[int] = None,
                         max_cents: Optional[int] = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM listings WHERE status='LIVE'"
        params: list[Any] = []
        if category:
            sql += " AND category=?"; params.append(category)
        if min_cents is not None:
            sql += " AND price_cents>=?"; params.append(min_cents)
        if max_cents is not None:
            sql += " AND price_cents<=?"; params.append(max_cents)
        row = await self.db.fetchone(sql, params)
        return row["c"]

    async def pending(self) -> list[dict]:
        rows = await self.db.fetchall("SELECT * FROM listings WHERE status='PENDING' ORDER BY id")
        return [dict(r) for r in rows]

    async def by_seller(self, seller_id: int) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM listings WHERE seller_id=? ORDER BY id DESC", (seller_id,))
        return [dict(r) for r in rows]

    async def checklist(self, listing_id: int) -> dict:
        row = await self.db.fetchone("SELECT ver_checklist FROM listings WHERE id=?", (listing_id,))
        try:
            return json.loads(row["ver_checklist"]) if row else {}
        except (TypeError, ValueError):
            return {}

    async def set_checklist(self, listing_id: int, data: dict) -> None:
        await self.db.execute(
            "UPDATE listings SET ver_checklist=? WHERE id=?", (json.dumps(data), listing_id))


class Credentials:
    def __init__(self, db: Database):
        self.db = db

    async def save(self, listing_id: int, vault_blob: str) -> None:
        await self.db.execute(
            "INSERT INTO credentials (listing_id, vault_blob) VALUES (?,?) "
            "ON CONFLICT(listing_id) DO UPDATE SET vault_blob=excluded.vault_blob, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (listing_id, vault_blob),
        )

    async def get_blob(self, listing_id: int) -> Optional[str]:
        row = await self.db.fetchone("SELECT vault_blob FROM credentials WHERE listing_id=?", (listing_id,))
        return row["vault_blob"] if row else None


class Payouts:
    def __init__(self, db: Database):
        self.db = db

    async def save(self, user_id: int, method: str, details: dict) -> None:
        await self.db.execute(
            "INSERT INTO payouts (user_id, method, details) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET method=excluded.method, "
            "details=excluded.details, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (user_id, method, json.dumps(details)),
        )

    async def get(self, user_id: int) -> Optional[dict]:
        row = _row_to_dict(await self.db.fetchone("SELECT * FROM payouts WHERE user_id=?", (user_id,)))
        if row:
            try:
                row["details"] = json.loads(row["details"])
            except (TypeError, ValueError):
                row["details"] = {}
        return row


class Deals:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, listing_id: int, buyer_id: int, seller_id: int,
                     price_cents: int, commission_cents: int) -> int:
        cur = await self.db.execute(
            "INSERT INTO deals (listing_id, buyer_id, seller_id, price_cents, commission_cents)"
            " VALUES (?,?,?,?,?)",
            (listing_id, buyer_id, seller_id, price_cents, commission_cents),
        )
        return cur.lastrowid

    async def get(self, deal_id: int) -> Optional[dict]:
        return _row_to_dict(await self.db.fetchone("SELECT * FROM deals WHERE id=?", (deal_id,)))

    async def update(self, deal_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        await self.db.execute(f"UPDATE deals SET {cols} WHERE id=?", (*fields.values(), deal_id))

    async def active_for_listing(self, listing_id: int) -> Optional[dict]:
        return _row_to_dict(await self.db.fetchone(
            "SELECT * FROM deals WHERE listing_id=? AND status IN "
            "('NEGOTIATING','AWAITING_PAYMENT','PAID_HELD','DISPUTED') ORDER BY id DESC LIMIT 1",
            (listing_id,)))

    async def for_user(self, tg_id: int, limit: int = 10) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT d.*, l.title FROM deals d JOIN listings l ON l.id=d.listing_id "
            "WHERE d.buyer_id=? OR d.seller_id=? ORDER BY d.id DESC LIMIT ?",
            (tg_id, tg_id, limit),
        )
        return [dict(r) for r in rows]

    async def active(self, limit: int = 50) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT d.*, l.title FROM deals d JOIN listings l ON l.id=d.listing_id "
            "WHERE d.status IN ('NEGOTIATING','AWAITING_PAYMENT','PAID_HELD','DISPUTED') "
            "ORDER BY d.id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    async def disputed(self) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT d.*, l.title FROM deals d JOIN listings l ON l.id=d.listing_id "
            "WHERE d.status='DISPUTED' ORDER BY d.id")
        return [dict(r) for r in rows]

    async def awaiting_autopay(self) -> list[dict]:
        """Deals in AWAITING_PAYMENT with an active auto-verify poll.

        Used on boot to re-register payment-poll jobs after a restart."""
        rows = await self.db.fetchall(
            "SELECT * FROM deals WHERE status='AWAITING_PAYMENT' "
            "AND payment_method IN ('binance','bybit') "
            "AND expected_usdt > 0 AND pay_poll_started_at IS NOT NULL")
        return [dict(r) for r in rows]

    async def count_by_status(self) -> dict:
        rows = await self.db.fetchall("SELECT status, COUNT(*) AS c FROM deals GROUP BY status")
        return {r["status"]: r["c"] for r in rows}


class Messages:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, deal_id: int, sender_role: str, text: str, masked_text: str) -> None:
        await self.db.execute(
            "INSERT INTO messages (deal_id, sender_role, text, masked_text) VALUES (?,?,?,?)",
            (deal_id, sender_role, text, masked_text),
        )

    async def transcript(self, deal_id: int, limit: int = 200) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM messages WHERE deal_id=? ORDER BY id LIMIT ?", (deal_id, limit))
        return [dict(r) for r in rows]


class Ledger:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, kind: str, amount_cents: int, actor: str,
                  deal_id: Optional[int] = None, note: str = "") -> None:
        await self.db.execute(
            "INSERT INTO ledger (deal_id, kind, amount_cents, actor, note) VALUES (?,?,?,?,?)",
            (deal_id, kind, amount_cents, actor, note),
        )

    async def all(self, limit: int = 500) -> list[dict]:
        rows = await self.db.fetchall("SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    async def totals(self) -> dict:
        rows = await self.db.fetchall(
            "SELECT kind, SUM(amount_cents) AS s FROM ledger GROUP BY kind")
        return {r["kind"]: (r["s"] or 0) for r in rows}


class Ratings:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, deal_id: int, rater_id: int, ratee_id: int, stars: int, comment: str = "") -> bool:
        try:
            await self.db.execute(
                "INSERT INTO ratings (deal_id, rater_id, ratee_id, stars, comment) VALUES (?,?,?,?,?)",
                (deal_id, rater_id, ratee_id, stars, comment),
            )
            return True
        except Exception:
            return False  # UNIQUE(deal_id, rater_id) — one rating per deal per side

    async def exists(self, deal_id: int, rater_id: int) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM ratings WHERE deal_id=? AND rater_id=?", (deal_id, rater_id))
        return row is not None

    async def low_sellers(self, min_deals: int = 3, max_avg: float = 2.5) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT tg_id, username, rating_sum*1.0/rating_count AS avg, rating_count "
            "FROM users WHERE rating_count>=? AND rating_sum*1.0/rating_count<=?",
            (min_deals, max_avg))
        return [dict(r) for r in rows]


class Audit:
    def __init__(self, db: Database):
        self.db = db

    async def log(self, actor: str, action: str, entity: str,
                  entity_id: Optional[int] = None, detail: str = "") -> None:
        await self.db.execute(
            "INSERT INTO audit (actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?)",
            (actor, action, entity, entity_id, detail),
        )


class Jobs:
    def __init__(self, db: Database):
        self.db = db

    async def upsert(self, deal_id: int, kind: str, run_at: str, aps_job_id: str = "") -> None:
        await self.db.execute(
            "INSERT INTO jobs (deal_id, kind, run_at, aps_job_id) VALUES (?,?,?,?) "
            "ON CONFLICT(deal_id, kind) DO UPDATE SET run_at=excluded.run_at, "
            "aps_job_id=excluded.aps_job_id",
            (deal_id, kind, run_at, aps_job_id),
        )

    async def delete(self, deal_id: int, kind: str) -> None:
        await self.db.execute("DELETE FROM jobs WHERE deal_id=? AND kind=?", (deal_id, kind))

    async def due(self, now: str) -> list[dict]:
        rows = await self.db.fetchall("SELECT * FROM jobs WHERE run_at<=?", (now,))
        return [dict(r) for r in rows]

"""APScheduler wiring. Business logic lives in bot/core/dealops.py —
this module only schedules and re-schedules timer jobs.

Job kinds: 'delivery_deadline' (12h), 'handover_lock' (8h), 'hold_release' (24h).
Each scheduled job is mirrored in the jobs DB table so a restart can
re-register everything via reschedule_all().
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import settings
from bot.db.base import Database
from bot.db.repo import Jobs

KIND_DELAY = {
    "delivery_deadline": lambda: settings.delivery_deadline_hours,
    "handover_lock": lambda: settings.handover_hours,
    "hold_release": lambda: settings.hold_hours,
}


def _run_at(kind: str) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=KIND_DELAY[kind]())


async def schedule_job(db: Database, scheduler: AsyncIOScheduler, bot: Any,
                       deal_id: int, kind: str) -> None:
    """Schedule (or re-schedule) a timer job for a deal."""
    from bot.core import dealops  # deferred import: dealops imports timers helpers

    func = {
        "delivery_deadline": dealops.on_delivery_deadline,
        "handover_lock": dealops.on_handover_lock,
        "hold_release": dealops.on_hold_release,
    }[kind]
    run_at = _run_at(kind)
    job_id = f"{kind}:{deal_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    scheduler.add_job(func, "date", run_date=run_at, id=job_id,
                      args=[db, bot, deal_id])
    await Jobs(db).upsert(deal_id, kind, run_at.strftime("%Y-%m-%dT%H:%M:%fZ"), job_id)


async def cancel_job(db: Database, scheduler: AsyncIOScheduler,
                     deal_id: int, kind: str) -> None:
    try:
        scheduler.remove_job(f"{kind}:{deal_id}")
    except Exception:
        pass
    await Jobs(db).delete(deal_id, kind)


async def cancel_all_jobs(db: Database, scheduler: AsyncIOScheduler, deal_id: int) -> None:
    for kind in KIND_DELAY:
        await cancel_job(db, scheduler, deal_id, kind)


def make_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone="UTC")

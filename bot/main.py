"""Bot entrypoint: validate config → init DB/vault/scheduler →
register everything → run webhook or long-polling.

Run with:  python -m bot.main   (from ~/workspace/tg-escrow-bot/)
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from bot.config import settings
from bot.core.timers import make_scheduler, schedule_job
from bot.db.base import Database
from bot.db.repo import Jobs
from bot.middlewares.auth import UserMiddleware
from bot.utils.vault import Vault, VaultError

ROOT = Path(__file__).resolve().parent.parent  # project root (has bot/db/schema.sql)


async def _reschedule_persisted_jobs(db: Database, scheduler, bot: Bot,
                                     vault=None) -> None:
    """Re-register APScheduler jobs that were persisted in the jobs table.

    ``timers.schedule_job`` recomputes the fire time from the configured
    delays, which is always >= now + 60s for the 12h/8h/24h timers.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")
    due = await Jobs(db).due(now)
    for job in due:
        try:
            await schedule_job(db, scheduler, bot, job["deal_id"], job["kind"])
            print(f"  ↻ rescheduled {job['kind']} for deal {job['deal_id']}")
        except Exception as exc:
            # e.g. dealops module not delivered yet — logged, not fatal.
            print(f"  ⚠️ could not reschedule {job['kind']} for deal {job['deal_id']}: {exc}")
    # Re-register auto-payment polls that were alive at shutdown.
    if vault is not None:
        from bot.db.repo import Deals
        from bot.payments import autoverify

        for deal in await Deals(db).awaiting_autopay():
            try:
                await autoverify.start_pay_poll(db, scheduler, bot, vault,
                                                settings, deal["id"])
                print(f"  ↻ rescheduled pay_poll for deal {deal['id']}")
            except Exception as exc:
                print(f"  ⚠️ could not reschedule pay_poll for deal "
                      f"{deal['id']}: {exc}")


async def _run_webhook(dp: Dispatcher, bot: Bot) -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    webhook_path = "/webhook"
    await bot.set_webhook(f"{settings.webhook_url}{webhook_path}")
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.port)
    await site.start()
    print(f"▶️ webhook mode: listening on 0.0.0.0:{settings.port}{webhook_path}")
    await asyncio.Event().wait()  # run forever


async def main() -> None:
    problems = settings.validate()
    if problems:
        print("❌ Configuration problems — fix .env and restart:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    db = Database(settings.database_path)
    await db.init(str(ROOT / "bot" / "db" / "schema.sql"))

    try:
        vault = Vault(settings.fernet_key)
    except VaultError as exc:
        print(f"❌ {exc}")
        sys.exit(1)

    scheduler = make_scheduler()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.update.middleware(UserMiddleware(db))
    # Shared objects travel ONLY via the workflow_data dict (never globals).
    dp.workflow_data.update(
        {"db": db, "scheduler": scheduler, "vault": vault, "settings": settings}
    )

    # Routers — every handler module exposes exactly `router`.
    from bot.handlers import admin, buy, deal, rating, sell, start, wallet

    for module in (start, sell, buy, wallet, deal, rating, admin):
        dp.include_router(module.router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Start / main menu"),
            BotCommand(command="help", description="Help & rules"),
            BotCommand(command="cancel", description="Cancel current action"),
        ]
    )

    scheduler.start()
    print("⏱ scheduler started — rescheduling persisted jobs…")
    await _reschedule_persisted_jobs(db, scheduler, bot, vault)

    if settings.webhook_url:
        await _run_webhook(dp, bot)
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        print("▶️ polling mode (no WEBHOOK_URL set)")
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

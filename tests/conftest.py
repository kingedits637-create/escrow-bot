"""Shared fixtures: throwaway SQLite DB per test session."""
import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.db.base import Database  # noqa: E402


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.init(os.path.join(os.path.dirname(__file__), "..", "bot", "db", "schema.sql"))
    yield database
    await database.close()

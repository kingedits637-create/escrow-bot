"""Editable category catalog (categories.yaml)."""
from __future__ import annotations

import pathlib

import yaml

_CACHED: list[dict] | None = None


def load_categories(path: str = "categories.yaml") -> list[dict]:
    """Return [{'name': ..., 'sub': [...]}, ...]. Cached after first load."""
    global _CACHED
    if _CACHED is None:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _CACHED = data.get("categories", [])
    return _CACHED


def category_names(path: str = "categories.yaml") -> list[str]:
    return [c["name"] for c in load_categories(path)]


def sub_names(category: str, path: str = "categories.yaml") -> list[str]:
    for c in load_categories(path):
        if c["name"] == category:
            return list(c.get("sub", []))
    return []

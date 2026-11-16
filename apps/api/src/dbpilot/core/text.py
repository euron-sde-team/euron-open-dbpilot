"""Small text helpers shared by domains."""

from __future__ import annotations

import re


def slugify(text: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    return slug or fallback


def first_line(text: str, limit: int = 500) -> str:
    return (text.strip().splitlines() or [""])[0][:limit]

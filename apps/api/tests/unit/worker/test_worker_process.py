"""The worker process itself (``dbpilot worker``): it exits with the error that ended its
loop instead of idling with its health check still answering, and no log call anywhere can
crash on a reserved LogRecord key (the bug that once stopped the loop on its first line)."""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from dbpilot import worker as worker_module
from dbpilot.core.config import Settings

SRC = Path(__file__).resolve().parents[3] / "src" / "dbpilot"
RESERVED = set(logging.LogRecord("x", 0, "x", 0, "x", None, None).__dict__) | {"message", "asctime"}


def test_no_log_call_passes_a_reserved_record_key() -> None:
    hits = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
                    hits += [
                        f"{path.name}:{node.lineno} {k.value}"
                        for k in kw.value.keys
                        if isinstance(k, ast.Constant) and k.value in RESERVED
                    ]
    assert hits == []


async def test_the_process_exits_with_the_error_that_ended_its_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        environment="test",
        worker_metrics_port=0,
        redis_url="",
        local_storage_dir=str(tmp_path / "objects"),
        work_dir=str(tmp_path / "work"),
    )
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_module, "configure_logging", lambda _level: None)

    async def broken(self: Any, stop: asyncio.Event) -> None:
        raise KeyError("Attempt to overwrite 'name' in LogRecord")

    monkeypatch.setattr(worker_module.Worker, "serve", broken)
    with pytest.raises(KeyError, match="overwrite"):
        await asyncio.wait_for(worker_module.main(), timeout=20)

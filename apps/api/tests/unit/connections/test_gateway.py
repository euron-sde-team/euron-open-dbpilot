"""The gateway writes the audit entry in every outcome, and its write door does not open
for a confirmation that is not committed as executing."""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

import pytest

from dbpilot.audit.models import AuditLog
from dbpilot.audit.service import Actor
from dbpilot.connections import gateway
from dbpilot.connections.connectors.sql import confirm_read_only
from dbpilot.connections.models import Connection
from dbpilot.core.config import Settings
from dbpilot.core.container import Container
from dbpilot.demo.sql_sources import build_subscriptions


class FakeSession:
    """Just enough of an AsyncSession: collects added rows and commits. ``moved`` is how
    many rows the SQLite file switch updates (0: someone else changed the file)."""

    def __init__(self, status: str | None = None, moved: int = 1) -> None:
        self.added: list[Any] = []
        self.commits = 0
        self.status = status
        self.moved = moved

    async def execute(self, _stmt: Any) -> Any:
        class Result:
            rowcount = self.moved

        return Result()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    async def scalar(self, _stmt: Any) -> Any:
        return self.status

    @property
    def audit(self) -> list[AuditLog]:
        return [a for a in self.added if isinstance(a, AuditLog)]


ACTOR = Actor(uuid.uuid4(), "maya@example.test", "user")


@pytest.fixture
def container(tmp_path: Path) -> Container:
    settings = Settings(
        environment="test",
        local_storage_dir=str(tmp_path / "objects"),
        work_dir=str(tmp_path / "work"),
        session_secret="test-only-session-secret-with-32-plus-bytes",
        local_master_key="test-only-master-key-with-32-plus-bytes-ok",
    )
    return Container.build(settings)


async def _sqlite_connection(container: Container, tmp_path: Path) -> Connection:
    path = build_subscriptions(tmp_path / "s.sqlite", dt.date(2026, 6, 15))
    key = "projects/p/connections/c/file.sqlite"
    await container.storage.put_file(key, path, "application/vnd.sqlite3")
    conn = Connection(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="Subs",
        kind="sqlite",
        config={},
        file_key=key,
        timeout_s=10,
        max_rows=10,
        max_scan_rows=1000,
    )
    return conn


async def test_a_successful_read_is_audited_with_its_summary(
    container: Container, tmp_path: Path
) -> None:
    conn = await _sqlite_connection(container, tmp_path)
    session = FakeSession()
    result = await gateway.read(
        container,
        session,
        conn,
        ACTOR,  # type: ignore[arg-type]
        action="probe.read",
        work=lambda h: confirm_read_only(h),
        org_id=None,  # type: ignore[arg-type]
        summarize=lambda r: {"read_only": r},
        details={"why": "test"},
    )
    assert result is True
    entry = session.audit[0]
    assert entry.action == "probe.read" and entry.details["outcome"] == "ok"
    assert entry.details["read_only"] is True and entry.details["why"] == "test"
    assert entry.details["mode"] == "read" and entry.details["unsaved"] is True
    assert entry.actor_email == "maya@example.test" and session.commits == 1


async def test_a_failing_read_is_audited_and_the_error_propagates(
    container: Container, tmp_path: Path
) -> None:
    conn = await _sqlite_connection(container, tmp_path)
    session = FakeSession()

    def boom(_h: Any) -> None:
        raise RuntimeError("the database said no")

    with pytest.raises(RuntimeError):
        await gateway.read(
            container, session, conn, ACTOR, action="probe.fail", work=boom, org_id=None
        )  # type: ignore[arg-type]
    entry = session.audit[0]
    assert entry.details["outcome"] == "error" and entry.details["error"] == "the database said no"


async def test_a_missing_sqlite_file_is_refused_and_audited(container: Container) -> None:
    conn = Connection(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="Gone",
        kind="sqlite",
        config={},
        timeout_s=10,
    )
    session = FakeSession()
    with pytest.raises(Exception, match="no database file"):
        await gateway.read(
            container, session, conn, ACTOR, action="probe.gone", work=lambda h: None, org_id=None
        )  # type: ignore[arg-type]
    assert session.audit[0].details["outcome"] == "error"


async def test_metadata_hosts_are_refused_at_connect_time(container: Container) -> None:
    conn = Connection(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="Meta",
        kind="postgresql",
        config={"host": "169.254.169.254", "port": 5432},
        timeout_s=5,
    )
    session = FakeSession()
    with pytest.raises(Exception, match="refuses"):
        await gateway.read(
            container, session, conn, ACTOR, action="probe.meta", work=lambda h: None, org_id=None
        )  # type: ignore[arg-type]


@pytest.mark.parametrize("status", [None, "pending", "executed", "failed", "cancelled"])
async def test_the_write_door_refuses_without_an_executing_confirmation(
    container: Container, tmp_path: Path, status: str | None
) -> None:
    conn = await _sqlite_connection(container, tmp_path)
    session = FakeSession(status=status)
    called = []
    with pytest.raises(RuntimeError, match="executing confirmation"):
        await gateway.write(
            container,
            session,
            conn,
            ACTOR,  # type: ignore[arg-type]
            confirmation_id=uuid.uuid4(),
            action="write.executed",
            work=lambda h: called.append(1),
            org_id=None,
        )
    assert called == [] and session.audit == []


async def test_error_text_uses_the_database_message() -> None:
    import sqlalchemy as sa

    exc = sa.exc.DBAPIError("SELECT 1", {}, Exception("first line\nsecond line"))
    assert gateway.error_text(exc) == "first line"
    assert gateway.error_text(ValueError("")) == "ValueError"


def test_the_write_control_lets_exactly_one_side_decide() -> None:
    import time

    late = gateway.WriteControl(deadline=time.monotonic() + 60)
    assert late.begin_commit() is True  # the thread got there first
    assert late.abandon() is False  # so the caller waits for the commit's outcome
    early = gateway.WriteControl(deadline=time.monotonic() + 60)
    assert early.abandon() is True and early.stopped() is True
    assert early.begin_commit() is False  # nothing commits after the caller gave up
    expired = gateway.WriteControl(deadline=time.monotonic() - 1)
    assert expired.begin_commit() is False


def test_an_abandoned_write_is_rolled_back_not_committed(tmp_path: Path) -> None:
    import sqlite3
    import time

    from dbpilot.connections.connectors.sql import WriteStoppedError, run_statements
    from dbpilot.connections.connectors.targets import SqlTarget

    path = build_subscriptions(tmp_path / "w.sqlite", dt.date(2026, 6, 15))
    control = gateway.WriteControl(deadline=time.monotonic() + 60)

    def work(handle: Any, stopped: Any) -> Any:
        out = run_statements(handle, ["DELETE FROM plans"], stopped)
        control.abandon()  # the caller stops waiting while the statements finish
        return out

    with pytest.raises(WriteStoppedError):
        gateway._run_write(SqlTarget(kind="sqlite", sqlite_path=str(path)), 10, work, control)
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT count(*) FROM plans").fetchone()[0] == 4


async def test_a_write_past_its_deadline_is_stopped_and_reported(
    container: Container, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    from dbpilot.connections.connectors.sql import WriteStoppedError, run_statements

    monkeypatch.setattr(gateway, "WRITE_GRACE_S", 0.1)
    conn = await _sqlite_connection(container, tmp_path)
    original = conn.file_key
    session = FakeSession(status="executing")
    finished = threading.Event()
    seen: list[Any] = []

    def slow(handle: Any, stopped: Any) -> Any:
        try:
            time.sleep(1.5)  # still running when the caller gives up at 1.1 s
            return run_statements(handle, ["DELETE FROM plans"], stopped)
        finally:
            finished.set()

    with pytest.raises(WriteStoppedError, match="stopped before committing"):
        await gateway.write(
            container,
            session,
            conn,
            ACTOR,  # type: ignore[arg-type]
            confirmation_id=uuid.uuid4(),
            action="write.executed",
            work=slow,
            org_id=None,
            deadline_s=1,
            on_done=lambda r, e: seen.append((r, e)) or {},
        )
    assert seen and seen[0][0] is None and isinstance(seen[0][1], WriteStoppedError)
    assert session.audit[0].details["outcome"] == "error"
    assert finished.wait(5)
    assert conn.file_key == original  # the stored file was never replaced


async def test_a_sqlite_write_that_is_not_stored_is_not_reported_as_done(
    container: Container, tmp_path: Path
) -> None:
    from dbpilot.connections.connectors.sql import run_statements
    from dbpilot.core.errors import AppError

    conn = await _sqlite_connection(container, tmp_path)
    session = FakeSession(status="executing", moved=0)  # another write switched the file
    seen: list[Any] = []
    with pytest.raises(AppError) as err:
        await gateway.write(
            container,
            session,
            conn,
            ACTOR,  # type: ignore[arg-type]
            confirmation_id=uuid.uuid4(),
            action="write.executed",
            org_id=None,
            work=lambda h, stopped: run_statements(h, ["DELETE FROM plans"], stopped),
            on_done=lambda r, e: seen.append((r, e)) or {},
        )
    assert err.value.code == "sqlite_conflict"
    # The statements ran on the private copy, but it was never stored: no success.
    assert seen == [(None, err.value)]
    assert session.audit[0].details["outcome"] == "error"

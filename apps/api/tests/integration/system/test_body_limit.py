"""Request bodies over MAX_BODY_BYTES are refused with 413 before any route reads them."""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.conftest import Stack

pytestmark = pytest.mark.integration


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"max_body_bytes": 10_000}


async def test_an_oversized_body_is_refused_with_413(ready: Stack) -> None:
    big = await ready.client.post(
        ready.p("/queries/sql"), content=b"x" * 20_000, headers={"content-type": "application/json"}
    )
    assert big.status_code == 413
    assert big.json()["error"] == {
        "code": "payload_too_large",
        "message": "The request body is larger than 10,000 bytes",
        "details": {"limit_bytes": 10000},
    }
    small = await ready.client.post(
        ready.p("/queries/validate"), json={"dialect": "postgresql", "sql": "SELECT 1"}
    )
    assert small.status_code == 200

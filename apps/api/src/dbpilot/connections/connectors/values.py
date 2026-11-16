"""Values read from a connected database, made JSON-safe for results, and column kinds."""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import ipaddress
import math
import uuid
from typing import Any


def to_json_value(v: Any) -> Any:
    """A JSON-safe rendering of a driver value for display (results, previews)."""
    if v is None or isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, decimal.Decimal):
        f = float(v)
        return f if math.isfinite(f) and abs(f) < 1e15 else str(v)
    if isinstance(v, dt.datetime | dt.date | dt.time):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, bytes | bytearray | memoryview):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, list | tuple):
        return [to_json_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): to_json_value(x) for k, x in v.items()}
    return str(v)


def kind_of(values: list[Any]) -> str:
    """number, text, date, datetime, boolean or other, from the values themselves."""
    seen = [v for v in values if v is not None]
    if not seen:
        return "other"
    if all(isinstance(v, bool) for v in seen):
        return "boolean"
    if all(isinstance(v, int | float | decimal.Decimal) and not isinstance(v, bool) for v in seen):
        return "number"
    if all(isinstance(v, dt.datetime) for v in seen):
        return "datetime"
    if all(isinstance(v, dt.date) for v in seen):
        return "date"
    if all(isinstance(v, str) for v in seen):
        return "text"
    return "other"


# ─── Lossless encoding for backups ───────────────────────────────────────────
# A backup must restore exactly what was read, so values that JSON cannot carry keep a
# one-key tag: {"$d": "12.50"} is a Decimal, {"$b": "<base64>"} bytes, and so on.


def encode_value(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else {"$f": str(v)}
    if isinstance(v, decimal.Decimal):
        return {"$d": str(v)}
    if isinstance(v, dt.datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, dt.date):
        return {"$date": v.isoformat()}
    if isinstance(v, dt.time):
        return {"$t": v.isoformat()}
    if isinstance(v, dt.timedelta):
        return {"$td": v.total_seconds()}
    if isinstance(v, uuid.UUID):
        return {"$u": str(v)}
    if isinstance(v, bytes | bytearray | memoryview):
        return {"$b": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, list | tuple):
        return [encode_value(x) for x in v]
    if isinstance(v, dict):
        return {"$j": {str(k): encode_value(x) for k, x in v.items()}}
    if isinstance(v, ipaddress.IPv4Address | ipaddress.IPv6Address):
        return str(v)
    # Types without a portable representation are restored from their text form.
    return str(v)


_DECODERS: dict[str, Any] = {
    "$f": float,
    "$d": decimal.Decimal,
    "$dt": dt.datetime.fromisoformat,
    "$date": dt.date.fromisoformat,
    "$t": dt.time.fromisoformat,
    "$td": lambda s: dt.timedelta(seconds=float(s)),
    "$u": uuid.UUID,
    "$b": base64.b64decode,
}


def decode_value(v: Any) -> Any:
    if isinstance(v, list):
        return [decode_value(x) for x in v]
    if isinstance(v, dict) and len(v) == 1:
        tag, raw = next(iter(v.items()))
        if tag == "$j" and isinstance(raw, dict):
            return {k: decode_value(x) for k, x in raw.items()}
        decoder = _DECODERS.get(tag)
        if decoder is not None:
            return decoder(raw)
    return v

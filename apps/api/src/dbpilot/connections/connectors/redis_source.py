"""Redis as a connected database: a read-only surface by construction.

Redis has no read-only session flag. ``RedisReader`` keeps the driver's client private and
runs a command only when ``check_command`` finds it in the read-only allowlist below
(``GET``, ``MGET``, ``HGETALL``, ``SMEMBERS``, ``SCAN``, ``TYPE``, ``TTL`` and the like).
Everything else is refused before it is sent: ``SET``, ``DEL``, ``FLUSHALL``, ``EVAL``,
``CONFIG``, ``SELECT``, ``MULTI``, ``SORT`` (it can ``STORE``), ``GEORADIUS`` (same), ...
Writes to Redis are not offered in 0.1.0 at all.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import redis

MAX_ARGS = 64
MAX_ARG_CHARS = 4_096

READ_COMMANDS = frozenset(
    {
        # strings
        "GET",
        "MGET",
        "STRLEN",
        "GETRANGE",
        "EXISTS",
        "TYPE",
        "TTL",
        "PTTL",
        "EXPIRETIME",
        "PEXPIRETIME",
        "BITCOUNT",
        "BITPOS",
        "GETBIT",
        # hashes
        "HGET",
        "HMGET",
        "HGETALL",
        "HKEYS",
        "HVALS",
        "HLEN",
        "HEXISTS",
        "HSTRLEN",
        "HSCAN",
        "HRANDFIELD",
        # lists
        "LRANGE",
        "LLEN",
        "LINDEX",
        "LPOS",
        # sets
        "SMEMBERS",
        "SCARD",
        "SISMEMBER",
        "SMISMEMBER",
        "SSCAN",
        "SRANDMEMBER",
        "SINTER",
        "SUNION",
        "SDIFF",
        "SINTERCARD",
        # sorted sets
        "ZRANGE",
        "ZRANGEBYSCORE",
        "ZRANGEBYLEX",
        "ZREVRANGE",
        "ZREVRANGEBYSCORE",
        "ZREVRANGEBYLEX",
        "ZCARD",
        "ZCOUNT",
        "ZLEXCOUNT",
        "ZSCORE",
        "ZMSCORE",
        "ZRANK",
        "ZREVRANK",
        "ZSCAN",
        "ZRANDMEMBER",
        # streams, hyperloglog, geo (the *_RO forms only)
        "XRANGE",
        "XREVRANGE",
        "XLEN",
        "XINFO",
        "PFCOUNT",
        "GEOPOS",
        "GEODIST",
        "GEOHASH",
        "GEOSEARCH",
        "GEORADIUS_RO",
        "GEORADIUSBYMEMBER_RO",
        "SORT_RO",
        # keyspace
        "SCAN",
        "KEYS",
        "DBSIZE",
        "RANDOMKEY",
        "OBJECT",
        "MEMORY",
    }
)
# Commands with subcommands: only these subcommands read.
READ_SUBCOMMANDS = {
    "OBJECT": frozenset({"ENCODING", "FREQ", "IDLETIME", "REFCOUNT"}),
    "MEMORY": frozenset({"USAGE"}),
    "XINFO": frozenset({"STREAM", "GROUPS", "CONSUMERS"}),
}
# ZRANGE with STORE does not exist (that is ZRANGESTORE), but these options of allowed
# commands would still write; they are refused explicitly.
_WRITING_OPTIONS = frozenset({"STORE", "STOREDIST"})

# How many elements a command reads, for the scan limit: commands that read a whole
# collection are measured with its O(1) length command first.
_WHOLE_COLLECTION = {
    "HGETALL": "HLEN",
    "HKEYS": "HLEN",
    "HVALS": "HLEN",
    "SMEMBERS": "SCARD",
    "LPOS": "LLEN",
    "GEOSEARCH": "ZCARD",
    "GEORADIUS_RO": "ZCARD",
    "GEORADIUSBYMEMBER_RO": "ZCARD",
}
_BY_INDEX = {"LRANGE": "LLEN", "ZRANGE": "ZCARD", "ZREVRANGE": "ZCARD"}
_BY_SCORE_OR_LEX = frozenset({"ZRANGEBYSCORE", "ZRANGEBYLEX", "ZREVRANGEBYSCORE", "ZREVRANGEBYLEX"})
_SCANS = frozenset({"SCAN", "HSCAN", "SSCAN", "ZSCAN"})
_SAMPLES = frozenset({"HRANDFIELD", "SRANDMEMBER", "ZRANDMEMBER"})
_SET_ALGEBRA = frozenset({"SINTER", "SUNION", "SDIFF"})
_LENGTH_BY_TYPE = {"list": "LLEN", "set": "SCARD", "zset": "ZCARD"}


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _option(args: list[str], name: str, width: int = 1) -> list[int] | None:
    """The integer values after option ``name`` (``COUNT 10``, ``LIMIT 0 50``), if given."""
    for i, a in enumerate(args):
        if a.upper() == name and i + width < len(args):
            values = [_int(v) for v in args[i + 1 : i + 1 + width]]
            if all(v is not None for v in values):
                return [v for v in values if v is not None]
    return None


def _index_span(length: int, start: int, stop: int, *, walk: bool) -> int:
    """Elements an index range reads; with ``walk`` (a list) also the walk to its first
    element, which Redis makes from the nearer end."""
    first = start if start >= 0 else max(length + start, 0)
    last = min(stop if stop >= 0 else length + stop, length - 1)
    if last < first:
        return 0
    return (last - first + 1) + (min(first, length - 1 - last) if walk else 0)


class RedisCommandError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def check_command(command: str, args: list[str]) -> tuple[str, list[str]]:
    """Accept a read-only command or raise RedisCommandError (nothing reaches Redis)."""
    name = command.strip().upper()
    if name not in READ_COMMANDS:
        raise RedisCommandError(
            f"{name or 'that command'} is not allowed: DBPilot only runs read-only Redis "
            "commands on a connected database",
            "not_a_read",
        )
    if len(args) > MAX_ARGS or any(len(a) > MAX_ARG_CHARS for a in args):
        raise RedisCommandError("too many or too long arguments", "too_long")
    allowed_subs = READ_SUBCOMMANDS.get(name)
    if allowed_subs is not None and (not args or args[0].upper() not in allowed_subs):
        raise RedisCommandError(
            f"{name} {args[0] if args else ''} is not a read-only subcommand", "not_a_read"
        )
    if any(a.upper() in _WRITING_OPTIONS for a in args):
        raise RedisCommandError("options that store results are not allowed", "not_a_read")
    return name, list(args)


def _text(value: Any) -> Any:
    """Redis replies as JSON-safe values: UTF-8 text when it decodes, else base64."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"$base64": base64.b64encode(value).decode()}
    if isinstance(value, list | tuple):
        return [_text(v) for v in value]
    if isinstance(value, dict):
        return {str(_text(k)): _text(v) for k, v in value.items()}
    if isinstance(value, float | int | bool) or value is None:
        return value
    return str(value)


@dataclass
class RedisResult:
    value: Any
    duration_ms: float


class RedisReader:
    """Read access to one Redis database. The client is private: no write path exists."""

    def __init__(self, client: redis.Redis) -> None:
        self.__client = client

    def ping(self) -> None:
        self.__client.ping()

    def info(self, section: str | None = None) -> dict[str, Any]:
        raw = self.__client.info(section) if section else self.__client.info()
        return {str(k): _text(v) for k, v in dict(raw).items()}

    def server_version(self) -> str:
        return "Redis " + str(self.info("server").get("redis_version", ""))

    def dbsize(self) -> int:
        return int(self.__client.dbsize())

    def scan_keys(self, match: str | None, count: int, limit: int) -> list[str]:
        out: list[str] = []
        for key in self.__client.scan_iter(match=match, count=count):
            out.append(str(_text(key)))
            if len(out) >= limit:
                break
        return out

    def key_info(self, key: str) -> dict[str, Any]:
        kind = _text(self.__client.type(key))
        ttl = int(self.__client.ttl(key))
        return {"key": key, "type": kind, "ttl_s": ttl}

    def config_value(self, parameter: str) -> str | None:
        """A server setting for the health card's security checks (None when CONFIG is
        disabled or not permitted for this login)."""
        try:
            values = self.__client.config_get(parameter)
        except redis.ResponseError:
            return None
        decoded = {str(_text(k)): v for k, v in dict(values).items()}
        value = decoded.get(parameter)
        return None if value is None else str(_text(value))

    def acl_whoami(self) -> str | None:
        try:
            return str(_text(self.__client.acl_whoami()))
        except redis.ResponseError:
            return None

    def slowlog(self, n: int = 5) -> list[dict[str, Any]]:
        try:
            entries = self.__client.slowlog_get(n)
        except redis.ResponseError:
            return []
        out = []
        for e in entries:
            command = str(_text(e.get("command") or ""))
            # Only the command and its first arguments: values can be sensitive.
            out.append(
                {
                    "command": " ".join(command.split()[:3])[:200],
                    "duration_us": int(e.get("duration") or 0),
                }
            )
        return out

    def _length(self, command: str, key: str) -> int:
        return int(self.__client.execute_command(command, key) or 0)  # type: ignore[no-untyped-call]

    def elements_read(self, name: str, args: list[str]) -> int:
        """How many keys or elements ``name`` would read (0 for a bounded handful)."""
        if name == "KEYS":
            return self.dbsize()
        if name in _SCANS:
            count = _option(args, "COUNT")
            return count[0] if count else 10
        if name in _SAMPLES:
            n = _int(args[1]) if len(args) > 1 else 1
            return abs(n) if n is not None else 1
        if name in ("XRANGE", "XREVRANGE") and args:
            count = _option(args, "COUNT")
            return count[0] if count else self._length("XLEN", args[0])
        if name in _BY_INDEX and len(args) >= 3:
            reads_by = any(a.upper() in ("BYSCORE", "BYLEX") for a in args[3:])
            limit = _option(args, "LIMIT", 2)
            if reads_by:
                return sum(limit) if limit else self._length(_BY_INDEX[name], args[0])
            start, stop = _int(args[1]), _int(args[2])
            if start is None or stop is None:
                return 0  # Redis refuses the command itself
            length = self._length(_BY_INDEX[name], args[0])
            return _index_span(length, start, stop, walk=name == "LRANGE")
        if name in _BY_SCORE_OR_LEX and args:
            limit = _option(args, "LIMIT", 2)
            return sum(limit) if limit else self._length("ZCARD", args[0])
        if name in _WHOLE_COLLECTION and args:
            return self._length(_WHOLE_COLLECTION[name], args[0])
        if name in _SET_ALGEBRA:
            return sum(self._length("SCARD", k) for k in args)
        if name == "SINTERCARD" and args:
            n = _int(args[0]) or 0
            return sum(self._length("SCARD", k) for k in args[1 : 1 + n])
        if name == "SORT_RO" and args:
            kind = str(_text(self.__client.type(args[0])))
            command = _LENGTH_BY_TYPE.get(kind)
            return self._length(command, args[0]) if command else 0
        return 0

    def run(self, command: str, args: list[str], *, max_scan_keys: int) -> RedisResult:
        """Run a command that passed ``check_command``, unless it would read more keys or
        elements than the scan limit: ``KEYS`` walks the keyspace, ``HGETALL`` a whole hash,
        ``SCAN ... COUNT n`` n keys; each is measured first (O(1)) and refused past it."""
        name, args = check_command(command, args)
        reads = self.elements_read(name, args)
        if reads > max_scan_keys:
            hint = (
                "use SCAN with MATCH and a COUNT within the limit"
                if name in ("KEYS", *_SCANS)
                else "read a range of it (LRANGE, ZRANGE with LIMIT, HSCAN) instead"
            )
            raise RedisCommandError(
                f"{name} would read {reads:,} keys or elements, more than this connection's "
                f"scan limit ({max_scan_keys:,}); {hint}",
                "scan_limit",
            )
        started = time.perf_counter()
        value = self.__client.execute_command(name, *args)  # type: ignore[no-untyped-call]
        return RedisResult(
            value=_text(value), duration_ms=round((time.perf_counter() - started) * 1000, 1)
        )

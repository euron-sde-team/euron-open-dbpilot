"""The SQL guard, two paths.

``check`` is the read-only gate (adapted from Euron Open SQLAgent's proven guard): a
statement runs on the read path only if it is exactly one read-only query (``SELECT``,
``UNION``, ``INTERSECT``, ``EXCEPT``, with or without CTEs) and nothing anywhere in its tree
writes, locks rows, changes session state, reads files or calls a function with side
effects. It applies to every query a person types without marking it as a write, and
unconditionally to every AI-generated statement (AC4): there is no flag that skips it.
It is the first of two locks; the second is the connection, opened read-only by the
database itself (``connections/connectors/openers.py``, AC3).

``classify`` serves the separate human write path (AC5). It splits the text at the
tokenizer's own statement separators, parses each statement, and says what it does (kind,
tables, whether a DELETE or UPDATE has no WHERE clause) so the confirmation step can show
exactly what will run. It refuses what it cannot describe (text the parser falls back to
treating as a raw command), transaction and session control (DBPilot runs the statements in
its own transaction), privilege changes and file access.

Both paths refuse code the parser cannot see. MySQL runs the body of a ``/*! ... */``
comment and obeys ``/*+ ... */`` optimizer hints (which can lift DBPilot's time and row
limits), so a comment like that is refused outright. And on MySQL and SQL Server the
statement that reaches the database is the guard's own re-serialization of its parse tree
(``Verdict.executable``, ``StatementInfo.sql``), with every comment dropped: whatever the
server's lexer might read differently from the guard's (T-SQL needs no ``;`` between
statements, so ``DELETE FROM t RECONFIGURE`` is two statements there) never reaches it.
PostgreSQL and SQLite run the text as typed: neither executes comments, and both refuse a
second statement on the path DBPilot uses (the extended query protocol, ``sqlite3``).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokenizer_core import Token, TokenType

# sqlglot warns for every statement it falls back to parsing as a raw command; the guard
# refuses those anyway, so the warning is noise.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

SQLGLOT_DIALECT = {"postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite", "mssql": "tsql"}
DIALECTS = tuple(SQLGLOT_DIALECT)
# Engines that run the guard's re-serialized statement instead of the text as typed.
CANONICAL_DIALECTS = frozenset({"mysql", "mssql"})
MAX_SQL_CHARS = 100_000
MAX_WRITE_STATEMENTS = 200
# Parenthesis depth past which the parser would exhaust Python's recursion limit.
MAX_NESTING = 40
# SQL Server table hints that take update, exclusive or held locks (a read query may not).
LOCKING_TABLE_HINTS = frozenset(
    {"tablock", "tablockx", "updlock", "xlock", "holdlock", "serializable", "repeatableread"}
)

# Functions with side effects, sleeps, file or network access, or session changes.
DENIED_FUNCTIONS: dict[str, frozenset[str]] = {
    "postgresql": frozenset(
        {
            "pg_sleep",
            "pg_sleep_for",
            "pg_sleep_until",
            "pg_read_file",
            "pg_read_binary_file",
            "pg_ls_dir",
            "pg_ls_logdir",
            "pg_ls_waldir",
            "pg_stat_file",
            "pg_terminate_backend",
            "pg_cancel_backend",
            "pg_reload_conf",
            "pg_rotate_logfile",
            "pg_promote",
            "pg_switch_wal",
            "pg_create_restore_point",
            "pg_advisory_lock",
            "pg_advisory_xact_lock",
            "pg_try_advisory_lock",
            "pg_notify",
            "pg_logical_emit_message",
            "pg_import_system_collations",
            "lo_import",
            "lo_export",
            "lo_unlink",
            "lo_create",
            "lo_put",
            "dblink",
            "dblink_exec",
            "dblink_connect",
            "set_config",
            "nextval",
            "setval",
            "txid_current",
            "query_to_xml",
            "query_to_json",
            "table_to_xml",
            "cursor_to_xml",
        }
    ),
    "mysql": frozenset(
        {
            "sleep",
            "benchmark",
            "load_file",
            "get_lock",
            "release_lock",
            "release_all_locks",
            "is_free_lock",
            "master_pos_wait",
            "source_pos_wait",
            "sys_exec",
            "sys_eval",
            "uuid_short",
        }
    ),
    "sqlite": frozenset(
        {"load_extension", "writefile", "readfile", "edit", "fts3_tokenizer", "zeroblob"}
    ),
    "mssql": frozenset(
        {
            "openrowset",
            "opendatasource",
            "openquery",
            "openxml",
            "xp_cmdshell",
            "xp_dirtree",
            "xp_fileexist",
            "xp_regread",
            "sp_oacreate",
            "sp_executesql",
            "fn_get_audit_file",
            "fn_xe_file_target_read_file",
        }
    ),
}
# A function on another dialect's list is refused everywhere too: the name gives away the
# intent even when this database would not know it.
_ALL_DENIED = frozenset().union(*DENIED_FUNCTIONS.values())

_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Set,
    exp.Pragma,
    exp.Copy,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Analyze,
    exp.LoadData,
)
# Roots that are real statements (refused as not a SELECT); any other root (a bare word read
# as an alias or column) means the text was not a statement at all.
_STATEMENT_KEYS = frozenset(
    {
        "insert",
        "update",
        "delete",
        "merge",
        "create",
        "drop",
        "alter",
        "truncatetable",
        "command",
        "copy",
        "pragma",
        "set",
        "use",
        "transaction",
        "commit",
        "rollback",
        "grant",
        "revoke",
        "analyze",
        "loaddata",
        "describe",
        "show",
        "cache",
        "uncache",
        "refresh",
        "values",
        "attach",
        "detach",
    }
)


class UnsafeSqlError(ValueError):
    """The SQL is not acceptable on this path. ``code`` says which rule refused it."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the guard learned about an accepted read-only query."""

    sql: str
    dialect: str
    tables: list[str] = field(default_factory=list)
    # alias (lower case) -> table name, for reading query plans that name aliases.
    aliases: dict[str, str] = field(default_factory=dict)
    tree: exp.Expression | None = None
    # The statement re-serialized from the parse tree, comments dropped.
    canonical_sql: str = ""

    def canonical(self) -> str:
        """The statement as the guard understood it, re-serialized from the parse tree."""
        return self.canonical_sql

    def executable(self) -> str:
        """What is sent to the database: the canonical form on MySQL and SQL Server (text
        the guard did not understand can never reach them), the text as typed elsewhere."""
        return self.canonical_sql if self.dialect in CANONICAL_DIALECTS else self.sql


def _tokenize(sql: str, dialect: str) -> list[Token]:
    if dialect not in SQLGLOT_DIALECT:
        raise UnsafeSqlError(f"unsupported dialect {dialect!r}", "unsupported_dialect")
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeSqlError(f"the query is longer than {MAX_SQL_CHARS:,} characters", "too_long")
    try:
        return list(sqlglot.Dialect.get_or_raise(SQLGLOT_DIALECT[dialect]).tokenize(sql))
    except TokenError as exc:
        first = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        raise UnsafeSqlError(f"the query does not parse: {first}", "parse_error") from exc


def _executable_comment(text: str) -> bool:
    """``/*! ... */`` (MySQL runs it), ``/*M! ... */`` (MariaDB) or ``/*+ ... */`` (a hint)."""
    body = text.strip()
    return body.startswith(("!", "+")) or body[:2].upper() == "M!"


def screen(sql: str, dialect: str) -> list[Token]:
    """Refuse what the parser would not see or could not survive: comments the database
    executes or obeys, and nesting deep enough to exhaust the parser. Returns the tokens."""
    tokens = _tokenize(sql, dialect)
    depth = 0
    for token in tokens:
        if token.token_type == TokenType.HINT or any(
            _executable_comment(c) for c in token.comments
        ):
            raise UnsafeSqlError(
                "executable comments and optimizer hints (/*! ... */, /*+ ... */) are not "
                "allowed: the database would act on text the guard does not check",
                "hidden_code",
            )
        if token.token_type == TokenType.L_PAREN:
            depth += 1
            if depth > MAX_NESTING:
                raise UnsafeSqlError(
                    f"the query nests more than {MAX_NESTING} levels deep", "too_deep"
                )
        elif token.token_type == TokenType.R_PAREN:
            depth -= 1
    return tokens


def parse(sql: str, dialect: str) -> list[exp.Expression]:
    if dialect not in SQLGLOT_DIALECT:
        raise UnsafeSqlError(f"unsupported dialect {dialect!r}", "unsupported_dialect")
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeSqlError(f"the query is longer than {MAX_SQL_CHARS:,} characters", "too_long")
    try:
        parsed = sqlglot.parse(sql, read=SQLGLOT_DIALECT[dialect])
        return [s for s in parsed if isinstance(s, exp.Expression)]
    except (ParseError, TokenError) as exc:
        first = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        raise UnsafeSqlError(f"the query does not parse: {first}", "parse_error") from exc
    except RecursionError as exc:
        raise UnsafeSqlError("the query nests too deeply to check", "too_deep") from exc


def serialize(tree: exp.Expression, dialect: str) -> str:
    """The canonical text of a parsed statement: re-generated, comments dropped."""
    try:
        return str(tree.sql(dialect=SQLGLOT_DIALECT[dialect], comments=False))
    except RecursionError as exc:
        raise UnsafeSqlError("the query nests too deeply to check", "too_deep") from exc


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return str(node.sql_name()).lower()


def _deny_functions_and_files(tree: exp.Expression) -> None:
    for node in tree.walk():
        if isinstance(node, exp.Func) and _function_name(node) in _ALL_DENIED:
            raise UnsafeSqlError(
                f"the function {_function_name(node)} is not allowed", "denied_function"
            )
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Func | exp.Literal):
            raise UnsafeSqlError("table functions and file paths are not allowed", "table_function")


def _deny_locking_hints(tree: exp.Expression) -> None:
    """SQL Server's spelling of ``FOR UPDATE``: table hints that lock what the query reads."""
    for hint in tree.find_all(exp.WithTableHint):
        for e in hint.expressions:
            if str(e.name or e.sql()).lower() in LOCKING_TABLE_HINTS:
                raise UnsafeSqlError(
                    f"the table hint {str(e.name or e.sql()).upper()} locks rows; a read-only "
                    "query may not",
                    "write_or_admin",
                )


def check(sql: str, dialect: str) -> Verdict:
    """Accept ``sql`` as one read-only query in ``dialect`` or raise UnsafeSqlError."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise UnsafeSqlError("the query is empty", "empty")
    screen(sql, dialect)
    statements = parse(sql, dialect)
    if len(statements) != 1:
        raise UnsafeSqlError(
            f"exactly one statement is allowed, found {len(statements)}", "multiple_statements"
        )
    tree = statements[0]
    if not isinstance(tree, _ALLOWED_ROOTS):
        if tree.key not in _STATEMENT_KEYS:
            raise UnsafeSqlError("the text does not read as a SQL statement", "parse_error")
        raise UnsafeSqlError(
            f"only SELECT queries run here, not {tree.key.upper()}", "not_a_select"
        )
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeSqlError(
                f"{node.key.upper()} is not allowed in a read-only query", "write_or_admin"
            )
    _deny_functions_and_files(tree)
    _deny_locking_hints(tree)
    return Verdict(
        sql=text,
        dialect=dialect,
        tables=referenced_tables(tree),
        aliases={
            str(t.alias).lower(): str(t.name)
            for t in tree.find_all(exp.Table)
            if t.alias and t.name
        },
        tree=tree,
        canonical_sql=serialize(tree, dialect),
    )


def referenced_tables(tree: exp.Expression) -> list[str]:
    """Tables the statement touches, schema qualified when written so; CTE names excluded."""
    ctes = {str(c.alias_or_name).lower() for c in tree.find_all(exp.CTE)}
    seen: dict[str, None] = {}
    for t in tree.find_all(exp.Table):
        name = str(t.name)
        if not name or name.lower() in ctes:
            continue
        db = str(t.db) if t.db else ""
        seen[f"{db}.{name}" if db else name] = None
    return list(seen)


def is_read_only(sql: str, dialect: str) -> tuple[bool, str | None, str | None]:
    """``(ok, reason, code)`` without raising."""
    try:
        check(sql, dialect)
    except UnsafeSqlError as exc:
        return False, exc.message, exc.code
    return True, None, None


# ─── The human write path ────────────────────────────────────────────────────


@dataclass(frozen=True)
class StatementInfo:
    index: int
    # Exactly the text that runs: as typed on PostgreSQL and SQLite, the guard's
    # re-serialization (comments dropped) on MySQL and SQL Server.
    sql: str
    kind: str
    tables: list[str]
    summary: str
    destructive: bool
    has_where: bool | None = None


_WRITE_KINDS: dict[type[exp.Expression], str] = {
    exp.Insert: "insert",
    exp.Update: "update",
    exp.Delete: "delete",
    exp.Merge: "merge",
    exp.Create: "create",
    exp.Drop: "drop",
    exp.Alter: "alter",
    exp.TruncateTable: "truncate",
}
_REFUSED_ON_WRITE: dict[type[exp.Expression], tuple[str, str]] = {
    exp.Transaction: (
        "transaction control: DBPilot runs the statements in its own transaction",
        "transaction_control",
    ),
    exp.Commit: (
        "transaction control: DBPilot runs the statements in its own transaction",
        "transaction_control",
    ),
    exp.Rollback: (
        "transaction control: DBPilot runs the statements in its own transaction",
        "transaction_control",
    ),
    exp.Set: ("session settings: each write runs in a fresh session", "session_control"),
    exp.Use: (
        "switching databases: a write runs on the connection's own database",
        "session_control",
    ),
    exp.Pragma: ("PRAGMA statements", "session_control"),
    exp.Grant: ("privilege changes (GRANT, REVOKE) are not offered in 0.1.0", "privileges"),
    exp.Copy: ("COPY reads or writes files on the database server", "file_access"),
    exp.LoadData: ("LOAD DATA reads files", "file_access"),
}


def split_statements(sql: str, dialect: str) -> list[str]:
    """The exact text of each statement, split where the dialect's tokenizer finds a
    statement separator (a ``;`` inside a string or comment does not split)."""
    if dialect not in SQLGLOT_DIALECT:
        raise UnsafeSqlError(f"unsupported dialect {dialect!r}", "unsupported_dialect")
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeSqlError(f"the script is longer than {MAX_SQL_CHARS:,} characters", "too_long")
    try:
        tokens = sqlglot.Dialect.get_or_raise(SQLGLOT_DIALECT[dialect]).tokenize(sql)
    except TokenError as exc:
        raise UnsafeSqlError(f"the script does not tokenize: {exc}", "parse_error") from exc
    parts: list[str] = []
    start = 0
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            parts.append(sql[start : token.start])
            start = token.end + 1
    parts.append(sql[start:])
    return [p.strip() for p in parts if p.strip()]


def _object_label(node: exp.Expression, tables: list[str]) -> str:
    """What a CREATE or DROP names: ``ix_orders_status on orders`` for an index."""
    obj = node.this
    if isinstance(obj, exp.Schema):
        obj = obj.this
    if isinstance(obj, exp.Index):
        table = obj.args.get("table")
        on = f" on {table.sql()}" if isinstance(table, exp.Expression) else ""
        return f"{obj.name}{on}"
    if isinstance(obj, exp.Expression) and obj.name:
        return str(obj.sql())
    return tables[0] if tables else "the object"


def _summary(
    kind: str, node: exp.Expression, tables: list[str], has_where: bool | None
) -> tuple[str, bool]:
    target = tables[0] if tables else "the table"
    if kind in ("create", "drop"):
        target = _object_label(node, tables)
    if kind == "insert":
        return f"Inserts rows into {target}", False
    if kind == "update":
        if has_where:
            return f"Changes rows in {target} that match its WHERE clause", False
        return f"Changes EVERY row in {target} (no WHERE clause)", True
    if kind == "delete":
        if has_where:
            return f"Deletes rows from {target} that match its WHERE clause", False
        return f"Deletes EVERY row from {target} (no WHERE clause)", True
    if kind == "merge":
        return f"Merges rows into {target}", False
    object_kind = str(node.args.get("kind") or "object").lower()
    if kind == "create":
        return f"Creates the {object_kind} {target}", False
    if kind == "drop":
        return f"Drops the {object_kind} {target} and everything in it", True
    if kind == "alter":
        dropping = any(isinstance(n, exp.Drop) for n in node.walk() if n is not node)
        return f"Alters the table {target}" + (" (drops part of it)" if dropping else ""), dropping
    if kind == "truncate":
        return f"Empties {target} (every row)", True
    return f"Reads from {', '.join(tables) or 'the database'}", False


def classify(sql: str, dialect: str) -> list[StatementInfo]:
    """Describe each statement of a human-marked write, or raise UnsafeSqlError."""
    pieces = split_statements(sql, dialect)
    if not pieces:
        raise UnsafeSqlError("the script is empty", "empty")
    if len(pieces) > MAX_WRITE_STATEMENTS:
        raise UnsafeSqlError(
            f"at most {MAX_WRITE_STATEMENTS} statements per confirmation", "too_many_statements"
        )
    out: list[StatementInfo] = []
    for i, piece in enumerate(pieces):
        try:
            screen(piece, dialect)
        except UnsafeSqlError as exc:
            raise UnsafeSqlError(f"statement {i + 1}: {exc.message}", exc.code) from exc
        parsed = parse(piece, dialect)
        if len(parsed) != 1:
            raise UnsafeSqlError(
                f"statement {i + 1} could not be separated from the next one", "parse_error"
            )
        node = parsed[0]
        for refused, (why, code) in _REFUSED_ON_WRITE.items():
            if isinstance(node, refused):
                raise UnsafeSqlError(f"statement {i + 1}: {why}", code)
        if isinstance(node, exp.Command) or any(isinstance(n, exp.Command) for n in node.walk()):
            raise UnsafeSqlError(
                f"statement {i + 1} is not something DBPilot can parse, so it cannot show "
                "exactly what it does; it runs only statements it can describe",
                "unparsed_statement",
            )
        for n in node.walk():
            if isinstance(n, exp.Into) and n is not node:
                raise UnsafeSqlError(
                    f"statement {i + 1}: SELECT INTO is not allowed", "file_access"
                )
        _deny_functions_and_files(node)
        kind = next((k for t, k in _WRITE_KINDS.items() if isinstance(node, t)), None)
        if kind is None:
            if isinstance(node, _ALLOWED_ROOTS):
                kind = "select"
            else:
                raise UnsafeSqlError(
                    f"statement {i + 1} ({node.key.upper()}) is not a data or schema change "
                    "DBPilot runs",
                    "unsupported_statement",
                )
        tables = referenced_tables(node)
        has_where = node.args.get("where") is not None if kind in ("update", "delete") else None
        summary, destructive = _summary(kind, node, tables, has_where)
        out.append(
            StatementInfo(
                index=i,
                sql=serialize(node, dialect) if dialect in CANONICAL_DIALECTS else piece,
                kind=kind,
                tables=tables,
                summary=summary,
                destructive=destructive,
                has_where=has_where,
            )
        )
    return out


def fingerprint(sql: str, dialect: str) -> str | None:
    """A normalized form of a statement for matching (whitespace, case and comments do not
    matter); None when it does not parse."""
    try:
        statements = sqlglot.parse(sql, read=SQLGLOT_DIALECT.get(dialect, dialect))
        text = ";".join(
            s.sql(dialect=SQLGLOT_DIALECT.get(dialect, dialect), comments=False).lower()
            for s in statements
            if isinstance(s, exp.Expression)
        )
    except (ParseError, TokenError, RecursionError):
        return None
    return hashlib.sha256(text.encode()).hexdigest() if text else None


def statement_fingerprints(sql: str, dialect: str) -> list[str]:
    """Fingerprints of the whole text and of each statement in it on its own, so a single
    statement lifted out of a longer generated script still matches."""
    prints: dict[str, None] = {}
    whole = fingerprint(sql, dialect)
    if whole:
        prints[whole] = None
    try:
        pieces = split_statements(sql, dialect)
    except UnsafeSqlError:
        pieces = []
    for piece in pieces:
        if fp := fingerprint(piece, dialect):
            prints[fp] = None
    return list(prints)


def pretty(sql: str, dialect: str) -> str:
    """The statements reformatted (the editor's Format button). Unparseable text comes back
    unchanged."""
    try:
        statements = sqlglot.parse(sql, read=SQLGLOT_DIALECT[dialect])
        out = [
            s.sql(dialect=SQLGLOT_DIALECT[dialect], pretty=True)
            for s in statements
            if isinstance(s, exp.Expression)
        ]
    except (ParseError, TokenError, KeyError, RecursionError):
        return sql
    return ";\n\n".join(out) + (";" if len(out) > 1 else "")

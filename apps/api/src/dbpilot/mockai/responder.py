"""How the deterministic mock model answers a DBPilot prompt, in any wire format.

The prompt (``ai/prompt.py``) names its task on the system message's first line and
carries the dialect and the schema block. The mock reads those back and answers with the
same JSON object a real model is asked for. Both the in-process ``mock`` provider and the
mock AI service (``dbpilot.mockai.app``) use this function, so they always agree.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from dbpilot.ai import advice
from dbpilot.ai.describe import describe
from dbpilot.ai.prompt import parse_block
from dbpilot.mockai.planner import plan

_DIALECT = re.compile(r"^Dialect: (postgresql|mysql|sqlite|mssql)$", re.MULTILINE)
_TODAY = re.compile(r"^Today: (\d{4}-\d{2}-\d{2})$", re.MULTILINE)
_SQL_BLOCK = re.compile(r"```sql\n(.*?)\n```", re.DOTALL)


def _question(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Question: "):
            return line[len("Question: ") :]
    return text


def respond(system: str, turns: list[tuple[str, str]]) -> str:
    """The answer to a prompt: ``system`` text and ``(role, content)`` turns."""
    dialect = m[1] if (m := _DIALECT.search(system)) else "postgresql"
    today = dt.date.fromisoformat(m[1]) if (m := _TODAY.search(system)) else None
    users = [content for role, content in turns if role == "user"]
    last = users[-1] if users else ""
    if system.startswith("TASK: explain"):
        sql = m[1] if (m := _SQL_BLOCK.search(last)) else last
        return json.dumps({"explanation": describe(sql, dialect)})
    if system.startswith("TASK: diagnose"):
        sql = m[1] if (m := _SQL_BLOCK.search(last)) else ""
        error = last.split("Error:", 1)[1].strip() if "Error:" in last else ""
        found = advice.diagnose(sql, error, parse_block(system))
        hint = f" Closest real names: {', '.join(found.similar)}." if found.similar else ""
        return json.dumps({"diagnosis": found.explanation + hint, "suggestion": found.suggestion})
    if not system.startswith("TASK: generate"):
        return json.dumps({"answer": "This mock only answers DBPilot prompts."})
    result = plan(parse_block(system), _question(last), dialect, today)
    if result.sql is None:
        return json.dumps({"sql": None, "explanation": result.note})
    return json.dumps({"sql": result.sql, "explanation": describe(result.sql, dialect)})

"""The mock AI service (port 5650): a deterministic stand-in for a model provider for tests,
end-to-end runs and the demo. No keys, no network, the same answer every time.

It speaks two wire formats, so both real provider adapters are exercised against a real
HTTP server:

* OpenAI (and every compatible gateway): ``POST /v1/chat/completions``, ``GET /v1/models``
* Anthropic: ``POST /anthropic/v1/messages``

Answers come from ``dbpilot.mockai.responder``, the same function the in-process ``mock``
provider uses. Test controls: ``POST /_mock/script`` queues exact answers for the next calls
(a fabricated column, a smuggled write), ``POST /_mock/fail`` makes the next calls fail with
a status, ``GET /_mock/calls`` lists what was asked, ``GET /_mock/auth`` shows the
credential each family last received, ``POST /_mock/reset`` clears everything.

Run it with ``uvicorn dbpilot.mockai.app:app --port 5650``.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from dbpilot.mockai.responder import respond

app = FastAPI(title="DBPilot mock model provider", docs_url=None, redoc_url=None)

AUTH: dict[str, str] = {}
CALLS: list[dict[str, Any]] = []
SCRIPT: deque[str] = deque()
FAILURES: deque[int] = deque()


class Scripted(BaseModel):
    answers: list[str]


def _answer(system: str, turns: list[tuple[str, str]]) -> str:
    CALLS.append({"task": system.splitlines()[0] if system else "", "turns": len(turns)})
    return SCRIPT.popleft() if SCRIPT else respond(system, turns)


def _failure() -> JSONResponse | None:
    if not FAILURES:
        return None
    return JSONResponse(
        {"error": {"message": "mock overload", "type": "server_error"}},
        status_code=FAILURES.popleft(),
        headers={"retry-after": "0"},
    )


def _tokens(text: str) -> int:
    return max(len(text) // 4, 1)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/_mock/script")
async def script(body: Scripted) -> dict[str, int]:
    SCRIPT.extend(body.answers)
    return {"queued": len(SCRIPT)}


@app.post("/_mock/fail")
async def fail(status: int = 503, times: int = 1) -> dict[str, int]:
    FAILURES.extend([status] * times)
    return {"queued": len(FAILURES)}


@app.get("/_mock/calls")
async def calls() -> list[dict[str, Any]]:
    return CALLS


@app.get("/_mock/auth")
async def last_auth() -> dict[str, str]:
    return AUTH


@app.post("/_mock/reset")
async def reset() -> dict[str, str]:
    AUTH.clear()
    CALLS.clear()
    SCRIPT.clear()
    FAILURES.clear()
    return {"status": "ok"}


@app.get("/v1/models")
async def openai_models(request: Request) -> dict[str, Any]:
    AUTH["openai"] = request.headers.get("authorization", "")
    return {"object": "list", "data": [{"id": "dbpilot-mock-sql", "object": "model"}]}


@app.post("/v1/chat/completions", response_model=None)
async def openai_chat(request: Request) -> JSONResponse:
    body = await request.json()
    AUTH["openai"] = request.headers.get("authorization", "")
    if failed := _failure():
        return failed
    messages = body.get("messages", [])
    system = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    turns = [
        (str(m.get("role")), str(m.get("content") or ""))
        for m in messages
        if m.get("role") != "system"
    ]
    text = _answer(system, turns)
    pin, pout = _tokens(json.dumps(messages)), _tokens(text)
    return JSONResponse(
        {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": body.get("model", "dbpilot-mock-sql"),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {"prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout},
        }
    )


@app.post("/anthropic/v1/messages", response_model=None)
async def anthropic_messages(request: Request) -> JSONResponse:
    body = await request.json()
    AUTH["anthropic"] = request.headers.get("x-api-key", "")
    if failed := _failure():
        return failed
    system = str(body.get("system") or "")
    turns = [(str(m.get("role")), str(m.get("content") or "")) for m in body.get("messages", [])]
    text = _answer(system, turns)
    return JSONResponse(
        {
            "id": "msg_mock",
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": _tokens(json.dumps(body)), "output_tokens": _tokens(text)},
        }
    )

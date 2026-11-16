# 0002: The AI assistant never writes, full stop, instead of an approval workflow

**Status:** accepted

**Context.** The brief's own roadmap (and most comparable tools, see
[docs/PLAN.md](../../PLAN.md)'s market research) assume an AI-proposed write is eventually
useful, gated behind a human approval step: generate, show the human what it would do,
let them approve, then run it under the same confirmation flow as a human-marked write.
That is a real option DBPilot could have built for 0.1.0 (acceptance criterion AC5 already
provides the token-plus-digest confirmation primitive an AI-proposed write could have
reused).

**Decision.** 0.1.0 does not build an AI-write-approval workflow at all. An AI-generated
statement that the guard identifies as anything other than a single read-only query is
refused before execution, unconditionally, with no flag, no approval and no "run anyway"
button anywhere in the product (AC4). The simplest possible answer to "can the AI write"
is "no", not "yes, behind an approval screen."

**Consequences.** This removes an entire class of risk (a model talked into generating a
plausible-looking destructive statement that a rushed human approves without reading
closely) at the cost of a real capability: 0.1.0 cannot ask the assistant to draft a
migration or a fix-up write for a human to review and run. That capability is an explicit,
named Roadmap item ("approval workflows for AI-proposed writes": [docs/PLAN.md](../../PLAN.md)),
not silently dropped. When it is eventually built, it will reuse the exact confirmation
primitive AC5 already has (`confirmations/service.py`), so the review-then-run shape stays
identical to a human-marked write; only the drafting step would change. Until then, a human
who wants an AI-drafted write types it into the editor themselves and marks it as a write,
going through the ordinary AC5 path like anything else a human decides to run.

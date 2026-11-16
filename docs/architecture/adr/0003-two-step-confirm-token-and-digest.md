# 0003: A write confirmation needs both a one-time token and a digest, not just an id

**Status:** accepted

**Context.** The easy version of "confirm before you run it" is a single id: show the
statement, the person clicks "confirm", the client sends the id back, the server runs it.
That is weak in two ways DBPilot's own threat model cares about: the id alone does not
prove the statement shown to the person is the one that actually runs (a race, a stale
cache, a tampered request could resend a different statement under the same id), and
possessing the id (visible in a URL, a log line, a browser history entry) is enough for
anyone to trigger execution, not only the person who was shown the confirmation.

**Decision.** `confirmations/service.py` requires two independent pieces of information
at `begin`, neither of which is the confirmation id alone: a one-time token (generated at
`create`, only its hash stored, so even a database read cannot recover it) and a digest of
exactly what will run. Both must match exactly what was stored, the confirmation must still
be pending and unexpired, or the attempt is rejected and the reason recorded. This makes
the confirmation prove two separate things at once: that the caller is the one who was
actually shown the token (not someone who merely learned the id), and that the statement
about to run is byte-identical to the one that was presented (not a stale or substituted
one), satisfying AC5, AC7 and AC8 identically for writes, migrations and restores.

**Consequences.** A client must carry both the token and the digest from the `create`
response through to `begin`; there is no "confirm with just the id" shortcut anywhere, by
design. Losing or not storing the token client-side means starting the confirmation over,
which is the intended failure mode (the token is one-time and not recoverable from the
server by design). The same primitive now backs three different domains (writes,
migrations, restores) through one shared code path, rather than three separate
ad hoc "are you sure" dialogs with three different guarantees.

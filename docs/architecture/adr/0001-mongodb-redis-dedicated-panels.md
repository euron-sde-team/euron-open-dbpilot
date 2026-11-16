# 0001: MongoDB and Redis get dedicated read-only panels, not a forced SQL abstraction

**Status:** accepted

**Context.** The SQL editor, autocomplete and the AI-to-SQL assistant all assume a
relational schema and a SQL dialect the guard can parse. MongoDB has collections,
documents and an aggregation pipeline; Redis has keys and a command set. Flattening either
into "generate some SQL-like text, then translate it" would mean inventing a private query
language, writing a second guard for it, and still not matching what either engine's own
operations actually look like to someone who knows them.

**Decision.** MongoDB and Redis get their own read-only query surfaces: a filter/aggregate
panel for MongoDB and a key browser plus a read-only command allowlist for Redis
(acceptance criterion AC11). Neither goes through `queries/guard.py`'s SQL parser; their
safety boundary is structural instead, documented in
[security.md](../security.md): the connector objects exposed to the query path
(`connections/connectors/mongo.py`, `connectors/redis_source.py`) have no write method at
all, so there is no generic command path a write could travel through, read-only or not.
The AI assistant, schema comparison and migration scripts are documented as SQL-engine-only
features and honestly do not apply to these two engines in 0.1.0, rather than being faked
with a translation layer that would not actually guarantee anything.

**Consequences.** A contributor adding a MongoDB or Redis feature touches
`connectors/mongo.py` or `connectors/redis_source.py` and the matching panel, not the SQL
guard. The two connector modules stay deliberately small, since their read-only guarantee
is a property of what methods they expose, which is why they are also small enough to
review by hand rather than by test coverage alone (see
[threat-model.md](../threat-model.md)). A future engine with the same "no read-only session
flag" shape (e.g. a wide-column store) would follow this pattern, not the SQL guard's.

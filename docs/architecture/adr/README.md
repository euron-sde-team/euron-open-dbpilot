# Architecture decisions

| # | Decision |
| --- | --- |
| [0001](0001-mongodb-redis-dedicated-panels.md) | MongoDB and Redis get dedicated read-only panels, not a forced SQL abstraction |
| [0002](0002-ai-never-writes.md) | The AI assistant never writes, full stop, instead of an approval workflow |
| [0003](0003-two-step-confirm-token-and-digest.md) | A write confirmation needs both a one-time token and a digest, not just an id |
| [0004](0004-no-queue-or-mail-module.md) | No SQS/EventBridge module and no SES/email module |

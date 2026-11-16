# 0004: No SQS/EventBridge module and no SES/email module

**Status:** accepted

**Context.** The program's own stack table (`euron-open-source/CLAUDE.md` section 0 item
7) lists queues/events and mail as resources every project provisions a Terraform module
for, local and AWS, "where the project needs it." DBPilot's backend was grepped for any
mail code path and any queue/broker usage before writing a single line of
infrastructure for either: there is none. The one background process, the scheduled-backup
worker (`worker.py`), claims due work with `FOR UPDATE SKIP LOCKED` directly against
Postgres and runs on a plain poll interval (`WORKER_POLL_INTERVAL_S`); it is never woken by
a queue message, and nothing in the product sends a notification email (no password reset
email, no backup-finished email, no digest).

**Decision.** `infra/terraform/modules/` ships nine modules (kms, storage, secrets,
database, cache, network, ecr, service, observability) and deliberately has no `queue` and
no `email` module, and `deploy/compose/` has no Mailpit-equivalent service and no
SQS/EventBridge Floci usage for this project. This mirrors the program's own documented
PIIShield lesson in the opposite direction: shipping infrastructure for a service the
application code never calls is exactly the "`command:` in Compose with no matching
implementation" mistake the program's own rules now explicitly check for
(acceptance criterion AC12), just applied to Terraform modules and Compose services instead
of a worker command.

**Consequences.** A feature that would need either (a password-reset email, a
webhook-triggered background job) is Roadmap, and adding it means adding the module and the
Compose service in the same change that adds the code that actually uses it, never ahead of
it. Nothing in 0.1.0 silently assumes a queue or a mail transport exists.

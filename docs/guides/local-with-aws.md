# Local with AWS (Floci)

`make up` starts DBPilot against **Floci**, a local AWS emulator, provisioned by the exact
same Terraform modules that build real AWS (`infra/terraform/modules/`), applied against
the emulator by a one-shot `terraform` container before the `migrate`, `api` and `worker`
containers start. This proves the IaC plans, applies and runs cleanly on every local run,
not just in CI, and, unlike some sibling projects, DBPilot's app code genuinely exercises
the emulated KMS key and S3 bucket, not just the Terraform that provisions them.

## What Floci actually emulates here

`deploy/compose/compose.floci.yaml` provisions, through `infra/terraform/envs/local-floci`:

- A KMS key and alias (`alias/dbpilot-local`).
- An S3 objects bucket (`dbpilot-local-objects`).

## What the app actually calls

Under the Floci overlay, the API, worker and migrate containers run with `STORAGE=s3` and
`KEY_MODE=kms` set for real (`compose.floci.yaml`'s `x-floci-env`):

| Resource Terraform provisions on Floci | App behavior |
| --- | --- |
| KMS key/alias | Real: every connection's `secret_sealed` and the AI provider key are sealed with a live `kms:Encrypt` call against Floci, encryption context bound to the record id (`core/sealing.py`'s `KmsSealer`) |
| S3 objects bucket | Real: backup dump files and uploaded SQLite files are written to and read from Floci's S3 (`core/storage.py`'s `S3Storage`), never local disk under this overlay |

This is why `make test-matrix` (storage local/S3, key_mode local/kms) and every e2e/QA run
against `make up` genuinely exercise both backends, not just their Terraform.

## Running it

```sh
make env
make up
```

Logs for the one-shot Terraform apply:

```sh
make logs SERVICE=terraform
```

Floci's own edge is reachable at `http://localhost:5866` if you want to inspect what was
created directly (for example with the AWS CLI against `--endpoint-url
http://localhost:5866`, region `us-east-1`, any placeholder credentials).

## Proving the infrastructure end to end

```sh
make tf-aws-on-floci
```

A separate, heavier check from `make up`'s own one-shot apply: this plans, applies,
re-plans (asserting zero adds or replacements) and destroys the full `aws-dev` environment
against a second, throwaway Floci instance on its own port (5896), independent of whatever
`make up` currently has running. See [deploy-aws.md](deploy-aws.md) and
[testing.md](testing.md) for the real resource counts from the most recent run.

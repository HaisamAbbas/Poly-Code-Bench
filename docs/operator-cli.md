# Operator CLI

The website is a public results viewer. It serves published benchmark/model results, public audit
reports, and attestation verification. It does not run evaluations, scan repositories or benchmark
sources, review submissions, or expose operator controls. Those actions use the authenticated CLI
and backend APIs.

## Setup

From the repository root, install the locked workspace and development tools:

```powershell
uv sync --locked --all-packages --group dev
```

For API-backed commands, configure the API origin and an access token in the current shell. Admin
review endpoints require the administrator permission and MFA; task and audit APIs also apply their
own tenant, reviewer, and object-level checks.

```powershell
$env:PCB_API_URL = "https://api.example.org"
$env:PCB_API_TOKEN = "<short-lived token>"
uv run --locked --all-packages pcb --help
```

`--dry-run` validates supported local inputs and prints the selected method/route without making
an HTTP request. Every remote mutation that supports replay requires an `--idempotency-key`.
Endpoint decisions also require an expected row version, so stale decisions fail instead of
overwriting newer review state. CLI output never includes endpoint secret references.

## CLI map

| Command | Operator work |
|---|---|
| `pcb submissions` | Submit a model for private review; list/show requests; approve a bounded evaluation run or reject a request. |
| `pcb endpoints` | Register endpoints; list/show registrations; record approval, rejection, or revocation decisions. |
| `pcb audit` | Preview benchmark scope, estimate bounded resources, list and inspect tenant-scoped audit documents, save immutable plans, create planned no-dispatch audit runs, and verify signed attestations locally. `pcb audit capabilities` lists supported and blocked operations. |
| `pcb-ops` | Deployment checks, identity checks, migrations, worker draining, orphan/artifact cleanup, backups/restores, signing keys, release synchronization, and alert-rule validation. |
| `pcb-release` | Build, validate, sign, publish, withdraw, and inspect public release manifests. Publication still requires the configured release authority. |
| `pcb-model` | Register/probe model endpoints, plan bounded model calls, inspect budgets, and reconcile provider delivery records. |
| `pcb-scheduler` / `pcb-worker` | Schedule and claim jobs, register worker capacity, process bounded solve/evaluation/grading queues, and inspect/recover leases. |
| `pcb-solve` / `pcb-judge` | Inspect solve attempts and protocols; build/validate judge packets, run enabled judge batches, and review/adjudicate stored results. |
| `pcb-track-a` / `pcb-score` | Validate and score evaluation evidence, replay scoring, and aggregate Track A results. |
| `pcb-taskgen` | Create generation secrets, validate and screen generated candidates, check exposure/agreement, and commit or verify generation rounds. |
| `python scripts/pcb.py` | Import and validate task packages, execute admission checks, and create/freeze task sets against the configured database. |

Each executable's `--help` is the source of truth for its full subcommand flags. API-backed operations
use `PCB_API_URL` and `PCB_API_TOKEN`; database, object-store, worker, signing, and provider tools use
their narrowly scoped service identities and environment configuration.

## Typical model-evaluation workflow

1. Submitter sends a strict metadata-only request with `pcb submissions submit --payload request.json
   --idempotency-key <key>`. Submission does not contact the model provider or enqueue work.
2. An authorized operator registers the endpoint with `pcb endpoints register`, then probes it with
   `pcb-model check`. A provider call is permitted only after the endpoint policy and credentials
   have passed their separate checks.
3. The operator records an endpoint decision using `pcb endpoints decide <id> --decision
   endpoint-decision.json --idempotency-key <key>`. Approval requires a passing conformance report;
   the API applies the supplied expected row version.
4. The operator reviews the queue with `pcb submissions list` and `pcb submissions show`. Approval
   uses a decision document containing an approved endpoint, a bounded run request, and a source
   permission review. `pcb submissions approve` records the run identity; it does not publish a
   result.
5. `pcb-worker` and `pcb-scheduler` execute only work enabled for their verified service roles.
   Scoring, judge review, release review, signing, and publication remain distinct stages.
6. The public website shows results only after the reviewed release workflow publishes them.

## Benchmark containment boundary

The `pcb audit` API supports scope preview, bounded resource estimates, tenant-scoped document
reads, registry reads, immutable audit-plan creation, and planned no-dispatch run creation/status
reads. Attestations can be read through authorized document access and verified locally. An audit
run returned by this API always has `dispatch_authorized=false`. No CLI command in this build can
launch source queries, benchmark/model/guest execution, or issue a clean-benchmark verdict.

An operator can inspect the catalog, request a bounded estimate, and review saved plan records
without dispatching work:

```powershell
pcb audit scope-preview
pcb audit resource-plan --payload resource-request.json
pcb audit list plans
$planId = "<copy the id from the plans list>"
pcb audit show plans $planId
```

`resource-request.json` contains registered benchmark counts, source groups, retrieval stages, and
an optional average item size. The API rejects unknown catalog entries and requests above the
frozen task, query, source, stage, or storage limits. Its result includes blockers and always sets
`dispatch_allowed=false`.

`pcb audit capabilities` also identifies local preparation and operations with no reviewed API
adapter. Match review, temporal assessment, sealed evaluation, firewall decisions, replacement
admission, monitoring/alerts, health generation, and the durable benchmark import/membership flow
must remain blocked until their reviewer/tenant authorization, source/artifact and rights validation,
database writers, and required key or delivery adapters are in place. Validation-only flags and
synthetic fixtures do not authorize live source access, external model calls, signing, or publication.

## Running checks

The full repository checks and browser tests are listed in [development.md](development.md). The
CLI and API regression coverage includes authenticated end-to-end tests against the local FastAPI
app and persistence adapters; live cloud, provider, source, and production-key operations require
their separately approved test environments.

# PolyCodeBench API

Public API for reviewed release projections and private authenticated benchmark-audit resources.

The metadata-only audit workspace uses two additional public routes:

- `GET /v1/public/benchmark-audit/scope-preview` returns the versioned benchmark scope report,
  source-policy rows, and configured planning limits. It reads local catalog configuration only.
- `POST /v1/public/benchmark-audit/resource-plan` accepts bounded task counts, registered source
  groups, a stage count, and an optional average item size. It returns a deterministic capacity
  estimate with blocker reasons. The request never fetches a source, creates a private plan/run,
  reserves budget, calls a model, or authorizes dispatch.

These routes expose catalog metadata and preflight estimates only. Private audit documents,
containment evidence, review transitions, scans, signing, and publication remain behind their
existing tenant, role, evidence, and service gates.

Private benchmark-audit reads require a bearer identity with a tenant claim, an RBAC permission,
and object-level access. New audit documents are tenant-bound; legacy rows without a tenant remain
invisible to this API. The default policy grants an owner access only to their own tenant-scoped
documents. Deployments that need shared reviewer access must inject a trusted ACL policy. The API
exposes no source URL scanning route. Plan and run creation are idempotent and budget checked;
creating a run does not authorize dispatch.

From the repository root, run `uv run --project packages/api pcb audit --help` to use the
workspace-installed `pcb audit` command. Every command supports
`--dry-run`, which parses bounded local inputs and prints a request plan without making an HTTP
request. Registry, plan, run, status and attestation reads have API adapters. Import, health
projection generation, reviews, temporal assessment, seal, firewall, replacement, monitor and
signature-verification mutations return a blocked exit code until their reviewed API adapters are
installed. CLI HTTP requests do not follow redirects, so bearer tokens cannot be forwarded to a
different origin.

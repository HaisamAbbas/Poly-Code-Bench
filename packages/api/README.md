# PolyCodeBench API

Public API for reviewed release projections and private authenticated benchmark-audit resources.

The metadata-only audit workspace uses two tenant-scoped routes:

- `GET /v1/benchmark-audit/scope-preview` returns the versioned benchmark scope report,
  source-policy rows, and configured planning limits. It requires the `run:plan` permission and a
  tenant claim.
- `POST /v1/benchmark-audit/resource-plan` accepts bounded task counts, registered source
  groups, a stage count, and an optional average item size. It returns a deterministic capacity
  estimate with blocker reasons. It also requires `run:plan` and a tenant claim. The request never
  fetches a source, creates a private plan/run, reserves budget, calls a model, or authorizes dispatch.

The web BFF grants operator access only to exact OIDC subjects listed in the server-side
`PCB_WEB_AUDIT_OPERATOR_ACCESS_JSON` configuration. Each entry binds one subject to one tenant UUID;
the default configuration grants no operator access, and the web BFF makes no private API call
without a matching entry. The mapping shape is:

```json
{"schema_version":1,"principals":[{"subject":"<exact issuer>|<verified sub>","tenant_id":"<tenant UUID>"}]}
```

The web BFF fixes the mapped role to `operator` (plus `submitter`) and does not accept role values
from the browser. Store this mapping in server-side deployment configuration, never in
browser-visible configuration. Private audit documents, containment evidence, review transitions,
scans, signing, and publication remain behind their existing tenant, role, evidence, and service
gates.

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

# Application container builds

The repository builds pinned, multi-stage images for the public API, Next.js web app, the
scheduler lease reaper, and the solve supervisor. All final images run as UID/GID `10001`, contain no local `.cache`,
`.local`, `.protected`, `.env`, task-pack, or test-fixture data, and are suitable for read-only
root filesystems with `/tmp` mounted writable. API and scheduler images include `pcb-ops` and the
migration/configuration files their guarded commands need.

## Local build and runtime checks

From the repository root:

```powershell
docker build --platform linux/amd64 --file Dockerfile.api --tag pcb-api:local .
docker build --platform linux/amd64 --build-arg PCB_PUBLIC_API_URL=http://api:8000/v1 `
  --file Dockerfile.web --tag pcb-web:local .
docker build --platform linux/amd64 --file Dockerfile.scheduler `
  --tag pcb-scheduler:local .
docker build --platform linux/amd64 --file Dockerfile.ops --tag pcb-ops:local .
docker build --platform linux/amd64 --file Dockerfile.solve-worker `
  --tag pcb-solve-worker:local .
```

`PCB_PUBLIC_API_URL` is captured by the Next build for its same-origin rewrites and read at
runtime by server routes. `http://api:8000/v1` is the private ECS Service Connect endpoint. For a
local container calling a host-run API, rebuild the web image with
`--build-arg PCB_PUBLIC_API_URL=http://host.docker.internal:<port>/v1`, then pass that same value
to the web container. Keep local ports bound to loopback.

The API and scheduler images default to the development environment manifest. A staging/production
build must pass its reconciled, `status: deployed` manifest through
`--build-arg PCB_ENV_MANIFEST_SOURCE=config/environments/<environment>.yaml`. The task definition
loads that copy from `/etc/pcb/environment.yaml`; a template manifest intentionally refuses the
identity guard. Never build or publish a production image with the development manifest.

The scheduler image's default command is `pcb-scheduler reap --watch`. It recovers expired leases
and emits metrics; it is **not** a job worker and does not execute queued model, judge, solve, score,
or publication work. The solve image runs the dedicated `pcb-worker ec2-run` command; it is not
combined with the lease reaper. The startup identity guard exports the STS principal it verified as
`PCB_SERVICE_IDENTITY`, which the scheduler uses for audit attribution.

The API process requires `PCB_DATABASE_URL`, `PCB_CURSOR_SIGNING_KEY`,
`PCB_WEB_AUTH_SIGNING_KEY`, and `PCB_API_IDENTITY_JSON` in staging/production. The stack injects
the role-scoped database and cursor references, an empty versioned identity export, and the
shared web/API HMAC-key reference from Secrets Manager. The HMAC value is generated and installed
out of band; it must be the same value in both tasks and at least 32 bytes. The identity export
contains SHA-256 token fingerprints and role claims, never raw bearer values. Add reviewer/admin
fingerprints only through the approved private operator process. The web task also needs
`PCB_WEB_AUTH_SIGNING_KEY`, `PCB_OIDC_ISSUER`, `PCB_OIDC_CLIENT_ID`, `PCB_OIDC_REDIRECT_URI`, and
`PCB_WEB_ORIGIN`. A confidential OIDC client may additionally set
`PCB_OIDC_CLIENT_SECRET` as a Secrets Manager reference; public PKCE clients do not need it.

## Deployment boundaries

The Terraform control-services module creates an environment-scoped ECS Service Connect namespace.
The API advertises the `api:8000` endpoint; web and worker clients join the same namespace. The
web image can therefore fetch release data for server-rendered pages and submission status without
calling the public CDN from inside the VPC. API and web task definitions have HTTP health checks;
the web target uses `/` and the API target uses `/healthz`.

The API, web, and scheduler images built locally on 2026-10-06. The API and web images were
exercised against the local API's synthetic internal test release. The API identity guard and
health route passed. A Playwright browser smoke on the web image rendered that release and its
synthetic-data notice, moved focus with Tab, passed at 375 px and 1440 px without document overflow,
and reported no page errors. The scheduler identity guard launched `pcb-scheduler --help` without
AWS credentials; its read-only image also ran `reap --limit 100` against a disposable migrated
PostgreSQL 17.6 database and returned an empty batch. Sixteen scheduler/operations PostgreSQL
integration cases passed with a separate disposable object store. The scheduler has not been
tested against staging PostgreSQL. Sanitized details are in
[`scheduler-disposable-postgres.json`](../implementation/evidence/prompt-33/scheduler-disposable-postgres.json).
Screenshots are in the ignored local cache, not the repository.

The solve image defaults to a development manifest, an empty candidate-image allowlist and an
empty guest known-hosts file, so it cannot dispatch work as built. A staging/production build must
override `PCB_ENV_MANIFEST_SOURCE`, `PCB_CANDIDATE_IMAGE_ALLOWLIST_SOURCE` and
`PCB_GUEST_KNOWN_HOSTS_SOURCE` with the reviewed deployed manifest, approved digest allowlist and
pinned AMI guest host keys. The OpenSSH client package is version-pinned in the Dockerfile.

The migrator's `ops` image is built from `Dockerfile.ops`; it contains both `pcb-ops` and the
guarded `pcb-worker ec2-register` command. Its default development manifest and empty image
allowlist cannot register a production worker. A staging/production build must override
`PCB_ENV_MANIFEST_SOURCE`, `PCB_WORKER_RESOURCE_SPEC_SOURCE` and
`PCB_CANDIDATE_IMAGE_ALLOWLIST_SOURCE` with the reviewed deployed manifest, bounded resource
document and approved digest allowlist. The migrator task defaults
`PCB_AWS_WORKER_SETUP_ENABLED=false`. A reviewed one-off ECS task override may run
`pcb-worker ec2-register --resource-spec /etc/pcb/solve-resource-spec.json --image-allowlist /etc/pcb/candidate-image-allowlist.json`
with that flag set to `true`; the command requires the live STS identity to match the startup
identity guard and the manifest's migrator role. It creates a verified worker-config artifact and
an idempotent solve registration only. Terraform supplies endpoint and bucket names, IAM limits
the migrator to the worker-config/provisional prefixes, and registration enforces the manifest's
aggregate capacity cap. The migrator has no EC2 launch permission. This setup command is separate
from solve dispatch.

The guest AMI must be built with the public half of the matching role-specific SSH key installed
for the manifest's `sandbox.control_user` using `infra/sandbox/aws/guest/bootstrap-control.sh`;
the private half belongs only in that supervisor role's Secrets Manager reference. The AMI build
must also publish its pinned SSH host key for the solve image's known-hosts input. These key pairs,
the AMI and the host-key manifest are not present in the local-only configuration.

Before raising `solve-supervisor.desired_count` above zero, an operator must provision the
role-specific guest-control secret, register a worker through that one-off migrator task, set the
returned worker ID in the solve task environment, and explicitly set
`PCB_WORKER_DISPATCH_ENABLED=true`. Until then the tfvars examples keep the service at zero and
dispatch false. The run command rechecks the live STS principal and manifest, verifies the object
store matches manifest values, uses strict SSH host-key checking and the EC2 provider's lane and
guest isolation checks. Local ops and solve images built and smoke-checked on 2026-10-06 as UID
10001; their default manifests/allowlists refuse production actions. No production image build,
worker registration, queue dispatch, guest launch, ECR push or model call has been performed. The
AWS account, approved AMI, host-key bundle, production candidate allowlist and spend authorization
remain unavailable. The evaluation, judging, scoring and publication processors still need their
own long-running modes and validated images.

## Current web image rebuild (2026-10-07)

The current web source was rebuilt as `pcb-web:local-20261007-node24` from the pinned
`node:24.21.0-trixie-slim` base. Its production container ran as UID/GID 10001 with a read-only
root filesystem, all capabilities dropped, `no-new-privileges`, a bounded writable `/tmp` tmpfs,
and a loopback-only host port. Against the real local PostgreSQL-backed synthetic release, all
nine public pages returned HTTP 200 and showed the synthetic-data notice; the compatible
comparison exposed the API's three common tasks. The 390 px metrics region scrolled with the
keyboard, and Playwright reported no page errors. The temporary smoke container was removed after
the check. OIDC was not exercised in this HTTP container check; the separate Node 24 Keycloak
browser smoke passed in development mode, while production callback validation still requires
HTTPS. Sanitized image/runtime data and screenshots:
[`web-container-node24-2026-10-07.json`](../implementation/evidence/prompt-33/web-container-node24-2026-10-07.json),
[`desktop`](../implementation/evidence/prompt-33/local-production-web-container-node24-desktop.png),
[`mobile`](../implementation/evidence/prompt-33/local-production-web-container-node24-mobile.png).

## Current API image rebuild (2026-10-07)

The current `Dockerfile.api` source was rebuilt as `pcb-api:local-20261007-python312` for
`linux/amd64`. The local container ran as UID/GID 10001 with a read-only root filesystem, all
capabilities dropped, `no-new-privileges`, and only a bounded `/tmp` tmpfs writable. Its temporary
port was bound to loopback and its minimal local environment connected to the existing local
PostgreSQL catalog. Health, release index/detail, leaderboard and task-list reads all returned
HTTP 200 for the published exploratory `synthetic_internal` release. The API returned four
leaderboard entries and the first 50 task rows. The temporary container and environment file were
removed after the check; no database writes, provider calls or cloud resources were used. This
verifies the current API image against local data only; it is not a staging deployment. Sanitized
details: [`api-container-python312-2026-10-07.json`](../implementation/evidence/prompt-33/api-container-python312-2026-10-07.json).

The same image then passed all 11 release-backed public GET routes, with zero errors, on the
same synthetic release. The explicit-origin rehearsal sent one request per route (plus route
discovery/warm-up), and two conditional requests returned 304. The observed 45.076 ms p50 and
47.636 ms p95 describe this workstation and this small sample only; they are not an SLA or a
staging performance claim. Details:
[`api-container-routes-python312-2026-10-07.json`](../implementation/evidence/prompt-33/api-container-routes-python312-2026-10-07.json).

## API and web production-container pair (2026-10-07)

Ran the restricted API and web images together, with the web server fetching release-backed data
from the API container. All nine public pages returned 200 and showed the synthetic-data notice;
Playwright recorded zero page errors. The 390 px mobile document had no horizontal overflow, and
the metrics table scrolled with ArrowRight. Both temporary containers were removed. This checked
the UI/API connection on a local exploratory test release; it did not exercise production OIDC or
any model endpoint. Sanitized evidence and screenshots:
[`api-web-container-pair-2026-10-07.json`](../implementation/evidence/prompt-33/api-web-container-pair-2026-10-07.json),
[`desktop`](../implementation/evidence/prompt-33/api-web-container-pair-desktop.png),
[`mobile`](../implementation/evidence/prompt-33/api-web-container-pair-mobile.png).

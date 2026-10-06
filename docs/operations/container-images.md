# Application container builds

The repository builds pinned, multi-stage images for the public API, Next.js web app, and the
scheduler lease reaper. All final images run as UID/GID `10001`, contain no local `.cache`,
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
or publication work. The startup identity guard exports the STS principal it verified as
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
and reported no page errors. The scheduler identity guard launched `pcb-scheduler --help` in the
container without AWS credentials or database access. Screenshots are in the ignored local cache,
not the repository.

These images are not a complete staging service set. The scheduler image only runs lease recovery;
the model/judge gateways, solve/evaluation supervisors, scorer, and publisher still need the
long-running work-processing modes and validated runtime images. Do not raise their Terraform
counts or treat the scheduler as a substitute. The scheduler has not yet been exercised against a
staging database. No image was pushed to ECR; AWS account access, deployment inputs, and a spend
authorization are not available.

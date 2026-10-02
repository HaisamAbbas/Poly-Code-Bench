# Internal aggregation and reviewed local releases

Prompt 16 implements internal behavior evidence. Synthetic fixtures and reports are
explicitly labeled `synthetic_internal`; they are not model benchmark results.
The optional full-product editorial index is disabled. No deployment or remote
publication target is enabled by these commands.

`pcb-release` (also `python -m polycodebench_publication.cli`) uses strict JSON,
rejects duplicate fields and non-finite numbers, and returns nonzero on refused
operations. Generate request contracts with
`python scripts/export_publication_schemas.py`; CI can use `--check`.

```powershell
pcb-release aggregate --request internal-request.json --output aggregate.json
pcb-release report --request internal-request.json --output report.md
pcb-release replay --request internal-request.json --archived aggregate.json
```

The request contains one immutable cohort, metric definitions, scorecard-linked
observations, entry IDs, bootstrap policy and optional paired entry IDs. Every
entry and comparison uses that cohort. Reports include sample/task/language
coverage, failure denominators, uncertainty method/seed/replicates and their
digest. Missing tasks or languages block a total instead of changing weights.
Conditional-on-pass metrics require explicitly labeled metric IDs.

Release administration is a local service Python API (`ReleaseStore`) and CLI;
HTTP route wiring belongs to the later public API work package. Local principals
are supplied by the operator; deployments must bind them to authenticated actors.
Roles are `curator`, `reviewer`, `publisher`, or `administrator`.

```powershell
pcb-release create --store internal-releases.sqlite --subject curator-1 --role curator --request-id draft-1 --content internal-report.json --projection safe-projection.json
pcb-release validate --store internal-releases.sqlite --subject curator-1 --role curator --request-id validation-1 --release-id RELEASE_ID --expected-version 1 --evidence validation-receipts.json
pcb-release review --store internal-releases.sqlite --subject reviewer-1 --role reviewer --request-id review-1 --release-id RELEASE_ID --expected-version 2 --reason "Reviewed the exact evidence and projection"
pcb-release approve --store internal-releases.sqlite --subject reviewer-1 --role reviewer --request-id approve-1 --release-id RELEASE_ID --expected-version 3 --reason "Approved the reviewed snapshot"
pcb-release publish --store internal-releases.sqlite --subject publisher-1 --role publisher --request-id publish-1 --release-id RELEASE_ID --expected-version 4 --expected-generation 0 --target local:board --key-file private-ed25519.pem --key-id local-review-key
pcb-release verify --manifest manifest.json --public-key public-ed25519.pem
```

Versions above illustrate an uninterrupted flow; use the returned version and
current target generation. A stale version or pointer generation conflicts.
Validation receipts must cover every `REQUIRED_CHECKS` item, refer to concrete
evidence, match expected/observed digests and bind the exact content digest.
Updating a draft with `update` invalidates validation, review and approval.
Draft creation and updates reject projections outside the strict public allowlist.
Published content is immutable; corrections use `create --predecessor RELEASE_ID
--correction-reason REASON` and receive independent review. `withdraw` requires a
reason, expected version and pointer generation, preserving historical content.

Track A aggregates can enter this same local review lifecycle through the
`track-a-draft` command. It accepts only a complete, full-coverage internal
aggregate, maps the fixed Track A metrics into the allowlisted projection and
creates a draft. The projection discloses that Track A uncertainty intervals are
unavailable. Creating a draft does not review, approve, sign or publish it.

```powershell
pcb-release track-a-draft --aggregate track-a-aggregate.json --store internal-releases.sqlite --subject curator-1 --role curator --request-id track-a-draft-1
```

The public accessor exposes only validated allowlisted projection fields and a
signed manifest. It never bulk exports internal reports, scorecard evidence,
validation receipts or audit events. Signing requires an operator-provided
Ed25519 private PEM; verification uses its trusted public PEM. Keep private keys
outside public artifacts. Public deployment/publication still requires an
authorized target and a reviewed release.

# Contamination controls for generated and parametric tasks

**Status: pilot, local only.** Implemented in `packages/taskgen` (`pcb-taskgen`). It produces
screening decisions and audit records. It does not admit, publish or score a task, and it has not
been calibrated against labelled contaminated pairs. Thresholds are pilot defaults.

Written for: PolyCodeBench maintainers and the task curators who review generated candidates.

## What the sources establish

Checked on 2026-10-08. "Read" means the page or paper text was fetched. "Snippet" means only a search
summary was seen, so the claim is unverified.

| Source | What it supports | Confidence |
| --- | --- | --- |
| [Vals.ai methodology](https://vals.ai/methodology) (read) | Keep the test set private, because test-set leakage into training data is a stated concern. Publish a small public validation set. Report scores only from the private test set. Run every model through one in-house harness. Says the private validation set is "correlated with" the test set, but does not describe how that was tested. Does not describe a refresh cadence or expert review. | Verified for its stated policy. Its validation method is undisclosed. |
| [BIG-bench](https://github.com/google/BIG-bench) README (read, Google) | Every task file carries a canary GUID and a do-not-train sentence, so web-crawl filters can drop it. | Verified that the practice exists. Effectiveness is unverified: blog posts report models reproducing the GUID, and they are not peer-reviewed. |
| [LiveCodeBench, arXiv 2403.07974](https://arxiv.org/html/2403.07974) (read) | Filter by contest date against each model's training cutoff. Scores fall sharply on problems released after the cutoff. Input generators plus verified reference solutions validate tests. | Verified. |
| PaLM, arXiv 2204.02311 (Google; snippet only) | A task is "contaminated" if at least 70% of its 8-grams occur in the pretraining corpus. Report clean and contaminated subsets. | Unverified: the primary PDF could not be parsed. A conflicting 15-gram figure for PaLM 2 is also unverified. |
| GSM1k and GSM-Symbolic (snippet only) | Fresh human-written sets, and template-based variants with changed numbers, expose memorised scores. | Unverified beyond the summaries. |
| Gemini technical reports (Google) | No contamination method was verified in the text that could be read. | No claim is made from them. |

Conclusion from the reading: the verified Google contribution is the canary GUID. The 8-gram
threshold is a secondary report of Google's method and needs the primary source before it is relied
on. The strongest verified practice is LiveCodeBench-style date filtering plus Vals-style private
held-out sets, with per-model exposure accounting added here.

## The design

The generator is untrusted. An AI model proposes text. Deterministic gates decide what enters
executable admission.

1. **Request** (`pcb-taskgen request`). A reviewer writes a `FamilySpec` (skill, language,
   constraints). The request text forbids reproducing known problems and fixes the reply shape. No
   model is called by this code.
2. **Parse** (`pcb-taskgen parse`). The reply must be strict JSON with exactly three string fields.
   Duplicate keys, extra keys, prose around the array and oversized replies are refused. Candidate
   identifiers are assigned here.
3. **Screen** (`pcb-taskgen screen`), in fixed order:
   - `components`: statement, reference and tests are non-empty and distinct.
   - `generator_approval`: an external generator must be on the approved list.
   - `canary_isolation`: the text carries no other item's canary.
   - `surface_*` and `structural_*`: n-gram overlap against every reference corpus the operator
     supplies, as a union measure and a single-document measure. The structural view replaces
     identifiers and numbers, so renamed copies are caught.
   - A batch is screened in order, and accepted candidates join the index, so copies inside one
     batch are caught.
4. **Exposure** (`ExposureLedger`). Every audience a task text reaches is recorded: the generator,
   each evaluated endpoint, reviewers and public release. `eligibility_for_model` allows a task into a
   post-cutoff subset for one endpoint only if its first external exposure is after that model's
   declared cutoff and the endpoint has not already received it. The rule follows
   `post_cutoff_eligibility` in `polycodebench_services`, which refuses unknown cutoffs and uses
   exposure dates rather than curation dates.
5. **Splits** (`assign_split`, `cluster_by_containment`). Related items form one cluster. By default
   (`split_mode: private_heldout_only`) every generated candidate lands in `private_heldout`, because
   nothing generated has been reviewed. A reviewed family can instead use `keyed_ratios`, where the
   cluster's split comes from an HMAC keyed by a private secret. A candidate related to a registered
   family inherits that family's cluster in either mode.
6. **Parametric rotation** (`ParametricFamily`, `RoundCommitment`). A reviewed template produces
   fresh instances from a round secret. The operator commits to the round secret before the round
   and reveals it afterwards, so anyone can verify that instances were not chosen after seeing model
   answers. A family whose parameter space is below the operator's minimum is refused, because a
   small space can be memorised or enumerated.
7. **Validation to test agreement** (`rank_agreement_bp`). Spearman correlation between public
   validation and private test scores, computed only for at least three shared models.

Every decision is recorded with a canonical digest of the policy and of the reference corpus, so a
report can be checked against the exact inputs it used.

## Pilot calibration (2026-10-08)

`scripts/taskgen_pack_overlap.py` indexes one authored pack and screens another against it. Those
are distinct problems, so the numbers are background overlap. Each probe pair has one reference
document, so the union and single-document measures coincide.

Values are basis points (10000 = 100%).

| Corpus | Pairs | Surface, median / p90 / max | Structural, median / p90 / max |
| --- | --- | --- | --- |
| `taskpacks/` (7 packs) | 42 | 0 / 541 / 4848 | 514 / 1571 / 5583 |
| `.protected/taskpacks/` (24 packs) | 552 | 24 / 237 / 852 | 242 / 1311 / 2769 |

Reading these with the pilot thresholds (union 7000, document 5000, link 3000, basis points):

- No distinct pair reaches the union threshold in either corpus.
- One structural pair in `taskpacks/` reaches the document threshold: `qa/py-configkit-qa-v1` against
  `repo-tasks/ini-interpolate` at 5583. Both are Python configuration-parsing problems, so this is
  plausibly real similarity and should be reviewed by a person rather than read as a false alarm.
- Structural overlap is consistently higher than surface overlap, as expected for a view that
  discards identifiers. Its median for distinct problems is about 5% in `taskpacks/` and about 2.4%
  in `.protected/taskpacks/`.

These numbers show the thresholds sit above background noise for this corpus. They do not show that
the thresholds catch contamination. That needs labelled known-copy pairs (verbatim, renamed, and
paraphrased), which do not yet exist in this repository.

## What this does not prove

- **Unseen by pretraining is not established.** Overlap only finds copies that exist in an operator's
  reference corpus. A problem a model saw on the public web and that is absent from the corpus passes.
- **The exposure ledger records what we sent, not what a provider keeps.** A recipient that trains on
  inputs is recorded as exposed, but the ledger cannot tell whether a provider retains data.
- **Hosted generators may echo what they memorised.** Treat an external generator as an exposure
  and as a possible source of recall. Its output must pass the screen like any other candidate.
- **Canaries are a detection hook.** BIG-bench's own uncertainty applies here. A canary does not
  stop training on a task.
- **Thresholds are uncalibrated.** Only the PaLM 8-gram rule has a cited origin, and that citation is
  unverified.

## Operating procedure

```text
pcb-taskgen new-secret --output <private>/canary.secret      # once; never commit the file
pcb-taskgen new-secret --output <private>/split.secret       # once; never commit the file
pcb-taskgen request --family family.json --count 10 --output request.txt
# an approved generator produces reply.txt out of band; this repo does not call it
pcb-taskgen parse --family family.json --reply reply.txt --generator-id <id> --output candidates.json
pcb-taskgen screen --candidates candidates.json --policy config/task-generation/screening-policy-v1.json \
  --reference-root taskpacks=taskpacks --reference-root protected=.protected/taskpacks \
  --canary-secret-file <private>/canary.secret --split-secret-file <private>/split.secret \
  --output reports.json
```

Accepted candidates are only `ready_for_executable_admission`. Each still needs a task package
(manifest, visible and hidden material, and reference, faulty and alternative fixtures), then
`pcb task validate` in the sandbox, then human curation and a freeze. Generated items stay in
`private_heldout` under the shipped policy. Promoting one to a public split is a manual procedure:
the reviewer must record a public exposure event in the ledger. No tooling does this yet.

## Approved external generators

- `glm-5.3-flash` is approved in `config/task-generation/screening-policy-v1.json` by owner decision on 2026-10-08. The owner states that the service keeps no data and does not train on it. That statement is the owner's, not verified here. Before any batch runs, the provider's data-retention terms must be recorded as the generator's `review_reference`.
- Approval in the policy does not make a call possible. No GLM provider configuration exists in the model gateway, no endpoint is approved, and no API key is configured. A batch also needs a spend cap and a price snapshot.

## Not done yet

- Build task packages from accepted candidates so they can enter `pcb task validate`.
- Calibrate the thresholds on labelled verbatim, renamed and paraphrased pairs.
- Probe models for canary reproduction and for completion of masked lines. This needs model calls
  and owner approval.
- Connect the exposure ledger to the model gateway so that evaluated endpoints are recorded
  automatically.
- Write the rotation schedule and the operating runbook for round commitments.

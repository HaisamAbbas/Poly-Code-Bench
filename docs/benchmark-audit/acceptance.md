# Benchmark audit traceability and acceptance ledger

The addendum §§3, 25–28 remains authoritative for exact requirement text and ticket/gate DoDs. This ledger assigns owners and records evidence without modifying those contracts.

At Prompt87, BWP-01 and BWP-02 are complete; BWP-03 through BWP-05 are partial pending live database/source/resource evidence. Capability requirements remain partial or pending until their assigned import, evidence, lifecycle and live gates close.

## Requirements (BREQ)

| ID | Primary prompts | Acceptance gates | Status | Evidence owner |
|---|---|---|---|
| BREQ-01 | 84–86 | BX-02,06–08 | partial | reports/prompt-86.md; exact adapters and membership are implemented, but live imports and PostgreSQL retry checks are pending |
| BREQ-02 | 84,86,104 | BX-02,59 | partial | config/benchmark-audit/registry-v1.yaml; broader support in Prompt104 |
| BREQ-03 | 87 | BX-09–11 | partial | reports/prompt-87.md; deterministic exact/lexical views implemented, parser/semantic/entity features remain blocked |
| BREQ-04 | 84,88 | BX-12–14 | pending | see prompt 84 report |
| BREQ-05 | 88–89 | BX-13,15–17 | pending | see prompt 88 report |
| BREQ-06 | 90 | BX-18–21 | pending | see prompt 90 report |
| BREQ-07 | 91 | BX-22–23 | pending | see prompt 91 report |
| BREQ-08 | 91,94 | BX-24,31–33 | pending | see prompt 91 report |
| BREQ-09 | 92 | BX-25–26 | pending | see prompt 92 report |
| BREQ-10 | 92–93 | BX-27–28 | pending | see prompt 92 report |
| BREQ-11 | 93,102 | BX-28–29,54 | pending | see prompt 93 report |
| BREQ-12 | 93,102 | BX-30,54 | pending | see prompt 93 report |
| BREQ-13 | 94,101 | BX-31–33,53 | pending | see prompt 94 report |
| BREQ-14 | 85,94 | BX-05,32–33 | partial | `run.purpose`, audit metadata and caps are implemented; dispatch authorization and frozen diagnostic protocols remain gated; see prompt 85 report |
| BREQ-15 | 95 | BX-34 | pending | see prompt 95 report |
| BREQ-16 | 95,102 | BX-35,54 | pending | see prompt 95 report |
| BREQ-17 | 95,102 | BX-36,54 | pending | see prompt 95 report |
| BREQ-18 | 86,95,97 | BX-08,37,42 | partial | reports/prompt-86.md; source versions stay immutable, derived-version semantics and score comparability remain later work |
| BREQ-19 | 96,102 | BX-38–40,55 | pending | see prompt 96 report |
| BREQ-20 | 90,96,100 | BX-21,39–40,49 | pending | see prompt 90 report |
| BREQ-21 | 97,99 | BX-41–43,46 | pending | see prompt 97 report |
| BREQ-22 | 98–99 | BX-44–47 | pending | see prompt 98 report |
| BREQ-23 | 100 | BX-48–50 | pending | see prompt 100 report |
| BREQ-24 | 86–90,93,98–99,103 | BX-07,11,14,20,28–30,44,47,58 | partial | reports/prompt-86.md and reports/prompt-87.md; storage visibility and private artifact guards exist, but worker isolation, tenant and live remote controls remain pending |
| BREQ-25 | 85,89,96,103 | BX-04–05,16–17,38,56–57 | partial | six exclusive queue FKs, CAS enqueue and fencing reuse are implemented; live migration/claim/recovery verification is pending; see prompt 85 report |
| BREQ-26 | 94,101 | BX-31–33,52–53 | pending | see prompt 94 report |
| BREQ-27 | 101–102 | BX-51–55 | pending | see prompt 101 report |
| BREQ-28 | 85,97,103,105 | BX-05,42,58–59 | partial | native scoring paths were not changed; diagnostic exclusion from recommendation surfaces is unverified because no such query exists in this checkout; see prompt 85 report |
| BREQ-29 | 84,88–89,101 | BX-12–17,51 | partial | packages/services/.../benchmark_audit_catalog.py; corpus indexing pending |
| BREQ-30 | 103,106 | BX-56–58,60 | pending | see prompt 103 report |
| BREQ-31 | 83,104–106 | BX-01,59–60 | partial | acceptance.md and reports/prompt-83.md; remaining prompts pending |
| BREQ-32 | 83 and every prompt,106 | BX-01,60 plus all96 ticket DoDs | partial | acceptance.md and reports/prompt-83.md; remaining prompts pending |

## Work packages (BWP)

| ID | Prompt | Status | Evidence |
|---|---|---|---|
| BWP-01 | 83 | complete | reports/prompt-83.md |
| BWP-02 | 84 | complete | reports/prompt-84.md |
| BWP-03 | 85 | partial | reports/prompt-85.md; relational migration/queue and diagnostics surfaces need live integration |
| BWP-04 | 86 | partial | reports/prompt-86.md; fixture-only adapters, no approved bytes/rights, DB integration or parser worker isolation |
| BWP-05 | 87 | partial | reports/prompt-87.md; exact/lexical fingerprints implemented; approved parser/model config, live persistence and semantic coverage pending |
| BWP-06 | 88 | pending | prompt report |
| BWP-07 | 89 | pending | prompt report |
| BWP-08 | 90 | pending | prompt report |
| BWP-09 | 91 | pending | prompt report |
| BWP-10 | 92 | pending | prompt report |
| BWP-11 | 93 | pending | prompt report |
| BWP-12 | 94 | pending | prompt report |
| BWP-13 | 95 | pending | prompt report |
| BWP-14 | 96 | pending | prompt report |
| BWP-15 | 97 | pending | prompt report |
| BWP-16 | 98 | pending | prompt report |
| BWP-17 | 99 | pending | prompt report |
| BWP-18 | 100 | pending | prompt report |
| BWP-19 | 101 | pending | prompt report |
| BWP-20 | 102 | pending | prompt report |
| BWP-21 | 103 | pending | prompt report |
| BWP-22 | 104 | pending | prompt report |
| BWP-23 | 105 | pending | prompt report |
| BWP-24 | 106 | pending | prompt report |

## Engineering tickets (BAT)

Each prompt owns four tickets (A–D). The addendum retains each exact DoD.

| ID | Prompt | Status | Evidence owner |
|---|---|---|---|
| BAT-01-A | 83 | complete | prompt-83.md |
| BAT-01-B | 83 | complete | prompt-83.md |
| BAT-01-C | 83 | complete | prompt-83.md |
| BAT-01-D | 83 | complete | prompt-83.md |
| BAT-02-A | 84 | complete | reports/prompt-84.md |
| BAT-02-B | 84 | complete | reports/prompt-84.md |
| BAT-02-C | 84 | complete | reports/prompt-84.md |
| BAT-02-D | 84 | complete | reports/prompt-84.md |
| BAT-03-A | 85 | complete | reports/prompt-85.md; 18 Python schemas and shared Python/TypeScript canonical vectors |
| BAT-03-B | 85 | partial | reports/prompt-85.md; migration renders, but no old-row PostgreSQL upgrade/rollback run |
| BAT-03-C | 85 | partial | reports/prompt-85.md; atomic enqueue and authorization gates implemented, live queue/fence/drain checks pending |
| BAT-03-D | 85 | partial | reports/prompt-85.md; attempt-scoped diagnostic metadata/caps implemented, dispatch approval and recommendation surface absent |
| BAT-04-A | 86 | partial | reports/prompt-86.md; frozen plans and semantic retry checks, DB retry not exercised |
| BAT-04-B | 86 | partial | reports/prompt-86.md; components/dates/lineage adapter-tested; no approved source import |
| BAT-04-C | 86 | partial | reports/prompt-86.md; bounded rejection paths tested, scoped parser worker isolation absent |
| BAT-04-D | 86 | partial | reports/prompt-86.md; denominator/self-source controls tested with synthetic snapshots only |
| BAT-05-A | 87 | partial | reports/prompt-87.md; exact/normalized/shingle separation tested; benchmark item corpus not available |
| BAT-05-B | 87 | partial | reports/prompt-87.md; local exact/lexical config pinned; Python/Java AST and embedding config unavailable |
| BAT-05-C | 87 | partial | reports/prompt-87.md; entity/answer/reasoning extractors remain explicitly blocked |
| BAT-05-D | 87 | partial | reports/prompt-87.md; append-only private artifact storage is implemented but DB/index rebuild is unverified |
| BAT-06-A | 88 | pending | prompt report |
| BAT-06-B | 88 | pending | prompt report |
| BAT-06-C | 88 | pending | prompt report |
| BAT-06-D | 88 | pending | prompt report |
| BAT-07-A | 89 | pending | prompt report |
| BAT-07-B | 89 | pending | prompt report |
| BAT-07-C | 89 | pending | prompt report |
| BAT-07-D | 89 | pending | prompt report |
| BAT-08-A | 90 | pending | prompt report |
| BAT-08-B | 90 | pending | prompt report |
| BAT-08-C | 90 | pending | prompt report |
| BAT-08-D | 90 | pending | prompt report |
| BAT-09-A | 91 | pending | prompt report |
| BAT-09-B | 91 | pending | prompt report |
| BAT-09-C | 91 | pending | prompt report |
| BAT-09-D | 91 | pending | prompt report |
| BAT-10-A | 92 | pending | prompt report |
| BAT-10-B | 92 | pending | prompt report |
| BAT-10-C | 92 | pending | prompt report |
| BAT-10-D | 92 | pending | prompt report |
| BAT-11-A | 93 | pending | prompt report |
| BAT-11-B | 93 | pending | prompt report |
| BAT-11-C | 93 | pending | prompt report |
| BAT-11-D | 93 | pending | prompt report |
| BAT-12-A | 94 | pending | prompt report |
| BAT-12-B | 94 | pending | prompt report |
| BAT-12-C | 94 | pending | prompt report |
| BAT-12-D | 94 | pending | prompt report |
| BAT-13-A | 95 | pending | prompt report |
| BAT-13-B | 95 | pending | prompt report |
| BAT-13-C | 95 | pending | prompt report |
| BAT-13-D | 95 | pending | prompt report |
| BAT-14-A | 96 | pending | prompt report |
| BAT-14-B | 96 | pending | prompt report |
| BAT-14-C | 96 | pending | prompt report |
| BAT-14-D | 96 | pending | prompt report |
| BAT-15-A | 97 | pending | prompt report |
| BAT-15-B | 97 | pending | prompt report |
| BAT-15-C | 97 | pending | prompt report |
| BAT-15-D | 97 | pending | prompt report |
| BAT-16-A | 98 | pending | prompt report |
| BAT-16-B | 98 | pending | prompt report |
| BAT-16-C | 98 | pending | prompt report |
| BAT-16-D | 98 | pending | prompt report |
| BAT-17-A | 99 | pending | prompt report |
| BAT-17-B | 99 | pending | prompt report |
| BAT-17-C | 99 | pending | prompt report |
| BAT-17-D | 99 | pending | prompt report |
| BAT-18-A | 100 | pending | prompt report |
| BAT-18-B | 100 | pending | prompt report |
| BAT-18-C | 100 | pending | prompt report |
| BAT-18-D | 100 | pending | prompt report |
| BAT-19-A | 101 | pending | prompt report |
| BAT-19-B | 101 | pending | prompt report |
| BAT-19-C | 101 | pending | prompt report |
| BAT-19-D | 101 | pending | prompt report |
| BAT-20-A | 102 | pending | prompt report |
| BAT-20-B | 102 | pending | prompt report |
| BAT-20-C | 102 | pending | prompt report |
| BAT-20-D | 102 | pending | prompt report |
| BAT-21-A | 103 | pending | prompt report |
| BAT-21-B | 103 | pending | prompt report |
| BAT-21-C | 103 | pending | prompt report |
| BAT-21-D | 103 | pending | prompt report |
| BAT-22-A | 104 | pending | prompt report |
| BAT-22-B | 104 | pending | prompt report |
| BAT-22-C | 104 | pending | prompt report |
| BAT-22-D | 104 | pending | prompt report |
| BAT-23-A | 105 | pending | prompt report |
| BAT-23-B | 105 | pending | prompt report |
| BAT-23-C | 105 | pending | prompt report |
| BAT-23-D | 105 | pending | prompt report |
| BAT-24-A | 106 | pending | prompt report |
| BAT-24-B | 106 | pending | prompt report |
| BAT-24-C | 106 | pending | prompt report |
| BAT-24-D | 106 | pending | prompt report |

## End-to-end gates (BX)

| ID | Owner prompt(s) | Status | Evidence |
|---|---|---|---|
| BX-01 | 83 | complete | prompt-83.md |
| BX-02 | 84 | complete | config/benchmark-audit/ and reports/prompt-84.md |
| BX-03 | 85 | complete | reports/prompt-85.md; 18 shared canonical vectors, strict Python validation and TS byte/digest agreement |
| BX-04 | 85 | partial | reports/prompt-85.md; six scopes and atomic/fenced queue code added, but old-row migration and recovery need PostgreSQL verification |
| BX-05 | 85 | partial | reports/prompt-85.md; old purposes remain NULL and diagnostic metadata is explicit, but recommendation exclusion and dispatch approval are not available to verify |
| BX-06 | 86 | partial | reports/prompt-86.md; pinned plans and deterministic sample tested; live source and DB retry verification pending |
| BX-07 | 86 | partial | reports/prompt-86.md; local safety/blocked cases tested; worker isolation and approved input absent |
| BX-08 | 86 | partial | reports/prompt-86.md; self-source, exposure and missingness controls fixture-tested; no real source/lineage evidence |
| BX-09 | 87 | partial | reports/prompt-87.md; method-level distinctions tested, imported benchmark coverage unavailable |
| BX-10 | 87 | partial | reports/prompt-87.md; exact/lexical config pinned; parser and embedding capabilities remain blocked |
| BX-11 | 87 | partial | reports/prompt-87.md; no unsupported feature is zero-filled; entity/answer features and live private index checks pending |
| BX-12 | 88 | pending | criterion in addendum §26; prompt report |
| BX-13 | 88 | pending | criterion in addendum §26; prompt report |
| BX-14 | 88 | pending | criterion in addendum §26; prompt report |
| BX-15 | 89 | pending | criterion in addendum §26; prompt report |
| BX-16 | 89 | pending | criterion in addendum §26; prompt report |
| BX-17 | 89 | pending | criterion in addendum §26; prompt report |
| BX-18 | 90 | pending | criterion in addendum §26; prompt report |
| BX-19 | 90 | pending | criterion in addendum §26; prompt report |
| BX-20 | 90 | pending | criterion in addendum §26; prompt report |
| BX-21 | 90 | pending | criterion in addendum §26; prompt report |
| BX-22 | 91 | pending | criterion in addendum §26; prompt report |
| BX-23 | 91 | pending | criterion in addendum §26; prompt report |
| BX-24 | 91 | pending | criterion in addendum §26; prompt report |
| BX-25 | 92 | pending | criterion in addendum §26; prompt report |
| BX-26 | 92 | pending | criterion in addendum §26; prompt report |
| BX-27 | 92 | pending | criterion in addendum §26; prompt report |
| BX-28 | 93 | pending | criterion in addendum §26; prompt report |
| BX-29 | 93 | pending | criterion in addendum §26; prompt report |
| BX-30 | 93 | pending | criterion in addendum §26; prompt report |
| BX-31 | 94 | pending | criterion in addendum §26; prompt report |
| BX-32 | 94 | pending | criterion in addendum §26; prompt report |
| BX-33 | 94 | pending | criterion in addendum §26; prompt report |
| BX-34 | 95 | pending | criterion in addendum §26; prompt report |
| BX-35 | 95 | pending | criterion in addendum §26; prompt report |
| BX-36 | 95 | pending | criterion in addendum §26; prompt report |
| BX-37 | 95 | pending | criterion in addendum §26; prompt report |
| BX-38 | 96 | pending | criterion in addendum §26; prompt report |
| BX-39 | 96 | pending | criterion in addendum §26; prompt report |
| BX-40 | 96 | pending | criterion in addendum §26; prompt report |
| BX-41 | 97 | pending | criterion in addendum §26; prompt report |
| BX-42 | 97 | pending | criterion in addendum §26; prompt report |
| BX-43 | 97 | pending | criterion in addendum §26; prompt report |
| BX-44 | 98 | pending | criterion in addendum §26; prompt report |
| BX-45 | 98 | pending | criterion in addendum §26; prompt report |
| BX-46 | 99 | pending | criterion in addendum §26; prompt report |
| BX-47 | 99 | pending | criterion in addendum §26; prompt report |
| BX-48 | 100 | pending | criterion in addendum §26; prompt report |
| BX-49 | 100 | pending | criterion in addendum §26; prompt report |
| BX-50 | 100 | pending | criterion in addendum §26; prompt report |
| BX-51 | 101 | pending | criterion in addendum §26; prompt report |
| BX-52 | 101 | pending | criterion in addendum §26; prompt report |
| BX-53 | 101 | pending | criterion in addendum §26; prompt report |
| BX-54 | 102 | pending | criterion in addendum §26; prompt report |
| BX-55 | 102 | pending | criterion in addendum §26; prompt report |
| BX-56 | 103 | pending | criterion in addendum §26; prompt report |
| BX-57 | 103 | pending | criterion in addendum §26; prompt report |
| BX-58 | 103 | pending | criterion in addendum §26; prompt report |
| BX-59 | 104, 105 | pending | criterion in addendum §26; prompt report |
| BX-60 | 106 | pending | criterion in addendum §26; prompt report |

## Phase ownership

| Phase | Prompts | Status | Report |
|---|---|---|---|
| BA0 | 83–85 | partial | `reports/phase-BA0.md` (created when Prompt85 closes) |
| BA1 | 86–88 | pending | `reports/phase-BA1.md` |
| BA2 | 89–91 | pending | `reports/phase-BA2.md` |
| BA3 | 92–94 | pending | `reports/phase-BA3.md` |
| BA4 | 95–97 | pending | `reports/phase-BA4.md` |
| BA5 | 98–100 | pending | `reports/phase-BA5.md` |
| BA6 | 101–103 | pending | `reports/phase-BA6.md` |
| BA7 | 104–106 | pending | `reports/phase-BA7.md` |

Update each status only when its evidence exists. A phase is partial if mandatory live, source, human or modality evidence remains unavailable.

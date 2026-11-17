# Exploratory study results

Updated November 17, 2026. The scale evaluation now includes the 1,000-question
SQL/SQLP comparison across Luna, Sol, and Astra, plus a matched NatSQL follow-up.
The broader public SQL challenge is complete for Luna/Sol and partial for Astra.
The earlier studies below retain their original
twelve Chinook development questions and 32 related constructed templates.
Questions, shared templates, repeated outputs, and snapshots are counted
separately. Human interpretation review remains pending; the thirty reserved
Chinook questions remain unrun.

## Three-model follow-up and matched NatSQL evaluation (November 17)

The complete **1,000-question paired stress workload** has now run on all three
models used previously. Sol and Astra use byte-identical frozen data, guides,
compiler, scoring runtime, question groups, and settings from the initial Luna
study; only the request's model identifier and its hash change. All conditions
use high reasoning, batches of four, and a 16,384-output-token cap.

| Model | SQL matches / 1,000 | SQLP matches / 1,000 | SQLP total-token change vs SQL |
| --- | ---: | ---: | ---: |
| Luna | 946 | 990 | -10.70% |
| Sol | 999 | 1,000 | -7.11% |
| Astra | 1,000 | 1,000 | -8.27% |

These supported-condition usage totals remain exact: every original response
was retained. SQL/SQLP totals are 946,742/845,484 tokens for Luna,
884,554/821,636 for Sol, and 886,339/813,062 for Astra. The same 1,000 constructed
questions recur across models; this is 3,000 paired question/model observations,
not 3,000 independent questions. No repair feedback was given to a model.

**NatSQL is now an actual model comparison on the expanded data**, rather than
only the earlier twelve-question study. An outcome-independent structural filter
selects 188 questions in 47 complete original batches, with no repeated physical
table bindings. All three models generated NatSQL for every selected question:
141 retained responses and 564 outputs. SQL/SQLP counterparts reuse those exact
questions, public request bodies, batch groups, model settings, and data.

| Model | SQL / 188 | SQLP / 188 | NatSQL / 188 | NatSQL on the 184 reference-compatible cases |
| --- | ---: | ---: | ---: | ---: |
| Luna | 188 | 188 | 171 | 171/184 |
| Sol | 188 | 188 | 184 | 184/184 |
| Astra | 188 | 188 | 183 | 183/184 |

Four prepared NatSQL encodings return no SQL under the pinned converter's
five-table network limit. They remain in the 188-target results and are flagged
separately; they must not be presented as four model reasoning mistakes. The
remaining misses are wrong inferred relationships or explicit encoding errors.
The preflight validates reference encodings, not every possible NatSQL encoding:
these failures do not prove general language unrepresentability. Reference
coverage was fixed before the new NatSQL model calls.

A further **176-question supplement**, selected as 44 complete original batches
whose reference encodings pass, permits matched whole-batch token accounting.
It contains mainly shallow paths. SQLP uses 1.14% fewer tokens than SQL for Luna,
but **1.15% more for Sol and 0.31% more for Astra**. This mixed result remains
reported alongside the longer-path 1,000-case result. Astra NatSQL uses 150,500
tokens on this supplement, compared with 138,438 for SQL and 138,870 for SQLP.
Luna/Sol NatSQL have known retained-response subtotals of 165,643/149,446 tokens,
but exact whole-API totals are unknown because each includes an original lost
transport attempt. Do not treat those subtotals as exact workflow-token totals.

The original converter setup could not complete on the larger full catalogs.
The new follow-up therefore declares an **indexed, cached, literal-preserving
adapter profile**, separately from the earlier original profile. It retains the
pinned upstream files and all table/column/key facts. An indexed duplicate
predicate runs the official enlargement algorithm without discarding alternative
join networks; control fixtures verify exact ordered network-list equality.
Prepared schemas are query-independent, hash-bound caches loaded afresh for each
query. Valid SQL string tokens are transported through the upstream tokenizer
and restored with their original values, fixing apostrophe corruption without
query repair or gold access. The original profile remains the default. See
[the adapter details and limits](NATSQL.md).

All 564 NatSQL outputs passed raw-response/query/converter/cache/literal binding
checks. All 554 successfully converted queries were re-executed on primary and
alternate data. NatSQL match counts remain 171/184/183 on the alternate data;
no primary match becomes a mismatch. Reference preparation includes 376 checks,
with four conversion rejections on each snapshot and one parallel timeout whose
unchanged SQL passes a documented serial check in 0.002 seconds.

The broader **1,034-question public SQL challenge** is complete for Luna
(828/1,032 executable references) and Sol (862/1,032). Astra is currently partial:
429 public outputs retained, 365 reference matches. This partial result must not
be compared as though it covered the same full question set. Completing Astra's
remaining 605 public questions awaits the budget-scope clarification requested
from the user; the full 1,000-case paired and 188-case NatSQL comparisons above
are already complete.

Five original API sends lost their responses: one Sol public batch, two Astra
public batches, and one NatSQL batch each for Luna and Sol. Each received one
explicit, identical-payload transport recovery under a separate paid label,
without query feedback. Original attempts, unknown usage, and their full
**$18.783549** spending reserves remain recorded. Consequently the overall
API-token total is unknown, even though retained-response subtotals and the
unaffected paired-condition totals are known. This does not erase or relabel an
uncertain first attempt as a successful first attempt.

All financial corrections and recoveries preserve the original lifetime $100
guard. Earlier conservative reservations were reconciled only against exact
retained response and submitted-request hashes, with immutable backups and
per-call audits. The user's reported external account balance is separate from this
local guard; its scope has not been silently reset or increased.
At the pause, its conservative held total is $91.925778, leaving $8.074222;
the next request cannot obtain its required worst-case reservation. This is
not a claim that the user's external account balance is $8.07. Deferred request
`request-00614` has zero paid sends; its journal and budget-deferral evidence are
preserved. Known received-response charge estimates total $65.251681 across the
historical ledger, excluding unknown original sends and any account adjustments.

Engineering checks pass with the pinned integrations configured: **502 Python
tests, 1,200 differential cases, 61 Node checks**, and 100,000 compiler mutations
under address/undefined-behavior sanitizers. Leak checking is unavailable under
the environment's ptrace restrictions and was explicitly disabled. A numeric
row-matching edge case was fixed and checked against exhaustive small-case
matching; no retained Luna/Sol or current Astra public score changes. Human
interpretation review and the earlier public-reference ambiguities remain
limitations. These results compare complete interfaces on controlled tasks,
not intrinsic language superiority or cross-provider generalization.

Artifacts are under `resources/experiments/scale-study-2026-11-16/multi-model/`:
`model-studies/`, `natsql-controls/`, the reference/cache/literal audits,
`quality-audit.json`, and `natsql-failure-review.json`. Frozen originals are
preserved. Analysis overlays distinguish known received-response usage from
unknown original sends; prepared checks and model results remain separate.

## Expanded evaluation: 2,034 questions (November 16)

A fresh **768-call** run generates **3,034 query outputs** for **2,034 distinct
English questions** across 24 primary databases with 599,764 rows. It uses
`gpt-6-luna`, high reasoning, one stateless attempt, batches of four, and a
16,384-output-token cap. There are no repairs or model retries. Exact public
requests, V2 SQL/SQLP guides, data, compiler, private answer plans, Python runtime,
raw responses, journals, reports, and accounting are frozen or retained under
`resources/experiments/scale-study-2026-11-16/study/`.

The **matched comparison** uses 1,000 new supported questions over four larger
synthetic graphs. Both languages receive the same questions and relationship
facts, with corresponding guides. Results are:

| Condition | Reference answer matches | Total reported input + output tokens |
| --- | ---: | ---: |
| SQL | 946/1,000 (94.6%) | 946,742 |
| SQLP | 990/1,000 (99.0%) | 845,484 |

SQLP uses **10.70% fewer total tokens** in this protocol, including guides,
schema, failures, and reported reasoning. Input/output totals are respectively
725,621/221,121 for SQL and 733,181/112,303 for SQLP. Guide overhead is included;
this does not isolate syntax. Of the 1,000 pairs, 939 match in both conditions,
51 match only in SQLP, seven only in SQL, and three in neither.

The harder paths reveal errors that the original small workload rarely did:

| Maximum path depth | Questions | SQL matches | SQLP matches |
| --- | ---: | ---: | ---: |
| 1 | 200 | 200 | 199 |
| 2 | 160 | 159 | 160 |
| 3 | 160 | 158 | 158 |
| 4 | 160 | 150 | 158 |
| 6 | 160 | 142 | 159 |
| 8 | 160 | 137 | 156 |

SQL has eight SQLite errors, one row-limit failure, and 45 executed wrong
answers. SQLP has ten executed wrong answers. Reviewed examples include
undefined SQL aliases, omitted filters, extra relationship hops, and wrong
terminal fields. Every SQLP mismatch has an explicit path, field, or literal
difference from its authored answer plan. Queries with several branches need
up to 24 distinct joins.

These are controlled stress cases, with 955 literal-normalized templates and
602 structures after abstracting table and role names. Four different graph
topologies share one synthetic data generator and generic field distributions.
The deliberately long possessive English chains are artificial; this is not a
production-language generalization claim. All 100 labelled empty controls
match in both languages. Excluding them gives SQL **846/900** and SQLP
**890/900**. Reference queries and independent Python traversal share authored
interpretations and still need independent human review. Depth changes question
length and graph structure, so the depth table is descriptive.

The **separate public SQL challenge** uses all 1,034 published Spider dev
questions over 20 databases. It includes aggregates, grouping, nested queries,
and set operators. SQL matches **828/1,032 executable reference answers (80.2%)**;
two published reference queries encounter invalid UTF-8 and remain unscorable.
There are 200 executed mismatches and four generated-query text-decoding errors.
All 1,034 attempts remain in usage totals: **597,446 tokens**.

This is a duplicate-sensitive, single-snapshot reference-execution metric with
column-order checks, outer ORDER BY handling, and documented numeric tolerance;
it is **not official Spider leaderboard or test-suite accuracy**. Some mismatches
are real query mistakes; others reflect ambiguous reference semantics, output
column order, zero-count inclusion, tied LIMIT results, or case-sensitive value
grounding without cell samples. They are not all established reasoning errors.
Of the 204 scored mismatches, 40 match after only permuting projected columns;
that supplementary diagnostic does not replace the frozen primary scores.
The 1,034 questions contain 564 distinct database/gold-query pairs and can occur
in model training data. Static analysis places 849 questions outside SQLP's
current fragment; 185 syntactic candidates remain unverified. No public SQLP
accuracy or cross-language ranking is reported for this challenge. See
[the workload and scoring details](WORKLOAD.md).

Before model generation, all **4,000 prepared SQL/SQLP executions** match Python
traversal on primary and alternate synthetic snapshots. Alternate data changes
836 expected answer bags. Saved generated queries retain 946 SQL and 990 SQLP
matches on the alternate data; no primary match becomes a mismatch there.
Passing these snapshots remains diagnostic rather than an equivalence proof.

An outcome-independent structural filter also preserves 188 questions in 47
complete original batches for a possible NatSQL follow-up. No NatSQL model
requests were sent: all eight converter preflight checks time out on the full
catalogs. A serial trace isolates the bottleneck to the pinned official
`enlarge_network` function, which exceeds 90 seconds after earlier setup takes
0.307 seconds. This exceeds the adapter's 60-second maximum before query parsing.
Eligibility, verified-source diagnostics, and setup status are retained beside
the main study. This is a converter setup limit, not NatSQL model accuracy;
no relationship facts were pruned to force a comparison.

All **768 responses report known usage**, totalling **2,389,672 tokens**.
Final validation checks exact frozen hashes, public-only prompts, all task
coverage, raw response/report/ledger bindings, and token totals. It re-executes
all 3,032 scoreable outputs and 2,000 supported outputs on alternate data;
`validation.json`, `failure-review.json`, and `public-failure-review.json`
retain the evidence. A post-freeze validator correction uses the declared public
row limit for public re-execution; both validator hashes are recorded. No main
requests, queries, scores, or frozen files changed.
The new estimated API charge is **$0.464549**, using the existing rate table;
this is not an invoice. The shared cumulative ledger remains below its original
$100 ceiling (conservative total $91.012978 including prior reserves).
Results and failure reviews stay separate from the historical studies below.
These single-model, constructed and public-dev observations broaden coverage;
they do not establish independent human gold, contamination-free accuracy,
cross-provider generalization, or a universal language advantage.

## Fresh three-language batching follow-up (November 13)

A fresh **432-call** experiment measures SQL, SQLP, and NatSQL at JSON batch sizes
1, 4, and 12. It reruns all three languages with the same twelve development
questions, three models, and three repetitions: **972 scored query outputs**,
not 972 independent questions. Corrected V2 SQL/SQLP guides and the generic V3
NatSQL clarification are frozen across batch sizes. Public facts, task groups,
JSON output contract, high reasoning, 8,192-output-token request cap, and
five-second SQLite execution limit are matched. There are no repairs.

Across all batch conditions, answer matches are **SQL 322/324, SQLP 324/324,
NatSQL 317/324**. Sol and Astra match every answer in all three languages.
Luna's seven NatSQL failures comprise three conversion rejections, three wrong
answers, and one execution timeout; its two SQL failures are a row-limit failure
and an undefined alias. All failures remain in usage totals.

At batch size 12:

| Model | SQLP token change vs SQL | NatSQL token change vs SQL | SQLP fewer tokens than NatSQL |
| --- | ---: | ---: | ---: |
| Sol | -9.39% | +2.75% | 11.82% |
| Luna | -9.34% | +65.16% | 45.11% |
| Astra | -10.37% | +4.62% | 14.33% |

These are pooled input-plus-output totals, including guides and reported reasoning.
Luna's largest difference is mainly reported reasoning tokens, rather than visible
query length. Guide lengths differ; this compares complete interfaces and does not
isolate syntax. Larger batches share a fixed output budget. Human interpretation
review remains pending; reserved questions remain unrun. The earlier experiments
below retain their own guides, records, and scores.

The estimated new API charge is **$3.72439**. Frozen plans, raw responses, journals,
reports, and independent validation are under
`resources/experiments/three-language-batches-2026-11-13/`. All 27 reports,
432 calls, 972 outputs, hashes, row bags, and chart percentages passed validation.
The presentation token slide uses this fresh experiment, with separate model
panels and a SQL zero baseline; the earlier single-query correctness table remains
separately labeled.

## Corrected SQL / SQLP / NatSQL comparison

Our first NatSQL guide incorrectly omitted native flat column-IN-column syntax.
Its 108 calls remain archived as **superseded calibration**. Their earlier 33/36
matches per model are not a fair basis for claiming SQLP superiority.

A fresh **324-call** comparison freezes corrected guides with two corresponding,
unrelated examples, equivalent schema/relationship facts, high reasoning,
an 8,192-output-token cap, and one stateless attempt with no repairs. Every model
answers the same twelve questions three times in each language:

| Model | SQL matches | SQLP matches | NatSQL matches | Total tokens: SQL / SQLP / NatSQL |
| --- | ---: | ---: | ---: | ---: |
| Sol | 36/36 | 36/36 | 36/36 | 64,864 / 65,069 / 71,160 |
| Luna | 35/36 | 36/36 | 33/36 | 66,256 / 66,609 / 77,997 |
| Astra | 36/36 | 36/36 | 36/36 | 64,638 / 64,847 / 70,750 |

All 108 NatSQL outputs converted. Luna's three remaining failures are execution
timeouts on one repeated task: it generated separate membership blocks and
cross joins. Re-execution with 30 seconds still timed out. Original V1.6
reconstruction of the same 108 saved queries gives identical correctness to
V1.6.1, without new model calls. SQL's retained Luna mismatch uses a wrong
media-type literal. These few errors do not establish a general language ranking.

SQLP uses 0.32–0.53% more total tokens than SQL here. The fixed guides contribute
30 extra input tokens/call for SQLP over SQL, and 138 for NatSQL over SQLP. Those
instruction costs are included, but are not intrinsic constants of the languages.
This compares complete interfaces/guides, not syntax in isolation.

A separate **post-hoc guide-sensitivity run** adds a generic explanation that
flat IN starts a membership subquery rather than replacing every foreign-key
join. It changes only NatSQL's guide, retaining both unrelated examples and the
same facts, models, questions, repetitions, and budgets. Its 108 fresh calls stay
separate from the 324-call comparison; no best-guide result replaces prior data.

With that clarification, Sol/Luna/Astra match **36/36, 35/36, 36/36**, with total
NatSQL usage **73,134 / 81,105 / 73,052 tokens**. All 108 queries convert; one Luna
execution still times out. The guide adds 72 input tokens/call. Luna improves
from 33/36 to 35/36 while total usage increases. This shows guide sensitivity,
not a confirmed language ranking or a reason to discard the earlier result.

## Sharing context changes the token tradeoff

The original fixed-guide JSON experiment uses matched batch sizes 1, 4, and 12. Size 1
controls response format before changing how many questions share one request.
The table is SQLP's percentage change in pooled provider input-plus-output tokens
relative to SQL; negative means fewer SQLP tokens.

| Model | JSON batch 1 | JSON batch 4 | JSON batch 12 |
| --- | ---: | ---: | ---: |
| Sol | +0.62% | −2.17% | −10.71% |
| Luna | +1.76% | −1.19% | −3.12% |
| Astra | +0.88% | −2.74% | −8.26% |

Each language matches 36/36 answers per model at every batch size. Whole-response
usage is counted once; per-question reasoning is not invented. Larger batches
also have less output budget per question under the fixed request cap. These
results use the original guides; do not splice corrected native-guide totals
into this curve. See [the batch protocol](BATCH_PILOT.md).

## Other controls

**Longer paths and wider catalogs:** the narrow constructed catalog has two
tables; the wide variant adds 16 unused tables without changing core data or
answers. SQLP uses more total tokens at tested depths 1 and 2 and fewer at 4 and 6.
Depth 6 reductions are 5.17–5.90% narrow and 2.05–3.08% wide. Depth also changes
English length and graph composition, so this is not a depth-only causal result.
Sol/Astra match 96/96 per language/catalog; narrow Luna matches 92/96 SQL and 95/96
SQLP, and wide Luna 96/96 each. Every failure remains included.

**Join views:** both languages receive the same nine generated views and base
facts. Optional root-view use is 25/108 SQL and 2/108 SQLP. A post-hoc follow-up
requires view roots: 108/108 per language use them and match, with 0.74–0.97% more
SQLP tokens. This tests fixed-depth full-catalog exposure, not optimized view
selection. See [view construction](JOIN_VIEWS.md).

**Alternate data:** unchanged saved path queries retain their original outcome
on two other valid graphs without model calls. A separate album-title contrast
exposes two original-data matches that incorrectly remove duplicate titles.
A match on one dataset is weaker than equivalence. [Snapshot rescoring](SNAPSHOT_EVALUATION.md)
is diagnostic, not a proof.

**Official NatSQL validation:** 6,204 offline reconstructions cover all 1,034
public Spider gold examples across version/grammar controls. The README driver
combination gives 970/1,034 execution matches for NatSQL and 1,003/1,034 for
NatSQL_G on real official databases with published preprocessed schema. This
validates reconstruction, not the original trained system or exact published
percentages. See [the reproduction scope](NATSQL.md).

## Accounting and interpretation

The November 6 archive contains **2,628 experimental API calls**, **2,988
generated/scored outputs**, and 108 reports, including the 108 superseded NatSQL
calibration calls. Offline gold reconstructions, alternate-data executions, and
manual browser edits add no model calls. The 432 newer calls comprise the 324-call
corrected comparison plus 108 guide-sensitivity calls; their totals stay separate.

The original SQL/SQLP matrix contains 1,872 calls/2,232 outputs after excluding the
108 superseded NatSQL calls; the required-view follow-up adds 216/216. The newer
three-language comparison and guide sensitivity have separate frozen records.
Provider totals include prompts, guides, schema, output, reported reasoning,
and failures; overlapping usage fields are not counted twice.

Local archives: `resources/experiments/demo-study-2026-11-06/`,
`natsql-corrected-2026-11-06/`, and `natsql-guide-sensitivity-2026-11-06/` under
the same experiments directory. Their validators bind exact plans, responses,
journals, usage, data, and expected bags. Original artifacts are never rewritten
to make a later correction look like an earlier success.

The useful observation is that **the workflow and language guide affect total usage**.
It does not establish general accuracy superiority, statistical significance,
cross-provider results, human productivity gains, or storage-engine savings.
Reference queries and record-walk answers share authored interpretations; their
execution checks do not replace independent human semantic review.

Try [Path Studio](DEMO_LAB.md) to inspect the compiler's actual joins, real table
samples, duplicate-sensitive answers, and separately recorded costs. The default
starter bundle is prepared, with no model calls or invented usage. Paper, video,
raw reports, and credentials remain outside public Git.

# Evaluation workloads

## Expanded scale study (November 16, 2026)

The expanded evaluation contains **2,034 distinct English questions**. The
matched SQL/SQLP stress workload has 1,000 questions over four fictional
relationship graphs: commerce, clinical encounters, logistics, and research.
Their primary snapshots contain 59,904 rows, 35 tables, and 66 relationships.
Each has an alternate snapshot of the same size. Every prepared SQL and compiled
SQLP answer matches a separate Python record traversal on both snapshots:
4,000 prepared executions. These checks validate the reference implementation;
generated model results are reported separately in [STUDY_RESULTS.md](STUDY_RESULTS.md).

Ten question families cover projections, competing roles, shared prefixes,
multiple filters, duplicate bags, distinct tuples, three branches, role-specific
filters, empty controls, and integer filters. Paths have one, two, three, four,
six, or eight relationships; the largest query needs 24 distinct joins. The
1,000 questions contain 955 templates after removing literal values and 602
structures after also abstracting table/role names. One hundred cases are
deliberately empty controls. Answers change on alternate data for 836 cases.
The four graphs have different topologies but share a synthetic record generator
and generic attribute distributions. They are correlated controlled stress
cases, with pending human interpretation review.

The separate public SQL challenge retains all **1,034 Spider development
questions** across 20 databases, 80 tables, and 539,860 rows. There are 564
distinct database/gold-query pairs: different questions sometimes paraphrase
the same query. This is the published development split, and possible model
training exposure is unknown. It includes 551 queries with aggregates, 277 with
GROUP BY, 81 with subqueries, and UNION/INTERSECT/EXCEPT cases. Its published
reference SQL executes on 1,032 questions; two WTA reference queries encounter
invalid UTF-8 and remain explicitly unscorable. Original database bytes and
published reference queries are preserved.

This challenge measures SQL generation separately. Static analysis identifies
849 questions outside the current SQLP fragment and 185 unverified syntactic
candidates. Candidates still need relationship-totality, type, and translation
review; none is presented as an evaluated SQLP translation. NatSQL's current
alias-free profile also cannot represent every repeated-table role in the
stress workload, so the expanded matched comparison uses SQL and SQLP.
The outcome-independent NatSQL subset exporter preserves 188 questions from
47 complete original batches without repeated physical-table bindings. Its
eight offline converter preflights time out on the full catalogs; a serial trace
locates the bottleneck in official join-network expansion beyond 90 seconds.
No new NatSQL model calls or accuracy denominator are reported.

Public scoring compares requested columns and duplicate counts, respects an
outer ORDER BY, and uses numeric relative tolerance 1e-6 and absolute tolerance
1e-8. Ordering within tied values can still reject equivalent alternatives.
These are single-snapshot execution matches, **not official Spider leaderboard
or test-suite accuracy**. Passing one database does not establish semantic
equivalence. See the [Spider source](https://yale-lily.github.io/spider) and
[evaluation guidance](https://github.com/taoyds/spider/blob/master/evaluation_examples/README.md).

Prepare the inputs and a frozen request plan without model calls:

```bash
make
python3 -m tools.prepare_scale_study build/scale-supported
python3 -m tools.public_textsql_benchmark build/scale-public
python3 -m tools.run_scale_study prepare build/scale-study \
  --supported build/scale-supported --public build/scale-public \
  --languages sql sqlp --model gpt-6-luna --batch-size 4 \
  --max-output-tokens 16384 --reasoning-effort high
python3 -m tools.run_scale_study verify build/scale-study
```

The public builder needs the existing pinned local Spider source under
`resources/icde-audit/natsql-reproduction-independent/`, or `--source-root` with
the same original JSON, extraction manifest, and databases. It makes no downloads
or dependency installations. Synthetic preparation needs only the compiler and
Python standard library. Both builders refuse existing destinations.

Live execution is explicit and uses the existing cumulative spending ledger:

```bash
python3 -m tools.run_scale_study run build/scale-study \
  --api-key-file resources/private/openai_api_key --workers 8
python3 -m tools.validate_scale_study build/scale-study --require-complete \
  --ledger resources/experiments/demo-study-2026-11-06/spending \
  --output build/scale-study/validation.json
```

The November 16 run executes the copied `study/runtime/tools` implementation,
with absolute study, credential, and ledger paths. It freezes corresponding V2
guides from the November 13 archive, model settings, public requests, private
answers, data, compiler, and Python sources. Public prompts contain questions
and schema facts only. One attempt is made per language/question, with four
questions sharing each request and the same 16,384-output-token cap. There are
768 requests and 3,034 planned outputs. Raw responses are retained before
scoring; resume reuses them and refuses uncertain duplicate sends. Usage belongs
to whole batches and includes failures. The saved-query validator checks
coverage, request/response bindings, usage, hashes, primary execution, and
alternate snapshots without additional model calls.

## Chinook case study

This is a bounded, project-authored comparison workload: **12 development and
30 reserved questions** over a pinned Chinook database snapshot. Each question
has prepared SQL, SQLP, and an independently interpreted Python answer plan.
The prepared queries validate the implementation. They are not generated model
results, and the answers still need a second human review before the main study.

## What the data represents

[Chinook](https://github.com/lerocha/chinook-database) is an established sample
digital-media store database. Its music metadata comes from an iTunes library;
its customers and employees are fictional, and its sales were generated.
It is useful for reproducible multi-table tests, but this snapshot is **not a
production database or a data lake**. A data-lake claim needs a separate workload
with appropriate provenance. See the
[upstream description](https://github.com/lerocha/chinook-database/blob/7f67772503d71ba90f19283c38e93923addb43fa/README.md).

We pin commit `7f67772503d71ba90f19283c38e93923addb43fa`, SQLite script version
1.4.5. The exact 595,545-byte SQL file has SHA-256
`caf31d698a4a79c628215b552dfe6575e71be052ae02b8f18e763498f55f5d44` and Git blob
SHA-1 `299610dc69f04a2b16b3a2e45bd2af82ed2872a2`. The repository's
[MIT license notice](../examples/chinook/LICENSE.txt) is retained with generated
snapshots. The downloaded SQL and databases stay under ignored `build/`.

## Prepare and check

Use Python 3.11+ with SQLite and the existing compiler. No Python packages,
model credentials, or model calls are needed. Download the pinned input once:

```bash
mkdir -p build/chinook-source
curl --fail --location \
  https://raw.githubusercontent.com/lerocha/chinook-database/7f67772503d71ba90f19283c38e93923addb43fa/ChinookDatabase/DataSources/Chinook_Sqlite.sql \
  --output build/chinook-source/Chinook_Sqlite.sql
make
python3 -m tools.prepare_chinook build/chinook-source/Chinook_Sqlite.sql \
  build/chinook-ready
python3 -m tools.run_comparison build/chinook-ready/snapshot.sqlite \
  build/chinook-ready/catalog.json build/chinook-ready/development.json
python3 -m tools.run_comparison build/chinook-ready/snapshot.sqlite \
  build/chinook-ready/catalog.json build/chinook-ready/held_out.json
python3 tests/chinook_test.py
```

Preparation checks the exact input digest **before executing** the public SQL
script in a temporary database. It copies the approved columns into a fresh
snapshot, verifies declared keys/FKs and actual totality, and cross-checks every
prepared SQL answer against the Python oracle. It refuses an existing output
directory. Reuse a prepared directory for subsequent runs, or choose a new one.
The test suite runs without a download; its complete 42-question source
integration check is skipped when that exact local SQL file is absent.

The generated directory contains `snapshot.sqlite`, `catalog.json`,
`development.json`, `held_out.json`, `CHINOOK_LICENSE.txt`, and `manifest.json`.
The manifest records source, builder, question-plan and output hashes, row
counts, zero preparation model calls, and the pending human-review status.
Hashes identify the artifact; they do not certify independent human review.

## Scope and answer checking

The snapshot retains **all 6,874 rows from nine selected tables and 39 columns**.
It omits money/decimal fields, optional employee-manager navigation, and
playlists. Primary keys remain explicit record identifiers. Declared source
FKs supply eight reviewed navigation roles:

```text
InvoiceLine.invoice -> Invoice.customer -> Customer.supportRep -> Employee
InvoiceLine.track   -> Track.album      -> Album.artist        -> Artist
                           .genre       -> Genre
                           .mediaType   -> MediaType
```

The supplied SQL and SQLP conditions receive the same schema/reference facts.
Some upstream FK declarations allow NULL, but these particular source rows
contain none in the retained roles. Preparation checks that fact before making
the curated snapshot's reference columns explicitly NOT NULL. It rejects an
ineligible snapshot rather than dropping rows or inventing relationships.
Nullable ordinary attributes, such as `Track.Composer`, remain nullable.

[questions.json](../examples/chinook/questions.json) fixes each question, both
prepared queries, a separate semantic plan, and descriptive tags. The Python
oracle reads individual tables into keyed dictionaries, follows reviewed
references, applies typed literal equalities, and projects row bags. It does
not parse either query language, call the compiler, execute joins, or use a
query's output as its own gold. The explicit prepared SQL and compiled SQLP
must separately match that oracle, including duplicates and empty-result width.
One project authored these interpretations, so this is implementation
independence, not an independent human judgment of every question's meaning.

Tasks cover direct fields, paths of one to three hops, branching, shared
prefixes, integer/string predicates, AND, DISTINCT, duplicate-sensitive answers,
and intentional empty answers. The largest answer has 760 rows under the fixed
1,000-row cap. For example, the Canadian-purchases artist task has 304 purchase
lines but only 91 distinct artist names; the two multiplicity questions should
therefore produce different answers. Do not count related questions or repeat
runs as statistically independent evidence from different databases.

There is also an important limitation: **every invoice billing country equals
its customer's country in this source**. Confusing those two roles would go
undetected by country-only answer checking. Customer versus support-employee
locations provide a useful contrast here, while the fictional academic fixture
covers distinct author/publisher roles and cycles. Neither fixture proves that
all possible relationship mistakes are detectable.

## Reserved questions and model evaluation

`scope: "held_out"` marks the reserved set, which has not been used for any
model calls. Inspecting prepared SQL/compiler correctness before a model study
is an implementation check. It does not make these questions an established
external benchmark. Before a main model run, review question interpretations,
freeze data/catalog/guides/settings and eligibility, and record that decision.
Only the question and approved facts/instructions may enter a model request.
Prepared queries, answer plans and expected rows remain scoring material.
Use development results to revise the system; retain reserved model failures
after the freeze. [MODEL_PILOT.md](MODEL_PILOT.md) describes the runner protocol.

## Optional logical-view integration

[arm_mapping.json](../examples/chinook/arm_mapping.json) maps the same physical
tables to `ARM_Artist`, `ARM_Track`, and other logical views with the same column
names and reviewed roles. Create both fresh artifacts with:

```bash
python3 -m tools.prepare_chinook build/chinook-source/Chinook_Sqlite.sql \
  build/chinook-direct --arm-output build/chinook-arm
python3 -m tools.run_comparison build/chinook-arm/snapshot.sqlite \
  build/chinook-arm/catalog.json build/chinook-arm/development.json
python3 -m tools.run_comparison build/chinook-arm/snapshot.sqlite \
  build/chinook-arm/catalog.json build/chinook-arm/held_out.json
```

The Python API is `prepare(source_sql, new_output_dir)` and, for an already
prepared artifact, `prepare_arm_variant(prepared_dir, new_output_dir)`. The view
variant rewrites all FROM/JOIN table bindings in prepared SQL and the FROM root
in SQLP, without changing literals or column names. Both languages use the same
views. These are **the same 42 tasks**, not 42 more independent observations.
The variant checks mapping/compiler integration; it does not establish abstract
entity IDs, identity resolution, or a theoretical ARM performance benefit.
[ARM_MAPPING.md](ARM_MAPPING.md) explains the implemented mapping subset and its
separate example with renamed physical columns.

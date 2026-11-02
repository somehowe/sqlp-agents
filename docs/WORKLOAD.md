# Chinook case study

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

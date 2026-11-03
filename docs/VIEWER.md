# Inspect saved query comparisons

The local [Query Explorer](../web/index.html) opens version-1 JSON reports from
[the prepared runner](QUERY_COMPARISON.md) or [model pilot](MODEL_PILOT.md). It
shows the existing records; it makes no model calls and executes no SQL.

## Open it

From the repository root:

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory web
```

Open `http://127.0.0.1:8000` in a modern browser. Choose a saved comparison JSON
file with the file picker. Only the static `web/` directory is served; selected
reports are read in the browser and are not uploaded, stored, or fetched over
the network. There are no package installs or frontend build steps. The classic
script also supports opening `web/index.html` directly in browsers that permit
local styles/scripts; the local server is the more consistent option.

Create fresh demonstration data and two actual saved-run files:

```bash
make
python3 -m tools.create_demo build/demo
```

Choose `build/demo/prepared-report.json` for the checked reference queries, or
`build/demo/deliberate-report.json` to inspect intentionally wrong queries and a
compiler error. The latter contains mistakes in **both** language conditions;
one case has identical wrong answers to demonstrate why agreement is insufficient.
Its deliberate-mistake note is visible in the viewer. All records are labeled
manual development examples, with unknown token usage. No model was called.

The output directory must be new; existing records are never overwritten. A
failed creation may leave an incomplete directory, so inspect the error and use
a fresh path after fixing it. The bundle also includes the database, catalogs,
workloads, and descriptive analysis in JSON/Markdown.

For another dataset, generate a report with [the prepared runner](QUERY_COMPARISON.md)
or [pilot](MODEL_PILOT.md), then choose that file. Dry-run plans and incomplete
setup-error reports have no scored model attempts.

## Walk through a case

1. Choose a question. Compare the two source queries, recorded answers,
   diagnostics, and provider-reported input/output/total tokens.
2. Expand SQLP's compiled SQL to see the statement SQLite executed and its typed
   parameters. The compiler translates SQLP; SQLite supplies execution.
3. Under **Follow a path**, choose an occurrence from SELECT or WHERE. The viewer
   shows its ordered reference joins and the final table/column. Direct columns
   have no joins. Quoted components remain distinct, including role names with
   dots, spaces, or quotes.
4. Expand the expected answer and data/run identity when reviewing the result.
5. Under each condition, expand **Inspect saved request and raw provider
   response**. When a unique saved entry has the same question ID, language, and
   recorded request hash as the attempt, its payload includes the saved guide,
   question, and supplied schema. The raw response and its original usage field
   are displayed separately. Prepared records explicitly say when these fields
   were not recorded; the viewer never reconstructs or invents a prompt.

The request drawer shows parsed saved JSON, not original HTTP wire bytes. Its
hash check compares the two recorded hash strings; it does not recompute the
payload digest or authenticate the record. A missing hash, differing hash,
duplicate request, or different case/language prevents payload association.
The attempt's own raw response remains inspectable. Raw cached/reasoning usage
fields are not added again to normalized token totals.

Correctness labels are the runner's recorded labels; the viewer does not grade
queries. Duplicate rows remain visible, and int64 values remain exact strings.
An empty answer is distinct from a failed attempt with no answer. Unknown token
usage displays **Unknown**; it is never converted to zero. Cached/reasoning
breakdowns are not added again to provider totals.

Origin labels distinguish **prepared queries**, **replayed responses**,
**dry-run plans**, and **live-run records**. A replay can contain synthetic data;
it is not a new model experiment. A live label is supplied by the file and is
not authentication. Incomplete records remain labeled as incomplete. Missing
conditions and unscored answers are shown explicitly. Setup-error records that
contain no scope are labeled **Scope unknown** rather than assigned one.

## Optional schema mapping

The second file picker independently opens the version-1 artifact produced by
[the restricted ARM mapping tool](ARM_MAPPING.md). Expand the mapping panel to
inspect each logical entity's physical table, its declared primary key, column
renamings, logical/physical reference equalities, and generated SQLite view DDL.
Loading a mapping does not execute its SQL or change the comparison record.
The ordinary comparison viewer works without one.

For the current local example, choose
`build/arm-offline-2026-11-02/mapping-artifact.json`, then its
`prepared-report.json`. This prepared report has no catalog/snapshot manifest,
so their association correctly remains **not verified** even though they were
created together. Merely choosing two files does not verify their relationship.

When available, the viewer compares the mapping's logical catalog with schema
facts inside each hash-associated request, and compares the recorded mapped
snapshot hash with the pilot's data-snapshot hash. Missing evidence stays
unverified; differing facts or comparable snapshot hashes produce a visible
mismatch. A snapshot difference can include database-copy/header changes; the
viewer does not diagnose mapping correctness from it. Catalog hash strings are
displayed only because the mapping and pilot use different JSON encodings for
Unicode. Structural fact comparison avoids treating that encoding difference as
a catalog mismatch. Even matching evidence establishes record consistency,
not authentication or independent data validation.

This is the restricted source-table-key mapping profile. It does not establish
cross-source entity identity, full EID semantics, or entity equality. The mapping
file's data-check flag is displayed as a claim supplied by that file.

## Bounds and checks

Files must be valid UTF-8 JSON, at most 16 MiB; individual text fields are capped at one million UTF-16 code units. The viewer checks supported record
shapes, safe token counters, typed cells, row widths, and trace join chains before
replacing the displayed record. It preserves a previous valid record if a new
file fails to load. Untrusted text is inserted as text, never interpreted as HTML.
Saved JSON is also bounded to 32 nesting levels and 250,000 values; numeric
integer fields must be safe JavaScript integers. Mapping entities, key columns,
references, logical catalog, and recorded view names must agree internally.
These checks inspect the artifact, not the source database or meaning of its DDL.

The table preview shows up to 100 rows and 30 columns and identifies any omitted
preview rows/columns. The runner's recorded score still concerns its full answer.
The current profile allows up to 63 joins, 2,000 projections, and 512 predicates;
the viewer accepts traces within those bounds. It is a record inspector, not a
query editor, live backend, or proof that the stored research claims are correct.

Optional regression checks require Node.js 18+ with no packages:

```bash
node tests/viewer_test.js
node tests/viewer_test.js build/academic-comparison.json
node tests/viewer_test.js build/offline-example/replay-report.json
node tests/viewer_test.js build/arm-offline-2026-11-02/prepared-report.json build/arm-offline-2026-11-02/mapping-artifact.json
```

The second command exercises an actual prepared report as input; the third
exercises the synthetic report from [the complete offline example](OFFLINE_EXAMPLE.md).
The optional report check selects every recorded question and verifies its
source queries and any associated saved response, preserving the origin label.
Tests cover validation, exact values, origin/usage labels, path selection,
markup-as-text, file errors, and asynchronous selection races using a small DOM
stub. The 23 checks also cover request/raw-usage inspection, mismatched or
ambiguous request hashes, Unicode catalog comparison, optional mapping records,
and independent mapping-file reads. These checks do not replace visual browser testing; no browser automation
engine was available during implementation.

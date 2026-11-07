# SQLP Path Studio

Path Studio lets a visitor edit SQLP, run the actual C++ compiler and SQLite,
and see how named relationships become joins. The graph shows all query
branches simultaneously, including separate aliases when different roles reach
the same table. Select a path to highlight its physical SQL joins; step through
it or press **Follow path**. The animation explains the compiler trace, not
execution timing or row provenance.

Click a node for real records from its frozen table, or use **Catalog map** to
inspect supplied relationships. Pan, zoom, and Fit make longer paths readable.
Answer tables group identical displayed rows with multiplicity badges while
scoring every duplicate. Snapshot changes can reveal an accidental match.

**All interventions are manual; the server makes zero model calls.** Recorded
SQL/SQLP measurements, optional NatSQL cards, input/output bars, and JSON-batch
curves stay separate from local edits. The batch curve connects only compatible
JSON cohorts; native requests or different guide revisions are not spliced in.
NatSQL is a recorded condition, not an editable execution language here.

## Run

```bash
make
python3 -m tools.demo_bundle build/lab-demo
python3 -m tools.demo_server --bundle build/lab-demo/bundle.json \
  --compiler build/lab-demo/sqlpc --port 8765
```

Open `http://127.0.0.1:8765/`. Stop with Ctrl+C. No API key, third-party Python
package, JavaScript build, or external CDN is needed. Use a new output folder.
The default bundle contains eleven **prepared synthetic scenarios**, including
a deliberately introduced DISTINCT mistake, and no invented provider usage.
Recorded bundles can be supplied separately; their claims remain labeled.

The server validates actual schema/types/keys/references, then privately freezes
registered databases and the compiler. Later edits to the originals do not
change the session. Starting a session does not certify human answer review.

## Walkthrough

1. Run the author-affiliation/publisher scenario. Both university instances stay
   visible with distinct aliases. Select each path and follow its highlighted joins.
2. Click a table to inspect up to eight real sample records. These are table
   samples with unspecified order, not rows traced from the current answer.
3. Change the publisher-country path to the author-affiliation path and **Run**.
   Observe changed joins, alias reuse, and answers. Typing alone leaves edits unrun.
4. Choose the duplicate-sensitive scene and switch its data snapshot. An extra
   valid row can expose unwanted DISTINCT. A match is not an equivalence proof.
5. With a recorded bundle, inspect language totals or click a batch-curve point.
   Preset changes do not rerun models or assign usage to the current edited query.

Ctrl/Cmd+Enter runs an edit. Late responses cannot replace newer edits; SQL, paths, and answers identify the last completed scenario/snapshot. Table
samples show their own scenario/snapshot context.
SQL is editable through the same read-only answer scorer.

## Bundle contract

The UTF-8 JSON bundle has version 1 and at most 100 scenarios. Every scenario has
`id`, `question`, `column_count`, `catalog`, starting `sql`/`sqlp`, and `snapshots`.
There is one `original` snapshot and up to three contrasts. Each snapshot supplies
an absolute owner-registered `database` path and raw-scalar `expected_rows`.
The browser never selects filesystem paths. See the generated bundle for a
complete example and [the catalog contract](DATASET_CHECKS.md) for metadata.

Optional `saved` carries a clear origin/model label and per-language recorded
runs with `token_usage`. Optional `experiments.groups` carries cohort metadata
(`id`, `model`, `dataset`, `protocol`, `guide_revision`, `batch_size`,
`question_count`, `repetitions`) and `sql`/`sqlp`, plus optional `natsql`, records.
Each record supplies `attempts`, `correct`, `input_tokens`, `output_tokens`, and
`total_tokens`. Unknown usage remains unknown. Usage belongs to a whole model
response; per-question batch displays are labeled amortized means.

Use approved snapshots only. The SQL scorer can read throughout the selected
database, so do not put private or expected-answer tables in it. This is a local
workbench, not automatic discovery or a user-intent inference system.

## Requests and limits

Only the lab assets and `/api/scenarios`, `/api/run`, `/api/preview` are served.
Database files, credentials, and arbitrary paths are not static routes. Hosts
must be loopback; Origin, when supplied, must match. Cross-site fetches, CORS
preflights, writes, and attached databases are rejected.

`POST /api/run` accepts exactly `scenario`, `language`, `query`, `snapshot`.
The languages are `sql` and `sqlp`. Extra fields, unknown registrations,
duplicate JSON keys, non-UTF-8/nonfinite JSON, or bodies over 32 KiB are rejected.
Defaults: two seconds per query, 500 result rows, a 16 KiB SQLite row/value limit,
a 4 MiB response limit, and four simultaneous operations. Errors/oversized results
are never labeled matches; result previews show 20 rows but score all accepted rows.

`POST /api/preview` accepts exactly three text fields:

```json
{
  "scenario": "academic-canadian_authors_danish_publishers",
  "snapshot": "original",
  "table": "AUTHOR"
}
```

Only a table in that scenario's supplied catalog is allowed. The server quotes
its registered identifiers and runs bounded, read-only SELECT/count statements
against the frozen snapshot. No query text or file path is accepted. The response
has typed cells, total `row_count`, at most **8 rows/16 columns**, `has_more`,
`columns_truncated`, and `snapshot_sha256`. A table sample is not an answer check
or provenance; order is unspecified. Preview timeouts/errors report unavailable
rather than presenting a partial sample as complete.

## Checks

```bash
python3 tests/demo_server_test.py
make test-viewer
```

The HTTP tests use ephemeral loopback ports and temporary data. Node checks
cover 23 saved-viewer and 38 Path Studio cases; actual Chromium checks cover the
original 14 categories plus 12 visual-studio categories. Human conference rehearsal
and review of question meanings still remain.

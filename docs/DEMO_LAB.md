# Interactive query lab

The lab lets a visitor edit a SQLP path, choose **Run** to compile and execute it,
inspect the generated joins, and switch between registered data snapshots.
An answer is compared with that snapshot's supplied expected row bag, including
duplicates. SQL can be edited and executed through the same read-only scorer.
**Every intervention is manual; this server makes zero model calls.** Recorded
model usage is displayed separately and is never charged to a local edit.

This complements the [saved-record viewer](VIEWER.md). It is a local workbench,
not automatic relationship discovery, a proof of SQL equivalence, or an
interactive system for choosing the user's intended meaning.

## Run

Build the compiler and create the public prepared-example bundle:

```bash
make
python3 -m tools.demo_bundle build/lab-demo
python3 -m tools.demo_server --bundle build/lab-demo/bundle.json \
  --compiler build/lab-demo/sqlpc --port 8765
```

Open `http://127.0.0.1:8765/`. The server always binds to loopback; there is no
option for a public interface. An optional `--compiler` selects another trusted
compiler binary. Stop with Ctrl+C. No API key or third-party package is needed.

The output folder must be new. This default bundle contains 11 prepared
synthetic scenarios, including a deliberately introduced `DISTINCT` mistake.
It includes no recorded model experiment and shows unknown model usage.
Recorded bundles can be supplied separately with `--bundle`; their saved model
usage remains distinct from new manual executions. See [the recorded study](STUDY_RESULTS.md).

The launch process validates actual schema/types/keys/references, makes private
copies of the registered SQLite snapshots and compiler, then serves the lab.
Later edits to the original database do not change the session. Snapshot and
compiler hashes are returned with each execution. Expected answers and model
records remain supplied evidence; startup checks do not certify human review.

## Bundle contract

The UTF-8 JSON file has version 1 and at most 100 scenarios. Database paths must
be absolute, are supplied only by the owner at launch, and never come from the
browser. Use approved data only: the shared SQL scorer permits reads throughout
the selected database, so do not put private or gold-answer tables in it.

```json
{
  "version": 1,
  "title": "SQLP Query Lab",
  "scenarios": [{
    "id": "album-titles",
    "question": "List album titles, once per album.",
    "column_count": 1,
    "catalog": {"version": 1, "tables": [], "references": []},
    "sql": "SELECT a.Title FROM Album AS a;",
    "sqlp": "SELECT a.Title FROM Album AS a;",
    "snapshots": {
      "original": {
        "database": "/absolute/path/to/original.sqlite",
        "label": "Original data",
        "expected_rows": [["First album"], ["Second album"]]
      },
      "contrast1": {
        "database": "/absolute/path/to/contrast.sqlite",
        "label": "Two albums share a title",
        "expected_rows": [["First album"], ["Second album"], ["First album"]]
      }
    }
  }]
}
```

Replace the empty example catalog with a valid [version-1 catalog](DATASET_CHECKS.md).
Every scenario needs `column_count`, raw JSON scalar `expected_rows`, and an
`original` snapshot; up to three contrasts may be added. The starting queries
may be recorded model output or prepared queries. They are re-executed on the
selected snapshot for comparison with the edit.

An optional scenario `saved` object can contain `label`, `model`, `origin`, and
`sql`/`sqlp` run objects carrying `token_usage`. Label real records clearly, for
example `"label": "Recorded Sol response; re-executed locally"`. Missing usage
stays unknown. Optional top-level `experiments` accepts a table:

```json
{
  "columns": ["Condition", "Questions", "Total tokens"],
  "rows": [["SQL", 12, 20762], ["SQLP", 12, 21142]],
  "note": "Exploratory recorded development batch; no new model calls here."
}
```

## Audience walkthrough

1. Choose a scenario, inspect its question, then run the starting SQLP query.
2. Select a path occurrence. The diagram shows each named role and distinct
   physical alias; the cards show the exact source-key/target-key equalities.
3. Change a role in the query and run again (Ctrl/Cmd+Enter also works). Compare
   the edited result, the starting query's result, and supplied snapshot answer.
4. Switch to a contrast snapshot. For example, adding another album with the
   same title can expose an unwanted `DISTINCT`. Passing one snapshot does not
   establish correctness on every valid database.
5. Inspect the separate recorded model experiment table. Source compactness,
   provider total tokens, and manual edits are distinct measurements.

## Local request boundary and limits

Only `/`, `/lab.html`, `/lab.js`, `/lab.css`, `/api/scenarios`, and `/api/run`
are served. Resources, credentials, database files, and arbitrary filesystem
paths are not static routes. Browser metadata omits registered database paths.
Requests require a loopback Host and matching Origin when present; cross-site
fetches, CORS preflights, and writable SQLite operations are rejected.

`POST /api/run` accepts exactly `scenario`, `language`, `query`, and `snapshot`.
Unknown registrations, extra file-path fields, duplicate JSON keys, non-UTF-8
JSON, and bodies over 32 KiB are rejected. Defaults are two seconds per query,
500 result rows, a 16 KiB SQLite row/value limit, and a 4 MiB response limit.
There are at most four simultaneous executions. A failed or oversized query is
displayed as an error; a truncated answer is never labeled a match. Tables
preview only 20 rows while all accepted rows participate in scoring.

Regression checks:

```bash
python3 tests/demo_server_test.py
node --check web/lab.js
```

The HTTP tests bind ephemeral loopback ports and use temporary fixture data
without API credentials. `make test-viewer` runs the saved viewer's 23 Node
checks and the lab's 27 checks. A Chromium walkthrough also passed 14 browser
checks; final conference rehearsal and a human visual check remain useful.

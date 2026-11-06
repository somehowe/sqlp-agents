# Check saved queries on contrasting snapshots

`tools.evaluate_snapshots` executes the same saved generated query text on
alternate valid databases and compares each answer with that snapshot's supplied
expected row bag. It makes **no model calls** and records **no new provider usage**.
This can expose an incorrect query that happened to match the original data.

```python
from tools.evaluate_snapshots import evaluate_snapshots

result = evaluate_snapshots(
    source_report, catalog,
    {"contrast-1": {"database_path": alternate_database,
                    "workload": alternate_workload}},
    compiler_path=frozen_compiler,
)
```

The source must be a completed `live` or `replay` report from `run_pilot` or
`run_batch_pilot`. Source request hashes, model responses, task bindings, and
questions are checked. Every case must have exactly one SQL and one SQLP attempt.
The alternate workload needs the same IDs, exact questions, scope, and projection
widths; only its expected row bags change. The supplied catalog facts must match
the original model condition, and rescoring requires the original compiler binary
hash. Pass the copied kit's compiler when the current build differs.

Each alternate database opens read-only. One held transaction covers schema,
key/reference preflight, snapshot hashing, and all query executions. The common
SQLite scorer enforces read-only execution, typed parameters, row limits,
timeouts, and bag-sensitive answer comparison. A private compiler copy prevents
a concurrent rebuild from changing later conditions. Default limits are 1,000
answer rows, five seconds per query, thirty seconds for preflight, and 64 MiB
per snapshot. Source reports are capped at 64 MiB and 256 cases; up to eight
alternate snapshots may be supplied.

Original generation, compilation, and execution failures remain false and are
skipped. Missing or malformed batch outputs are never run as empty queries.
Executable queries that were wrong on the source data are still rescored, but
cannot become robust merely by matching an alternate snapshot.

The output has `kind: "cross_snapshot_scoring"` and `origin: "local_rescoring"`:

- `source` preserves the source report hash, origin, model, and experiment manifest.
- `snapshots` records actual read-snapshot/workload hashes and preflight results.
- Each case/language retains the exact query, its hash, original status/answer/
  correctness, and original request/response identifiers. It then records typed
  returned and expected rows, diagnostics, and correctness for each contrast.
- `robust_on_tested_snapshots` requires original correctness **and** correctness
  on every supplied contrast. The summary counts these outcomes by language.

Provider usage is not copied into new attempts; each local scoring record has
`token_usage: null`. Batch tokens remain in the original batch report. Source
correctness labels are preserved rather than independently regraded, and hashes
are consistency checks rather than signatures authenticating an experiment.

Passing these databases means agreement on these tested instances, **not general
SQL equivalence or independently reviewed natural-language meaning**. Contrasts
are additional executions of the same questions, not additional independent
tasks. Preserve original snapshot scores and distinguish deliberately constructed
counterexamples from a held-out benchmark. See [the controlled study](../examples/path_study/README.md).

Offline checks: `python3 tests/snapshot_evaluation_test.py`. They include duplicate
values, role collisions, missing batch outputs, original failures, changed IDs/
questions/catalogs, binary drift, and input/database immutability. No key or
network is needed.

# Shared-context batch comparison

`tools.batch_pilot` tests whether sharing a schema and language guide across
several questions changes the SQL/SQLP tradeoff. It is an additional workflow
condition, separate from the original raw-query first-attempt protocol.

Use **batch size 1 as the JSON-format control**, then sizes 4 and 12 on the same
12 questions. Every condition returns a JSON array of `{id, query}` objects.
Comparing a batch with the earlier raw-query protocol alone would mix batching
with a response-format change.

```python
from tools.batch_pilot import run_batch_pilot

plan = run_batch_pilot(
    database_path, catalog, workload, compiler_path,
    guides={"sql": sql_guide, "sqlp": sqlp_guide},
    settings={"model": selected_model, "max_output_tokens": 8192,
              "reasoning": {"effort": "high"}},
    batch_size=4, seed=0,
)
```

The default dry run never calls a provider or executes generated queries. For
execution, supply `origin="live"`, a callable `provider`, a fresh `journal_path`,
and `max_requests` covering the exact plan. The caller controls credentials and
spending; request/output caps do not themselves enforce a dollar limit. Replay
uses the existing request-bound `ReplayProvider` and `origin="replay"`.

The runner freezes a private database snapshot and compiler, then reuses the
established dataset preflight, compiler smoke query, and safe SQLite scorer.
Both conditions receive equivalent public facts and the same task grouping;
prepared queries and expected rows stay out of requests. A common wrapper
replaces the guides' single-query output rule with the JSON-array contract.
The seed shuffles language order within each group, not model sampling.

Malformed JSON, unknown IDs, or malformed entries fail all tasks in that
response. A missing or duplicated known ID fails that task; other unambiguous
entries can still be scored. No Markdown stripping, output repair, or hidden
retry occurs. Refused/incomplete outputs fail every task in their group.
Provider/transport failures and returned-model drift abort, retaining received
responses in an owner-only, flushed journal before parsing or scoring.

Reports have `kind: "batch_pilot"`. Exact requests, grouping, source/data/compiler
hashes, settings, raw batch responses, and task outcomes are retained. **Usage
belongs to the whole response:** `batches[].generation.usage` and per-language
`summary` contain actual provider counters. Per-question `token_usage` is null.
`amortized_batch_tokens_per_question` divides the condition total by all its
attempts, including failures; it is not measured usage for an individual query.
Missing usage in any batch makes that condition's aggregate total unknown.

This report intentionally differs from the single-attempt analyzer format.
Use `summarize_batch_report(report)` for its descriptive totals. Repeating a
question or batching several questions does not create additional independent
research tasks. Freeze grouping/settings before the measured calls and retain
all outcomes; an exploratory batching win is not a reserved-workload result.

Offline checks: `python3 tests/batch_pilot_test.py`. These use only dummy local
providers and standard-library SQLite; no API key or network is needed.

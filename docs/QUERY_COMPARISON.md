# Run a prepared SQL/SQLP comparison

This is the execution and answer-checking foundation for the demo. It runs prepared queries, saves their actual SQLite answers and SQLP translations, and checks them against separately written expected rows. It makes no model calls. Records are labeled `origin: "manual"`, `scope: "development"`, with `token_usage: null`.

## Run the academic example

Requirements: Python 3.11+ with SQLite, plus the [compiler build requirements](COMPILER_CLI.md). Create `build/academic.sqlite` using the [dataset instructions](DATASET_CHECKS.md), then run:

```bash
make
python3 -m tools.run_comparison \
  build/academic.sqlite \
  examples/academic/catalog.json \
  examples/academic/workload.json > build/academic-comparison.json
```

The workload has six development cases covering shared paths, relationship roles, duplicate records, DISTINCT, mentor cycles, and an empty answer. All twelve prepared condition runs should match their expected answers. These are pipeline checks, not AI accuracy results or a benchmark contribution.

The runner opens a read-only connection and starts a transaction before preflight. Both conditions and all cases use that same read snapshot. Use fixed data and deterministic queries/views; a snapshot does not freeze functions such as `random()` or the current time.

## Workload and scoring

The workload has `version: 1`, `scope: "development"`, and a nonempty `cases` list. Each case supplies:

- `id` and `question`.
- `sql` and `sqlp`: prepared query text, preserved without repair or normalization.
- `column_count`: expected projection width, including for empty answers.
- `expected_rows`: manually prepared row lists. Cells can be null, strings, int64 numbers, or finite real numbers. Boolean gold values are rejected.

Scoring compares row values and duplicate counts, ignoring row order and column aliases. Column order and projection width matter. Numeric `1` and `1.0` compare equal; text `"1"` differs. Large integer comparisons retain precision. A single finite fixture cannot establish query equivalence, so prepare contrasting data and independently review answers before a real study.

The [pilot adapter](MODEL_PILOT.md) receives only the question, approved schema/relationships, and language instructions. Keep prepared queries and expected answers out of model prompts.

## Saved records

Each case records its question, expected width/rows, and two `runs`, identified by `language` (`sql` or `sqlp`). A run contains:

- Original `query`, executed `sql`, typed `parameters`, and SQLP `trace`.
- `columns`, typed `rows`, `status`, and `correct`.
- An `error` for failed compilation/execution, and null `token_usage`.

Cells use `{"type":"int64","value":"9007199254740993"}` for integers, `string` for text, `real` with a string value for floating-point output, `blob` with hexadecimal bytes, and `null` with a null value. This lets Sam's interface display exact values without JavaScript rounding large integers. Expected rows use the same output encoding.

Sam can load `build/academic-comparison.json` as a recorded-run fixture. A SQLP trace maps each path to its physical joins; `correct` comes from expected rows, not agreement between the two conditions. Failed runs retain their diagnostics and have null rows, rather than pretending to return an empty answer.

## Limits and failure handling

Defaults are `--max-rows 1000`, `--timeout 5`, and `--preflight-timeout 30`. The timeout applies separately to the compiler process and each SQLite query. A progress handler interrupts long-running SQLite execution; the connection also limits SQL/string/blob/row size to 1,000,000 bytes. These controls bound this local development runner; they are not a hostile-code sandbox. Preflight has its own 30-second default budget; its timeout aborts setup rather than scoring an incorrect answer. These are SQLite progress-handler budgets, not process isolation.

An authorizer blocks database writes, TEMP creation, ATTACH, PRAGMA, transactions, and extension loading. Limits apply equally when executing both conditions' SQL. A row-limit failure never grades a truncated prefix as a complete answer.

Query compilation rejections and SQL errors are retained as failed condition runs. Timeouts and size/row limits are recorded as execution failures. Bad configuration, failed preflight, unavailable/malformed compiler responses, and unexpected failures executing compiler-produced SQL instead abort the comparison as setup/infrastructure failures. Fix those before collecting study results.

Exit codes: `0` means every prepared answer matched; `1` means the comparison completed with an incorrect or failed condition; `2` means setup failed and no comparison should be scored. Root `limits` saves the selected settings. Choose and freeze limits before evaluation, not after inspecting outcomes.

This tool remains a manual development runner. The separate [pilot harness](MODEL_PILOT.md) adds prompts, provider usage, journals, provenance, and offline replay; no real model comparison or repair loop has been collected. The academic data includes a US publisher without US-affiliated authors so following the wrong country role cannot silently pass the empty-US case.

[Dataset checks](DATASET_CHECKS.md) · [Compiler protocol](COMPILER_CLI.md) · [Project context](AGENTS.md)

Use the [local viewer](VIEWER.md) to inspect saved rows and path mappings, or [paired analysis](ANALYSIS.md) for descriptive counts. `python3 -m tools.create_demo build/demo` creates a fresh fixture and reference/deliberate-mistake reports for a local walkthrough.

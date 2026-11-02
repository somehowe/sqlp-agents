# Prepared-query measurements

`tools.benchmark_prepared` measures **prepared reference queries**, not an AI
agent's accuracy, output, or provider token usage. It never calls a model or
downloads a tokenizer. Its purpose is to check translation correctness and
separate source compactness, guide overhead, and local execution costs before
the model study.

## Run it

After [preparing Chinook](WORKLOAD.md), run from the repository root:

```bash
make
python3 -m tools.benchmark_prepared build/chinook/snapshot.sqlite \
  build/chinook/catalog.json build/chinook/development.json \
  --repetitions 7 --warmups 1 --seed 20261101 > build/chinook-microbenchmark.json
python3 tests/benchmark_test.py
```

The optional `--encoding PATH` accepts only an explicitly supplied local
`cl100k_base.tiktoken` vocabulary, with SHA-256
`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`.
Its public source is
https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken .
The hash and tokenization pattern follow the
[official tiktoken implementation](https://github.com/openai/tiktoken/blob/main/tiktoken_ext/openai_public.py).
The optional `tiktoken` package must already be installed. Without this option,
token estimates are `null`; byte and timing measurements still work. The tool
does not consult a global tokenizer cache or fetch missing files. The vocabulary
can stay in ignored `resources/tokenizers/`.

## What the report records

- UTF-8 source bytes and Unicode character counts for each prepared SQL/SQLP
  query, plus maximum path depth and distinct compiler join count.
- Optional static text counts: the exact language guide, the same serialized
  question/catalog input used by the pilot, and the prepared query. Their sum
  **excludes API framing, hidden reasoning, actual model output, and retries**.
  This is a fixed tokenizer comparison, not `total_tokens` from a provider.
- Every raw compilation and execution sample, including separate warmups,
  seeded execution order, and per-language summaries. Compilation includes a
  fresh compiler subprocess, startup, IPC and JSON validation; it is not a
  measurement of the compiler function alone. Catalog transport is prepared
  once outside timing. Execution includes SQL preparation and bounded result
  fetching; answer checking happens outside its timed interval.
- Compiler, catalog, workload, guides, implementation source and consistent
  database snapshot hashes, plus Python/SQLite/platform metadata and limits.
  The serialized database hash includes committed WAL pages.

Every pair must match the expected **row bag and width** before any timing is
accepted. Failed queries remain in the report; any validation failure skips
all timing. A later changed compilation, incorrect answer, or execution error
aborts timing, retains partial samples, and withholds aggregate timing results.
Exit codes are `0` for a completed correct benchmark, `1` for validation/timing
failure, and `2` for invalid configuration or setup failure.

For each case, the language executed first alternates across rounds, with a
seeded starting orientation and shuffled case order. With an odd number of
repetitions, first-position counts differ by one at most. Both conditions use
one read-only SQLite transaction and fresh SQL preparation
(`cached_statements=0`); the page/process cache is warm, so these are **not cold
I/O measurements**. API limits: 1–100 repetitions, 0–20 warmups, up to 1,000
cases, a bounded snapshot, per-query/preflight timeouts and bounded results.

## Recorded development observation

On November 2, 2026, 7 measured rounds after 1 warmup round produced the
following results. All 12 Chinook prepared pairs and all 3 mapped ARM fixture
pairs passed before timing. These are project-authored development examples;
the expected answers still require human review before the model study.

| Prepared workload | SQL → SQLP bytes | SQL → SQLP static query tokens | SQL → SQLP guide + task + query text tokens |
|---|---:|---:|---:|
| Chinook development, 12 questions | 1,790 → 1,148 | 481 → 275 | 20,184 → 20,494 |
| Mapped ARM fixture, 3 questions | 500 → 222 | 153 → 56 | 3,286 → 3,318 |

For Chinook, source bytes fell 35.9% and static query tokens fell 42.8%, but the
visible text sum rose 1.54%. The SQLP guide used 587 tokens versus SQL's 544:
43 extra tokens per independent request outweighed the query reduction on
most examples. The one depth-three question saved 50 query tokens, enough to
offset this guide difference by seven tokens. This describes **these guides
and prepared queries**; formatting, examples and language instructions affect
the outcome. There is no claim that this pattern holds for model-generated
queries, other guides, API token accounting, or other tasks. Independent calls
pay the guide overhead each time; many questions do not automatically amortize
it away.

On the same local run, the median across Chinook's 84 execution samples per
condition was 0.299 ms for prepared SQL and 0.390 ms for SQLP's emitted SQL.
SQLP additionally incurred a median 3.052 ms compiler-subprocess sample. The
3-case mapped fixture's corresponding medians were 0.058 ms, 0.165 ms and
2.245 ms. These small local timings depend on machine load, query shape,
parameterization, and SQLite plans; they do not establish a speedup or a
general performance ranking. Raw samples remain under ignored
`build/offline-benchmarks-2026-11-02/`.

The useful lesson is methodological: shorter source alone does not establish
lower complete interaction cost. The [paired model study](MODEL_PILOT.md)
must still collect actual provider usage and model-generated query outcomes.

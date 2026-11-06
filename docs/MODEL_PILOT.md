# First-attempt model pilot

The [prepared comparison](QUERY_COMPARISON.md) tests execution with hand-written
queries. `tools.run_pilot` adds separate SQL/SQLP requests, raw response and usage
records, and request-bound replay. The [November 6 development pilot](PILOT_RESULTS.md)
contains the first live evidence; it is not a held-out study.
The six academic questions are development fixtures, not held-out research data.
The [Chinook workload](WORKLOAD.md) adds 12 development and 30 reserved questions,
with human interpretation review still pending. The [experiment kit](EXPERIMENT_KIT.md)
freezes their data, compiler, guides, runtime, and private scoring material and
plans requests without API access. Live runs require authorized access and spending.

## Plan without calling a model

Build the compiler and create `build/academic.sqlite` using the
[dataset instructions](DATASET_CHECKS.md). From the repository root:

```bash
python3 -m tools.run_pilot \
  build/academic.sqlite examples/academic/catalog.json \
  examples/academic/workload.json --model offline-fixture \
  > build/pilot-plan.json
```

The default is `origin: "dry_run"`: it validates data and saves twelve exact
requests, with no provider call or query execution. Replace the model identifier
with your chosen model when preparing its actual requests. Model capabilities
and access still need checking; this example does not select a research model.

Requests contain only the question, approved schema/relationship facts, the
corresponding [SQL](../prompts/sql.md) or [SQLP](../prompts/sqlp.md) guide, and
settings. Prepared queries, expected rows, case metadata, and arbitrary catalog
annotations are excluded. Both guides use the same unrelated employee example.
They teach corresponding syntax; their lengths are not artificially equalized.

## Exercise replay offline

A replay file has this shape:

```json
{"version":1,"entries":[{"request_sha256":"64-character hash from the plan",
  "response":{"id":"recorded-response-id","model":"returned-model-id",
    "status":"completed","output":[{"type":"message","role":"assistant",
      "content":[{"type":"output_text","text":"SELECT ...;"}]}],
    "usage":null}}]}
```

Supply exactly one entry for each planned request. Replaying a response requires
an identical request hash, including the guide and settings. Duplicate requests,
missing/extra entries, and changed requests are rejected before execution.

```bash
python3 -m tools.run_pilot \
  build/academic.sqlite examples/academic/catalog.json \
  examples/academic/workload.json --model offline-fixture \
  --replay build/replay.json --max-requests 12 \
  --journal build/replay-journal.jsonl > build/replay-report.json
```

Use a fresh journal filename each time. To make a local smoke test, construct
responses using the fixture's prepared queries and label the artifact as replay.
Such records check plumbing; they are **not model results**, even if every answer
matches. Actual journals can also supply replay entries: select each
`response_received` event's `request_sha256` and `response` fields. Preserve the
original journal separately as provenance. Replay validates requests, while the
new manifest identifies the data/compiler used for rescoring; a changed data or
compiler hash means it is a new scoring environment.

## What a run preserves

- One first attempt per condition, in a seeded shuffled order. Calls have no
  shared conversation, tools, previous response, gold feedback, or hidden retry.
- The exact request/hash; returned model and response ID; raw response, text,
  completion status, and provider usage. Text is not stripped of Markdown or
  silently repaired. Refusal, empty output, and truncation remain failed attempts.
- SQLP translation/parameters/trace, SQLite answers, bag-sensitive correctness,
  diagnostics, and generation wall time. This is not time spent reasoning.
- Data snapshot, full/public catalog, workload, guide, frozen compiler binary, and source
  hashes; Git state; Python/SQLite versions; settings and resource limits.

Data hashing observes the same read transaction as preflight and scoring,
including WAL pages. Default snapshot size is capped at 64 MiB. Use a bounded
frozen dataset and deterministic tables/views. The study database must contain
only approved data, with no gold-answer or unrelated private tables: SQL reads
are not currently restricted to catalog tables/columns. The read-only authorizer
prevents writes; it is not a catalog access policy. Each workload explicitly
labels its `scope` as `"development"` or `"held_out"`; the report preserves it.
A reserved file alone does not establish a reviewed held-out protocol: freeze
question eligibility, human-checked answers, guides, model/settings, and limits
before the first held-out call.

Token totals use provider-reported `input_tokens + output_tokens`, including
instructions and reasoning tokens counted by that provider. `--max-output-tokens`
covers visible output and reasoning tokens, so a small cap can truncate a query.
Cached/reasoning
details remain in the raw response and are not added twice. Missing/partial usage
is `null`; a language summary is unknown if any of its attempts lacks usage.
Failed attempts with reported usage still count. Replay usage describes the
recorded response, not new billed calls. See the official
[token-counting](https://developers.openai.com/api/docs/guides/token-counting) and
[reasoning](https://developers.openai.com/api/docs/guides/reasoning) guides.

Before any provider call, the runner smoke-compiles a valid direct-column query
against the selected compiler/catalog. Both Python runners pass metadata through
a private bounded [catalog argument file](COMPILER_CLI.md), avoiding operating-system
argument-size limits. The compiler still receives SQLP text on stdin.

The journal is created exclusively with owner-only permissions and fsynced after
each event. It records a request before sending and a response before compilation
or scoring. Transport/protocol/compiler/data failures, failed/cancelled provider responses,
and returned-model drift abort the run as setup
errors; received responses remain available. Incomplete pilots must not enter
accuracy denominators as completed paired experiments. A transport failure may
have incurred provider usage even when no usage record arrives; do not report
that missing usage as zero. No automatic retry or resume is implemented.

## Before a paid pilot

Agree on model/access and spending, independently check the dataset/questions and
answers, then freeze guides/settings/limits. Run the prepared comparison on the
chosen compiler/catalog first, so executable or argv-size incompatibilities are
found before spending on generation. The optional HTTP adapter uses only
Python's standard library and the official Responses endpoint. `--live` is an
explicit opt-in and also requires `--max-requests`, a fresh `--journal`, and a key.
Use `--api-key-file PATH` ([file format](CREDENTIALS.md)) or the process environment's
`OPENAI_API_KEY` when that option is omitted. The file option is valid only with
`--live`; dry runs and replay never read credentials. It sends at most the planned requests, has
no redirects/retries, and limits each response to 8 MiB. Request/output-token caps
are not a dollar budget. Use the same `--reasoning-effort high` (or another supported
effort) in both conditions and the dry-run plan. Omitting the option preserves
the provider default; a model may reject an effort supported by another model.

Run `make test` for offline protocol, replay, leakage, and execution regressions.
Exit codes: `0` means dry-run planning succeeded or every executed answer matched;
`1` means a completed pilot contains wrong/failed attempts; `2` means setup failed.
A successful dry run is not evidence of model accuracy.

Saved results can be inspected with the [local viewer](VIEWER.md) and summarized with the [paired analyzer](ANALYSIS.md). Neither tool makes model calls or authenticates research claims.

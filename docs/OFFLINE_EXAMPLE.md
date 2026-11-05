# Run the complete offline example

This example exercises the compiler, SQLite scoring, a frozen runtime, request
planning, synthetic replay, analysis, and saved records. It uses the small
academic fixture already in the repository. No model account, key, download,
private resources, or Python packages are needed.

From the repository root, build the compiler and choose a **new** output folder:

```bash
make
python3 -m tools.offline_example build/offline-example
```

The command prints a JSON summary with paths to the generated artifacts. The
output folder is private to its owner. An existing folder is never overwritten;
choose another name for a later run. A failed run can leave partial files and
does not publish a successful `summary.json`.

| Artifact | What to inspect |
|---|---|
| `demo/prepared-report.json` | Six prepared SQL/SQLP pairs and their expected answers |
| `demo/deliberate-report.json` | Explicitly introduced mistakes in both languages, including agreement on a wrong answer |
| `kit/` | Frozen data, executable, guides, runtime, questions, private answers, and checksums |
| `plan.json` | Twelve exact independent requests with prepared queries and expected answers excluded |
| `replay.json`, `journal.jsonl` | Synthetic reference-query responses and the durable replay events |
| `replay-report.json` | Twelve scored attempts from the copied runtime, including SQLP translation traces |
| `analysis.json`, `summary.json` | Paired correctness, unknown model usage, and artifact locations |

The replay supplies the hand-written reference queries as synthetic responses.
Every response has unknown token usage. Successful scoring demonstrates that
the components agree on their interfaces; it supplies no model accuracy or
token-efficiency evidence. The replay is explicitly labeled synthetic, and the
frozen kit still records that a model experiment has not started.

Open [the viewer](VIEWER.md) and choose `replay-report.json` to inspect the
requests, response records, answers, and path translations. Choose either report
under `demo/` to compare the prepared examples or deliberate mistakes. The
viewer displays recorded labels; it does not independently grade the answers.

Verify the copied inputs separately with:

```bash
python3 -m tools.experiment_kit verify build/offline-example/kit
```

The optional `--compiler PATH` selects another built compiler. Exit status `0`
means the complete example succeeded; `2` means setup or an integration check
failed. See [EXPERIMENT_KIT.md](EXPERIMENT_KIT.md) for the frozen artifact format
and [MODEL_PILOT.md](MODEL_PILOT.md) for the later real-model protocol.

## Automatic checks

[GitHub Actions](../.github/workflows/offline-checks.yml) runs on pull requests
to `main` and pushes to `main`. It builds on Ubuntu 24.04, runs the C++/Python
suites, creates this example, and checks the viewer against all three generated
reports. It uses no model credentials or private resources. These are interface
and execution checks, not visual browser tests or model experiments.

A fresh checkout discovers 264 Python tests. Ten optional tests skip without
the separately downloaded Chinook source, tokenizer vocabulary, and NatSQL
environment. The standard-library tests and 23 Node viewer checks run without
those extras. See [the compiler checks](COMPILER_CHECKS.md) for optional fuzzing.

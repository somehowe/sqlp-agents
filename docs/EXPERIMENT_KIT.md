# Frozen offline experiment kit

An experiment kit freezes the database, compiler, language guides, questions,
reference answers, and Python/C++ source used for a paired SQL/SQLP study.
Preparation validates the supplied SQL and SQLP against the expected row bags.
Planning produces the exact requests from the frozen runtime without making
model calls. This makes the next model experiment reviewable and reproducible.

`offline_validated` means the prepared pairs passed. It does not mean a model
answered anything, that tokens were measured, or that a second person reviewed
the questions. Preparation/verification keep `model_run_status: not_started`;
subsequent live evidence belongs in separate reports, such as the
[November 6 development pilot](PILOT_RESULTS.md).

For the next human review, [export a review packet](REVIEW_PACKET.md). It contains
readable questions/queries/paths, answer-bag previews, full typed answers, and a
blank CSV for notes, bound to this kit's versions. It makes no model calls and
does not certify that anyone has checked the workload.

## Prepare, verify, and plan

First build the compiler and prepare a dataset. For the project-authored Chinook
workload, follow [WORKLOAD.md](WORKLOAD.md). Then choose a fresh output directory:

```bash
make
python3 -m tools.experiment_kit prepare \
  build/chinook-ready/snapshot.sqlite build/chinook-ready/catalog.json \
  build/chinook-ready/development.json build/experiment-001 \
  --held-out build/chinook-ready/held_out.json
python3 -m tools.experiment_kit verify build/experiment-001
python3 -m tools.experiment_kit plan build/experiment-001 \
  --scope development --model gpt-6.1-sol --reasoning-effort high \
  --max-output-tokens 8192 --seed 0 \
  > build/experiment-001-development-plan.json
```

`--model` records the model label for the plan. Planning does not check account
access, availability, price, or credentials. It always uses the
dry-run path. Review the generated payloads before choosing a real model run.
`--scope held_out` prepares reserved requests with that scope preserved. There
are two independent first-attempt requests per question, one for each language.
Choose an effort supported by the model; the provider can reject unsupported
values. The output cap includes reasoning tokens. Older kits retain their old
runner, so prepare a new kit to use newly added CLI options.

After reviewing the plan and authorizing spending, run the copied runtime. This
example uses the 12-question Chinook development split and fresh output paths:

```bash
(
  cd build/experiment-001/runtime
  python3 -m tools.run_pilot ../data.sqlite ../public/catalog.json \
    ../evaluation/development.json --compiler ../sqlpc \
    --sql-guide ../public/guides/sql.md --sqlp-guide ../public/guides/sqlp.md \
    --model gpt-6.1-sol --reasoning-effort high --max-output-tokens 8192 \
    --live --api-key-file /home/ryanh/sql-p/resources/private/openai_api_key \
    --max-requests 24 --journal ../../pilot-development-001.jsonl \
    > ../../pilot-development-001.json
)
```

The key file is read only in live mode; omitting it uses `OPENAI_API_KEY` from the
environment. Request/output-token caps do not enforce a dollar budget. See the
[pilot guide](MODEL_PILOT.md) for credential, usage, and failure handling.

The `prepare` command accepts `--compiler` and optional `--mapping`. A mapping
must be a materialized [ARM artifact](ARM_MAPPING.md) whose catalog and final
snapshot hash match the supplied database. An inspection-only mapping cannot
claim that binding. Limits and custom guides are available through the Python
API:

```python
from tools.experiment_kit import prepare_kit, verify_kit, plan_kit

prepare_kit(database_path, catalog, {
    "development": development_workload,
    "held_out": reserved_workload,
}, new_output_dir, guides={"sql": sql_guide, "sqlp": sqlp_guide})
```

Omit the reserved split when it is not ready. Each workload uses the prepared
comparison format with its matching `scope`. Across supplied splits, IDs must
be unique and questions must differ after whitespace/case normalization. This
check does not detect semantically similar questions. Reference-answer review
and appropriate experimental independence still require judgment.

## What is frozen

| Location | Contents |
|---|---|
| `data.sqlite`, `sqlpc` | Copied database snapshot and executable compiler |
| `public/catalog.json` | Allowlisted table, column/type/key, and relationship facts |
| `public/guides/` | Corresponding SQL and SQLP instructions |
| `public/tasks/` | Question IDs and question text, without prepared queries or answers |
| `evaluation/` | Original catalog metadata, prepared queries, and expected row bags |
| `validation/` | Saved prepared comparison records, labeled manual |
| `runtime/tools/`, `runtime/src/` | Frozen Python runner modules and compiler source |
| `mapping.json` | Optional validated mapping metadata |
| `manifest.json` | Completion marker, file hashes, limits, versions, and split sizes |

The model request builder uses only the question, allowlisted schema/relationship
facts, guide, and generation settings. Arbitrary catalog notes, prepared SQL,
SQLP, and expected rows stay out of the public input directory and request
payloads. The whole kit contains private scoring material, so sharing the kit
is different from sharing only its public inputs. Language guides and question
text are supplied inputs; review them for accidental answer disclosure too.

The source opens read-only. One transaction covers its preflight and SQLite
backup, including committed WAL pages. Later source commits do not change the
validated copy. Preparation reserves a new private directory and refuses an
existing one. Files are read-only and the copied compiler is executable;
`manifest.json` appears last. A failed preparation can leave the reserved
directory without a manifest, which is an incomplete kit. Choose a fresh path
after correcting the failure. Existing experiment directories are never reused.

Verification checks the completion marker, checksums, required split files,
public/scoring question consistency, and catalog consistency. It rejects
changed/missing listed files, symlinks, and unsafe manifest paths. Hashes detect
changes relative to the recorded manifest; they are not signatures or proof of
independent approval. Treat runtime code as trusted local project code.

Planning verifies the kit, then runs its copied Python modules with its copied
compiler and data. Later changes to the host checkout or original database do
not change that plan. Python and SQLite themselves are recorded rather than
bundled; this is not a container image or a promise of identical behavior on
every platform. Source Git metadata is recorded only when the source root is
the actual Git root, avoiding an unrelated ancestor checkout in a copied kit.

Kit preparation, verification, and planning require no API call or dependency
installation. Default limits are 1,000 answer rows, five seconds per query, thirty seconds
for preflight, and a 64 MiB snapshot. JSON CLI failures return exit status 2.
Before a real study, freeze model/settings, human answer review, eligibility,
and spending separately. See [MODEL_PILOT.md](MODEL_PILOT.md) for generation and
token-recording semantics.

Verification tests:
`python3 -m unittest discover -s tests -p experiment_kit_test.py`.

# Review the study questions and answers

A review packet makes a frozen workload readable without running an agent. It
copies the database, catalog, reference queries, translation paths, and typed
expected answers into one private folder. A blank CSV records a person's notes.
Exporting a packet does not mean that anyone has reviewed it.

First prepare an [experiment kit](EXPERIMENT_KIT.md), or create the kit included
in [the offline example](OFFLINE_EXAMPLE.md). Choose a new output directory:

```bash
python3 -m tools.review_packet create \
  build/offline-example/kit build/question-review --preview-rows 20
```

Use `--scope held_out` for a kit containing the reserved split. The command
verifies the kit before copying anything and refuses an existing output folder.
There are no model calls, credentials, downloads, or query execution.

| File | Purpose |
|---|---|
| `review.md` | Questions, catalog relationships, reference SQL/SQLP, compiled paths, and answer previews |
| `cases.json` | Complete typed answers and translation records, including duplicate rows |
| `data.sqlite`, `catalog.json` | The copied data and approved schema/relationship facts |
| `review.csv` | Editable reviewer names, decisions, and notes |
| `manifest.json` | Completion marker and checksums binding this packet to its frozen kit |

Read each numbered question and check its intended relationship roles, filters,
expected values, and duplicate counts against the data. A preview can omit rows;
`cases.json` contains the complete answer. Compiler traces explain the joins the
compiler generated, not whether the query expresses the intended question.
The current Chinook kits do not include their separate Python oracle plans;
this exporter does not add them. Inspect those plans separately when needed.

For each CSV row, enter a reviewer name and one decision: `ok`, `revise`, or
`uncertain`. Leave unreviewed rows blank. Use the notes field to explain ambiguity
or proposed changes. Keep its case number and hash unchanged; row order can
change. Generated cells contain only numbers and hashes, not question text or
arbitrary case identifiers.

```bash
python3 -m tools.review_packet status build/question-review \
  --kit build/offline-example/kit
```

Status is `pending` while annotations remain incomplete, `needs_changes` when a
reviewer marks a question for revision or is uncertain, and `recorded` when all
cases have named `ok` annotations. These are recorded notes, not authenticated
proof of independent review or correctness. The status command does not approve
an experiment or trigger model calls.

Fixed packet files are checksummed; editing `review.csv` is expected. Missing,
duplicate, rebound, or invalid CSV rows are rejected. The optional `--kit`
checks the original kit identity too. Without it, a portable packet still checks
its own copied files. Hashes detect changes relative to the supplied manifest;
they are not signatures. If questions or answers need revision, update the
source workload and create a new kit and packet instead of editing fixed files.
With `--kit`, the copied database, catalog, and complete case records are also
compared directly to that kit; changing a packet and recomputing its checksums
does not establish that it still describes the original source.

The folder is owner-only and generated files start with owner-only permissions.
It contains reference answers and copied study data, so share it deliberately
with reviewers rather than placing it in model inputs. Exit `0` means export or
status inspection succeeded, including a pending review; `2` means setup or
consistency checks failed.

# Iris's schedule — agent runner and evaluation

Build the runner: separate model calls, Ryan's compiler, SQLite execution, independent answer checking, and saved queries/results/tokens for Sam's website. Add NatSQL through its existing converter if the integration gate passes.

**Current course proposal:** read IDEA.md and the NatSQL/DIN-SQL overviews; draft their related-work paragraph by Canvas's actual deadline.

| Due | Deliverable |
|---|---|
| **Wed Oct 28** | Agree on catalog/compiler/run-record formats, model access, and call budget. |
| **Sat Oct 31** | Load a tiny SQLite fixture; execute/reference-score hand-written SQL. Give Sam one example record and run Ryan's compiled query. Choose a manageable dataset and start questions with Sam. Share the three-hour NatSQL compatibility check. |
| **Wed Nov 4** | Connect Ryan's minimal text compiler. Verify one complete SQLP-to-SQLite run against an independently checked answer. |
| **Sat Nov 7** | Run a paired development pilot on one model, aiming for 12 questions. Save prompts, failures, and raw usage; freeze language guides. If its check passed, integrate NatSQL within three additional shared person-hours; otherwise defer it. |
| **Mon Nov 9** | Supply methods, related work, and labeled pilot evidence for the proposed internal outline review. |
| **Wed Nov 11** | With Sam, finish/cross-check held-out questions and reference answers, targeting 30. Freeze workload, data/catalog versions, and run settings before any held-out calls. |
| **Sat Nov 14** | Complete the Nov 12–14 comparison and correctness/token summaries. Inspect failures. Expose a thin local interface so Sam can trigger the same runner. |
| **Wed Nov 18** | Complete methods/results/limitations material for the four-page draft. Report every condition actually tested. |
| **Sat Nov 21** | Check paper numbers against saved records; rerun affected cases after fixes. Save reproduction instructions and real recorded runs for the demo. |
| **Sun Nov 22** | Review/approve the final numbers and claims; support submission. Reserve Nov 23 for submission issues. |

**Controls:** same model/settings, questions, relationship facts, and comparable guides/workflow. First attempts are required; repairs are optional. Keep reference answers hidden from the model. Count all input/output tokens without double-counting usage fields; total tokens do not directly measure thinking time.

**Shared work:** draft half the held-out questions/reference SQL; Sam drafts half. Cross-check answers, including wrong-role and duplicate-sensitive cases. Set any smaller workload before evaluation, never based on results. Compare NatSQL on a declared supported overlap and retain conversion failures.

[Shared schedule](SCHEDULE.md) · [Agent context](../AGENTS.md)

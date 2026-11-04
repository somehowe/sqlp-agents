# Delivery schedule — SQLP for AI Agents

Updated November 4, 2026. Goal: one interactive comparison and four-page ICDE
demo paper. Required delivery assumes Ryan at 12–15 hours a week. Iris and Sam's
plans are optional handoffs until their availability is confirmed; this does
not settle authorship. The first Sunday checkpoint, November 1, has passed.

The compiler, SQLite runner, saved-record viewer, restricted ARM views, pinned
Chinook workload, offline benchmarks, and frozen experiment kit now work. **No
model results exist.** Current priorities are understanding the system, human
answer review, and a real pilot once the user provides model access.

| Due, 2026 | Concrete result |
|---|---|
| **Wed Nov 4** | Trace one question end to end; inspect the frozen kit and 12 development questions. Arrange a second human answer review and choose model/access/budget when available. |
| **Sat Nov 7** | If access is ready, run the 12-question paired development pilot; inspect errors and actual usage. Freeze syntax/guides. Decide whether NatSQL has a fair, feasible common profile. |
| **Mon Nov 9** | Have a working four-page draft and ARM/saved-run demo ready for the internal mentor target Ryan reported. Confirm review availability. This is not an official conference outline deadline. |
| **Wed Nov 11** | Finish human review; freeze data/catalog/views, question eligibility, expected answers, model/settings, and limits before reserved model calls. Refresh and verify the kit. |
| **Sat Nov 14** | Finish the Nov 12–14 paired comparison and descriptive analysis. Inspect wrong answers, failures, and token totals. Load real records in the viewer. |
| **Wed Nov 18** | Revise the working four-page draft with actual main-comparison results, audience interactions, and limitations. The current private draft contains offline observations only. |
| **Sat Nov 21** | Reproduce setup, reconcile every paper number with saved records, test in a real browser, rehearse, and record the encouraged video. |
| **Sun Nov 22** | Contributors approve PDF/authorship; designated submitter aims to submit. Official deadline: **Nov 23, 11:59 pm AoE**. |

Use one capable accessible model first. The workload has 12 development and 30
reserved questions. Separate Python answer plans check the prepared queries;
a person still needs to check the interpretation. Reduce the workload before
seeing reserved model outcomes if review capacity requires it. Repeating these
questions through ARM views does not double the number of independent tasks.

ARM's restricted implementation is ready for review: declared single-column
non-null keys, reviewed total references, generated SQLite views, and literal
answers. Choose direct tables or generated views before freezing the study;
both languages receive equivalent facts. Full identity reasoning and discovery
remain outside this deadline.

NatSQL's pinned converter runs, but manual compatibility checks retain failures
on some longer/repeated-role queries. A matched NatSQL model condition is still
unimplemented. Add it only if its guide, eligible profile, and failure handling
are settled by Nov 7; otherwise keep it as related work and compatibility evidence.
Cut extra models, repairs, syntax, and animation before core review/writing.

No API calls until the user supplies access. If the Nov 7 pilot or Nov 11 answer
review slips, revise the submission scope with Enamul; do not relabel prepared
queries or replay as model evidence. Paper files stay local under resources.
Course assignments follow Canvas separately. The verified ICDE demo call asks
for four IEEE pages including references and an interactive scenario; video is
encouraged, at most five minutes/50 MB.

[Ryan](Ryan_Schedule.md) · [Iris](Iris_Schedule.md) · [Sam](Sam_Schedule.md) ·
[Experiment kit](../EXPERIMENT_KIT.md) · [Context](../AGENTS.md)

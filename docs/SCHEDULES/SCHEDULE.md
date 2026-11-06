# Delivery schedule — SQLP for AI Agents

Updated November 6, 2026. Goal: one interactive comparison and four-page ICDE
demo paper in this repository. Required delivery assumes Ryan at 12–15 hours a
week. Sam is planning a separate paper, and Iris has discussed a data-lake
extension. Their files here describe optional handoffs, not agreed assignments
for those projects. This does not settle authorship.

The compiler, SQLite runner, saved-record viewer, restricted ARM views, pinned
Chinook workload, offline benchmarks, and frozen experiment kit now work. The
[first real development pilots](../PILOT_RESULTS.md) ran on Nov 6. Current priorities
are understanding their mixed results, human answer review, and the reserved
study freeze. Review packets are ready; completed human review remains open.

| Due, 2026 | Concrete result |
|---|---|
| **Wed Nov 4** | Trace one question end to end; inspect the frozen kit and 12 development questions. Arrange a second human answer review and choose model/access/budget when available. |
| **Sat Nov 7** | Review the completed development pilots, including wrong filter values and the duplicate-title counterexample. Freeze syntax/guides. Decide whether NatSQL has a fair, feasible common profile. |
| **Mon Nov 9** | Have a working four-page draft and ARM/saved-run demo ready for the internal mentor target Ryan reported. Confirm review availability. This is not an official conference outline deadline. |
| **Wed Nov 11** | Finish human review; freeze data/catalog/views, question eligibility, expected answers, model/settings, and limits before reserved model calls. Refresh and verify the kit. |
| **Sat Nov 14** | Finish the Nov 12–14 paired comparison and descriptive analysis. Inspect wrong answers, failures, and token totals. Load real records in the viewer. |
| **Wed Nov 18** | Revise the working four-page draft with actual main-comparison results, audience interactions, and limitations. The current private draft includes exploratory development results only. |
| **Sat Nov 21** | Reproduce setup, reconcile every paper number with saved records, test in a real browser, rehearse, and record the encouraged video. |
| **Sun Nov 22** | Contributors approve PDF/authorship; designated submitter aims to submit. The demo CFP lists **Nov 23, 11:59 pm AoE**; confirm the portal cutoff (see below). |

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

The user supplied API access and a $30 spending allowance on Nov 6. Consult the
private ledger before further calls. If Nov 11 answer review slips, revise the
submission scope with Enamul; do not relabel prepared
queries or replay as model evidence. Paper files stay local under resources.
Course assignments follow Canvas separately. The [ICDE demo call](https://icde2027.github.io/cf-demonstrations.html)
asks for four IEEE pages including references and an interactive scenario;
video is encouraged, at most five minutes/50 MB.

The demo call specifies 11:59 pm AoE, while the [general dates page](https://icde2027.github.io/important-dates.html)
says all deadlines are 5 pm Pacific. Both list Nov 23. Confirm the submission
portal's cutoff before the final upload; the internal Nov 22 target precedes
both published times.

[Ryan](Ryan_Schedule.md) · [Iris](Iris_Schedule.md) · [Sam](Sam_Schedule.md) ·
[Experiment kit](../EXPERIMENT_KIT.md) · [Context](../AGENTS.md)

# Group schedule — SQLP for AI Agents

Internal checkpoints for group review, updated October 27, 2026.

**Goal:** one interactive demo and four-page paper comparing correctness and total tokens for SQL/SQLP, with NatSQL if its existing converter is usable. SQLite executes all conditions.

| Owner | Main responsibility | Weekly budget |
|---|---|---|
| [Ryan](Ryan_Schedule.md) | C++ SQLP-to-SQL compiler | 12–15 hours |
| [Iris](Iris_Schedule.md) | Agent runner, SQLite execution, evaluation | 8–10 hours |
| [Samridh](Sam_Schedule.md) | TypeScript website, demo walkthrough | 8–10 hours |

**By Wed Oct 28:** read [IDEA.md](../IDEA.md); agree on catalog/compiler/run-record examples, model access, budget, and contribution expectations. Iris starts with hand-written SQL; Sam starts with sample records.

**Course writing:** Ryan drafts the opening/SQLP motivation; Iris drafts NatSQL/DIN-SQL related work; Sam reviews the walkthrough. Follow Canvas's deadline. November 9 is a proposed internal review, not an official outline deadline.

| Due | What the group should be able to show |
|---|---|
| **Sat Oct 31** | Query objects → SQL/parameters → checked SQLite answers. UI displays a record. Select dataset/start questions; decide NatSQL feasibility. |
| **Wed Nov 4** | Ryan hands Iris a callable text compiler for a tiny supported query. Iris connects it before the pilot. |
| **Sat Nov 7** | Multi-hop queries, development pilot, and real UI records. Freeze features/guides; NatSQL must be integrated to enter the study. |
| **Mon Nov 9** | Outline and rough section material for Enamul's review; confirm his intended timing. |
| **Wed Nov 11** | Finish answer checks; freeze held-out questions, data/catalog versions, workload size, and settings before any held-out calls. |
| **Sat Nov 14** | Complete the Nov 12–14 evaluation, initial analysis, and connected demo. |
| **Wed Nov 18** | Complete four-page draft for group/mentor feedback. |
| **Sat Nov 21** | Checked paper, reproducible artifact, video, and rehearsal ready. |
| **Sun Nov 22** | All authors approve; designated submitter aims to submit. **Nov 23 is the official deadline and contingency day.** |

Show a working example each Saturday; flag blocked handoffs early. Hours include reading, writing, and integration.

Use one model, one dataset, about 12 development questions, and a target of 30 held-out questions. Iris/Sam split preparation and cross-check answers. Reduce workload by Nov 11 if needed, before seeing held-out outcomes.

**NatSQL gate:** three shared person-hours checking five manual cases by Oct 31, then at most three additional shared person-hours integrating by Nov 7. Declare common features and retain conversion failures. If not ready, defer/narrow claims. Cut extra models, repairs, grammar, and animation first.

The [ICDE demo call](https://icde2027.github.io/cf-demonstrations.html) requires IEEE format and four pages including references. See [AGENTS.md](../AGENTS.md) for compiler semantics and fair-evaluation rules.

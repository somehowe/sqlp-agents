# Exploratory study results

November 6, 2026. The completed study has **2,196 real model calls**, producing
**2,556 scored query attempts** in 81 reports. It uses 12 Chinook development
questions and 32 related synthetic templates: eight families at depths 1, 2, 4,
and 6. There are three repetitions per model/condition. Repetitions, depth
variants, catalog variants, and alternate snapshots are correlated observations.
Human interpretation review is pending; the 30 reserved questions remain unrun.

The models were `gpt-6.1-sol`, `gpt-6-luna`, and `gpt-6-astra`, all from the same
provider. Every condition used high reasoning effort, an 8,192 output-token cap,
and one stateless attempt with no repairs. Both languages received equivalent
schema and relationship facts, with a guide for the corresponding language.
All attempts, failures, and reported input/output tokens remain included.

## Sharing context changes the token tradeoff

The table gives **SQLP's percentage change in total tokens relative to SQL**.
Negative numbers mean fewer SQLP tokens. The native protocol returns raw query
text. JSON batches return arrays of queries; **JSON batch size 1 is the format
control**, so batching is not confused with a response-format change.

| Model | Native one-question | JSON batch 1 | JSON batch 4 | JSON batch 12 |
|---|---:|---:|---:|---:|
| Sol | +1.87% | +0.62% | −2.17% | −10.71% |
| Luna | +1.65% | +1.76% | −1.19% | −3.12% |
| Astra | +1.20% | +0.88% | −2.74% | −8.26% |

For batch size 12, the SQL/SQLP totals were 9,167/8,185 tokens for Sol,
10,420/10,095 for Luna, and 9,033/8,287 for Astra. Each language matched all
36 expected answers per model, across the three repetitions of 12 questions.
Whole-batch usage is charged **once per response**; per-question reasoning usage
is not invented. See [the batch protocol](BATCH_PILOT.md).

Shorter generated source alone did not establish lower total usage. In the
native one-question runs, all SQL attempts matched the oracle; SQLP matched
36/36 for Sol and Astra and 35/36 for Luna. Luna's mismatch used the wrong
media-type string and returned an empty result. The [first pilot](PILOT_RESULTS.md)
retains its original observations; the broader study adds the workflow controls.

## Other controls and failures

**NatSQL:** each model matched 33/36 expected answers. The same six-table
question was rejected by the converter in all nine attempts. This uses the pinned official
converter with a reviewed structural adapter, which omits upstream linguistic
preprocessing. It is not a reproduction of the full NatSQL system or evidence
that another encoding could never work. See [the NatSQL condition](NATSQL.md).

**Longer paths and wider catalogs:** the synthetic narrow catalog has two
tables; the wider variant adds 16 unused tables while preserving core data,
questions, and answers. Sol and Astra matched 96/96 answers per language in
both catalogs. In the narrow catalog, Luna matched 92/96 SQL and 95/96 SQLP;
in the wider catalog both matched 96/96. The narrow failures included SQL alias
and path-depth mistakes and one SQLP role-name case error. These small,
constructed results do not establish a general correctness advantage.

**Join views:** both languages received the same nine generated views and base
facts. Optional adoption was low: SQL used a view root in 25/108 attempts and
SQLP in 2/108. A post-hoc follow-up required a view root: all 108 attempts per
language used one and matched their answers. SQLP still used 0.74–0.97% more
total tokens. This fixed-depth, full-catalog control is not optimized view
selection, and does not show that optimized view interfaces are inferior.
See [how the views are constructed](JOIN_VIEWS.md).

**Contrasting data:** saved path queries were re-executed on two other valid
snapshots without new model calls. Their observed success/failure counts stayed
the same. A separate album-title counterexample exposed two original-data
matches that incorrectly removed duplicate titles. A match on one snapshot
does not prove query equivalence; [snapshot rescoring](SNAPSHOT_EVALUATION.md)
is a diagnostic backed by supplied expected row bags.

## What the evidence supports

The useful result is a **workflow-dependent tradeoff**: compact path queries
can save total tokens when shared context amortizes their instructions. This
study does not establish state-of-the-art performance, cross-provider results,
statistical significance, human productivity gains, or general SQLP superiority.
The tested system includes its compiler/checker, not syntax in isolation.
The templates and reference answers were constructed for development. Synthetic
reference queries and record-walk answers share authored path plans; they have
not received independent human semantic review.

The local archive is `resources/experiments/demo-study-2026-11-06/`.
`study-summary.json` and its read-only analysis script validate frozen data,
requests, journals, raw usage, answers, and all 81 completed reports. The first
1,980 calls cover the initial matrix; the required-view follow-up adds 216.
Raw experiments, the working paper, and video remain local and Git-ignored.
No credentials are included in public documentation or the default demo bundle.

Try the [interactive lab](DEMO_LAB.md) to edit paths, inspect joins, and switch
snapshots. Its default examples are prepared and make no model calls. Saved
model records and manual interventions are labeled separately. The current
bounded exploratory demo can be reviewed on this evidence; a future reserved
study needs human answer review and a protocol freeze before any calls.

# SQLP for AI Agents: Correctness and Token Efficiency

We are studying whether SQLP helps models write database queries correctly and
with fewer total tokens. SQLP names the relationships a query follows. Our C++
compiler turns those paths into SQL, and **SQLite executes both conditions**.

**Research question:** Given equivalent schema and relationship information,
how does using SQLP instead of SQL affect correctness and total token usage?

## A small example

```sql
SELECT DISTINCT w.author.name
FROM WROTE AS w
WHERE w.author.university.country = 'DK';
```

The compiler follows approved author and university mappings, creates their
joins, and binds `DK` as a parameter. In ordinary SQL, the model writes those
joins using the same relationship facts. It still has to choose the right path:
an author's university country and a paper's publisher country can differ.

## What we have now

The compiler, experiment runners, saved-record viewer, and **SQLP Path Studio**
work. The compiler supports one root table, finite multi-hop paths, projections,
literal equality, AND, and DISTINCT. A restricted ARM layer can expose logical
views over approved physical tables. We receive the relationships; automatic
data-lake discovery and a new storage engine remain separate extensions.

The [results](STUDY_RESULTS.md) cover three models, twelve Chinook questions,
and 32 related synthetic templates. The workflow matters: separate requests
used slightly more SQLP tokens, while shared-context batches and longer tested
paths could save tokens. We have not established general accuracy superiority.

We also fixed a mistake in our first NatSQL guide: it omitted native flat `IN`.
Fresh comparisons now exercise that syntax, and the official converter has
been checked against all 1,034 public Spider gold examples. The original failed
guide remains in the archive, separate from the corrected comparison.

## What someone can do in the demo

Open [Path Studio](DEMO_LAB.md), choose a scenario, edit a path, and click **Run**.
The graph shows every branch and keeps different aliases of the same table
separate. Click a path to highlight its SQL joins, or use **Follow path** to step
through its roles. The animation explains compilation; it is not measured query
execution. Click a table to inspect real sample records, or explore the supplied
relationships in **Catalog map**.

The answer panel preserves duplicates. Switching snapshots can expose an
unwanted DISTINCT, even when a query matched the first dataset. Recorded token
charts stay separate from these manual edits. The public starter bundle uses
prepared examples; recorded bundles can add SQL/SQLP/NatSQL measurements.
Nothing here calls a model, and typing a new English question does not launch
an agent. Table samples show data, not proof of which rows produced an answer.

## What comes next

Review the question meanings, expected answers, claims, and actual walkthrough
with Enamul. The paper and captioned video remain local working artifacts.
Human interpretation review is still pending; the thirty reserved questions
have received no model calls. A reserved follow-up needs review and a protocol
freeze before its first calls.

Ryan handles required delivery. Sam is planning a separate paper, and Iris has
discussed a data-lake extension. Additional review or demo work should be agreed
rather than assumed; these old roles do not settle authorship.

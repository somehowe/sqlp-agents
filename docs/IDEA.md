# SQLP for AI Agents: Correctness and Token Efficiency

We are studying whether SQLP makes it easier for models to write database
queries correctly and with fewer total tokens. SQLP lets a query name the
relationships it follows. Our C++ compiler turns those paths into SQL, and
**SQLite executes both SQL and SQLP conditions**.

**Research question:** Given equivalent schema and relationship information,
how does using SQLP instead of SQL affect query correctness and total token usage?

## A small example

```sql
SELECT DISTINCT w.author.name
FROM WROTE AS w
WHERE w.author.university.country = 'DK';
```

The compiler follows the approved author and university mappings, creates the
joins, and binds `DK` as a parameter. In the SQL condition, the model writes
those joins itself. Both conditions receive the same relationship facts.
The model still chooses the path: an author's university country and a paper's
publisher country can mean different things.

## What we have now

The compiler, Python experiment runners, interactive lab, and saved-record
viewer work. The compiler handles a tested subset: one root table, finite
multi-hop paths, projections, literal equality, AND, and DISTINCT. A restricted
ARM mapping layer can expose logical views over approved physical tables.
We are not building a new storage engine or automatically discovering links.

The [study](STUDY_RESULTS.md) contains 2,196 model calls across three models,
using 12 Chinook questions and 32 related synthetic path templates. These are
exploratory observations, not thousands of independent research tasks.

The interesting finding is that the workflow matters. Asking questions one
at a time made SQLP's longer guide outweigh its shorter queries. Sharing the
same guide and schema across twelve questions reduced total usage by
3.12–10.71% relative to SQL in these runs. We also tested a JSON one-question
control, NatSQL, wider schemas, and shared generated join views. The results
include the cases where SQLP did not help.

## What someone can do in the demo

Open the [query lab](DEMO_LAB.md), choose a scenario, and edit a SQLP path.
Choose **Run** to compile and execute the edited query, inspect its joins, and
compare its answer with the supplied expected rows. Typing alone does not run
the edit. Switching snapshots executes the current query on the selected data,
so visitors can see whether its answer still matches. SQL can be edited
through the same scorer. These edits are manual and make no model calls.

The public starter bundle contains prepared examples with no invented usage.
Recorded model results can be loaded separately. A query can match one dataset
and still miss its intended meaning, for example by merging different albums
that share a title. Snapshot checks help expose this; they do not prove general
query equivalence.

## What we still need to do

We have a working four-page demo-paper draft and a short captioned video locally.
The next priority is mentor feedback, checking question meanings and expected
answers, and rehearsing the actual audience walkthrough. Human interpretation
review is still pending. The 30 reserved questions have received no model calls.
A bounded exploratory demo can report the current evidence honestly; adding a
reserved study requires review and a freeze before its first calls.

Ryan currently handles the required implementation and delivery. Sam is planning
a separate paper, and Iris has discussed a data-lake extension. Help with review
or the demonstration can fit alongside those directions, with tasks agreed
rather than assumed. These old coding roles do not decide authorship.

Data-lake discovery is a possible extension. The current study receives approved
relationships and uses a sample-store database plus constructed records. We
should describe that setting precisely instead of calling it a complete data lake.

# Exploratory study results

Updated November 6, 2026. Three models from one provider answer twelve Chinook
development questions and 32 related constructed templates. The templates are
eight families at depths 1, 2, 4, and 6. Repetitions, depth/catalog variants, and alternate
snapshots are correlated observations. Human interpretation review is pending;
the thirty reserved questions remain unrun.

## Corrected SQL / SQLP / NatSQL comparison

Our first NatSQL guide incorrectly omitted native flat column-IN-column syntax.
Its 108 calls remain archived as **superseded calibration**. Their earlier 33/36
matches per model are not a fair basis for claiming SQLP superiority.

A fresh **324-call** comparison freezes corrected guides with two corresponding,
unrelated examples, equivalent schema/relationship facts, high reasoning,
an 8,192-output-token cap, and one stateless attempt with no repairs. Every model
answers the same twelve questions three times in each language:

| Model | SQL matches | SQLP matches | NatSQL matches | Total tokens: SQL / SQLP / NatSQL |
| --- | ---: | ---: | ---: | ---: |
| Sol | 36/36 | 36/36 | 36/36 | 64,864 / 65,069 / 71,160 |
| Luna | 35/36 | 36/36 | 33/36 | 66,256 / 66,609 / 77,997 |
| Astra | 36/36 | 36/36 | 36/36 | 64,638 / 64,847 / 70,750 |

All 108 NatSQL outputs converted. Luna's three remaining failures are execution
timeouts on one repeated task: it generated separate membership blocks and
cross joins. Re-execution with 30 seconds still timed out. Original V1.6
reconstruction of the same 108 saved queries gives identical correctness to
V1.6.1, without new model calls. SQL's retained Luna mismatch uses a wrong
media-type literal. These few errors do not establish a general language ranking.

SQLP uses 0.32–0.53% more total tokens than SQL here. The fixed guides contribute
30 extra input tokens/call for SQLP over SQL, and 138 for NatSQL over SQLP. Those
instruction costs are included, but are not intrinsic constants of the languages.
This compares complete interfaces/guides, not syntax in isolation.

A separate **post-hoc guide-sensitivity run** adds a generic explanation that
flat IN starts a membership subquery rather than replacing every foreign-key
join. It changes only NatSQL's guide, retaining both unrelated examples and the
same facts, models, questions, repetitions, and budgets. Its 108 fresh calls stay
separate from the 324-call comparison; no best-guide result replaces prior data.

With that clarification, Sol/Luna/Astra match **36/36, 35/36, 36/36**, with total
NatSQL usage **73,134 / 81,105 / 73,052 tokens**. All 108 queries convert; one Luna
execution still times out. The guide adds 72 input tokens/call. Luna improves
from 33/36 to 35/36 while total usage increases. This shows guide sensitivity,
not a confirmed language ranking or a reason to discard the earlier result.

## Sharing context changes the token tradeoff

The original fixed-guide JSON experiment uses matched batch sizes 1, 4, and 12. Size 1
controls response format before changing how many questions share one request.
The table is SQLP's percentage change in pooled provider input-plus-output tokens
relative to SQL; negative means fewer SQLP tokens.

| Model | JSON batch 1 | JSON batch 4 | JSON batch 12 |
| --- | ---: | ---: | ---: |
| Sol | +0.62% | −2.17% | −10.71% |
| Luna | +1.76% | −1.19% | −3.12% |
| Astra | +0.88% | −2.74% | −8.26% |

Each language matches 36/36 answers per model at every batch size. Whole-response
usage is counted once; per-question reasoning is not invented. Larger batches
also have less output budget per question under the fixed request cap. These
results use the original guides; do not splice corrected native-guide totals
into this curve. See [the batch protocol](BATCH_PILOT.md).

## Other controls

**Longer paths and wider catalogs:** the narrow constructed catalog has two
tables; the wide variant adds 16 unused tables without changing core data or
answers. SQLP uses more total tokens at tested depths 1 and 2 and fewer at 4 and 6.
Depth 6 reductions are 5.17–5.90% narrow and 2.05–3.08% wide. Depth also changes
English length and graph composition, so this is not a depth-only causal result.
Sol/Astra match 96/96 per language/catalog; narrow Luna matches 92/96 SQL and 95/96
SQLP, and wide Luna 96/96 each. Every failure remains included.

**Join views:** both languages receive the same nine generated views and base
facts. Optional root-view use is 25/108 SQL and 2/108 SQLP. A post-hoc follow-up
requires view roots: 108/108 per language use them and match, with 0.74–0.97% more
SQLP tokens. This tests fixed-depth full-catalog exposure, not optimized view
selection. See [view construction](JOIN_VIEWS.md).

**Alternate data:** unchanged saved path queries retain their original outcome
on two other valid graphs without model calls. A separate album-title contrast
exposes two original-data matches that incorrectly remove duplicate titles.
A match on one dataset is weaker than equivalence. [Snapshot rescoring](SNAPSHOT_EVALUATION.md)
is diagnostic, not a proof.

**Official NatSQL validation:** 6,204 offline reconstructions cover all 1,034
public Spider gold examples across version/grammar controls. The README driver
combination gives 970/1,034 execution matches for NatSQL and 1,003/1,034 for
NatSQL_G on real official databases with published preprocessed schema. This
validates reconstruction, not the original trained system or exact published
percentages. See [the reproduction scope](NATSQL.md).

## Accounting and interpretation

The historical archive now contains **2,628 experimental API calls**, **2,988
generated/scored outputs**, and 108 reports, including the 108 superseded NatSQL
calibration calls. Offline gold reconstructions, alternate-data executions, and
manual browser edits add no model calls. The 432 newer calls comprise the 324-call
corrected comparison plus 108 guide-sensitivity calls; their totals stay separate.

The original SQL/SQLP matrix contains 1,872 calls/2,232 outputs after excluding the
108 superseded NatSQL calls; the required-view follow-up adds 216/216. The newer
three-language comparison and guide sensitivity have separate frozen records.
Provider totals include prompts, guides, schema, output, reported reasoning,
and failures; overlapping usage fields are not counted twice.

Local archives: `resources/experiments/demo-study-2026-11-06/`,
`natsql-corrected-2026-11-06/`, and `natsql-guide-sensitivity-2026-11-06/` under
the same experiments directory. Their validators bind exact plans, responses,
journals, usage, data, and expected bags. Original artifacts are never rewritten
to make a later correction look like an earlier success.

The useful observation is that **the workflow and language guide affect total usage**.
It does not establish general accuracy superiority, statistical significance,
cross-provider results, human productivity gains, or storage-engine savings.
Reference queries and record-walk answers share authored interpretations; their
execution checks do not replace independent human semantic review.

Try [Path Studio](DEMO_LAB.md) to inspect the compiler's actual joins, real table
samples, duplicate-sensitive answers, and separately recorded costs. The default
starter bundle is prepared, with no model calls or invented usage. Paper, video,
raw reports, and credentials remain outside public Git.

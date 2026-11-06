# First real development results

November 6, 2026. We made **144 real API calls**: SQL and SQLP for the same
12 Chinook development questions, repeated three times with each of two models.
These are **12 unique questions**, not 72 independent tasks. Human review is
pending; the 30 reserved questions have received no model calls.

Both conditions received equivalent schema/relationships and their corresponding
language guide. All runs used `reasoning.effort=high`, an 8,192 output-token cap,
one independent attempt per question/language, and no repairs. Seeds 0–2 changed
condition order; they were not model sampling seeds. Guides, data, catalog, and
compiler stayed fixed. Repetitions and the smaller-model extension were planned
after inspecting the first Sol development run, so this is exploratory evidence.

| Returned model | SQL answers matching oracle | SQLP answers matching oracle | SQL total tokens | SQLP total tokens | SQLP change |
|---|---:|---:|---:|---:|---:|
| `gpt-6.1-sol` | 36/36 | 36/36 | 62,389 | 63,556 | +1.87% |
| `gpt-6-luna` | 36/36 | 35/36 | 63,884 | 64,939 | +1.65% |

Total tokens include the guide, schema, question, and all output, including
reported reasoning. Every attempt and its usage remain in these totals.
SQLP used more total tokens in each of the six individual batches:

| Model / ordering seed | SQL total | SQLP total | SQLP change |
|---|---:|---:|---:|
| Sol / 0 | 20,762 | 21,142 | +1.83% |
| Sol / 1 | 20,785 | 21,225 | +2.12% |
| Sol / 2 | 20,842 | 21,189 | +1.66% |
| Luna / 0 | 21,263 | 21,746 | +2.27% |
| Luna / 1 | 21,176 | 21,639 | +2.19% |
| Luna / 2 | 21,445 | 21,554 | +0.51% |

## What this tells us

SQLP generated shorter queries: about 42–43% fewer UTF-8 source bytes and
43–44% fewer nonreasoning output tokens across these repetitions. But its guide
added 43 input tokens per question. Reasoning output was also higher in the
SQLP conditions. Together, these costs outweighed the shorter generated text.
This pilot supports **source compactness**, without establishing lower total
token usage or better answer correctness. It does not isolate syntax from the
compiler/checker, establish statistical significance, or generalize to other
databases, languages, or repair workflows.

The one snapshot mismatch was Luna / seed 0 / `ch-dev-12`. SQLP used
`mediaType.Name = 'MPEG audio'` instead of the supplied question's
`'MPEG audio file'`. It compiled and returned no rows instead of 127. Paths do
not prevent an agent from selecting the wrong literal value. All other attempts
matched the frozen expected row bags; human interpretation review remains open.

## A stronger check exposed a shared mistake

`ch-dev-02` asks for album titles by AC/DC, **once per album**. Both queries in
the first run used `DISTINCT Title`. They matched the original data because its
two matching albums have different titles.

In a separate copied snapshot, we added another album with a new primary key
and the same artist/title as album 1. All catalog constraints still passed.
The Python record-walk oracle and both prepared reference queries returned
three rows, including the repeated title. The first-run model queries returned
two. Original snapshot scores stay unchanged; this is a post-pilot controlled
counterexample, not an additional model call or a new benchmark accuracy score.

This makes question wording, duplicate-sensitive data, and independent answer
review priorities. Matching an expected answer on one snapshot does not prove
that a query captures the intended meaning on other valid data. The demo can
show this distinction alongside the queries and their path traces.

## Evidence, spending, and next step

The local archive is `resources/experiments/2026-11-06/`, outside `build/` so
`make clean` does not remove it. It contains the frozen `kit/`, six folders named
`sol-seed0` through `luna-seed2`, exact plans, response journals, saved reports,
analyses, `summary.json`, `counterexample.json`, and the spending ledger. Each
batch has 24 received responses, and its requests match the saved plan. The
analyzer and all 23 viewer checks passed for every live report. Credentials are
excluded. The paper and raw experiment archive remain local and ignored.

Estimated Standard API charge was **$0.1631**, including reported cache writes
and cache-read discounts, using the published [OpenAI pricing](https://developers.openai.com/api/docs/pricing)
on November 6 and the documented default 30-minute cache-write tariff. We did
not set cache TTLs; the response's legacy `prompt_cache_retention=24h` is separate
from the minimum TTL used for write pricing ([caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)).
This is a token-based estimate, not an invoice. A durable
conservative spending guard stayed below the authorized $30; consult the local
ledger before additional paid calls. Cached tokens are still included in the
token totals above. No further model calls are running.

Next: get human review of meanings/answers, especially duplicates, then freeze
the reserved workload and settings. Use this pilot to improve the study, while
retaining all original records. NatSQL remains an optional third condition,
not an experiment we have already performed. See [the schedule](SCHEDULES/SCHEDULE.md)
and [live-run instructions](EXPERIMENT_KIT.md).

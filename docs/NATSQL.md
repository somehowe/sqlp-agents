# Optional NatSQL comparison

The pinned official NatSQL converter runs in an isolated compatibility
environment. A separate first-attempt model condition is now implemented in
[tools/natsql_pilot.py](../tools/natsql_pilot.py), using the corresponding
[NatSQL guide](../prompts/natsql.md). It receives the same public catalog facts
as SQL and SQLP, then converts and scores every generated query.

## Correction to the first comparison

The first guide incorrectly restricted NatSQL to equality predicates and omitted
its native **flat column-IN-column** form. The original 108 model calls remain
preserved, but their 33/36 answer matches per model are **not a fair basis for
claiming SQLP superiority**. The repeated six-table failure was affected by our
guide, not evidence that NatSQL could not express the question.

The unmodified converter can answer that question correctly using its native
flat IN inference. Its generated SQL separates the existence test from the
remaining join chain. Investigation found that upstream's network enlargement
prunes networks above five tables; forcing all six tables into one inferred
network reached an internally caught TypeError. Changing the supplied encoding,
without changing converter code or schema metadata, returns all 15 expected
emails. The correction is in [the guide](../prompts/natsql.md), with an unrelated
employee/department existence example rather than a benchmark answer.

A new offline check converts **all 12 handwritten Chinook encodings correctly**,
including that task, and verifies both actual guide examples. These are manual
compatibility checks, not replacement model results. The script, exact source
hashes, queries, emitted SQL, row bags, and diagnostics remain locally under
`resources/icde-audit/natsql-reproduction-2026-11-06/`. Fresh model comparisons
now use corrected frozen guides, separately from the original runs. Old prompts,
responses, failures, and scores are preserved.

The original paper describes **V1.6**; our preserved adapter pins the later
**V1.6.1** release. These versions and gold annotations differ. Running the
unchanged official converter is distinct from reproducing the entire paper's
trained-model experiment or its original NLP/value-recovery pipeline.

## Corrected model comparisons

The fresh 324-call comparison gives the same two unrelated examples and public
facts to SQL, SQLP, and NatSQL. NatSQL converts all 108 generated queries. Sol
and Astra match 36/36 expected answers each; Luna matches 33/36. The three Luna
failures put IN on every foreign-key edge, producing independent membership
subqueries and cross joins that time out after five seconds (also after 30).
These are execution failures, not converter rejections.

A separate post-hoc 108-call guide sensitivity clarifies that IN starts a
membership block, while joins inside it are inferred. Sol/Luna/Astra then match
36/36, 35/36, 36/36; all convert and one Luna execution times out. The longer guide
adds 72 input tokens/call and raises total tokens. Both guide versions remain
reported in [STUDY_RESULTS.md](STUDY_RESULTS.md); the better result does not
replace the earlier comparison.

Original V1.6 reconstruction of all 108 corrected-guide outputs gives identical
correctness to V1.6.1, with no new model calls. Small SQL text differences also
occur when rerunning V1.6.1 because bare-join table iteration order can vary;
they are not evidence of a converter-version effect. Chinook uses the declared
`reviewed-structural-v1` adapter in both cases.

## Official gold reconstruction check

A separate offline reproduction uses **all 1,034 official Spider gold examples**,
real author-linked databases, published RESDSQL preprocessed schema, and the
unchanged original Spider evaluator. It does not use our Chinook schema adapter.
Six converter, gold-version, and grammar configurations retain 6,204
reconstructions, including every failure. They are repeated checks of 1,034 examples, not new model calls.

The official README driver combines current V1.6.1 code with V1.6 gold:

| Form | Converted | Official execution matches | Official exact matches |
| --- | ---: | ---: | ---: |
| NatSQL, GROUP BY inferred | 1,034/1,034 | 970/1,034 (93.81%) | 963/1,034 (93.13%) |
| NatSQL_G, GROUP BY retained | 1,034/1,034 | 1,003/1,034 (97.00%) | 1,006/1,034 (97.29%) |

The [authors' README](https://github.com/ygan/NatSQL) reports 93.4% execution /
92.7% exact for NatSQL, and 96.8% / 97.3% for NatSQL_G. Our scores are close
but not identical. The
published metadata and modern Python 3.12/NLTK 3.9.4 environment differ from
rerunning the full original preprocessing stack. Two WTA references contain
invalid UTF-8 text and remain failures; database bytes were not repaired.
This validates the reconstruction stage, **not original model training/inference
or exact reproduction of the paper's end-to-end accuracy**.

The [original V1.6 source](https://github.com/ygan/NatSQL/tree/87f39aaa7b169285135a664ee54ff262198cdfc1),
later source, gold files, [published schema](https://github.com/RUCKBReasoning/RESDSQL/tree/7472f7a51fdd054d8139b1bc2627d955aff855e4/NatSQL/NatSQLv1_6),
[official databases](https://yale-lily.github.io/spider), evaluator, commands,
hashes, per-case outcomes, and all six results are recorded locally under
`resources/icde-audit/natsql-reproduction-independent/`. V1.6.1 gold changes
annotations, so scores against the original SQL are not a standalone version ranking.

## What runs

[tools/natsql_adapter.py](../tools/natsql_adapter.py) calls
`create_sql_from_natSQL` from official
[NatSQL V1.6.1, commit dcd716f](https://github.com/ygan/NatSQL/tree/dcd716fc9b920419cf838e490aad4a6f3b4fd231).
It verifies the exact Git blob hashes of all 13 imported upstream files and copies
only those files into a private directory before importing them. It never
downloads code, installs packages, calls a model, rewrites a query, or executes
the resulting SQL. Returned SQL still needs the shared read-only scorer.

The adapter exports our reviewed catalog, then calls the unmodified upstream
structural schema functions to create per-table stars, column indices, networks,
and bridge metadata. Its `reviewed-structural-v1` profile deliberately skips the
Spider NLP pipeline, name-based synonym inference, type correction, and value
recovery. Declared keys/references provide these facts; `same_col_idxs` records
only each column itself. This is an explicit schema adapter, **not a reproduction
of the full Spider preprocessing pipeline**.

Conversion receives a private SQLite snapshot after the shared dataset preflight.
Upstream opens that private copy using its original API. A subprocess timeout and
output limit bound the conversion. Dependency/setup errors, parser exceptions,
empty SQL, timeouts, and later execution/wrong-answer failures remain distinct.
Successful conversion alone does not establish correctness.

The profile accepts simple ASCII table/column names, up to 12 tables, 24 declared
references, and 256 columns. It requires at most one declared unique key per
table; keyless relationship tables retain duplicates. These bounds describe the
adapter, **not NatSQL's general expressiveness**.

## Earlier handwritten compatibility checks

The earlier, preserved compatibility report tested every one of the twelve
Chinook development questions: **11 matched; one did not**. The corrected native
IN encoding described above now makes the fresh manual check 12/12. Neither
report is a model result; the earlier failures remain visible rather than being
replaced by the corrected encodings. The five ordinary academic controls also matched: direct selection,
one hop, duplicate-sensitive two hops, AND predicates, and DISTINCT. Empty
answers and malformed queries are covered by the optional integration tests.

| Probe | Observed outcome |
| --- | --- |
| Chinook development, all 12 questions | 11 correct handwritten encodings; one wrong answer. |
| Queen purchase question, six-table chain | Compact encoding inferred `Artist.ArtistId = Customer.CustomerId`, an undeclared relationship; returned 1 row instead of 15. |
| Same question with all five explicit approved FK equalities | Full converter returned no SQL; retained as a development refinement failure. |
| Author and mentor via two `AUTHOR` aliases | Tested encoding collapsed to one physical table and returned each author twice, rather than author/mentor pairs. |
| Author affiliation and publisher via two `UNIVERSITY` aliases | Tested encoding reused the country relationship and returned an empty answer instead of two Alice records. |

Both repeated-table encodings were tried with upstream's default
`use_from_info=False` and an explicit `True` ablation. Both settings still
produced wrong answers for those encodings. This is evidence about the pinned
converter, these inputs, and this schema profile. It does not prove that no
alternative NatSQL encoding or configuration could answer the questions.

The local evidence is under ignored
`resources/icde-audit/natsql-full/compatibility.json`, including source NatSQL,
emitted SQL, returned/expected rows, errors, options, and input hashes. Its
`run_compatibility.py` reproduces all 12 development cases, the failed refinement,
and four repeated-role option probes. Source, wheel, and tokenizer manifests are
stored alongside it. Nothing in that directory is required by the repository
tests or committed as third-party code.

## Use it locally

Supply an external copy of the pinned official source and an isolated Python
environment containing NLTK plus English Punkt text tables. No external code or
dependencies are vendored in this repository. Upstream code is
[CC BY-SA 4.0](https://github.com/ygan/NatSQL/blob/dcd716fc9b920419cf838e490aad4a6f3b4fd231/README.md#license);
its data have separate terms.

The tested environment uses Python 3.12.3 and **NLTK 3.9.4**, rather than upstream's
original NLTK 3.4.3/spaCy 2.2.3 requirements. NLTK is the only direct third-party
dependency required by this value-bearing conversion path. No spaCy model or
upstream pickle files were loaded. Resolved dependency versions were click
8.5.0, joblib 1.6.0, regex 2026.9.29, tqdm 4.70.1, and cloudpickle 3.1.2. Exact
wheel hashes are retained in the local manifest. This is a modern-environment
compatibility result, not an exact legacy-environment reproduction.

English `punkt_tab` text tables came from official
[nltk_data commit 550b662](https://github.com/nltk/nltk_data/tree/550b6625bcef1f2abff2ff770a5a0d272c9c6b2a).
The ZIP's declared SHA-256 was verified before extracting only the four English
text tables and README. The adapter itself has no download step.

For the current local setup:

```bash
python3 tools/natsql_adapter.py \
  --upstream /tmp/sqlp-natsql-spike/upstream \
  --python /tmp/sqlp-natsql-spike/venv/bin/python \
  --nltk-data /tmp/sqlp-natsql-spike/nltk_data \
  --database build/chinook-ready/snapshot.sqlite \
  --catalog build/chinook-ready/catalog.json <<'QUERY'
select Album.Title from Album where Artist.Name = 'AC/DC'
QUERY
```

The pinned parser **requires a FROM clause**, even though the paper describes
removing FROM. Preserve the exact interface used in experiments. The adapter
does not insert it or repair syntax. Use the SQL-like form shown above and put
spaces around operators; upstream's tokenizer is sensitive to some compact
forms. Value-bearing queries use `sq=None` and `remove_values=False`. Normal
upstream `Args` defaults apply; `--use-from-info` enables the explicit ablation.

Run dependency-free adapter tests:

```bash
python3 tests/natsql_test.py
```

Run those tests plus the complete external converter tests:

```bash
SQLP_NATSQL_UPSTREAM=/tmp/sqlp-natsql-spike/upstream \
SQLP_NATSQL_PYTHON=/tmp/sqlp-natsql-spike/venv/bin/python \
SQLP_NATSQL_DATA=/tmp/sqlp-natsql-spike/nltk_data \
python3 tests/natsql_test.py
```

There are 11 adapter tests and 9 opt-in full-converter tests, including a public
six-table existence-query regression using native flat IN. The adapter tests
use local process fixtures only to check protocol handling and snapshot isolation;
they are not counted as NatSQL compatibility results.

## Model-run contract

`plan_natsql` constructs public requests without reading a database or calling
a model. `run_natsql_pilot` accepts an explicitly supplied provider, fresh
journal, request cap, settings, guide, and converter configuration. A converter
smoke test and dataset preflight precede paid calls. Raw responses and provider
usage are journaled before conversion/scoring; no syntax repair is performed.
Generation, conversion, execution, and wrong-answer failures remain distinct.

Reports use `kind: "natsql_first_attempt"` and the language `natsql`; they are
not renamed SQL/SQLP records. Their per-language summary retains full usage.
The two-condition saved viewer/analyzer expects SQL and SQLP, so inspect this
standalone report or the study aggregation rather than relabeling its records.
The interactive lab's manually editable languages remain SQL and SQLP.

The first study retained all twelve development questions, including the
six-table failure affected by its restrictive guide. The corrected follow-up
must retain the same complete question set and every newly generated failure.
The 32 synthetic role/depth templates and wider catalogs are separate controls,
outside this NatSQL comparison. The schema adapter omits
upstream linguistic preprocessing and value recovery; no result proves that
another NatSQL encoding, configuration, or full pipeline could not succeed.
No API credential is needed for the public demo or offline protocol tests.

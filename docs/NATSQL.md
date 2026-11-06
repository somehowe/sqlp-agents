# Optional NatSQL comparison

The pinned official NatSQL converter runs in an isolated compatibility
environment. A separate first-attempt model condition is now implemented in
[tools/natsql_pilot.py](../tools/natsql_pilot.py), using the corresponding
[NatSQL guide](../prompts/natsql.md). It receives the same public catalog facts
as SQL and SQLP, then converts and scores every generated query.

The [recorded study](STUDY_RESULTS.md) contains three repetitions of the same
12 Chinook questions for each of Sol, Luna, and Astra. Each model matched 33/36
expected answers. The six-table Queen question was rejected by the converter
in all nine attempts; those failures remain in the denominator. These results
describe this declared adapter/profile, not the full published NatSQL system.

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

Full conversion and shared row-bag scoring were checked on every one of the
12 Chinook development questions. **11 matched their expected answers; one did
not.** All twelve remain in the report. These are handwritten queries, not model
outputs. The five ordinary academic controls also matched: direct selection,
one hop, duplicate-sensitive two hops, AND predicates, and DISTINCT. Empty
answers and malformed queries are covered by the optional integration tests.

| Probe | Observed outcome |
|---|---|
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

There are 11 adapter tests and 8 opt-in full-converter tests. The adapter tests
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

The study retained all 12 eligible development questions, including the failed
six-table case. The 32 synthetic role/depth templates and wider catalogs are
separate controls, outside this NatSQL comparison. The schema adapter omits
upstream linguistic preprocessing and value recovery; no result proves that
another NatSQL encoding, configuration, or full pipeline could not succeed.
No API credential is needed for the public demo or offline protocol tests.

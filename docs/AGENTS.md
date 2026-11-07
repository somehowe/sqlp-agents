# Agent context — SQLP for AI Agents

Updated November 6, 2026. Read this for project context before working on the repository. It lives under `docs/`; agents working elsewhere may need to be explicitly directed to read it.

## Current direction and context

Build one SQLP-to-SQL compiler written in C++, one reproducible agent comparison, and one interactive demo for the ICDE 2027 Demonstrations Track. A restricted ARM mapping/view layer is implemented and tested. The visual Path Studio and corrected NatSQL experiments are implemented; the working four-page paper and captioned video are being reviewed and refreshed against them. Human interpretation review remains pending; the 30 reserved questions are unrun. Read [STUDY_RESULTS.md](STUDY_RESULTS.md) before proposing more experiments.

**Research question:** Given equivalent schema and relationship information, how does using SQLP instead of SQL affect AI agents' query correctness and total token usage?

The same model generates SQL and SQLP in separate conditions. The C++ compiler translates SQLP into SQL and parameters. SQLite executes both against the same data. The runner checks separately computed expected row bags and records prompts, attempts, results, diagnostics, and token usage. Constructed reference queries and record-walk checks share authored interpretations; independent human semantic review remains pending. The website makes the records and path translations inspectable.

Ryan is learning C++ while working on this research project with mentor Enamul Haque, supervisor Grant Weddell, and teammates Iris Mo and Samridh Sood. Ryan's latest update says Sam is planning a separate paper, while Iris has discussed a possible data-lake extension. Required delivery here currently rests with Ryan; agree on any additional contributions rather than assuming availability. This does not decide authorship or erase earlier contributions. Enamul does not require a native executor. He has offered writing help; do not assume he has approved every detail or accepted an assigned deadline.

The current user request and actual code take precedence over dated planning notes. [IDEA.md](IDEA.md) is the short team overview. This file contains the working context; the [shared schedule](SCHEDULES/SCHEDULE.md) and individual schedules contain proposed delivery dates.

## Responsibilities and time

| Person | Owns | Weekly time |
| --- | --- | --- |
| Ryan | Required delivery: compiler, pilot/evaluation, thin demo, paper | 12–15 hours |
| Iris | Considering a data-lake extension; any answer-review help here needs agreement | Availability unconfirmed |
| Samridh | Planning a separate paper; previous demo role is an optional handoff only | No required tasks here |

The Python runner orchestrates execution; the local viewer consumes its saved records. Seek a second human check of study answers from an available collaborator or mentor. Do not assume an AI-generated answer is independently reviewed research gold. Task ownership does not settle publication author order.

## How to work with Ryan

- Continue the existing repository. Teach and pair-program in small, meaningful increments; Ryan wants to understand and write the central logic.
- Start from a concrete input/output example. Explain the relevant concept, identify the interface and files, then help implement and check that increment.
- Ryan has explicitly authorized substantial autonomous implementation and offline experiments. Complete that work while leaving concrete examples and readable interfaces he can learn from; do not misrepresent generated implementation as work he already understands.
- Use C++20, `.h`/`.cc`, ordinary includes, and Make. Preserve the current organization. Introduce templates, inheritance, libraries, or frameworks when a concrete need justifies them.
- Explain ownership/lifetimes and invariants when they matter. Prefer a counterexample to a long abstract lecture. Avoid unnecessary quizzes or repeated permission questions for already authorized work.
- For documentation requests, complete the documents and check their consistency/links. Do not turn them into implementation sessions or run unrelated builds.
- Inspect uncommitted changes before editing. Preserve user work and Git identity. Do not commit, push, publish, install dependencies, or expose private mentor material without authorization. Do not add AI co-author trailers to commits. Conference AI-use disclosure is a separate obligation.

## Actual code at this update

The current checks cover the C++ suites, 360 Python tests, 1,200 differential
queries, and 61 Node checks (23 saved-viewer plus 38 Path Studio checks). Optional
integrations skip when their separate inputs are unavailable; fresh checkouts
can have more skips than Ryan's configured environment. A real Chromium
walkthrough passed 26 categories (14 original plus 12 visual-studio checks).
Recheck the current tree before claiming new changes passed. Tests are implementation checks, not model evidence.

The historical [study archive](STUDY_RESULTS.md) contains 108 reports, 2,628
experimental API calls, and 2,988 generated/scored outputs, including 108
superseded NatSQL calibration calls. Those first prompts wrongly omitted native
flat IN; their old 33/36 scores must not be used as evidence of SQLP superiority.
The valid original SQL/SQLP matrix is 1,872 calls/2,232 outputs, plus 216/216 in the
post-hoc required-view follow-up. A fresh 324-call corrected three-language study
and separate 108-call post-hoc NatSQL-guide sensitivity have their own frozen kits.

Corrected V2 answer matches, with 36 attempts per cell:

| Model | SQL | SQLP | NatSQL |
| --- | ---: | ---: | ---: |
| Sol | 36/36 | 36/36 | 36/36 |
| Luna | 35/36 | 36/36 | 33/36 |
| Astra | 36/36 | 36/36 | 36/36 |

All NatSQL outputs convert; three Luna executions time out. With generic IN-block
clarification (V3), NatSQL matches 36/36 for Sol/Astra and 35/36 for Luna, with one
execution timeout but higher total usage. Keep both guide versions and count all
input/output. Their instruction lengths differ; gains are not intrinsic syntax
constants. The study has twelve Chinook cases and 32 related templates, three
same-provider models, and three repetitions. Human semantic review is pending;
reserved questions remain unrun.

Local archives under `resources/experiments/`: `demo-study-2026-11-06/`,
`natsql-corrected-2026-11-06/`, and `natsql-guide-sensitivity-2026-11-06/`.
Their validators bind prompts, journals, raw responses, usage, inputs and scores.
The earlier six-run pilot remains separate. All paid studies share one private
cumulative spending ledger; no process renews the authorized $100 ceiling.

The [offline example](OFFLINE_EXAMPLE.md) and [prepared Path Studio bundle](DEMO_LAB.md)
work without private data or API access. The studio shows simultaneous role/alias
branches, SQL highlighting, animated path steps, pan/zoom/Fit, a catalog map,
bounded actual table samples, duplicate-sensitive answers, and recorded cost
charts. Animation explains the compiler trace, not execution time or lineage;
table previews are samples, not result provenance. Recorded bundles may add
NatSQL cards, but manual execution languages remain SQL and SQLP. No arbitrary
English-question model generation is wired into the local demo.

An additional 6,204 offline gold reconstructions validate the official NatSQL
converter on all 1,034 Spider examples, using author-linked databases and published
preprocessed metadata. They are not new model calls or a reproduction of original
trained-model accuracy. See [NATSQL.md](NATSQL.md). Review packets still need real
human interpretation checks; exporting one does not certify review occurred.

| Files | Present behavior |
| --- | --- |
| `src/path.h` | Owned root alias and feature vector; rejects empty components. Representation supports longer paths. |
| `src/mapping.h`, `.cc` | Five-field `ReferenceMapping`; lookup by source table and feature name. |
| `src/catalog.h`, `.cc` | Tables, Integer/String columns, uniqueness declarations, references, column lookup. |
| `src/sql.h`, `.cc` | `ColumnRef`, `JoinSpec`, one-join `ResolvedPath`, renderers, one-hop `resolve_path`. |
| `src/query.h`, `.cc` | Query AST and `compile_query`: one FROM binding, direct/multi-hop paths, AND equalities, DISTINCT, quoted SQL, typed parameters, and an owned `TranslationTrace`. Reuses joins by source binding plus role; records each path's ordered join IDs and terminal column. |
| `src/lexer.h`, `.cc` | Owned-input `Lexer::next()` produces tokens with source spans, decoded text, and checked int64 values. Supports keywords, identifiers, SQL-style quoting, punctuation, and comments; malformed input throws a located `LexerError`. |
| `src/parser.h`, `.cc`, `.yy` | Bison C++ grammar and lexer adapter. `parse_query` builds the existing query AST; `compile_sqlp` compiles text using a supplied C++ catalog. Syntax errors throw `ParseError` with a source location. Generated parser files stay under ignored `build/`. |
| `src/compiler_main.cc` | `build/sqlpc`: catalog flags and SQLP stdin produce version-1 JSON with SQL, typed parameters, path/join traces, or a structured diagnostic. Validates supplied reference metadata before compilation. See [COMPILER_CLI.md](COMPILER_CLI.md) for the provisional runner/UI interface. |
| `tools/validate_dataset.py` | Python standard-library preflight for actual schema, storage types, keys, and reference cardinality in a SQLite read snapshot. A version-1 JSON catalog also supplies `sqlpc` arguments through `compiler_arguments`. See [DATASET_CHECKS.md](DATASET_CHECKS.md). |
| `tools/inspect_schema.py`, `tools/arm_mapping.py` | Inspect actual SQLite declarations, then validate reviewed single-key entity/role mappings and materialize logical SQLite views plus a compiler catalog. Preserve duplicate relationship records with explicit keys. See [ARM_MAPPING.md](ARM_MAPPING.md). |
| `tools/prepare_chinook.py`, `examples/chinook/` | Hash-pinned licensed sample-store data, 12 development and 30 reserved questions, prepared query pairs, and separate Python answer plans. The same 42 questions also pass through ARM views. Human interpretation review is pending. See [WORKLOAD.md](WORKLOAD.md). |
| `tools/run_comparison.py` | Prepared SQL/SQLP execution, row-bag answer checking, typed records, and per-condition diagnostics/limits on one read-only snapshot. No model calls or usage estimates. See [QUERY_COMPARISON.md](QUERY_COMPARISON.md). |
| `tools/run_pilot.py`, `tools/model_provider.py`, `prompts/` | Dry-run requests, request-bound replay, one first attempt per language, raw provider usage/status, durable response journals, and artifact hashes. Explicit live adapter has been used; private key-file loading and explicit reasoning effort are supported. See [MODEL_PILOT.md](MODEL_PILOT.md). |
| `tools/batch_pilot.py` | Shared-context JSON batches with B1/B4/B12 controls; provider usage belongs to the whole response, never an invented per-query allocation. See [BATCH_PILOT.md](BATCH_PILOT.md). |
| `tools/prepare_path_study.py`, `examples/path_study/` | 32 matched synthetic templates at depths 1, 2, 4, and 6, two catalog widths, and alternate valid snapshots. Reference queries and Python record-walk answers share authored path plans; separate execution checks do not supply independent semantic gold. Human interpretation review is pending. |
| `tools/prepare_join_views.py` | Workload-independent, fixed-depth catalog views and a language-neutral mapping guide exposed to both conditions. See [JOIN_VIEWS.md](JOIN_VIEWS.md). |
| `tools/evaluate_snapshots.py` | Re-score saved queries on supplied alternate databases with no new model usage. Diagnostic evidence, not equivalence proof. See [SNAPSHOT_EVALUATION.md](SNAPSHOT_EVALUATION.md). |
| `tools/research_budget.py` | Durable shared spending reservations and raw response retention for explicitly authorized calls; estimates are not invoices. |
| `tools/demo_bundle.py`, `tools/demo_server.py`, `web/lab.*` | Prepared bundle and local Path Studio: branching aliases, SQL highlights/step playback, catalog map, bounded table previews, duplicate badges, snapshot edits, and recorded token charts. No model calls. See [DEMO_LAB.md](DEMO_LAB.md). |
| `tools/compiler_catalog.py` | Private bounded catalog argument files shared by both runners; avoids OS argv limits while keeping the JSON catalog semantics unchanged. |
| `tools/analyze_runs.py` | Descriptive paired correctness, failures, token totals/distributions and known-pair deltas; rejects incomplete or inconsistent batches. See [ANALYSIS.md](ANALYSIS.md). |
| `tools/experiment_kit.py` | Freeze and verify data/compiler/source/guides/questions and private scoring material; produce exact dry-run requests from the copied runtime. No API calls. See [EXPERIMENT_KIT.md](EXPERIMENT_KIT.md). |
| `tools/review_packet.py` | Export a portable question/answer packet for a person's review; validate immutable artifacts and editable case-bound CSV notes. See [REVIEW_PACKET.md](REVIEW_PACKET.md). |
| `tools/benchmark_prepared.py` | Counterbalanced offline execution/compiler timings, prepared source sizes, and optional static token counts using a pinned local vocabulary. These are not model usage. See [OFFLINE_BENCHMARKS.md](OFFLINE_BENCHMARKS.md). |
| `tools/natsql_adapter.py`, `tools/natsql_pilot.py`, `prompts/natsql.md` | Pinned official converter plus an actual standalone model condition, with retained conversion failures and full raw usage. The structural adapter omits upstream linguistic preprocessing. See [NATSQL.md](NATSQL.md). |
| `tools/create_demo.py`, `web/` | Creates fresh reference/deliberate-mistake records and displays saved reports locally with exact answers, usage, and interactive path mappings. No model/backend calls in the viewer. See [VIEWER.md](VIEWER.md). |
| `tools/offline_example.py` | One command creates demo records, freezes a kit, plans requests, executes synthetic replay through its copied runtime, and analyzes the resulting records. See [OFFLINE_EXAMPLE.md](OFFLINE_EXAMPLE.md). |
| `examples/academic/` | Fictional development fixture, catalog, and six manually prepared paired-query cases. Covers multi-hop roles, duplicate rows, and a finite mentor cycle. Not a held-out benchmark or model result. |
| `src/main.cc` | Constructs and prints `w.author.name`. It does not compile text. |
| `tests/` | C++ path/mapping/SQL/query/lexer/parser suites and Python CLI integration tests with standard-library SQLite. Legacy Test 10 still demonstrates manual SQL rendering. |

The legacy lower-level `resolve_path` still requires exactly two features and a caller-supplied source table/target alias. `compile_query` walks finite reference chains, allocating aliases, validating roots, checking key types/uniqueness at every hop, and checking the terminal column/literal type. Join reuse uses the source SQL alias and role; different prefixes stay distinct even when they reach the same table. Unlike the C++ object API, the CLI also rejects duplicate catalog declarations and validates unused references. C++ compilation does not check actual rows; use the separate Python preflight below.

The separate Python preflight has a 30-second CLI/default runner timeout and rejects malformed text encodings before scoring. It checks declarations against actual data: exact main-schema names, non-NULL storage classes, non-null/unique declared keys, and exactly one target per non-null reference source. It permits NULL ordinary attributes and duplicate source rows. Source-left SQLite equality preserves collation behavior; TEMP shadows are rejected. Use fixed tables/deterministic views. The CLI is read-only, and the API preserves caller transactions. Run before both experimental conditions; setup failures are separate from agent query failures.

`CompiledQuery.trace` owns join metadata and every path occurrence (SELECT order, then WHERE order). `PathTrace` stores clause/index, decoded `Path` components, ordered join IDs, and the final table/column. Join IDs are array positions local to one compilation; direct columns use no joins. Keep components structured because a quoted role may contain dots. Traces describe translation; source-text spans and row provenance remain future work.

Text parsing, multi-hop compilation, and AND filters work through C++ functions and the CLI. The Python runners execute prepared pairs and replayed model-shaped responses. Provider usage/status handling and live HTTP calls have now been exercised with real paired development results; a repair loop remains unimplemented. The saved viewer and interactive manual lab are implemented and have browser/DOM checks; a final human conference rehearsal remains. Boolean syntax beyond AND-connected literal equalities remains unsupported. The old empty-vector test failure-return issue is fixed; do not reassign it from historical notes.

Accepted text shape: `SELECT [DISTINCT] path [AS alias], ... FROM table [AS] alias [WHERE path = literal [AND path = literal ...]];`. The FROM alias and final semicolon are required; projection aliases require AS. A literal is a string or signed int64. Paths may follow finite chains, including repeated visits to a table. Every component before the terminal column must resolve to a declared reference. Extra statements, trailing non-comment input, OR, NOT, and parentheses are rejected.

From the repository root:

```bash
make
make test
make run
make test-viewer  # optional: Node.js 18+, no packages
# Optional memory/undefined-behavior checks: make test-sanitize
```

Requirements are GNU Make 4.3+, a C++20-capable `g++`, and Bison 3.8+. `make` builds the path demo and `build/sqlpc`; `make run` still prints `w.author.name`. The prepared runner and full `make test` require Python 3.11+ with its standard-library SQLite module. See [COMPILER_CHECKS.md](COMPILER_CHECKS.md) for reproducible differential/mutation checks and sanitizer limits. Run relevant build/tests when changing implementation, and report what actually ran.

## Next review increment

The object compiler, tokenizer, and Bison parser are implemented. Trace `tests/parser_test.cc` from SQLP text through `parse_query` and `compile_sqlp`. `Query.distinct` defaults to false; `std::optional` represents absent projection aliases; `SqlValue` preserves string/int64 types. `Query.where` is now a vector of `EqualityPredicate`, replacing the old optional single predicate; an empty vector omits WHERE. The prototype caps compilation at 63 distinct reference joins, 2,000 projections, and 512 equalities to stay within the tested SQLite profile. Conditions and parameters retain source order, and every term is validated. Tokens own their text and carry start/exclusive-end locations: zero-based byte offsets and one-based byte columns, with CRLF as one line break. Lexical failures propagate as `LexerError`; grammar failures use `ParseError`. Name/type errors still come from `compile_query` without source locations.

1. Ryan should trace one parser test and one saved SQLP run end to end, then explain the AST, role-specific joins, parameters, and scoring. The compiler is already multi-hop; do not repeat completed grammar work.
2. Review the completed [study](STUDY_RESULTS.md), question meanings, oracle plans, and demonstration. Use [review packets](REVIEW_PACKET.md) for readable row bags. The user authorized a cumulative $100 research ceiling; consult the shared private ledger before further paid work, rather than renewing a budget per process. Read keys privately with tools.credentials; never print or commit them. Inspect plans and never send prepared queries/gold rows to a query-generation model.
3. Rehearse the interactive lab, including path/alias mistakes and duplicate-sensitive contrasts. Freeze the submission after mentor/human feedback. The restricted ARM layer, NatSQL condition, batches, and view controls are implemented; do not rebuild them or add grammar/native execution before review. No reserved calls are needed to describe the completed bounded exploratory demo honestly.

Lexer conventions: bare identifiers use ASCII letters/underscore followed by letters/digits/underscore; double quotes allow other identifier characters. Strings use single quotes; repeated delimiters escape a quote and backslashes are literal. Decimal int64 values may have an adjacent minus sign. Plus signs, fractional/exponent/hex forms, empty quoted identifiers, embedded NULs, and unterminated quotes/comments are rejected. Line comments (`--`) and non-nested block comments (`/* ... */`) are skipped. Lexical acceptance does not imply that syntax or semantics are supported.

For a learning block, trace a concrete example before extending it. For an
explicit autonomous-work request, choose a useful unfinished increment from the
current code and deliver it within the user's authorized scope.

## Implemented ARM mapping/view subset

User-authorized timeline addition on November 1; implemented November 2. The thesis
constructs an ARM layer from existing RM schemas, then rewrites queries back to
SQL. This milestone implements a restricted, thesis-inspired mapping/view layer,
not full ARM identity reasoning or general ARM-to-RM physical schema generation.
The relevant actual thesis sections are 5.1.11–5.1.13 (printed pp.136–157); Chapter
4 supplies reference semantics and Chapter 6 supplies integration examples.

- `tools.arm_mapping` validates reviewed entity/column/role mappings against real
  single-column non-null primary keys and total declared foreign keys, emits
  SQLite views/catalog/identification metadata, and materializes a separate
  snapshot without modifying the source. See [ARM_MAPPING.md](ARM_MAPPING.md).
- Twenty-five tests cover rejection, multiplicity, snapshot binding, and publication.
  A separate keyed academic fixture has three passing prepared pairs; Chinook's
  42 prepared pairs also pass through generated views. This reuses questions and
  is not 84 independent tasks or evidence of agent improvement.
- The old duplicate `WROTE` fixture remains keyless. Do not assume its
  `(author, paper)` pair is unique; the separate ARM fixture has an explicit record key.
- Nov 9 is now a human walkthrough/review checkpoint, not an implementation promise.
  Freeze syntax/guides Nov 7 and choose direct tables or equivalent generated
  views before the Nov 11 study freeze. Both conditions receive the same facts.

Cross-source identity correspondences remain reviewed inputs. Automatic entity
resolution, alternative identifiers, preference/duration semantics, inheritance,
and full `BuildARM`/`Unfold` are deferred. Schema construction is setup work;
measure it separately from per-question generation tokens. Describe accepted
behavior as a restricted mapping/view layer, without claiming new theory or a
complete implementation of the thesis.

## Deadline language profile and semantics

Required profile: projections, one FROM binding/alias, finite multi-hop named references, literal equality, AND, DISTINCT, parameters, and basic name/type checks. Preserve the evaluated profile when reproducing recorded runs; later syntax changes need their own condition. Correlated EXISTS, UNION, multiple FROM bindings, broader Boolean syntax, and additional SQL features remain later learning goals. Aggregates, ordering, optional/null navigation, general ARM identity reasoning, native execution, B+ trees, and automatic discovery are outside the required release.

Important implementation contracts:

- References are approved, non-null, total, type-compatible links to unique targets. Check these assumptions on imported rows as well as declarations. INNER JOIN expansion depends on them.
- A reference role is part of its meaning. Two references to the same target table may require distinct aliases. Reuse joins by the appropriate source binding/reference prefix, not just table name. Finite paths may revisit tables.
- Parameterize literal values. Validate/quote catalog identifiers separately; parameters do not substitute table or column names.
- Reject unsupported syntax, unknown names, malformed literals, numeric overflow, and trailing input. Include useful source locations in parser diagnostics.
- Keep result multiplicity explicit. DISTINCT removes duplicate projected rows; otherwise do not silently compare sets when duplicates matter. No row order is promised without supported ordering semantics.
- Concrete keys, entity identity, and storage locations are different concepts. Matching names or literal IDs does not prove cross-source identity. Current key-based navigation is a restricted reference implementation, not full BuildARM/Unfold or identity resolution.

## Component handoffs

Agree on examples before independently building formats:

- **Catalog:** versioned table/column types and named references with physical source/target columns and validated assumptions. Both conditions receive equivalent facts.
- **Compiler:** SQLP text plus catalog in; SQL, ordered typed parameters, minimal path-to-join mappings, or structured diagnostics out. It does not execute SQL.
- **Runner:** question/condition in; saved model/version/settings, guides, data/catalog versions, source query, compiled SQL, parameters, rows, errors, token usage, and timing out.
- **Website:** the saved viewer consumes records; the interactive lab compiles and executes manual SQL/SQLP edits and switches snapshots. These interventions make no model calls. Natural-language model generation from the browser remains unimplemented.

Keep int64 values exact across JSON, using the existing lossless representation. Python's SQLite adapter supplies execution. A direct C++ SQLite wrapper is outside the required compiler scope.

The implemented CLI encodes both string and int64 parameter values as JSON strings, with a separate `type` field. The runner converts `int64` values to Python integers before binding. Successful responses include `trace.joins` and `trace.paths`; consumers should ignore unknown fields. Error responses contain no partial trace. Runners accept version-1 JSON catalogs and transport their compiler arguments through private files. CLI catalog failures are setup errors and must be treated consistently across SQL/SQLP conditions, separately from query failures. See [COMPILER_CLI.md](COMPILER_CLI.md) for the full contract and exit codes.

Both runners use private catalog argument files. The pilot smoke-compiles a direct-column query before provider calls, freezes a private compiler binary copy, holds one read transaction, hashes the serialized snapshot including WAL pages, excludes gold from requests, and journals responses before scoring. Refusals/incomplete/empty outputs are failed attempts; provider/protocol failures and returned-model drift abort the pilot. Usage missing from any attempt makes the condition total unknown, not zero. Replay is labeled separately from live calls.

The saved viewer lets a visitor inspect both attempts/answers/tokens and path mappings. The interactive lab additionally executes manual edits and switches snapshots without model calls. Arbitrary questions remain **not independently benchmark-scored** until an expected answer is established. Label development fixtures, recorded real runs, and live runs accurately.

## Evaluation and research claims

The required experiment compares SQL with SQLP using the same approved schema/relationship information, model/settings, questions, data, corresponding guides, and feedback policy. Keep calls separate so conditions cannot see each other's answer.

First-attempt generation is primary. Repairs are optional, with the same allowance for both conditions, for example two revisions. Never give the agent expected answers or gold correctness labels. Compiler/database diagnostics and query results are distinct from the scoring oracle.

The completed comparison uses three models from the same provider, 12 Chinook
development questions, and 32 related synthetic templates at four path depths.
The 30 reserved questions remain unrun. Expected row bags come from separate
record-walk oracles and fixture interpretations; this is automated validation,
not independent human semantic review. Retain that limitation in the paper.

The core experiment is exploratory. Native one-question and JSON B1/B4/B12
conditions are distinct; B1 controls the JSON response format. View availability
and required-root use are also distinct: the latter was added after observing
low adoption and must remain labeled post hoc. Both conditions receive the same
facts, and every attempted eligible task remains in its denominator. Any future
reserved study needs reviewed meanings/answers and a protocol freeze before its
first model calls; it is not a prerequisite for reporting this bounded demo.

Measure answer correctness and total provider-reported input/output tokens across all attempts, including guides, schema, feedback, and retries. Preserve raw usage; cache/reasoning fields may overlap totals and must not be double-counted. Report tokens per task including failures; tokens per correct task is undefined when none succeed. Query bytes, latency, and API spending are separate secondary measures. Query-source savings do not establish database-storage savings or human productivity gains.

Compare result values and multiplicity, not exact SQL strings. Execution success, agreement between backends, or an LLM judge alone is insufficient. Fix compiler defects and rerun affected comparisons rather than treating them as evidence about SQLP itself.

The full paired results are recorded in [STUDY_RESULTS.md](STUDY_RESULTS.md): SQLP used more total tokens for native independent requests and fewer in shared-context batches on these tasks. This does not establish general efficiency or accuracy superiority. Earlier scripted probes establish feasibility and illustrate failure cases. The human SQLP study motivates the agent question; NatSQL/DIN-SQL already use intermediate representations and DAIL-SQL studies token efficiency. The proposed interest is named relationship roles, path length, and full interaction tokens. Stronger compiler checks mean the tested contribution is **SQLP plus compiler/checker**; syntax-only claims require further controls. Report positive, mixed, or negative findings without promising acceptance.

NatSQL has actual standalone model conditions, not relabeled SQL/SQLP records.
Its first guide was defective because it excluded native flat IN. Preserve that
archive as calibration; do not claim SQLP superiority from its failures. Fresh
V2 comparisons and V3 guide sensitivity retain every attempted task and full
usage, and must remain separate. Use their frozen guides rather than assuming
current prompts reproduce older runs. The official-gold reconstruction check
uses published metadata; Chinook uses a declared structural adapter. Neither
reproduces the original paper's complete trained-model system. See [NATSQL.md](NATSQL.md).

The join-view control expands all catalog prefixes through a fixed depth,
without reading questions or gold. Optional views were used in only 25/108 SQL
and 2/108 SQLP attempts. The required-view follow-up reached 108/108 use and
answer matches per language. These controls do not show that optimized view
selection is inferior. Alternate-snapshot checks can expose accidental matches;
passing several snapshots still does not prove semantic equivalence.


The offline Chinook microbenchmark found shorter SQLP prepared queries (35.9% fewer bytes, 42.8% fewer static query tokens), but guide + task + prepared-query text used 1.54% more static tokens overall. The fixed guide overhead can offset query savings. Static counts omit actual model reasoning, failed generations, retries, and provider framing; they cannot establish total API usage or agent accuracy. Timing measures include compiler startup/IPC and SQLite execution separately. Preserve the mixed result rather than promise a win.

## Delivery and paper context

| Checkpoint, 2026 | Target |
| --- | --- |
| Nov 6 — review | Visual Path Studio, corrected comparisons and guide sensitivity complete; refresh local paper/video and obtain mentor review. |
| Nov 7 | Trace and explain a real example; check question meanings, duplicate handling, and claims. |
| Nov 9 | Mentor walkthrough of paper and demonstration if available; internal target, not an official outline deadline. |
| Nov 11 | Freeze submission claims/scenarios after feedback; freeze any optional reserved follow-up before calls. |
| Nov 14 | Incorporate human/mentor feedback and rehearse the actual interactions. |
| Nov 18 | Reconcile figures and numbers with evidence; finalize four pages including references. |
| Nov 21 | Fresh-checkout reproduction, human browser rehearsal, contributor and video checks. |
| Nov 22 | Internal submission target; Nov 23 is the official demo deadline and contingency. |


The ICDE demo paper uses IEEE format, four pages including references. It must explain motivation, architecture/core techniques, novelty/significance, scenarios, and audience interaction. Video is encouraged, at most five minutes/50 MB. Future submission should follow the then-current call, including AI-use disclosure. A polished website alone does not supply research evidence.

The [demo CFP](https://icde2027.github.io/cf-demonstrations.html) lists Nov 23 at
11:59 pm AoE, while the [general dates page](https://icde2027.github.io/important-dates.html)
says 5 pm Pacific. Keep the internal Nov 22 target and confirm the portal's
actual cutoff before submission.

The separate UR2PhD Part 1 assignment requests a section titled “Research context and problem statement”: motivation, related work/limitations, and a concise research problem. It does not prescribe IEEE format; IEEE is used to prepare for ICDE. Preserve/revise earlier substantive writing rather than erase its history. Its Canvas deadline/group number must be checked separately. The current course draft has no results.

## Documentation and historical material

- Shared entry point: [IDEA.md](IDEA.md). Agent handoff: this file. Delivery details: [SCHEDULES/SCHEDULE.md](SCHEDULES/SCHEDULE.md) and individual schedules.
- Keep the current code-status/next-step sections here updated after meaningful implementation progress. Avoid another competing roadmap or status file.
- `resources/` is Git-ignored and contains optional local references and historical notes. Shared docs should be understandable without that directory.
- `resources/NEW.md`, `FINDINGS.md`, and `LEARNING.md` are historical. Their native-engine priorities, old role assignments, grammar promises, and earlier snapshots are superseded. Retain useful learning preferences and source detail, not stale instructions.
- Local deeper material includes `resources/paper.tex`, `resources/PhD_Thesis_Oct_10_Enam (1).pdf`, and `resources/icde-audit/`. Verify exact thesis locations/pages before quoting; do not import page maps from a differently named copy without checking.
- Course source is `resources/ASSIGMENTS.tex` plus `ASSIGMENTS.bib` (spelling intentional). It uses the IEEEtran class and BibTeX style; upload both to Overleaf. Local build: `pdflatex`, `bibtex`, `pdflatex`, `pdflatex` from `resources/`.
- The separate conference draft is local-only under `resources/icde2027/`; do not commit or publish it. Its four pages include the completed bounded exploratory study. The captioned video is also local. Do not publish draft content or private review correspondence through source documentation.
- `resources/BACKUP_README.md` preserves the old README notes. Private mentor documents and generated companion code should not be published or adopted wholesale. The separate AI-built repository is a reference source, not evidence of ownership or a working feature in this repository.

Consult sources in `resources/` when needed for a specific semantic question; do not make Ryan reread the entire thesis before the next small compiler increment.

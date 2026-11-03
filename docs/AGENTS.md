# Agent context — SQLP for AI Agents

Updated November 3, 2026. Read this for project context before working on the repository. It lives under `docs/`; agents working elsewhere may need to be explicitly directed to read it.

## Current direction and context

Build one SQLP-to-SQL compiler written in C++, one reproducible agent comparison, and one interactive demo for the ICDE 2027 Demonstrations Track. A restricted ARM mapping/view layer is implemented and tested. Actual model experiments and human review of study answers remain open.

**Research question:** Given equivalent schema and relationship information, how does using SQLP instead of SQL affect AI agents' query correctness and total token usage?

The same model generates SQL and SQLP in separate conditions. The C++ compiler translates SQLP into SQL and parameters. SQLite executes both against the same data. The runner checks independently prepared answers and records prompts, attempts, results, diagnostics, and token usage. The website makes those records and path translations inspectable.

Ryan is learning C++ while working on this research project with mentor Enamul Haque, supervisor Grant Weddell, and teammates Iris Mo and Samridh Sood. Ryan has since reported that Iris and Sam may no longer participate actively; plan required delivery around Ryan until their availability is confirmed. This does not decide authorship or erase earlier contributions. Enamul does not require a native executor. He has offered writing help; do not assume he has approved every detail or accepted an assigned deadline.

The current user request and actual code take precedence over dated planning notes. [IDEA.md](IDEA.md) is the short team overview. This file contains the working context; the [shared schedule](SCHEDULES/SCHEDULE.md) and individual schedules contain proposed delivery dates.

## Responsibilities and time

| Person | Owns | Weekly time |
|---|---|---|
| Ryan | Required delivery: compiler, pilot/evaluation, thin demo, paper | 12–15 hours |
| Iris | Previous runner/evaluation role; optional help with independent answer review | Availability unconfirmed |
| Samridh | Previous website/demo role; optional help with record viewer | Availability unconfirmed |

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

Verified November 3 from a fresh copy of public repository files: `make -j2 all test` passes the C++ suites and discovers 255 Python tests, with ten optional tests skipped (eight NatSQL, one downloaded Chinook integration, one tokenizer integration). It also passes 1,200 differential queries against an independent path oracle. The earlier configured environment passed the optional integrations. The core checks need no model access or private resources. The six-case academic fixture and 42-question Chinook workload pass prepared-query checks. These are implementation checks, not AI results. Recheck the working tree before assigning unfinished work.

The viewer's 23 Node checks pass, including actual ARM artifacts. Two local frozen
kits are available at `build/experiment-ready-2026-11-02/` and
`build/experiment-arm-ready-2026-11-02/`; each verifies and plans 24 development
requests plus 60 reserved requests without API use. Synthetic prepared-query
replay through their copied runtimes, analysis, and viewer also passes, with
unknown usage and explicit synthetic labels. These generated paths are ignored;
use [EXPERIMENT_KIT.md](EXPERIMENT_KIT.md) to reproduce them. No rendered browser
check or actual model experiment has been performed.

The [complete offline example](OFFLINE_EXAMPLE.md) now reproduces the academic
demo, freezes its runtime, plans twelve requests, replays explicitly synthetic
reference responses, and analyzes them with unknown usage. The viewer checks
all its generated prepared, deliberate-mistake, and replay cases. The workflow
under `.github/workflows/offline-checks.yml` runs these checks for pull requests
and pushes to `main`; no API credentials are supplied.

| Files | Present behavior |
|---|---|
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
| `tools/run_pilot.py`, `tools/model_provider.py`, `prompts/` | Dry-run requests, request-bound replay, one first attempt per language, raw provider usage/status, durable response journals, and artifact hashes. Optional explicit live adapter is implemented but has not been used. See [MODEL_PILOT.md](MODEL_PILOT.md). |
| `tools/compiler_catalog.py` | Private bounded catalog argument files shared by both runners; avoids OS argv limits while keeping the JSON catalog semantics unchanged. |
| `tools/analyze_runs.py` | Descriptive paired correctness, failures, token totals/distributions and known-pair deltas; rejects incomplete or inconsistent batches. See [ANALYSIS.md](ANALYSIS.md). |
| `tools/experiment_kit.py` | Freeze and verify data/compiler/source/guides/questions and private scoring material; produce exact dry-run requests from the copied runtime. No API calls. See [EXPERIMENT_KIT.md](EXPERIMENT_KIT.md). |
| `tools/benchmark_prepared.py` | Counterbalanced offline execution/compiler timings, prepared source sizes, and optional static token counts using a pinned local vocabulary. These are not model usage. See [OFFLINE_BENCHMARKS.md](OFFLINE_BENCHMARKS.md). |
| `tools/natsql_adapter.py` | Optional isolated official NatSQL converter, with reviewed structural schema export and explicit compatibility limits. Not a third condition in the paired model runner. See [NATSQL.md](NATSQL.md). |
| `tools/create_demo.py`, `web/` | Creates fresh reference/deliberate-mistake records and displays saved reports locally with exact answers, usage, and interactive path mappings. No model/backend calls in the viewer. See [VIEWER.md](VIEWER.md). |
| `tools/offline_example.py` | One command creates demo records, freezes a kit, plans requests, executes synthetic replay through its copied runtime, and analyzes the resulting records. See [OFFLINE_EXAMPLE.md](OFFLINE_EXAMPLE.md). |
| `examples/academic/` | Fictional development fixture, catalog, and six manually prepared paired-query cases. Covers multi-hop roles, duplicate rows, and a finite mentor cycle. Not a held-out benchmark or model result. |
| `src/main.cc` | Constructs and prints `w.author.name`. It does not compile text. |
| `tests/` | C++ path/mapping/SQL/query/lexer/parser suites and Python CLI integration tests with standard-library SQLite. Legacy Test 10 still demonstrates manual SQL rendering. |

The legacy lower-level `resolve_path` still requires exactly two features and a caller-supplied source table/target alias. `compile_query` walks finite reference chains, allocating aliases, validating roots, checking key types/uniqueness at every hop, and checking the terminal column/literal type. Join reuse uses the source SQL alias and role; different prefixes stay distinct even when they reach the same table. Unlike the C++ object API, the CLI also rejects duplicate catalog declarations and validates unused references. C++ compilation does not check actual rows; use the separate Python preflight below.

The separate Python preflight has a 30-second CLI/default runner timeout and rejects malformed text encodings before scoring. It checks declarations against actual data: exact main-schema names, non-NULL storage classes, non-null/unique declared keys, and exactly one target per non-null reference source. It permits NULL ordinary attributes and duplicate source rows. Source-left SQLite equality preserves collation behavior; TEMP shadows are rejected. Use fixed tables/deterministic views. The CLI is read-only, and the API preserves caller transactions. Run before both experimental conditions; setup failures are separate from agent query failures.

`CompiledQuery.trace` owns join metadata and every path occurrence (SELECT order, then WHERE order). `PathTrace` stores clause/index, decoded `Path` components, ordered join IDs, and the final table/column. Join IDs are array positions local to one compilation; direct columns use no joins. Keep components structured because a quoted role may contain dots. Traces describe translation; source-text spans and row provenance remain future work.

Text parsing, multi-hop compilation, and AND filters work through C++ functions and the CLI. The Python runners execute prepared pairs and replayed model-shaped responses. Provider usage/status handling and optional live HTTP calls are implemented and tested offline; no actual paired model results or repair loop exist. A local saved-record viewer is implemented; visual browser testing remains to be done. Boolean syntax beyond AND-connected literal equalities remains unsupported. The old empty-vector test failure-return issue is fixed; do not reassign it from historical notes.

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

## Next compiler increment

The object compiler, tokenizer, and Bison parser are implemented. Trace `tests/parser_test.cc` from SQLP text through `parse_query` and `compile_sqlp`. `Query.distinct` defaults to false; `std::optional` represents absent projection aliases; `SqlValue` preserves string/int64 types. `Query.where` is now a vector of `EqualityPredicate`, replacing the old optional single predicate; an empty vector omits WHERE. The prototype caps compilation at 63 distinct reference joins, 2,000 projections, and 512 equalities to stay within the tested SQLite profile. Conditions and parameters retain source order, and every term is validated. Tokens own their text and carry start/exclusive-end locations: zero-based byte offsets and one-based byte columns, with CRLF as one line break. Lexical failures propagate as `LexerError`; grammar failures use `ParseError`. Name/type errors still come from `compile_query` without source locations.

1. Ryan should trace one parser test and one saved SQLP run end to end, then explain the AST, role-specific joins, parameters, and scoring. The compiler is already multi-hop; do not repeat completed grammar work.
2. Review the Chinook questions/answer plans and frozen kit, then choose model/access/spending. The user explicitly wants no model API use until they provide access later. Inspect a dry-run plan first; never send prepared queries/gold rows. Use [MODEL_PILOT.md](MODEL_PILOT.md). The development fixture and offline replay are not a study.
3. Once access is supplied, run a small real paired development pilot, inspect failures/usage, and freeze the query language/guides. The restricted ARM layer is already implemented; review its assumptions rather than rebuilding it. Use the viewer/analyzer for inspection; extra grammar/native execution is lower priority. Large catalogs use bounded argument files; prompts and data still need explicit size limits.

Lexer conventions: bare identifiers use ASCII letters/underscore followed by letters/digits/underscore; double quotes allow other identifier characters. Strings use single quotes; repeated delimiters escape a quote and backslashes are literal. Decimal int64 values may have an adjacent minus sign. Plus signs, fractional/exponent/hex forms, empty quoted identifiers, embedded NULs, and unterminated quotes/comments are rejected. Line comments (`--`) and non-nested block comments (`/* ... */`) are skipped. Lexical acceptance does not imply that syntax or semantics are supported.

These are incremental tasks, not authorization to implement the entire sequence unattended. For Ryan's next block, select the smallest useful unfinished step after reading the current code.

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

Required profile: projections, one FROM binding/alias, finite multi-hop named references, literal equality, AND, DISTINCT, parameters, and basic name/type checks. Freeze the evaluated profile by November 7. Correlated EXISTS, UNION, multiple FROM bindings, broader Boolean syntax, and additional SQL features remain later learning goals. Aggregates, ordering, optional/null navigation, general ARM identity reasoning, native execution, B+ trees, and automatic discovery are outside the required release.

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
- **Website:** consumes these records. Start with labeled sample records, then real saved runs, then a thin local connection for live runs. A C++ HTTP server or compiler daemon is unnecessary for the first release.

Keep int64 values exact across JSON, using an agreed lossless representation. Python's SQLite adapter is the planned execution route. A direct C++ SQLite wrapper is not needed for the compiler milestone.

The implemented CLI encodes both string and int64 parameter values as JSON strings, with a separate `type` field. The runner converts `int64` values to Python integers before binding. Successful responses now include additive `trace.joins` and `trace.paths` fields; consumers should ignore unknown fields. Error responses contain no partial trace. Catalog flags are a provisional handoff to review, not a finalized dataset format. CLI catalog failures are setup errors and must be treated consistently across SQL/SQLP conditions, separately from query failures. See [COMPILER_CLI.md](COMPILER_CLI.md) for the full contract and exit codes.

Both runners use private catalog argument files. The pilot smoke-compiles a direct-column query before provider calls, freezes a private compiler binary copy, holds one read transaction, hashes the serialized snapshot including WAL pages, excludes gold from requests, and journals responses before scoring. Refusals/incomplete/empty outputs are failed attempts; provider/protocol failures and returned-model drift abort the pilot. Usage missing from any attempt makes the condition total unknown, not zero. Replay is labeled separately from live calls.

The implemented viewer lets a visitor choose a question, inspect both attempts/answers/tokens, and click a path to see its mapping. Arbitrary questions remain **not independently benchmark-scored** until an expected answer is established. Label development fixtures, recorded real runs, and live runs accurately.

## Evaluation and research claims

The required experiment compares SQL with SQLP using the same approved schema/relationship information, model/settings, questions, data, corresponding guides, and feedback policy. Keep calls separate so conditions cannot see each other's answer.

First-attempt generation is primary. Repairs are optional, with the same allowance for both conditions, for example two revisions. Never give the agent expected answers or gold correctness labels. Compiler/database diagnostics and query results are distinct from the scoring oracle.

Start with one capable accessible model, one reviewed dataset, 12 development questions, and a target of 30 held-out questions. Ryan prepares reference answers and seeks a separate collaborator/mentor review; reduce the workload before evaluation if that review cannot be completed. Include longer paths, competing roles, repeated references, empty answers, and duplicate-sensitive cases. Use contrasting rows so wrong relationships do not accidentally produce correct answers.

Use development outcomes to improve the guides/system. Freeze the evaluated features and language guides on November 7. Finish independent reference-answer checks and freeze held-out questions, task eligibility, data/catalog versions, workload size, and run settings on November 11, before any held-out model calls; run the main comparison November 12–14. Keep failures, including unsupported model outputs on eligible tasks. Choose any smaller workload for capacity before inspecting held-out performance. Repetitions of one task are not extra independent questions.

Measure answer correctness and total provider-reported input/output tokens across all attempts, including guides, schema, feedback, and retries. Preserve raw usage; cache/reasoning fields may overlap totals and must not be double-counted. Report tokens per task including failures; tokens per correct task is undefined when none succeed. Query bytes, latency, and API spending are separate secondary measures. Query-source savings do not establish database-storage savings or human productivity gains.

Compare result values and multiplicity, not exact SQL strings. Execution success, agreement between backends, or an LLM judge alone is insufficient. Fix compiler defects and rerun affected comparisons rather than treating them as evidence about SQLP itself.

No paired model result has been established in the existing planning work. Scripted development probes establish feasibility and illustrate failure cases. The human SQLP study motivates the agent question; NatSQL/DIN-SQL already use intermediate representations and DAIL-SQL studies token efficiency. The proposed interest is named relationship roles, path length, and full interaction tokens. Stronger compiler checks mean the tested contribution is **SQLP plus compiler/checker**; syntax-only claims require further controls. Report positive, mixed, or negative findings without promising acceptance.

NatSQL remains optional. The pinned [official converter](https://github.com/ygan/NatSQL/tree/dcd716fc9b920419cf838e490aad4a6f3b4fd231) now runs in an isolated modern environment, using a reviewed structural-schema export rather than its entire Spider preprocessing pipeline. Nineteen tests pass with that environment. Eleven of twelve handwritten Chinook development encodings match the oracle; the sixth-table case infers an undeclared ID join and returns a wrong answer. Repeated-table role probes also fail for the tested encodings. These are converter compatibility observations, not model results or proofs of impossibility. Do not discard the failing case to manufacture a favorable subset. See [NATSQL.md](NATSQL.md). A matched NatSQL guide and third model condition are not implemented. Add them only if a declared eligible profile, truthful failure handling, and time permit by Nov 7. DIN-SQL and DAIL-SQL inform workflow/prompt/evaluation design; they are not automatically additional conditions. Advertised credits do not establish activated model access or authorize spending.

The offline Chinook microbenchmark found shorter SQLP prepared queries (35.9% fewer bytes, 42.8% fewer static query tokens), but guide + task + prepared-query text used 1.54% more static tokens overall. The fixed guide overhead can offset query savings. Static counts omit actual model reasoning, failed generations, retries, and provider framing; they cannot establish total API usage or agent accuracy. Timing measures include compiler startup/IPC and SQLite execution separately. Preserve the mixed result rather than promise a win.

## Delivery and paper context

| Checkpoint, 2026 | Target |
|---|---|
| Nov 1 | Multi-hop compiler, checked development fixture, offline pilot/replay, local viewer, and descriptive analysis. Real model results remain open. |
| Nov 4 | Review frozen offline inputs, question meanings, and ARM mappings; choose model/access/budget when available. |
| Nov 7 | Run a real development pilot if access is supplied; inspect failures and freeze syntax/guides. Decide whether a matched NatSQL condition is feasible. |
| Nov 9 | Human mapping/demo walkthrough and proposed mentor review of outline/architecture/actual evidence; not an official outline deadline. |
| Nov 11 | Independent answer checks and held-out workload/settings freeze, including any accepted mappings/views and equal SQL access. |
| Nov 14 | Nov 12–14 main comparison and initial analysis complete; connected demo. |
| Nov 18 | First complete four-page draft. |
| Nov 21 | Reviewed paper, reproducible artifact, rehearsal, and video. |
| Nov 22 | Internal submission target; Nov 23 is the official demo deadline and contingency. |

The ICDE demo paper uses IEEE format, four pages including references. It must explain motivation, architecture/core techniques, novelty/significance, scenarios, and audience interaction. Video is encouraged, at most five minutes/50 MB. Future submission should follow the then-current call, including AI-use disclosure. A polished website alone does not supply research evidence.

The separate UR2PhD Part 1 assignment requests a section titled “Research context and problem statement”: motivation, related work/limitations, and a concise research problem. It does not prescribe IEEE format; IEEE is used to prepare for ICDE. Preserve/revise earlier substantive writing rather than erase its history. Its Canvas deadline/group number must be checked separately. The current course draft has no results.

## Documentation and historical material

- Shared entry point: [IDEA.md](IDEA.md). Agent handoff: this file. Delivery details: [SCHEDULES/SCHEDULE.md](SCHEDULES/SCHEDULE.md) and individual schedules.
- Keep the current code-status/next-step sections here updated after meaningful implementation progress. Avoid another competing roadmap or status file.
- `resources/` is Git-ignored and contains optional local references and historical notes. Shared docs should be understandable without that directory.
- `resources/NEW.md`, `FINDINGS.md`, and `LEARNING.md` are historical. Their native-engine priorities, old role assignments, grammar promises, and earlier snapshots are superseded. Retain useful learning preferences and source detail, not stale instructions.
- `resources/AUDIT_2026-11-01.md` contains optional local review notes and scratch experiment details; current behavior is documented in the component guides.
- Local deeper material includes `resources/paper.tex`, `resources/PhD_Thesis_Oct_10_Enam (1).pdf`, and `resources/icde-audit/`. Verify exact thesis locations/pages before quoting; do not import page maps from a differently named copy without checking.
- Course source is `resources/ASSIGMENTS.tex` plus `ASSIGMENTS.bib` (spelling intentional). It uses the IEEEtran class and BibTeX style; upload both to Overleaf. Local build: `pdflatex`, `bibtex`, `pdflatex`, `pdflatex` from `resources/`.
- The separate conference draft is local-only under `resources/icde2027/`; do not commit or publish it. Its current four pages report offline observations only and explicitly leave actual model outcomes open.
- `resources/BACKUP_README.md` preserves the old README notes. Private mentor documents and generated companion code should not be published or adopted wholesale. The separate AI-built repository is a reference source, not evidence of ownership or a working feature in this repository.

Consult sources in `resources/` when needed for a specific semantic question; do not make Ryan reread the entire thesis before the next small compiler increment.

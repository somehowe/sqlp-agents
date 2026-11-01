# Agent context — SQLP for AI Agents

Updated November 1, 2026. Read this for project context before working on the repository. It lives under `docs/`; agents working elsewhere may need to be explicitly directed to read it.

## Current direction and context

Build one SQLP-to-SQL compiler written in C++, one reproducible agent comparison, and one interactive demo for the ICDE 2027 Demonstrations Track.

**Research question:** Given equivalent schema and relationship information, how does using SQLP instead of SQL affect AI agents' query correctness and total token usage?

The same model generates SQL and SQLP in separate conditions. The C++ compiler translates SQLP into SQL and parameters. SQLite executes both against the same data. The runner checks independently prepared answers and records prompts, attempts, results, diagnostics, and token usage. The website makes those records and path translations inspectable.

Ryan is learning C++ while working on this research project with mentor Enamul Haque, supervisor Grant Weddell, and teammates Iris Mo and Samridh Sood. Enamul does not require a native executor. He has offered writing help; do not assume he has approved every detail or accepted an assigned deadline.

The current user request and actual code take precedence over dated planning notes. [IDEA.md](IDEA.md) is the short team overview. This file contains the working context; the [shared schedule](SCHEDULES/SCHEDULE.md) and individual schedules contain proposed delivery dates.

## Responsibilities and time

| Person | Owns | Weekly time |
|---|---|---|
| Ryan | C++ AST, lexer/parser, binding/path checks, SQL generation, compiler tests | 12–15 hours |
| Iris | Model calls, SQLite loading/execution, prompts, scoring, token records, analysis | 8–10 hours |
| Samridh | TypeScript interface, query/answer comparison, path inspection, demo/video | 8–10 hours |

Iris's runner orchestrates execution; Sam's UI calls it. Iris and Sam share question/reference-answer preparation and cross-check each other's work. Ryan checks reference assumptions. Everyone writes/reviews the paper. Task ownership does not settle publication author order.

## How to work with Ryan

- Continue the existing repository. Teach and pair-program in small, meaningful increments; Ryan wants to understand and write the central logic.
- Start from a concrete input/output example. Explain the relevant concept, identify the interface and files, then help implement and check that increment.
- Bounded implementation, scaffolding, and examples are welcome when requested. Do not replace the project with a large generated implementation for Ryan to study afterward.
- Use C++20, `.h`/`.cc`, ordinary includes, and Make. Preserve the current organization. Introduce templates, inheritance, libraries, or frameworks when a concrete need justifies them.
- Explain ownership/lifetimes and invariants when they matter. Prefer a counterexample to a long abstract lecture. Avoid unnecessary quizzes or repeated permission questions for already authorized work.
- For documentation requests, complete the documents and check their consistency/links. Do not turn them into implementation sessions or run unrelated builds.
- Inspect uncommitted changes before editing. Preserve user work and Git identity. Do not commit, push, publish, install dependencies, or expose private mentor material without authorization. Do not add AI co-author trailers to commits. Conference AI-use disclosure is a separate obligation.

## Actual code at this update

Verified November 1: `make test` passes the three legacy suites, query-compiler checks, lexer/parser checks, twenty-three CLI integration tests, and nineteen dataset-preflight tests. Coverage includes translation traces, actual SQLite row bags, typed parameters, collation ambiguity, missing schema/keys, read-only file handling, and transaction preservation. Recheck the working tree before assigning unfinished work.

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
| `examples/academic/` | Fictional development fixture and catalog covering multi-hop roles, duplicate rows, and a finite mentor cycle. Not a held-out benchmark or model result. |
| `src/main.cc` | Constructs and prints `w.author.name`. It does not compile text. |
| `tests/` | C++ path/mapping/SQL/query/lexer/parser suites and Python CLI integration tests with standard-library SQLite. Legacy Test 10 still demonstrates manual SQL rendering. |

The legacy lower-level `resolve_path` still requires exactly two features and a caller-supplied source table/target alias. `compile_query` walks finite reference chains, allocating aliases, validating roots, checking key types/uniqueness at every hop, and checking the terminal column/literal type. Join reuse uses the source SQL alias and role; different prefixes stay distinct even when they reach the same table. Unlike the C++ object API, the CLI also rejects duplicate catalog declarations and validates unused references. C++ compilation does not check actual rows; use the separate Python preflight below.

The separate Python preflight now checks those declarations against actual data: exact main-schema names, non-NULL storage classes, non-null/unique declared keys, and exactly one target per non-null reference source. It permits NULL ordinary attributes and duplicate source rows. Source-left SQLite equality preserves collation behavior; TEMP shadows are rejected. Use fixed tables/deterministic views. The CLI is read-only, and the API preserves caller transactions. Run before both experimental conditions; setup failures are separate from agent query failures.

`CompiledQuery.trace` owns join metadata and every path occurrence (SELECT order, then WHERE order). `PathTrace` stores clause/index, decoded `Path` components, ordered join IDs, and the final table/column. Join IDs are array positions local to one compilation; direct columns use no joins. Keep components structured because a quoted role may contain dots. Traces describe translation; source-text spans and row provenance remain future work.

Text parsing, multi-hop compilation, and AND filters work through C++ functions and the CLI. There is no production SQLite adapter, model runner, or web interface yet. Boolean syntax beyond AND-connected literal equalities remains unsupported. The old empty-vector test failure-return issue is fixed; do not reassign it from historical notes.

Accepted text shape: `SELECT [DISTINCT] path [AS alias], ... FROM table [AS] alias [WHERE path = literal [AND path = literal ...]];`. The FROM alias and final semicolon are required; projection aliases require AS. A literal is a string or signed int64. Paths may follow finite chains, including repeated visits to a table. Every component before the terminal column must resolve to a declared reference. Extra statements, trailing non-comment input, OR, NOT, and parentheses are rejected.

From the repository root:

```bash
make
make test
make run
```

Requirements are GNU Make 4.3+, a C++20-capable `g++`, and Bison 3.8+. `make` builds the path demo and `build/sqlpc`; `make run` still prints `w.author.name`. `make test` additionally requires Python 3 with its standard-library SQLite module. Run the relevant build/tests when changing implementation, and report what actually ran.

## Next compiler increment

The object compiler, tokenizer, and Bison parser are implemented. Trace `tests/parser_test.cc` from SQLP text through `parse_query` and `compile_sqlp`. `Query.distinct` defaults to false; `std::optional` represents absent projection aliases; `SqlValue` preserves string/int64 types. `Query.where` is now a vector of `EqualityPredicate`, replacing the old optional single predicate; an empty vector omits WHERE. Conditions and parameters retain source order, and every term is validated. Tokens own their text and carry start/exclusive-end locations: zero-based byte offsets and one-based byte columns, with CRLF as one line break. Lexical failures propagate as `LexerError`; grammar failures use `ParseError`. Name/type errors still come from `compile_query` without source locations.

1. Review the provisional CLI and trace format with Iris/Sam; connect the runner and path inspection using [COMPILER_CLI.md](COMPILER_CLI.md). The compiler does not execute SQL. Larger catalogs will need a file interface beyond the current argv limit.
2. Select/review a workload and dataset, using the preflight and shared catalog format. Keep the tiny academic fixture in development; prepare independent answers for the real comparison.

Lexer conventions: bare identifiers use ASCII letters/underscore followed by letters/digits/underscore; double quotes allow other identifier characters. Strings use single quotes; repeated delimiters escape a quote and backslashes are literal. Decimal int64 values may have an adjacent minus sign. Plus signs, fractional/exponent/hex forms, empty quoted identifiers, embedded NULs, and unterminated quotes/comments are rejected. Line comments (`--`) and non-nested block comments (`/* ... */`) are skipped. Lexical acceptance does not imply that syntax or semantics are supported.

These are incremental tasks, not authorization to implement the entire sequence unattended. For Ryan's next block, select the smallest useful unfinished step after reading the current code.

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

The minimum UI lets a visitor choose a question, inspect both attempts/answers/tokens, and click a path to see its mapping. Arbitrary questions remain **not independently benchmark-scored** until an expected answer is established. Label development fixtures, recorded real runs, and live runs accurately.

## Evaluation and research claims

The required experiment compares SQL with SQLP using the same approved schema/relationship information, model/settings, questions, data, corresponding guides, and feedback policy. Keep calls separate so conditions cannot see each other's answer.

First-attempt generation is primary. Repairs are optional, with the same allowance for both conditions, for example two revisions. Never give the agent expected answers or gold correctness labels. Compiler/database diagnostics and query results are distinct from the scoring oracle.

Start with one capable accessible model, one reviewed dataset, 12 development questions, and a target of 30 held-out questions. Iris/Sam prepare independent reference SQL/answers and cross-check them. Include longer paths, competing roles, repeated references, empty answers, and duplicate-sensitive cases. Use contrasting rows so wrong relationships do not accidentally produce correct answers.

Use development outcomes to improve the guides/system. Freeze the evaluated features and language guides on November 7. Finish independent reference-answer checks and freeze held-out questions, task eligibility, data/catalog versions, workload size, and run settings on November 11, before any held-out model calls; run the main comparison November 12–14. Keep failures, including unsupported model outputs on eligible tasks. Choose any smaller workload for capacity before inspecting held-out performance. Repetitions of one task are not extra independent questions.

Measure answer correctness and total provider-reported input/output tokens across all attempts, including guides, schema, feedback, and retries. Preserve raw usage; cache/reasoning fields may overlap totals and must not be double-counted. Report tokens per task including failures; tokens per correct task is undefined when none succeed. Query bytes, latency, and API spending are separate secondary measures. Query-source savings do not establish database-storage savings or human productivity gains.

Compare result values and multiplicity, not exact SQL strings. Execution success, agreement between backends, or an LLM judge alone is insufficient. Fix compiler defects and rerun affected comparisons rather than treating them as evidence about SQLP itself.

No paired model result has been established in the existing planning work. Old audit probes are scripted feasibility/failure demonstrations. The human SQLP study motivates the agent question; NatSQL/DIN-SQL already use intermediate representations and DAIL-SQL studies token efficiency. The proposed interest is named relationship roles, path length, and full interaction tokens. Stronger compiler checks mean the tested contribution is **SQLP plus compiler/checker**; syntax-only claims require further controls. Report positive, mixed, or negative findings without promising acceptance.

Prioritize NatSQL as a third representation using its existing converter. Cap the initial compatibility check at three shared person-hours by October 31, then at most three additional shared person-hours connecting it to the runner/UI by November 7. Pin its version, check representative manual queries against actual answers, and use a declared common feature subset. Retain conversion failures. If not callable by the gate, defer it and narrow comparison claims. DIN-SQL and DAIL-SQL inform workflow/prompt/evaluation design; they are not automatically additional representation conditions. Extra models, datasets, repetitions, and discovery must not delay the core. Model access and any spending require the team's agreed budget; advertised credits are not proof of an activated account.

## Delivery and paper context

| Checkpoint, 2026 | Target |
|---|---|
| Oct 28 | Shared catalog/compiler/run-record examples; role and contribution expectations. |
| Oct 31 | Query objects compile into SQL that executes; initial dataset/question bank and UI records. |
| Nov 4 | Minimal callable text-compiler handoff to Iris. |
| Nov 7 | Multi-hop text compilation, development pilot, feature/guide freeze, NatSQL integration gate. |
| Nov 9 | Proposed internal outline/rough-material review with Enamul; not an official outline deadline. |
| Nov 11 | Independent answer checks complete; held-out workload/settings freeze before model calls. |
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
- Local deeper material includes `resources/paper.tex`, `resources/PhD_Thesis_Oct_10_Enam (1).pdf`, and `resources/icde-audit/`. Verify exact thesis locations/pages before quoting; do not import page maps from a differently named copy without checking.
- Course source is `resources/ASSIGMENTS.tex` plus `ASSIGMENTS.bib` (spelling intentional). It uses the IEEEtran class and BibTeX style; upload both to Overleaf. Local build: `pdflatex`, `bibtex`, `pdflatex`, `pdflatex` from `resources/`.
- `resources/BACKUP_README.md` preserves the old README notes. Private mentor documents and generated companion code should not be published or adopted wholesale. The separate AI-built repository is a reference source, not evidence of ownership or a working feature in this repository.

Consult sources in `resources/` when needed for a specific semantic question; do not make Ryan reread the entire thesis before the next small compiler increment.

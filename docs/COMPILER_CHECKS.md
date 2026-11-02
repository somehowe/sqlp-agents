# Compiler correctness checks

The ordinary unit/integration suites exercise known examples and error cases.
These additional checks generate queries and compare execution against a
separate semantic oracle. They make no model calls and provide no evidence that
SQLP improves model accuracy or token usage.

## Differential execution

Build `build/sqlpc`, then run from the repository root:

```bash
python3 tests/differential_test.py  # also included in make test
python3 tests/differential_test.py --seed 42 --cases 5000
```

The default is 1,200 cases with seed `20261101`; `--compiler PATH` selects another
compiler, and `--timeout SECONDS` bounds each invocation. A failure prints its
seed, zero-based case index, SQLP input, and reproduction arguments. Case order
is deterministic for a given seed and script version.

The fixture includes a cyclic reference graph, a self-reference, competing
roles, shared path prefixes, and integer/string keys. Names include quoted
keywords, spaces, dots, quotes, Unicode, and CRLF. Values include empty/quoted
strings, NULL terminal attributes, duplicate source records, duplicate projected
rows, and integer keys beyond JavaScript's exact range through both int64 bounds.
Generated valid queries vary projections, aliases, finite paths up to six hops,
literal equality/AND filters, DISTINCT, and comments.

Expected answers come from walking Python dictionaries through the declared
references, without generating or executing reference SQL. The check compares
row bags, projection names/order, parameter values/types/order, and every trace
path's role prefixes, physical columns, table, and binding. Oracle sensitivity
checks require duplicate-sensitive and competing-role examples to differ and
verify that float rounding cannot silently pass int64 comparisons.

The fixture uses ordinary binary text comparison and total, non-null references
to unique targets. Existing focused tests cover rejected metadata and other
SQLite collation/preflight cases. This finite generated workload is regression
evidence, not a proof of translation equivalence or a research benchmark.

## Mutation stress under sanitizers

The optional `make test-sanitize` target builds isolated instrumented lexer and
mutation executables, then runs both. It needs compiler sanitizer support.
For a manual build after generating Bison files through the normal build:

```bash
g++ -std=c++20 -Wall -Wextra -Wpedantic -g -O1 \
  -fsanitize=address,undefined -fno-omit-frame-pointer -Isrc -Ibuild \
  tests/compiler_fuzz.cc src/parser.cc build/sqlp_parser.cc \
  src/lexer.cc src/query.cc src/sql.cc src/catalog.cc src/mapping.cc \
  -o build/compiler_fuzz_sanitize
./build/compiler_fuzz_sanitize
./build/compiler_fuzz_sanitize --iterations 200000 --seed 42
```

The default is 100,000 mutations with seed `20261101`. It starts from valid
queries covering quoting, repeated paths, comments, and int64 boundaries, then
inserts/removes ASCII and arbitrary bytes. Each case exercises tokenization and
text-to-SQL compilation. Expected lexer/parser/semantic `invalid_argument`
rejections are counted. Other exceptions fail with the seed, iteration, and
input bytes in hexadecimal; crashes and sanitizer findings fail the process.
This is a deterministic mutation driver, not a coverage-guided fuzzer.

If LeakSanitizer cannot run under a debugger/ptrace environment, use:

```bash
ASAN_OPTIONS=detect_leaks=0 ./build/compiler_fuzz_sanitize
```

That disables leak checking only; report this limitation with results. Address
and undefined-behavior instrumentation remain enabled. Keep the sanitizer build
separate from normal executables, and do not interpret a clean run as proof that
all memory, language, or semantic bugs have been ruled out.

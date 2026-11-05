# Calling the SQLP compiler

`build/sqlpc` compiles one SQLP query into SQL, parameters, and a path-to-join trace. The Python runner executes that SQL in SQLite; the viewer can inspect the trace.

Build from the repository root with `make`. Requirements: GNU Make 4.3+, Bison 3.8+, and a C++20-capable `g++`. `make test` also uses Python 3.11+ and its standard-library SQLite module.

## Try one query

```bash
./build/sqlpc \
  --column AUTHOR aid integer unique \
  --column AUTHOR name string nonunique \
  --column AUTHOR uni_id integer nonunique \
  --column UNIVERSITY uid integer unique \
  --column UNIVERSITY country string nonunique \
  --column WROTE author_aid integer nonunique \
  --reference WROTE author author_aid AUTHOR aid \
  --reference AUTHOR university uni_id UNIVERSITY uid <<'SQLP'
SELECT w.author.name FROM WROTE w
WHERE w.author.university.country = 'CA' AND w.author.aid = 1;
SQLP
```

Each column declares its table, name, type, and uniqueness. A reference declares its source table, role, source key, target table, and target key. Repeat either flag as needed; their order does not matter. A table is introduced by its columns. Types are `integer` or `string`; uniqueness is `unique` or `nonunique`.

The runner keeps a JSON catalog and converts its entries into these tokens. C++ does not need a JSON input parser. Use a subprocess argument list rather than building a shell command. Inline flags remain supported for small calls; use the response-file transport below when metadata may exceed the operating system's argument-size limit.

## Large catalogs: response-file transport

```bash
./build/sqlpc --catalog-args-file build/catalog.args < query.sqlp
```

This option replaces inline catalog flags; the query still comes from stdin.
The file is a serialization of the same argument tokens, not a separate catalog
language. Its bytes are:

1. The exact ASCII header `SQLP-CATALOG-ARGS-1\n`.
2. Zero or more UTF-8 tokens, each terminated by a NUL byte (`\0`).

The file may contain at most **16 MiB total**, including the header and
terminators, and **100,000 tokens**. Spaces, quotes, tabs, and newlines inside a
token are preserved. A token cannot contain NUL, matching the existing argv
contract. There is no shell expansion, escaping, comment syntax, or recursive
response-file inclusion. Only the existing `--column`/`--reference` tokens are
accepted inside the file; `--help` is available only as a standalone CLI option.

For example, a Python caller can transport the arguments returned by
`compiler_arguments(catalog)` through a private temporary file:

```python
from pathlib import Path
import subprocess
import tempfile
from tools.validate_dataset import compiler_arguments

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / 'catalog.args'
    arguments = compiler_arguments(catalog)
    path.write_bytes(b'SQLP-CATALOG-ARGS-1\n' + b''.join(
        token.encode('utf-8') + b'\0' for token in arguments))
    process = subprocess.run(
        ['./build/sqlpc', '--catalog-args-file', str(path)],
        input=query, text=True, encoding='utf-8',
        capture_output=True, timeout=10,
    )
```

Missing/unreadable files, invalid headers/UTF-8, unfinished tokens, exceeded
transport limits, and mixed inline/file options are `arguments` failures (exit
`2`). Validly transported declarations still receive the same full catalog
checks and `catalog` diagnostics (exit `1`). A header-only file represents an
empty argument list and fails the existing empty-catalog check.

## Response and errors

Except for `--help` supplied alone, stdout contains one UTF-8 JSON object followed by a newline. Here is a successful response for a direct-column query:

```json
{
  "version": 1, "ok": true,
  "sql": "SELECT \"a\".\"name\"\nFROM \"AUTHOR\" AS \"a\";",
  "parameters": [],
  "trace": {
    "joins": [],
    "paths": [{"clause":"select","index":0,"root":"a","features":["name"],
               "join_ids":[],"column":{"table":"AUTHOR","alias":"a","column":"name"}}]
  }
}
```

The `sql` field contains the complete generated SQL. Parameters follow the conditions' source order and match the generated `?` placeholders. Repeated conditions retain separate parameters. **Both parameter types encode their values as JSON strings.** For example, `{"type":"int64","value":"9223372036854775807"}` preserves an integer that JavaScript numbers cannot represent exactly. Convert `int64` values to Python integers before binding; leave `string` values unchanged.

`trace.joins` lists the generated joins in SQL order. Each entry has a `role` and `source`/`target` objects containing the raw physical `table`, SQL `alias`, and join `column`. Its array position is its join ID. A path's ordered `join_ids` point into this same array, so shared prefixes point to the same joins. IDs are local to one compilation and can change when a query changes.

`trace.paths` records every path occurrence, SELECT first and then WHERE. `clause` is `select` or `where`; `index` is the zero-based position within that clause's projection/condition list. `root` and `features` preserve decoded path components; `column` identifies the final SQL column. Direct columns have empty `join_ids`. For the first example, the WHERE country path has features `["author","university","country"]` and join IDs `[0,1]`.

The viewer uses these records to show a path's reference roles and join columns. Keep component arrays intact: `["author.university","country"]` names a different path from `["author","university","country"]`. Names in the trace are unquoted; SQL identifiers are quoted in `sql`. Source-text spans and row provenance remain future work. Failed compilations return an error without a partial trace.

The trace is an additive version-1 field. Consumers should ignore unknown output fields; the existing SQL, parameter, and error formats are unchanged.

Failures have this shape:

```json
{"version":1,"ok":false,"error":{"stage":"parser","message":"...","location":{"offset":12,"line":1,"column":13}}}
```

Lexer/parser errors include a location. Offsets count bytes from zero; lines and byte columns start at one. Other errors omit `location`. Diagnostic wording may change; use `stage`, not message text, for classification.

| Exit code | Meaning |
|---|---|
| `0` | Success, or standalone `--help`. |
| `1` | `catalog`, `input`, `lexer`, `parser`, or `compiler` failure. |
| `2` | `arguments`: unknown/incomplete options, unsupported types, or invalid option values. |
| `3` | Unexpected `internal` failure, or failure to write the response. |

If stdout cannot be written, the response may be incomplete; the exit code still reports failure.

The CLI checks all supplied reference metadata, even references unused by the query. Catalog failures are **setup failures**, not wrong answers generated by the agent. Preflight the same reviewed catalog for both experimental conditions. Duplicate physical names that differ only in ASCII case, duplicate roles, missing columns, incompatible key types, and nonunique targets are rejected. Query/catalog names must otherwise match their declared spelling exactly. Inputs must be valid UTF-8.

Uniqueness here is a declaration. Use the [dataset preflight](DATASET_CHECKS.md) to check actual rows, non-null keys, and total references when importing a dataset. Its JSON catalog can supply the same facts to the compiler through `compiler_arguments`. Compilation and preflight do not establish that a relationship is factually correct.

## Python handoff

Given an existing SQLite `connection`, the minimal call looks like this. Replace the example metadata with the runner's catalog.

```python
import json
import subprocess

arguments = [
    "./build/sqlpc",
    "--column", "AUTHOR", "aid", "integer", "unique",
    "--column", "AUTHOR", "name", "string", "nonunique",
]
process = subprocess.run(
    arguments,
    input="SELECT a.name FROM AUTHOR a WHERE a.aid = 1;",
    text=True, encoding="utf-8", capture_output=True, timeout=10,
)
record = json.loads(process.stdout)
if process.returncode != 0 or not record["ok"]:
    raise RuntimeError(record["error"])  # The runner should save this diagnostic.
parameters = [
    int(p["value"]) if p["type"] == "int64" else p["value"]
    for p in record["parameters"]
]
rows = connection.execute(record["sql"], parameters).fetchall()
```

Save compiler diagnostics separately from SQLite execution errors and answer scores. No model calls, scoring, token logging, or SQLite execution happen inside the compiler.

## Current limits

One FROM alias, projections with optional `AS`, optional `DISTINCT`, and an optional WHERE clause containing literal equalities joined by AND. AND is case-insensitive. Paths support direct columns and finite chains of named references. Each component before the final column is a reference role. For example, `w.author.university.country` expands into two joins.

Shared prefixes reuse joins across projections and all conditions. Different roles and repeated visits to the same table get separate aliases; finite paths such as `a.mentor.mentor.name` are supported. Every hop checks key types and the target's uniqueness declaration. Every condition is validated, including later terms after a contradictory pair.

The FROM alias and final semicolon are required. OR, NOT, parentheses, and comparisons beyond literal equality are unsupported. The inline metadata flags remain supported.

[Project idea](IDEA.md) · [Agent context](AGENTS.md)

## Tested profile limits

Compilation rejects more than 63 distinct reference joins, 2,000 projections, or 512 equality predicates before emitting SQL. Reused prefixes do not consume another join. These fixed prototype caps fit the tested SQLite limits, not the full SQLP language; a different SQLite build may impose lower limits.

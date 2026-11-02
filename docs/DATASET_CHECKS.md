# Check a dataset before agent runs

The compiler trusts declared references. This preflight checks those declarations against actual SQLite rows before either experimental condition runs. It checks structure and values; relationship meaning still needs human review.

Use fixed tables or deterministic views in the `main` schema. The checker opens one read snapshot and changes no tables. The Python API preserves an existing caller transaction. TEMP objects that shadow catalog names are rejected because they could change what the compiler's unqualified SQL reads.

## Try the development fixture

From the repository root, create a fresh database under ignored `build/`:

```bash
python3 - <<'PY'
from pathlib import Path
import sqlite3

path = Path('build/academic.sqlite')
path.parent.mkdir(exist_ok=True)
if path.exists():
    raise SystemExit('Fixture already exists; choose a fresh path to recreate it.')
with sqlite3.connect(path) as database:
    database.executescript(Path('examples/academic/data.sql').read_text())
PY
python3 tools/validate_dataset.py build/academic.sqlite examples/academic/catalog.json
```

Success returns `{"version": 1, "ok": true, "issues": []}`. The CLI opens an existing database with `mode=ro`; a missing path is an error, and no database is created. Exit codes are `0` for valid data, `1` for reported violations, and `2` for catalog/database/argument errors.

The fixture has fictional authors, universities, publications, duplicate authorship rows, and a mentor cycle. It is development data, not a held-out benchmark or evidence of an AI advantage.

## Catalog and checks

[The sample catalog](../examples/academic/catalog.json) defines version `1`, `tables`, and `references`. Each table has a `name` and `columns`; each column has `name`, `type` (`integer` or `string`), and Boolean `unique`. References use the same five fields as C++: `source_table`, `feature`, `source_column`, `target_table`, and `target_column`.

Names must exactly match physical names. Duplicate names differing only in ASCII case, invalid types, duplicate JSON keys, and incompatible reference declarations are rejected. Extra descriptive fields may be retained in the catalog; the compiler arguments use the declared schema/reference fields.

Checks cover:

- Missing tables/columns and TEMP shadows.
- Actual SQLite storage types: integer values for `integer`, text for `string`. Ordinary attributes may be NULL. Raw text must decode under the database's declared UTF-8/UTF-16 encoding; malformed text reports `invalid_text_encoding`.
- Declared unique columns treated as non-null keys, including keys unused by the current query.
- Non-null reference sources and exactly one target match for every source row. Duplicate source values are allowed.

Reference checks preserve the compiler's operand order, `source.key = target.key`. This matters because SQLite's collation rules can let a source value match two distinct target values. [SQLite comparison and collation rules](https://www.sqlite.org/datatype3.html#collating_sequences).

Each issue has a `code`, `count`, and relevant table/column or reference fields. Data counts measure violating rows; `duplicate_unique_key.count` measures duplicate value groups. Missing/shadowed schema objects have count `1`. Malformed catalogs and database failures instead return an `error` with a `stage` and `message`.

Run preflight after importing or changing data, before model calls. Save its report as setup evidence. A failed preflight is a dataset/setup failure and must not count as an agent's wrong answer. Validation can scan whole tables, so it belongs outside per-query timing/token measurements. The CLI accepts `--timeout` (30 seconds by default) and reports `preflight_timeout` as a setup error. Target-key indexes are strongly recommended: reference validation can otherwise scan targets for each source row. The checker never creates indexes or changes the data.

## Reuse the same catalog with the compiler

After `make`, this compiles and executes a query over the fixture:

```python
from contextlib import closing
import json
import sqlite3
import subprocess
from tools.validate_dataset import compiler_arguments, load_catalog, validate_dataset

catalog = load_catalog('examples/academic/catalog.json')
with closing(sqlite3.connect('file:build/academic.sqlite?mode=ro', uri=True)) as database:
    report = validate_dataset(database, catalog, timeout_seconds=30)
    if not report['ok']:
        raise RuntimeError(report)
    query = ("SELECT w.author.name FROM WROTE w "
             "WHERE w.author.university.country = 'CA' "
             "AND w.paper.publisher.country = 'DK';")
    process = subprocess.run(['./build/sqlpc', *compiler_arguments(catalog)],
                             input=query, text=True, encoding='utf-8',
                             capture_output=True, timeout=10)
    compiled = json.loads(process.stdout)
    if process.returncode or not compiled['ok']:
        raise RuntimeError(compiled)
    parameters = [int(p['value']) if p['type'] == 'int64' else p['value']
                  for p in compiled['parameters']]
    rows = database.execute(compiled['sql'], parameters).fetchall()
    print(rows)  # [('Alice',), ('Alice',)]
```

The duplicate result is intentional. Add DISTINCT only when the question requests distinct answers. [Compiler protocol](COMPILER_CLI.md) · [Project context](AGENTS.md).

The [prepared comparison runner](QUERY_COMPARISON.md) uses this same fixture/catalog to execute both conditions, check duplicate-sensitive answers, and save records for the interface.

API: `validate_dataset(connection, catalog, *, timeout_seconds=None)` preserves a caller transaction. With `None`, it leaves the caller's progress handler alone. An explicit timeout owns/replaces the handler for this call and clears it afterward; expiry raises `PreflightTimeoutError`. Call it on a connection whose handler you can hand over, or omit the timeout to retain your own handler.

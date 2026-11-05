# Inspect source schemas

This is the first preparation step for the [restricted ARM mapping layer](ARM_MAPPING.md). It reads
declared SQLite tables, primary keys, and foreign keys. It does not generate an
ARM schema, assign relationship roles, create views, or resolve entity identity.

First create `build/academic.sqlite` with the [dataset instructions](DATASET_CHECKS.md).
Then, from the repository root:

```bash
python3 -m tools.inspect_schema build/academic.sqlite > build/source-schema.json
python3 tests/schema_inspection_test.py
```

Requirements: Python 3.11+ and SQLite 3.37+ (`PRAGMA table_list`). The CLI opens an
existing database read-only. Missing paths fail without creating a database.
Exit `0` means inspection completed; exit `2` means the database or metadata
could not be inspected. Unsupported tables/references are reported within a
successful inspection rather than silently discarded.

The version-1 JSON report contains:

- `tables`: ordinary main-schema tables, columns, primary-key order, foreign keys,
  candidate flags, and issue codes.
- `excluded`: views, virtual tables, and virtual-table shadow tables.
- `data_checked: false` and `review_required: true`.

A table is a candidate for the supported mapping subset when it has one declared,
non-null, integer/text primary-key column. SQLite's implicit non-null behavior
for rowid-alias keys, STRICT tables, and WITHOUT ROWID tables is respected.
`INTEGER PRIMARY KEY DESC` is not assumed to be a rowid alias. Keyless and
composite-key tables remain visible with explanatory issues.

A reference candidate additionally needs a single non-null source column with a
matching integer/text type hint, a supported target entity, and the target's
primary key. Generated reference columns are excluded from this initial subset.
Valid SQLite references to non-primary UNIQUE keys are reported but marked
unsupported. Omitted target columns resolve to the declared primary-key order;
the inspector never guesses that the first column is a key. Identifier matching
uses SQLite's ASCII case rules and retains the original declared target spelling.

Type hints describe declared affinities, not actual stored values. Candidate
flags do not establish unique/non-null data, total references, or the meaning of
a relationship. Follow with [dataset validation](DATASET_CHECKS.md) and review
role names/correspondences before generating a compiler catalog. This report is
not itself a compiler catalog.

The Python API `inspect_schema(connection)` uses one main-schema metadata
snapshot and preserves an existing caller transaction. TEMP objects and attached
databases do not change which schema it inspects. No row data is read or changed.

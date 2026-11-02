# Restricted ARM mapping and views

The mapping layer gives a reviewed physical SQLite schema stable logical names
and explicit relationship roles. For example, `researcher_rows.person_key`
becomes `AUTHOR.aid`; the approved `institution_key` foreign key becomes the
named `AUTHOR.university` reference. SQLP compiles against the resulting views.
SQL can query the same views and receives the same relationship facts.

This implements a restricted mapping/view layer inspired by the thesis's
physical-to-logical construction and SQL view unfolding. It does not implement
full `BuildARM`, abstract entity IDs, entity-result/equality semantics,
alternative identifiers, identity merging, inheritance, preferences, duration,
or automatic relationship discovery. SQLite expands the generated views.
The present compiler returns ordinary scalar values, including physical keys.

## Example

The fictional [fixture](../examples/arm/data.sql) uses separate physical table
and column names. [mapping.json](../examples/arm/mapping.json) supplies reviewed
names and roles. Each authorship occurrence has an explicit `event_key`, mapped
to `WROTE.wid`. Two distinct occurrences can have the same author and paper;
the views preserve both. The existing academic fixture remains unchanged.

From the repository root:

```bash
mkdir -p build/arm-example
python3 - <<'PY'
from pathlib import Path
import sqlite3
with sqlite3.connect('build/arm-example/source.sqlite') as db:
    db.executescript(Path('examples/arm/data.sql').read_text())
PY
python3 -m tools.arm_mapping build/arm-example/source.sqlite examples/arm/mapping.json \
  --destination build/arm-example/mapped.sqlite > build/arm-example/mapping-artifact.json
```

The source and destination must be different, and the destination must not
exist. The fixture setup script is for a fresh source database. Omit
`--destination` to inspect and validate without creating views. The JSON artifact
contains `catalog`, which existing compiler/runner tools accept:

```bash
python3 - <<'PY'
import json
from pathlib import Path
artifact = json.loads(Path('build/arm-example/mapping-artifact.json').read_text())
Path('build/arm-example/catalog.json').write_text(json.dumps(artifact['catalog']))
PY
make
python3 -m tools.run_comparison build/arm-example/mapped.sqlite \
  build/arm-example/catalog.json examples/arm/workload.json
```

The three prepared development cases check competing author/publisher roles,
duplicate-sensitive answers, DISTINCT, and finite mentor cycles. Correct
prepared translations establish implementation behavior, not model gains.

## Mapping format

Version 1 requires exactly `version` and `entities`. Every entity supplies
`name` (logical view), `source_table`, `key` (logical key column), `columns`, and
`references`. Columns contain `name` and `source_column`. References contain
`feature`, `source_column` (logical column), and `target_entity`; the target's
explicit key determines the target column. Role names are supplied, not guessed.

```json
{
  "version": 1,
  "entities": [{
    "name": "AUTHOR",
    "source_table": "researcher_rows",
    "key": "aid",
    "columns": [{"name": "aid", "source_column": "person_key"}],
    "references": []
  }]
}
```

Each entity needs an ordinary main-schema source table and a declared,
non-null, single-column primary key. Mapped columns must have supported integer
or text affinity and actual integer/text storage when non-NULL. Every approved
reference needs exactly one matching declared unary foreign key to the target
entity's key, a non-null source column, compatible types, and exactly one target
under the compiler's source-left join equality. An implicit FK target is resolved
to the declared PK. Checks reuse [dataset preflight](DATASET_CHECKS.md).

The layer rejects keyless/composite/nullable anchors, mapped generated columns,
view/virtual sources, undeclared or ambiguous references, dangling targets,
name collisions, duplicate mappings, and unsupported storage types. A physical
table maps to at most one entity. A role cannot reuse a mapped scalar name.
Valid SQLite relationships to non-PK UNIQUE columns remain outside this version.
Physical names follow SQLite's ASCII case matching; logical names use their
explicit mapping spelling. Quoted Unicode names are supported.

## API, snapshots, and limits

`build_mapping(connection, config)` validates one read snapshot and returns an
artifact without creating views. It preserves a caller transaction, including
on failure. `materialize_snapshot(source_path, new_destination_path, config)`
opens the source read-only, holds a read transaction through SQLite backup, adds
views to a private copy, and preflights the logical catalog before publishing.
An atomic exclusive hard link refuses concurrent destination creation and
existing files or symlinks. Failed construction leaves no partial destination.
The finished snapshot has mode `0600`. Only `main` is copied; attached and TEMP
objects are not part of the published dataset. Existing unmapped physical
tables remain in the copy, so this layer is not an access-control boundary.

Both APIs accept `timeout_seconds=30.0` and `max_snapshot_bytes=67108864`.
They own and clear the SQLite progress-handler slot; provide a connection
without a custom handler. Limits cover SQLite work and snapshot size; lock
waiting also depends on the caller connection's busy timeout. Configurations
are limited to 1 MiB, 256 entities, 4,096 columns and 4,096 references. The CLI
offers `--timeout` and `--max-snapshot-bytes`. It reports errors as JSON and exits
2; success exits 0. Python 3.11+ with SQLite serialization and SQLite 3.37+ are
required. No third-party dependencies or model calls are involved.

Artifacts include logical/physical identification and reference metadata,
generated view SQL, the compiler-compatible catalog, configuration/schema/view/
catalog hashes, and `source_snapshot_sha256`. Materialization also records
`copied_snapshot_sha256` before views and `snapshot_sha256` after views. These
hash SQLite's serialized transaction snapshots, including committed WAL pages.
Backup can change database header counters, so source/copy hashes need not
match byte for byte. Hashes identify artifacts; they do not prove real-world
entity identity or independent human approval. Keep schema construction cost
separate from per-question agent tokens.

Verification: `python3 -m unittest discover -s tests -p arm_mapping_test.py`.

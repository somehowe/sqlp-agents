"""Prepare a pinned Chinook snapshot and independently interpreted task oracles.

No network access, model calls, or third-party Python packages are used here.
The upstream SQL input is accepted only when its exact pinned digest matches.
"""

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from .validate_dataset import _unique_object, validate_dataset


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "chinook"
UPSTREAM_COMMIT = "7f67772503d71ba90f19283c38e93923addb43fa"
SOURCE_SHA256 = "caf31d698a4a79c628215b552dfe6575e71be052ae02b8f18e763498f55f5d44"
SOURCE_BLOB_SHA1 = "299610dc69f04a2b16b3a2e45bd2af82ed2872a2"
SOURCE_URL = ("https://raw.githubusercontent.com/lerocha/chinook-database/" +
              UPSTREAM_COMMIT + "/ChinookDatabase/DataSources/Chinook_Sqlite.sql")
MAX_SOURCE_BYTES = 2_000_000

# Names and integer/text columns are retained, not guessed or discovered.
# Money/decimal columns, nullable manager navigation, and playlists are omitted.
TABLE_COLUMNS = {
    "Artist": ("ArtistId", "Name"),
    "Album": ("AlbumId", "Title", "ArtistId"),
    "Employee": ("EmployeeId", "FirstName", "LastName", "Title", "City", "Country", "Email"),
    "Customer": ("CustomerId", "FirstName", "LastName", "City", "Country", "Email", "SupportRepId"),
    "Genre": ("GenreId", "Name"),
    "MediaType": ("MediaTypeId", "Name"),
    "Track": ("TrackId", "Name", "AlbumId", "MediaTypeId", "GenreId", "Composer", "Milliseconds"),
    "Invoice": ("InvoiceId", "CustomerId", "InvoiceDate", "BillingCity", "BillingCountry"),
    "InvoiceLine": ("InvoiceLineId", "InvoiceId", "TrackId", "Quantity"),
}
REFERENCES = (
    ("Album", "artist", "ArtistId", "Artist", "ArtistId"),
    ("Customer", "supportRep", "SupportRepId", "Employee", "EmployeeId"),
    ("Track", "album", "AlbumId", "Album", "AlbumId"),
    ("Track", "genre", "GenreId", "Genre", "GenreId"),
    ("Track", "mediaType", "MediaTypeId", "MediaType", "MediaTypeId"),
    ("Invoice", "customer", "CustomerId", "Customer", "CustomerId"),
    ("InvoiceLine", "invoice", "InvoiceId", "Invoice", "InvoiceId"),
    ("InvoiceLine", "track", "TrackId", "Track", "TrackId"),
)
INTEGER_COLUMNS = {"ArtistId", "AlbumId", "EmployeeId", "CustomerId", "SupportRepId",
                   "GenreId", "MediaTypeId", "TrackId", "Milliseconds", "InvoiceId",
                   "InvoiceLineId", "Quantity"}
REFERENCE_FIELDS = ("source_table", "feature", "source_column", "target_table", "target_column")


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_catalog():
    return {"version": 1, "tables": [
        {"name": table, "columns": [
            {"name": col, "type": "integer" if col in INTEGER_COLUMNS else "string",
             "unique": col == columns[0]} for col in columns]}
        for table, columns in TABLE_COLUMNS.items()],
        "references": [dict(zip(REFERENCE_FIELDS, ref)) for ref in REFERENCES]}


def _check_declared_source(source):
    """Check retained physical keys/FKs before copying the reviewed subset."""
    for table, columns in TABLE_COLUMNS.items():
        physical = source.execute("SELECT type FROM main.sqlite_schema WHERE name=?", (table,)).fetchone()
        if physical != ("table",):
            raise ValueError("Source must contain ordinary table: " + table)
        info = source.execute("PRAGMA main.table_info(" + _quote(table) + ")").fetchall()
        by_name = {row[1]: row for row in info}
        if any(col not in by_name for col in columns):
            raise ValueError("Source is missing retained columns: " + table)
        keys = [row[1] for row in info if row[5]]
        if keys != [columns[0]] or by_name[columns[0]][2].upper() != "INTEGER":
            raise ValueError("Expected the declared single-column INTEGER primary key: " + table)
        for col in columns:
            declared = by_name[col][2].upper()
            if col in INTEGER_COLUMNS and declared != "INTEGER":
                raise ValueError("Expected declared INTEGER column: " + table + "." + col)
            if col not in INTEGER_COLUMNS and not any(x in declared for x in ("CHAR", "TEXT", "DATE")):
                raise ValueError("Expected declared textual/date column: " + table + "." + col)
    for table, role, source_key, target, target_key in REFERENCES:
        fks = source.execute("PRAGMA main.foreign_key_list(" + _quote(table) + ")").fetchall()
        grouped = {}
        for row in fks:
            grouped.setdefault(row[0], []).append(row)
        if not any(len(rows) == 1 and rows[0][2:5] == (target, source_key, target_key)
                   for rows in grouped.values()):
            raise ValueError("Missing declared unary foreign key for role: " + table + "." + role)


def copy_snapshot(source, destination):
    """Copy exactly the retained columns/rows from an already-open read snapshot.

    The supplied destination must be empty. All relationships are checked against
    actual rows; source declarations that permit NULL do not prove totality.
    """
    if not source.in_transaction:
        raise ValueError("Source copying requires an active read snapshot")
    if destination.execute("SELECT 1 FROM main.sqlite_schema LIMIT 1").fetchone():
        raise ValueError("Snapshot destination must be empty")
    _check_declared_source(source)
    catalog = make_catalog()
    check = validate_dataset(source, catalog, timeout_seconds=30)
    if not check["ok"]:
        raise ValueError("Source fails reviewed reference assumptions: " + json.dumps(check))
    destination.execute("PRAGMA foreign_keys=ON")
    counts = {}
    for table, columns in TABLE_COLUMNS.items():
        declarations = []
        for i, col in enumerate(columns):
            declaration = _quote(col) + (" INTEGER" if col in INTEGER_COLUMNS else " TEXT")
            if i == 0:
                declaration += " PRIMARY KEY NOT NULL"
            elif any(r[0] == table and r[2] == col for r in REFERENCES):
                declaration += " NOT NULL"
            declarations.append(declaration)
        for owner, _, left, target, right in REFERENCES:
            if owner == table:
                declarations.append("FOREIGN KEY (" + _quote(left) + ") REFERENCES " +
                                    _quote(target) + " (" + _quote(right) + ")")
        destination.execute("CREATE TABLE " + _quote(table) + " (" + ", ".join(declarations) + ")")
        query = "SELECT " + ", ".join(map(_quote, columns)) + " FROM main." + _quote(table)
        query += " ORDER BY " + _quote(columns[0])
        rows = source.execute(query).fetchall()
        destination.executemany("INSERT INTO " + _quote(table) + " VALUES (" +
                                ",".join("?" for _ in columns) + ")", rows)
        counts[table] = len(rows)
    destination.commit()
    if destination.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Copied snapshot failed foreign key checks")
    if not validate_dataset(destination, catalog, timeout_seconds=30)["ok"]:
        raise ValueError("Copied snapshot failed catalog preflight")
    return catalog, counts


def load_records(connection):
    """Load plain records and key indexes; no SQL joins or compiler are used."""
    records, indexes = {}, {}
    for table, columns in TABLE_COLUMNS.items():
        cursor = connection.execute("SELECT " + ", ".join(map(_quote, columns)) +
                                    " FROM main." + _quote(table))
        rows = [dict(zip(columns, row)) for row in cursor]
        records[table] = rows
        indexes[table] = {row[columns[0]]: row for row in rows}
        if len(indexes[table]) != len(rows):
            raise ValueError("Oracle requires distinct record keys")
    return records, indexes


def oracle_rows(records, indexes, task):
    """Interpret a separate reviewed semantic plan using Python dictionary lookups.

    This does not parse SQL or SQLP, execute joins, invoke the compiler, or use
    either query's answer as gold. It is still authored by this project and needs
    human review of question/plan interpretation before a research submission.
    """
    root, projections, predicates = task["root"], task["projections"], task["where"]
    if (root not in TABLE_COLUMNS or not isinstance(projections, list) or not projections or
            not isinstance(predicates, list) or type(task["distinct"]) is not bool):
        raise ValueError("Malformed oracle plan")
    references = {(r[0], r[1]): r for r in REFERENCES}

    def checked_path(path):
        table = root
        if not isinstance(path, str) or not path:
            raise ValueError("Malformed oracle path")
        parts = path.split(".")
        if any(not part for part in parts):
            raise ValueError("Malformed oracle path")
        for role in parts[:-1]:
            if (table, role) not in references:
                raise ValueError("Unknown oracle relationship: " + table + "." + role)
            table = references[table, role][3]
        if parts[-1] not in TABLE_COLUMNS[table]:
            raise ValueError("Unknown oracle terminal: " + table + "." + parts[-1])
        return parts

    for path in projections:
        checked_path(path)
    for predicate in predicates:
        if not isinstance(predicate, list) or len(predicate) != 2:
            raise ValueError("Oracle predicates must be path/literal pairs")
        path, literal = predicate
        terminal = checked_path(path)[-1]
        if terminal in INTEGER_COLUMNS:
            if type(literal) is not int or not -(2**63) <= literal < 2**63:
                raise ValueError("Oracle integer literal must be int64")
        elif not isinstance(literal, str):
            raise ValueError("Oracle text literal must be a string")

    def value(record, path):
        table, parts = root, path.split(".")
        for role in parts[:-1]:
            _, _, source_col, target, _ = references[table, role]
            record = indexes[target][record[source_col]]
            table = target
        return record[parts[-1]]

    rows = []
    for record in records[root]:
        if all(value(record, path) == literal for path, literal in predicates):
            rows.append(tuple(value(record, path) for path in projections))
    if task["distinct"]:
        rows = list(set(rows))
    # Canonical serialization is for stable artifacts only; scoring uses bags.
    return [list(row) for row in sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False))]


def build_workloads(connection, plans):
    if (not isinstance(plans, dict) or type(plans.get("version")) is not int or plans["version"] != 1 or
            not isinstance(plans.get("tasks"), list)):
        raise ValueError("Expected a version-1 question plan")
    records, indexes = load_records(connection)
    results = {scope: {"version": 1, "scope": scope, "cases": []}
               for scope in ("development", "held_out")}
    seen = set()
    for task in plans["tasks"]:
        scope, case_id = task["scope"], task["id"]
        if scope not in results or not isinstance(case_id, str) or case_id in seen:
            raise ValueError("Duplicate/invalid task identity or scope")
        seen.add(case_id)
        for key in ("question", "sql", "sqlp"):
            if not isinstance(task[key], str) or not task[key]:
                raise ValueError("Empty task field: " + key)
        expected = oracle_rows(records, indexes, task["oracle"])
        if len(expected) > 1000:
            raise ValueError("Task exceeds the fixed 1,000-row study cap: " + case_id)
        # An independently supplied explicit SQL query must agree with the Python
        # oracle before any workload can be emitted. SQLP is checked separately
        # with the real C++ compiler in integration validation.
        cursor = connection.execute(task["sql"])
        actual = cursor.fetchall()
        if (len(cursor.description) != len(task["oracle"]["projections"]) or
                Counter(actual) != Counter(map(tuple, expected))):
            raise ValueError("Prepared SQL disagrees with the independent oracle: " + case_id)
        results[scope]["cases"].append({
            "id": case_id, "question": task["question"], "sql": task["sql"],
            "sqlp": task["sqlp"], "column_count": len(task["oracle"]["projections"]),
            "expected_rows": expected, "tags": task.get("tags", []),
        })
    if len(results["development"]["cases"]) != 12 or len(results["held_out"]["cases"]) != 30:
        raise ValueError("Expected the predeclared 12 development and 30 reserved tasks")
    return results


def prepare(source_sql, output_dir, *, question_plan=EXAMPLES / "questions.json"):
    """Create a fresh, local artifact directory; refuse changed source/overwrite."""
    source_sql, output_dir, question_plan = map(Path, (source_sql, output_dir, question_plan))
    if source_sql.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source SQL exceeds pinned input size budget")
    raw = source_sql.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Source SQL does not match the pinned upstream SHA256")
    plans = json.loads(question_plan.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    # Do not execute an arbitrary SQL input. Only the exact verified public script
    # above is executed, in a temporary database, never against existing work.
    with tempfile.TemporaryDirectory(prefix="sqlp-chinook-") as scratch:
        source_path = Path(scratch) / "upstream.sqlite"
        with closing(sqlite3.connect(source_path)) as source:
            source.executescript(raw.decode("utf-8"))
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
            source.execute("BEGIN")
            staged = Path(scratch) / "snapshot.sqlite"
            with closing(sqlite3.connect(staged)) as destination:
                catalog, counts = copy_snapshot(source, destination)
            with closing(sqlite3.connect(staged.as_uri() + "?mode=ro", uri=True)) as destination:
                destination.execute("BEGIN")
                workloads = build_workloads(destination, plans)
        # All source/schema/oracle checks precede claiming a fresh destination.
        output_dir.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copyfile(staged, output_dir / "snapshot.sqlite")
            shutil.copyfile(EXAMPLES / "LICENSE.txt", output_dir / "CHINOOK_LICENSE.txt")
            for name, value in (("catalog", catalog), *workloads.items()):
                (output_dir / (name + ".json")).write_text(
                    json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
            manifest = {
                "version": 1, "dataset": "Chinook 1.4.5, curated integer/text snapshot",
                "upstream_commit": UPSTREAM_COMMIT, "source_url": SOURCE_URL,
                "source_sha256": SOURCE_SHA256, "source_git_blob_sha1": SOURCE_BLOB_SHA1,
                "license": "MIT", "license_notice": "CHINOOK_LICENSE.txt", "row_counts": counts,
                "question_plan_sha256": _hash(question_plan),
                "builder_sha256": _hash(Path(__file__)),
                "files_sha256": {p.name: _hash(p) for p in sorted(output_dir.iterdir())},
                "oracle": "Independent Python keyed-record traversal, cross-checked with prepared SQL",
                "human_answer_review": "pending",
                "model_calls": 0,
                "limitations": [
                    "Project-authored bounded case study, not an established benchmark or data lake",
                    "Real music metadata; fictional customer/employee details and generated sales",
                    "All selected rows retained; decimal money fields and nullable manager paths omitted",
                    "Original nullable FK declarations strengthened only after fixed-snapshot validation",
                    "Billing and customer countries coincide here; they are not discriminative roles",
                    "Reserved questions have not been tested with models; human review and protocol freeze remain",
                ],
            }
            (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        except Exception:
            # This directory was exclusively created above, never a user's old one.
            shutil.rmtree(output_dir)
            raise
    return manifest


def rewrite_table_bindings(query, names):
    """Rename FROM/JOIN bindings in this fixture's restricted prepared queries.

    Tokens inside string literals and comments are never rewritten. This helper
    is not a general SQL rewriter: schema-qualified sources, subqueries, CTEs,
    and unknown tables are deliberately rejected at a binding position.
    """
    tokens = list(re.finditer(
        r"(?P<string>'(?:''|[^'])*')|(?P<quoted>\"(?:\"\"|[^\"])*\")|"
        r"(?P<comment>--[^\r\n]*|/\*[\s\S]*?\*/)|"
        r"(?P<word>[A-Za-z_][A-Za-z_0-9]*)|(?P<symbol>[^\s])", query))
    tokens = [token for token in tokens if token.lastgroup != "comment"]
    edits = []
    for index, token in enumerate(tokens):
        if token.lastgroup != "word" or token[0].upper() not in ("FROM", "JOIN"):
            continue
        if index + 1 == len(tokens):
            raise ValueError("Missing prepared table binding")
        target = tokens[index + 1]
        if target.lastgroup == "quoted":
            name = target[0][1:-1].replace('""', '"')
        elif target.lastgroup == "word":
            name = target[0]
        else:
            raise ValueError("Only ordinary prepared table bindings can be renamed")
        if name not in names or (index + 2 < len(tokens) and tokens[index + 2][0] == "."):
            raise ValueError("Unknown or schema-qualified prepared table binding: " + name)
        edits.append((target.start(), target.end(), _quote(names[name])))
    if not edits:
        raise ValueError("Prepared query needs a table binding")
    for start, end, replacement in reversed(edits):
        query = query[:start] + replacement + query[end:]
    return query


def prepare_arm_variant(prepared_dir, output_dir, *, mapping_path=EXAMPLES / "arm_mapping.json"):
    """Make an optional, identity-preserving view-integration validation artifact.

    These are the same 42 questions and expected answers, not new independent
    questions or evidence of a theoretical ARM benefit. SQL gets the same views.
    """
    from .arm_mapping import materialize_snapshot

    prepared_dir, output_dir, mapping_path = map(Path, (prepared_dir, output_dir, mapping_path))
    config = json.loads(mapping_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    names = {entity["source_table"]: entity["name"] for entity in config["entities"]}
    if set(names) != set(TABLE_COLUMNS) or len(config["entities"]) != len(TABLE_COLUMNS):
        raise ValueError("Chinook variant must map exactly the retained source tables")
    for entity in config["entities"]:
        table = entity["source_table"]
        expected_columns = [{"name": col, "source_column": col} for col in TABLE_COLUMNS[table]]
        expected_references = [
            {"feature": role, "source_column": col, "target_entity": names[target]}
            for source, role, col, target, _ in REFERENCES if source == table]
        if (entity["columns"] != expected_columns or entity["key"] != TABLE_COLUMNS[table][0] or
                entity["references"] != expected_references):
            raise ValueError("Chinook table-binding variant must preserve columns, keys, and roles")
    rewritten = {}
    for scope in ("development", "held_out"):
        workload = json.loads((prepared_dir / (scope + ".json")).read_text(encoding="utf-8"),
                              object_pairs_hook=_unique_object)
        if workload.get("scope") != scope:
            raise ValueError("Input workload scope does not match file name")
        for case in workload["cases"]:
            for language in ("sql", "sqlp"):
                case[language] = rewrite_table_bindings(case[language], names)
        rewritten[scope] = workload
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        artifact = materialize_snapshot(prepared_dir / "snapshot.sqlite", output_dir / "snapshot.sqlite", config)
        shutil.copyfile(EXAMPLES / "LICENSE.txt", output_dir / "CHINOOK_LICENSE.txt")
        for name, value in (("catalog", artifact["catalog"]), ("mapping-artifact", artifact), *rewritten.items()):
            (output_dir / (name + ".json")).write_text(
                json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        manifest = {
            "version": 1, "dataset": "Chinook logical-view integration variant",
            "source_manifest_sha256": _hash(prepared_dir / "manifest.json"),
            "mapping_sha256": _hash(mapping_path), "model_calls": 0,
            "files_sha256": {p.name: _hash(p) for p in sorted(output_dir.iterdir())},
            "human_answer_review": "pending",
            "interpretation": "The same tasks/answers through mechanically renamed views; not additional independent questions",
            "limitations": ["No abstract entity IDs or identity resolution", "Same logical views and facts for SQL and SQLP"],
        }
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(output_dir)
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_sql", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--arm-output", type=Path,
                        help="also prepare a fresh optional logical-view variant directory")
    args = parser.parse_args()
    try:
        manifest = prepare(args.source_sql, args.output_dir)
        if args.arm_output is not None:
            prepare_arm_variant(args.output_dir, args.arm_output)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2
    print(json.dumps({"ok": True, "output_dir": str(args.output_dir),
                      "row_counts": manifest["row_counts"], "model_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

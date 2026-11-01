"""Read-only checks for the SQLite data behind an approved SQLP catalog."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_REFERENCE_FIELDS = ("source_table", "feature", "source_column", "target_table", "target_column")


def _name(value):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("Catalog names must be nonempty strings without NUL")
    value.encode("utf-8")
    return value


def _validate_catalog(catalog):
    if not isinstance(catalog, dict) or type(catalog.get("version")) is not int or catalog["version"] != 1:
        raise ValueError("Expected a version-1 catalog object")
    tables, references = catalog.get("tables"), catalog.get("references")
    if not isinstance(tables, list) or not tables or not isinstance(references, list):
        raise ValueError("Catalog needs a nonempty tables list and a references list")
    names, columns = set(), {}
    for table in tables:
        if not isinstance(table, dict):
            raise ValueError("Each table must be an object")
        name = _name(table.get("name"))
        folded = name.translate(_ASCII_FOLD)
        if folded in names:
            raise ValueError("Duplicate table name: " + name)
        names.add(folded)
        fields = table.get("columns")
        if not isinstance(fields, list) or not fields:
            raise ValueError("Each table needs a nonempty columns list")
        seen = set()
        for column in fields:
            if not isinstance(column, dict):
                raise ValueError("Each column must be an object")
            field = _name(column.get("name"))
            if field.translate(_ASCII_FOLD) in seen:
                raise ValueError("Duplicate column in table: " + name)
            seen.add(field.translate(_ASCII_FOLD))
            if column.get("type") not in ("integer", "string") or type(column.get("unique")) is not bool:
                raise ValueError("Columns need type integer|string and a Boolean unique declaration")
            columns[name, field] = column
    roles = set()
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError("Each reference must be an object")
        source, role, source_key, target, target_key = (_name(reference.get(k)) for k in _REFERENCE_FIELDS)
        identity = source, role.translate(_ASCII_FOLD)
        if identity in roles:
            raise ValueError("Duplicate reference role: " + role)
        roles.add(identity)
        if (source, source_key) not in columns or (target, target_key) not in columns:
            raise ValueError("Reference names an undeclared table or column: " + role)
        left, right = columns[source, source_key], columns[target, target_key]
        if left["type"] != right["type"] or not right["unique"]:
            raise ValueError("Reference needs matching key types and a declared unique target: " + role)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def load_catalog(path):
    catalog = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    _validate_catalog(catalog)
    return catalog


def compiler_arguments(catalog):
    """Turn the same catalog facts into native sqlpc arguments, without a shell."""
    _validate_catalog(catalog)
    result = []
    for table in catalog["tables"]:
        for column in table["columns"]:
            result.extend(["--column", table["name"], column["name"], column["type"],
                           "unique" if column["unique"] else "nonunique"])
    for reference in catalog["references"]:
        result.extend(["--reference", *(reference[k] for k in _REFERENCE_FIELDS)])
    return result


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _inspect_rows(connection, catalog):
    issues, available = [], set()

    def issue(code, count, **details):
        if count:
            issues.append({"code": code, "count": count, **details})

    # Inspect names before querying them: SQLite can treat unknown quoted names as strings.
    actual = {row[0] for row in connection.execute(
        "SELECT name FROM main.sqlite_schema WHERE type IN ('table', 'view')")}
    temporary = {row[0].translate(_ASCII_FOLD) for row in connection.execute(
        "SELECT name FROM temp.sqlite_schema WHERE type IN ('table', 'view')")}
    for table in catalog["tables"]:
        name = table["name"]
        if name.translate(_ASCII_FOLD) in temporary:
            issue("shadowed_table", 1, table=name)
        if name not in actual:
            issue("missing_table", 1, table=name)
            continue
        physical_columns = {row[1] for row in connection.execute(
            "PRAGMA main.table_xinfo(" + _quoted(name) + ")")}
        for column in table["columns"]:
            field = column["name"]
            if field not in physical_columns:
                issue("missing_column", 1, table=name, column=field)
                continue
            available.add((name, field))
            sql_table, sql_column = 'main.' + _quoted(name), 's.' + _quoted(field)
            expected = "integer" if column["type"] == "integer" else "text"
            invalid = connection.execute(
                f"SELECT COUNT(*) FROM {sql_table} AS s "
                f"WHERE {sql_column} IS NOT NULL AND typeof({sql_column}) != ?", (expected,)).fetchone()[0]
            issue("invalid_type", invalid, table=name, column=field)
            if column["unique"]:
                nulls = connection.execute(
                    f"SELECT COUNT(*) FROM {sql_table} AS s WHERE {sql_column} IS NULL").fetchone()[0]
                issue("null_unique_key", nulls, table=name, column=field)
                duplicates = connection.execute(
                    f"SELECT COUNT(*) FROM (SELECT {sql_column} FROM {sql_table} AS s "
                    f"WHERE {sql_column} IS NOT NULL GROUP BY {sql_column} HAVING COUNT(*) > 1)").fetchone()[0]
                issue("duplicate_unique_key", duplicates, table=name, column=field)

    for reference in catalog["references"]:
        source, role, source_key, target, target_key = (reference[k] for k in _REFERENCE_FIELDS)
        if (source, source_key) not in available or (target, target_key) not in available:
            continue
        source_table, target_table = 'main.' + _quoted(source), 'main.' + _quoted(target)
        left, right = 's.' + _quoted(source_key), 't.' + _quoted(target_key)
        nulls = connection.execute(
            f"SELECT COUNT(*) FROM {source_table} AS s WHERE {left} IS NULL").fetchone()[0]
        issue("null_reference_key", nulls, source_table=source, feature=role)
        missing, ambiguous = 0, 0
        # Keep source on the LEFT, as in the compiler's JOIN, including collation precedence.
        matches = connection.execute(
            f"SELECT (SELECT COUNT(*) FROM {target_table} AS t WHERE {left} = {right}) "
            f"FROM {source_table} AS s WHERE {left} IS NOT NULL")
        for (count,) in matches:
            missing += count == 0
            ambiguous += count > 1
        issue("missing_reference_target", missing, source_table=source, feature=role)
        issue("ambiguous_reference_target", ambiguous, source_table=source, feature=role)
    return {"version": 1, "ok": not issues, "issues": issues}


def validate_dataset(connection, catalog):
    """Check one read snapshot; preserve any transaction already owned by the caller."""
    _validate_catalog(catalog)
    owned_transaction = not connection.in_transaction
    if owned_transaction:
        connection.execute("BEGIN")
    try:
        return _inspect_rows(connection, catalog)
    finally:
        if owned_transaction and connection.in_transaction:
            connection.execute("ROLLBACK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("catalog", type=Path)
    arguments = parser.parse_args()
    stage = "catalog"
    try:
        catalog = load_catalog(arguments.catalog)
        stage = "database"
        uri = arguments.database.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            report = validate_dataset(connection, catalog)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"version": 1, "ok": False, "error": {"stage": stage, "message": str(error)}}))
        return 2
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

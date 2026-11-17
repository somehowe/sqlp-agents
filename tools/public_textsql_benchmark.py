"""Freeze and check public Spider development questions without model calls.

Public prompts contain the question and complete schema only. Published SQL and
execution answers stay in private/workload.json. This is a bounded single-data
execution check, not Spider's official structural/official execution metric.
"""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, deque
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3

from .run_comparison import QueryError, _execute, _rows


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "resources/icde-audit/natsql-reproduction-independent"
SOURCE_HASHES = {
    "spider-dev.json": "30d64a3fccde493226df79687aed9e4a1c0129525baf44f29c0573d914d758a4",
    "spider-tables.json": "61bb20aa401f03164e2d7f3b16509b7b5f79cc9c943ca7bd159046df1159e2ed",
}
SOURCE_URL = "https://yale-lily.github.io/spider"


def _hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1_048_576), b""):
            value.update(chunk)
    return value.hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def query_features(ast):
    """Describe published Spider ASTs, retaining SQLP-incompatible operators.

    Absence of these features is only a syntactic candidate: total references,
    physical types and representability as one rooted path still need review.
    No candidate is claimed to be a valid SQLP translation.
    """
    features = set()
    reasons = set()
    scopes = []

    def column(unit):
        if not isinstance(unit, (list, tuple)) or len(unit) != 3:
            return
        aggregation, column_id, distinct = unit
        if aggregation:
            features.add("aggregate")
            reasons.add("aggregate")
        if column_id == 0:
            features.add("star_projection_or_operand")
            reasons.add("star_projection_or_operand")

    def value(unit):
        if not isinstance(unit, (list, tuple)) or len(unit) != 3:
            return
        arithmetic, first, second = unit
        if arithmetic:
            features.add("arithmetic")
            reasons.add("arithmetic")
        column(first)
        if second is not None:
            column(second)

    def conditions(items, *, join=False):
        for item in items:
            if isinstance(item, str):
                if item.lower() == "or":
                    features.add("or")
                    reasons.add("or")
                continue
            if not isinstance(item, (list, tuple)) or len(item) != 5:
                continue
            negated, operator, left, first, second = item
            value(left)
            if negated:
                features.add("negation")
                reasons.add("negation")
            if operator != 2:
                names = {1: "between", 3: "greater_than", 4: "less_than", 5: "greater_equal",
                         6: "less_equal", 7: "not_equal", 8: "in", 9: "like", 10: "is", 11: "exists"}
                name = names.get(operator, "other_predicate")
                features.add(name)
                reasons.add(name)
            for operand in (first, second):
                if isinstance(operand, dict):
                    features.add("subquery")
                    reasons.add("subquery")
                    scope(operand)
                elif isinstance(operand, (list, tuple)):
                    column(operand)
                    if not join:
                        features.add("column_comparison")
                        reasons.add("column_comparison")
                elif type(operand) is float and (not operand.is_integer() or not math.isfinite(operand)):
                    features.add("real_literal")
                    reasons.add("real_literal")

    def scope(node):
        if not isinstance(node, dict) or "select" not in node:
            return
        scopes.append(node)
        selections = node["select"]
        if selections[0]:
            features.add("distinct")
        for aggregate, expression in selections[1]:
            if aggregate:
                features.add("aggregate")
                reasons.add("aggregate")
            value(expression)
        tables = node.get("from", {}).get("table_units", [])
        if len(tables) > 1:
            features.add("multi_table")
        for kind, table in tables:
            if kind == "sql":
                features.add("derived_table")
                reasons.add("derived_table")
                scope(table)
        conditions(node.get("from", {}).get("conds", []), join=True)
        conditions(node.get("where", []))
        conditions(node.get("having", []))
        for field, name in (("groupBy", "group_by"), ("having", "having"), ("orderBy", "order_by")):
            if node.get(field):
                features.add(name)
                reasons.add(name)
        for expression in node.get("groupBy", []):
            column(expression)
        if node.get("orderBy"):
            for expression in node["orderBy"][1]:
                value(expression)
        if node.get("limit") is not None:
            features.add("limit")
            reasons.add("limit")
        for operator in ("union", "intersect", "except"):
            if node.get(operator):
                features.add(operator)
                reasons.add(operator)
                scope(node[operator])

    scope(ast)
    max_tables = max((len(s.get("from", {}).get("table_units", [])) for s in scopes), default=0)
    return {
        "features": sorted(features),
        "sql_scopes": len(scopes),
        "maximum_tables_per_scope": max_tables,
        "sqlp_profile": {
            "status": "outside_current_fragment" if reasons else "unverified_syntactic_candidate",
            "reasons": sorted(reasons),
            "evaluated_as_sqlp": False,
        },
        "ordered_result": bool(ast.get("orderBy")),
    }


def public_schema(connection, published):
    """Retain actual types, nullable columns and composite keys without coercion."""
    tables = []
    for name, create_sql in connection.execute(
            "SELECT name, sql FROM main.sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        columns = [{"name": row[1], "declared_type": row[2], "not_null": bool(row[3]),
                    "primary_key_position": row[5]}
                   for row in connection.execute("PRAGMA main.table_info(" + _quote(name) + ")")]
        foreign_keys = [{"id": row[0], "sequence": row[1], "target_table": row[2],
                         "source_column": row[3], "target_column": row[4],
                         "on_update": row[5], "on_delete": row[6], "match": row[7]}
                        for row in connection.execute("PRAGMA main.foreign_key_list(" + _quote(name) + ")")]
        tables.append({"name": name, "columns": columns, "foreign_keys": foreign_keys,
                       "create_sql": create_sql})
    declared = []
    columns = published["column_names_original"]
    table_names = published["table_names_original"]
    for source, target in published.get("foreign_keys", []):
        left, right = columns[source], columns[target]
        declared.append({"source_table": table_names[left[0]], "source_column": left[1],
                         "target_table": table_names[right[0]], "target_column": right[1]})
    return {"version": 1, "database_id": published["db_id"], "tables": tables,
            "published_foreign_keys": declared}


def _equivalent(first, second):
    if type(first) in (int, float) and type(second) in (int, float):
        return first == second or math.isclose(first, second, rel_tol=1e-6, abs_tol=1e-8)
    return type(first) is type(second) and first == second


def _matching_rows(actual, expected, ordered):
    if ordered:
        return (len(actual) == len(expected) and
                all(len(first) == len(second) and all(map(_equivalent, first, second))
                    for first, second in zip(actual, expected)))
    if Counter(map(tuple, actual)) == Counter(map(tuple, expected)):
        return True
    if len(actual) != len(expected):
        return False
    # Exact text/null partitions preserve multiplicity. Sorting numeric tuples
    # is a fast sufficient check, but close first coordinates can sort rows in
    # different orders, so ambiguous tuples need a one-to-one matching.
    def groups(rows):
        result = {}
        for row in rows:
            key = tuple(("numeric",) if type(cell) in (int, float) else
                        ("null",) if cell is None else ("value", type(cell).__name__, cell)
                        for cell in row)
            numeric = tuple(cell for cell in row if type(cell) in (int, float))
            result.setdefault(key, []).append(numeric)
        return result
    first_groups, second_groups = groups(actual), groups(expected)
    if first_groups.keys() != second_groups.keys():
        return False
    return all(_matching_numeric_bags(rows, second_groups[key])
               for key, rows in first_groups.items())


def _matching_numeric_bags(actual, expected):
    if len(actual) != len(expected):
        return False
    actual, expected = sorted(actual), sorted(expected)
    if _matching_rows(actual, expected, True):
        return True
    if not actual or not actual[0]:
        return False
    # A mismatch in a column's sorted marginal rules out any row matching.
    for column in range(len(actual[0])):
        first = sorted(row[column] for row in actual)
        second = sorted(row[column] for row in expected)
        if not all(map(_equivalent, first, second)):
            return False
    coordinates = [row[0] for row in expected]
    candidates = []
    for row in actual:
        # math.isclose uses max(abs(a), abs(b)); this wider interval includes
        # every permitted b, after which every coordinate is checked exactly.
        window = max(1e-8, abs(row[0]) * 1e-6 / (1 - 1e-6))
        low = bisect_left(coordinates, math.nextafter(row[0] - window, -math.inf))
        high = bisect_right(coordinates, math.nextafter(row[0] + window, math.inf))
        matches = [index for index in range(low, high)
                   if all(map(_equivalent, row, expected[index]))]
        if not matches:
            return False
        candidates.append(matches)
    owners = [-1] * len(expected)
    for source in sorted(range(len(actual)), key=lambda index: len(candidates[index])):
        queue, previous_left, previous_right = deque([source]), {source: None}, {}
        terminal = None
        while queue and terminal is None:
            left = queue.popleft()
            for right in candidates[left]:
                if right in previous_right:
                    continue
                previous_right[right] = left
                if owners[right] == -1:
                    terminal = right
                    break
                owner = owners[right]
                if owner not in previous_left:
                    previous_left[owner] = right
                    queue.append(owner)
        if terminal is None:
            return False
        right = terminal
        while right is not None:
            left = previous_right[right]
            owners[right] = left
            right = previous_left[left]
    return True


def score_case(connection, case, query, *, max_rows=100_000, timeout_seconds=5.0):
    """Score one SQL answer; unavailable gold remains an unscorable attempt.

    Explicit ORDER BY in published gold requires the same ordered values.
    Otherwise compare row bags. Numeric values permit relative tolerance 1e-6
    and absolute tolerance 1e-8; strings/nulls and multiplicities are exact.
    Column labels are ignored. Gold ORDER BY ties can be sequence-sensitive.
    """
    record = {"language": "sql", "query": query, "correct": None,
              "status": "unscorable_gold", "error": case.get("gold_error"),
              "columns": None, "rows": None}
    if case.get("gold_status") != "ok":
        return record
    try:
        columns, rows = _execute(connection, query, [], max_rows, timeout_seconds)
    except QueryError as error:
        record.update(status="error", correct=False,
                      error={"stage": "execute", "code": error.code, "message": str(error)})
        return record
    expected = case["expected_rows"]
    actual = [list(row) for row in rows]
    matches = _matching_rows(actual, expected, case["metadata"]["ordered_result"])
    record.update(status="ok", correct=len(columns) == case["column_count"] and matches,
                  columns=columns, rows=_rows(rows), error=None)
    return record


def score_generated(database, case, query, *, max_rows=100_000, timeout_seconds=5.0):
    """Read-only path wrapper around score_case for callers without a connection."""
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True,
                                 cached_statements=0)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1_000_000)
        return score_case(connection, case, query, max_rows=max_rows, timeout_seconds=timeout_seconds)


def prepare(output, *, source_root=DEFAULT_SOURCE, max_rows=100_000, timeout_seconds=5.0,
            verify_pinned=True):
    """Copy all official dev questions and DBs into a fresh reviewable artifact."""
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Output directory must be fresh")
    if type(max_rows) is not int or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    for filename, digest in SOURCE_HASHES.items():
        if verify_pinned and _hash(source_root / filename) != digest:
            raise ValueError("Pinned source SHA256 mismatch: " + filename)
    questions = json.loads((source_root / "spider-dev.json").read_text())
    schemas = {schema["db_id"]: schema for schema in
               json.loads((source_root / "spider-tables.json").read_text())}
    groups = {}
    for index, question in enumerate(questions):
        db_id = question["db_id"]
        if not isinstance(db_id, str) or not db_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in db_id):
            raise ValueError("Invalid source database ID")
        if not isinstance(question.get("question"), str) or not question["question"]:
            raise ValueError("Missing natural language question")
        if not isinstance(question.get("query"), str) or not question["query"]:
            raise ValueError("Missing published SQL")
        if db_id not in schemas or not (source_root / "database" / db_id / (db_id + ".sqlite")).is_file():
            raise ValueError("Missing source schema/database: " + db_id)
        groups.setdefault(db_id, []).append((index, question))
    if not groups:
        raise ValueError("Source has no questions")
    extraction = source_root / "spider-extraction-manifest.json"
    pinned_databases = {entry["path"]: entry["sha256"] for entry in
                        json.loads(extraction.read_text())} if extraction.is_file() else {}
    database_hashes = {}
    for db_id in groups:
        relative = "database/" + db_id + "/" + db_id + ".sqlite"
        digest = _hash(source_root / relative)
        if verify_pinned and pinned_databases.get(relative) != digest:
            raise ValueError("Pinned database SHA256 mismatch: " + db_id)
        database_hashes[db_id] = digest
    output.mkdir(parents=True)
    feature_counts, profile_counts, error_counts = Counter(), Counter(), Counter()
    datasets = []
    total_rows, total_tables, gold_ok, gold_errors, ordered = 0, 0, 0, 0, 0
    for db_id, members in groups.items():
        folder = output / db_id
        folder.mkdir()
        database = folder / "database.sqlite"
        shutil.copyfile(source_root / "database" / db_id / (db_id + ".sqlite"), database)
        if _hash(database) != database_hashes[db_id]:
            raise ValueError("Copied database hash mismatch: " + db_id)
        private_cases, public_cases, row_counts = [], [], {}
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, cached_statements=0)) as connection:
            connection.execute("PRAGMA query_only=ON")
            schema = public_schema(connection, schemas[db_id])
            for table in schema["tables"]:
                row_counts[table["name"]] = connection.execute("SELECT count(*) FROM " + _quote(table["name"])).fetchone()[0]
            for index, question in members:
                metadata = {"source_index": index, **query_features(question["sql"])}
                feature_counts.update(metadata["features"])
                profile_counts.update([metadata["sqlp_profile"]["status"]])
                ordered += int(metadata["ordered_result"])
                case = {"id": "spider-dev-" + str(index).zfill(4), "question": question["question"],
                        "sql": question["query"], "sqlp": "", "metadata": metadata,
                        "gold_status": "ok", "gold_error": None, "expected_rows": None, "column_count": None}
                try:
                    columns, rows = _execute(connection, question["query"], [], max_rows, timeout_seconds)
                except QueryError as error:
                    case["gold_status"] = "error"
                    case["gold_error"] = {"stage": "gold_execute", "code": error.code, "message": str(error)}
                    gold_errors += 1
                    error_counts.update([error.code])
                else:
                    case.update(expected_rows=[list(row) for row in rows], column_count=len(columns))
                    gold_ok += 1
                private_cases.append(case)
                public_cases.append({"id": case["id"], "question": case["question"]})
        _write(folder / "public/schema.json", schema)
        _write(folder / "public/tasks.json", {"version": 1, "cases": public_cases})
        _write(folder / "private/workload.json", {"version": 1, "scope": "development", "cases": private_cases,
               "oracle": "Published Spider SQL execution; not independently authored semantic answers",
               "metric": "ordered values when gold has ORDER BY; otherwise duplicate-preserving row bags",
               "numeric_tolerance": {"relative": 1e-6, "absolute": 1e-8}})
        paths = {name: str((folder / relative).relative_to(output)) for name, relative in
                 (("database", "database.sqlite"), ("schema", "public/schema.json"),
                  ("tasks", "public/tasks.json"), ("workload", "private/workload.json"))}
        datasets.append({"id": db_id, "directory": db_id, **paths, "case_count": len(members),
                         "table_count": len(row_counts), "row_count": sum(row_counts.values()),
                         "table_rows": row_counts,
                         "hashes": {name: _hash(output / path) for name, path in paths.items()}})
        total_rows += sum(row_counts.values())
        total_tables += len(row_counts)
    manifest = {
        "version": 1, "kind": "public_textsql_benchmark", "source": "Spider official development split",
        "source_url": SOURCE_URL, "origin": "published_reference_execution", "model_calls": 0,
        "source_license": {"dataset": "CC BY-SA 4.0", "source_url": SOURCE_URL,
                           "evaluator_code": "Apache-2.0 (not copied or used by this scorer)"},
        "question_count": len(questions), "database_count": len(groups), "table_count": total_tables,
        "unique_question_count": len({q["question"] for q in questions}),
        "unique_database_query_count": len({(q["db_id"], q["query"]) for q in questions}),
        "row_count": total_rows, "gold_executable": gold_ok, "gold_errors": gold_errors,
        "gold_error_counts": dict(error_counts), "ordered_question_count": ordered,
        "feature_counts": dict(sorted(feature_counts.items())), "sqlp_profile_counts": dict(profile_counts),
        "source_hashes": {name: _hash(source_root / name) for name in SOURCE_HASHES},
        "limits": {"max_rows": max_rows, "timeout_seconds": timeout_seconds}, "datasets": datasets,
        "scoring": {"metric": "single_snapshot_execution_match", "numeric_relative_tolerance": 1e-6,
                    "numeric_absolute_tolerance": 1e-8, "order_sensitive_when_gold_orders": True,
                    "duplicate_sensitive": True, "projection_width_sensitive": True},
        "limitations": [
            "Published development questions may occur in model training; this is not a held-out contamination-free test.",
            "Different questions can paraphrase the same database/query; question count is not independent semantic-task count.",
            "Gold execution on one original database is not semantic equivalence or independent human review.",
            "Static SQLP candidates have not been translated or checked for total references and physical type compatibility.",
            "Outside-fragment queries are coverage limits, not SQLP accuracy failures or evidence of another language's superiority.",
            "Failed gold execution remains explicit and unscorable; original database bytes are never repaired.",
            "Execution scoring is not the official Spider structural/execution evaluator.",
            "Sequence comparison of ordered outputs can reject valid alternative ordering within ties; numeric row-bag matching uses sorted numeric tuples within exact nonnumeric groups.",
        ],
    }
    _write(output / "manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--max-rows", type=int, default=100_000)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    options = parser.parse_args(argv)
    result = prepare(options.output, source_root=options.source_root, max_rows=options.max_rows,
                     timeout_seconds=options.timeout_seconds)
    print(json.dumps({key: result[key] for key in ("question_count", "database_count", "table_count", "row_count",
                                                  "gold_executable", "gold_errors", "feature_counts", "sqlp_profile_counts")}, indent=2))


if __name__ == "__main__":
    main()

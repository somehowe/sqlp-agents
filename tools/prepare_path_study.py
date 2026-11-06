"""Construct controlled relationship-path tasks and contrasting valid snapshots.

This is a project-authored synthetic development study, not an external
benchmark. No model calls, downloads, or private credentials are involved.
"""

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import platform
import random
import sqlite3

from .run_comparison import DEFAULT_COMPILER, run_comparison
from .validate_dataset import validate_dataset

DEPTHS = (1, 2, 4, 6)
COUNTRIES = ("Canada", "Denmark", "Japan", "Kenya")
BASE_COLUMNS = {
    "Assignment": ("AssignmentId", "Code", "Country", "Label", "RequesterId", "ApproverId", "ReviewerId", "OwnerId"),
    "Person": ("PersonId", "Name", "Country", "Label", "ManagerId", "MentorId", "ReviewerId"),
}
BASE_REFERENCES = (
    ("Assignment", "requester", "RequesterId", "Person", "PersonId"),
    ("Assignment", "approver", "ApproverId", "Person", "PersonId"),
    ("Assignment", "reviewer", "ReviewerId", "Person", "PersonId"),
    ("Assignment", "owner", "OwnerId", "Person", "PersonId"),
    ("Person", "manager", "ManagerId", "Person", "PersonId"),
    ("Person", "mentor", "MentorId", "Person", "PersonId"),
    ("Person", "reviewer", "ReviewerId", "Person", "PersonId"),
)
REFERENCE_FIELDS = ("source_table", "feature", "source_column", "target_table", "target_column")


def _check_count(distractor_tables):
    if type(distractor_tables) is not int or not 0 <= distractor_tables <= 64:
        raise ValueError("distractor_tables must be an integer from zero to 64")


def columns_and_references(distractor_tables=0):
    _check_count(distractor_tables)
    columns, references = dict(BASE_COLUMNS), list(BASE_REFERENCES)
    for index in range(1, distractor_tables + 1):
        table = "Archive" + str(index)
        columns[table] = ("ArchiveId", "Name", "Country", "OwnerId")
        references.append((table, "owner", "OwnerId", "Person", "PersonId"))
    return columns, tuple(references)


def make_catalog(distractor_tables=0):
    columns, references = columns_and_references(distractor_tables)
    return {"version": 1, "tables": [
        {"name": table, "columns": [
            {"name": column, "type": "integer" if column.endswith("Id") else "string", "unique": i == 0}
            for i, column in enumerate(names)]} for table, names in columns.items()],
        "references": [dict(zip(REFERENCE_FIELDS, ref)) for ref in references]}


def create_snapshot(path, seed, *, distractor_tables=0):
    """Create keyed records with total cyclic references and repeated labels."""
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("seed must be a nonnegative int64 integer")
    path = Path(path)
    if path.exists():
        raise ValueError("Snapshot destination already exists")
    columns, references = columns_and_references(distractor_tables)
    rng = random.Random(seed)
    people = list(range(1, 17))
    manager_order = rng.sample(people, len(people))
    manager = {person: manager_order[(i + 1) % len(people)] for i, person in enumerate(manager_order)}
    mentor_order, reviewer_order = rng.sample(people, len(people)), rng.sample(people, len(people))
    mentor, reviewer = dict(zip(people, mentor_order)), dict(zip(people, reviewer_order))
    country_order = rng.sample(COUNTRIES * 4, len(people))
    person_rows = [(person, "Person " + str((person - 1) % 5 + 1), country_order[person - 1],
                    "Person label " + str(person % 3), manager[person], mentor[person], reviewer[person])
                   for person in people]
    assignment_rows = []
    for identifier in range(1, 33):
        role_people = rng.sample(people, 4)
        assignment_rows.append((identifier, "A" + str(identifier).zfill(3),
                                COUNTRIES[identifier % len(COUNTRIES)],
                                "Assignment label " + str(identifier % 3), *role_people))
    rows = {"Person": person_rows, "Assignment": assignment_rows}
    for table in columns:
        if table not in rows:
            rows[table] = [(i, "Archive item " + str(i % 3), COUNTRIES[i % 4], rng.choice(people))
                           for i in range(1, 9)]
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        for table, names in columns.items():
            declarations = [name + (" INTEGER" if name.endswith("Id") else " TEXT") +
                            (" PRIMARY KEY NOT NULL" if i == 0 else " NOT NULL")
                            for i, name in enumerate(names)]
            declarations += ["FOREIGN KEY (" + source + ") REFERENCES " + target + " (" + key +
                             ") DEFERRABLE INITIALLY DEFERRED"
                             for owner, _, source, target, key in references if owner == table]
            connection.execute("CREATE TABLE " + table + " (" + ", ".join(declarations) + ")")
        for table, names in columns.items():
            connection.executemany("INSERT INTO " + table + " VALUES (" + ",".join("?" for _ in names) + ")", rows[table])
        connection.commit()
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Constructed snapshot violates foreign keys")
        if not validate_dataset(connection, make_catalog(distractor_tables))["ok"]:
            raise ValueError("Constructed snapshot fails catalog checks")
    return {table: len(items) for table, items in rows.items()}


def _chain(first, depth, start="manager"):
    rest = ("manager", "mentor") if start == "manager" else ("mentor", "manager")
    return (first,) + tuple(rest[i % 2] for i in range(depth - 1))


def _person_phrase(roles):
    return "the assignment's " + "'s ".join(roles)


def task_plans():
    """Private, explicit interpretations; neither queries nor plans enter prompts."""
    plans = []
    for depth in DEPTHS:
        left = _chain("requester", depth)
        right = _chain("approver", depth, "mentor")
        left_name, right_name = (*left, "Name"), (*right, "Name")
        left_country, right_country = (*left, "Country"), (*right, "Country")
        qualifier = " Keep one row per qualifying assignment, even if projected values repeat."
        variants = [
            ("List the code of every assignment and the name of " + _person_phrase(left) + ". Keep one row per assignment.",
             [("Code",), left_name], [], False, "projection"),
            ("For each assignment, return its code and the country where " + _person_phrase(right) + " lives.",
             [("Code",), right_country], [], False, "projection"),
            ("For assignments where " + _person_phrase(left) + " lives in Canada, list the assignment code and that person's name." + qualifier,
             [("Code",), left_name], [(left_country, "Canada")], False, "filter"),
            ("Find assignment codes and names of " + _person_phrase(right) + " when that person lives in Denmark." + qualifier,
             [("Code",), right_name], [(right_country, "Denmark")], False, "filter"),
            ("For every assignment, list its code, the name of " + _person_phrase(left) + ", and the name of " + _person_phrase(right) + ", in that order. Keep one row per assignment.",
             [("Code",), left_name, right_name], [], False, "competing_roles"),
            ("List assignment code, name of " + _person_phrase(left) + ", and name of " + _person_phrase(right) +
             " for assignments where the former person lives in Canada and the latter lives in Denmark." + qualifier,
             [("Code",), left_name, right_name], [(left_country, "Canada"), (right_country, "Denmark")], False, "competing_filters"),
            ("List the name of " + _person_phrase(left) + " for every assignment. Return only the name column, with one row for each assignment. If different assignments reach people with the same name, keep all repeated name rows.",
             [left_name], [], False, "duplicates"),
            ("Among assignments whose own recorded country is Canada, which distinct person names occur for " + _person_phrase(right) + "? Return only the name column, with each name once.",
             [right_name], [(("Country",), "Canada")], True, "distinct"),
        ]
        for index, (question, projections, predicates, distinct, kind) in enumerate(variants, 1):
            plans.append({"id": "path-d" + str(depth) + "-" + str(index).zfill(2), "question": question,
                          "depth": depth, "kind": kind, "projections": projections,
                          "predicates": predicates, "distinct": distinct})
    return plans


def load_records(connection, distractor_tables=0):
    records, indexes = {}, {}
    columns, _ = columns_and_references(distractor_tables)
    for table, names in columns.items():
        records[table] = [dict(zip(names, row)) for row in connection.execute(
            "SELECT " + ",".join(names) + " FROM " + table + " ORDER BY " + names[0])]
        indexes[table] = {row[names[0]]: row for row in records[table]}
    return records, indexes


def oracle_rows(plan, records, indexes):
    """Follow Python dictionaries; no generated SQL or compiler participates."""
    references = {(owner, role): (source, target) for owner, role, source, target, _ in BASE_REFERENCES}
    def value(root, path):
        table, row = "Assignment", root
        for role in path[:-1]:
            source, target = references[table, role]
            row, table = indexes[target][row[source]], target
        return row[path[-1]]
    result = []
    seen = set()
    for root in records["Assignment"]:
        if not all(value(root, path) == literal for path, literal in plan["predicates"]):
            continue
        projected = tuple(value(root, path) for path in plan["projections"])
        if not plan["distinct"] or projected not in seen:
            result.append(list(projected))
            seen.add(projected)
    return result


def reference_queries(plan):
    """Render independent explicit key joins and the corresponding SQLP source."""
    mappings = {(owner, role): (source, target, key) for owner, role, source, target, key in BASE_REFERENCES}
    aliases = {(): ("a", "Assignment")}
    joins = []
    def sql_column(path):
        prefix = ()
        for role in path[:-1]:
            source_alias, owner = aliases[prefix]
            extended = prefix + (role,)
            if extended not in aliases:
                source, target, key = mappings[owner, role]
                alias = "j" + str(len(aliases))
                aliases[extended] = (alias, target)
                joins.append("INNER JOIN " + target + " AS " + alias + " ON " + source_alias + "." + source + " = " + alias + "." + key)
            prefix = extended
        return aliases[prefix][0] + "." + path[-1]
    prefix = "SELECT DISTINCT " if plan["distinct"] else "SELECT "
    selected = [sql_column(path) for path in plan["projections"]]
    predicates = [sql_column(path) + " = '" + literal.replace("'", "''") + "'" for path, literal in plan["predicates"]]
    sql = prefix + ", ".join(selected) + "\nFROM Assignment AS a"
    if joins:
        sql += "\n" + "\n".join(joins)
    if predicates:
        sql += "\nWHERE " + " AND ".join(predicates)
    sqlp = prefix + ", ".join("a." + ".".join(path) for path in plan["projections"]) + "\nFROM Assignment AS a"
    if plan["predicates"]:
        sqlp += "\nWHERE " + " AND ".join("a." + ".".join(path) + " = '" + literal.replace("'", "''") + "'" for path, literal in plan["predicates"])
    return sql + ";", sqlp + ";"


def build_workload(connection, *, distractor_tables=0):
    records, indexes = load_records(connection, distractor_tables)
    cases = []
    for plan in task_plans():
        sql, sqlp = reference_queries(plan)
        cases.append({"id": plan["id"], "question": plan["question"], "sql": sql, "sqlp": sqlp,
                      "column_count": len(plan["projections"]), "expected_rows": oracle_rows(plan, records, indexes),
                      "design": {key: plan[key] for key in ("depth", "kind", "projections", "predicates", "distinct")}})
    return {"version": 1, "scope": "development", "dataset_kind": "constructed synthetic path study",
            "human_answer_review": "pending", "cases": cases}


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def role_swap_sensitivity(snapshot_paths, *, distractor_tables=0):
    """Check a specific deliberate role mistake, not general equivalence.

    Swapping requester with approver preserves all schema/cardinality rules.
    Expected rows are compared as bags, so duplicate effects remain visible.
    """
    detected = {plan["id"]: [] for plan in task_plans()}
    for index, path in enumerate(snapshot_paths):
        with closing(sqlite3.connect(path)) as connection:
            records, indexes = load_records(connection, distractor_tables)
        for plan in task_plans():
            def swap(components):
                names = {"requester": "approver", "approver": "requester"}
                return tuple(names.get(component, component) for component in components)
            changed = {**plan, "projections": [swap(path) for path in plan["projections"]],
                       "predicates": [(swap(path), literal) for path, literal in plan["predicates"]]}
            original = Counter(map(tuple, oracle_rows(plan, records, indexes)))
            substituted = Counter(map(tuple, oracle_rows(changed, records, indexes)))
            if original != substituted:
                detected[plan["id"]].append("primary" if index == 0 else "contrast-" + str(index))
    return {"mistake": "requester/approver role substitution in all selected/filter paths",
            "cases_detected_on_at_least_one_snapshot": sum(bool(paths) for paths in detected.values()),
            "undetected_cases": [case for case, paths in detected.items() if not paths],
            "case_detection_snapshots": detected,
            "interpretation": "Specific constructed wrong-role check, not a guarantee against all wrong queries"}


def prepare(output_dir, *, seed=20261106, alternate_seeds=(20261107, 20261108), distractor_tables=0,
            compiler_path=DEFAULT_COMPILER):
    """Produce one primary and alternate snapshots with identical public tasks."""
    _check_count(distractor_tables)
    seeds = (seed, *alternate_seeds)
    if len(seeds) > 6 or len(set(seeds)) != len(seeds):
        raise ValueError("Use up to six different snapshot seeds")
    if any(type(value) is not int or not 0 <= value < 2**63 for value in seeds):
        raise ValueError("Snapshot seeds must be nonnegative int64 integers")
    output_dir = Path(output_dir)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    catalog = make_catalog(distractor_tables)
    _write_json(output_dir / "catalog.json", catalog)
    _write_json(output_dir / "oracle-plans.json", task_plans())
    snapshots = []
    for index, value in enumerate(seeds):
        folder = output_dir if index == 0 else output_dir / ("contrast-" + str(index))
        if index:
            folder.mkdir(mode=0o700)
        database = folder / "snapshot.sqlite"
        counts = create_snapshot(database, value, distractor_tables=distractor_tables)
        with closing(sqlite3.connect(database)) as connection:
            workload = build_workload(connection, distractor_tables=distractor_tables)
        _write_json(folder / "development.json", workload)
        report = run_comparison(database, catalog, workload, compiler_path)
        if not report["ok"]:
            raise ValueError("Prepared reference pairs disagree with record-walk oracle")
        _write_json(folder / "prepared-report.json", report)
        snapshots.append({"name": "primary" if index == 0 else "contrast-" + str(index), "seed": value,
                          "database": str(database.relative_to(output_dir)),
                          "workload": str((folder / "development.json").relative_to(output_dir)),
                          "rows": counts, "prepared_pairs_correct": True,
                          "empty_cases": [case["id"] for case in workload["cases"] if not case["expected_rows"]]})
    manifest = {"version": 1, "kind": "constructed_synthetic_path_study", "model_calls": 0,
                "human_answer_review": "pending", "unique_question_templates": 32,
                "depth_counts": dict(Counter(plan["depth"] for plan in task_plans())),
                "distractor_tables": distractor_tables, "snapshots": snapshots,
                "generation_versions": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version},
                "role_swap_sensitivity": role_swap_sensitivity(
                    [output_dir / snapshot["database"] for snapshot in snapshots],
                    distractor_tables=distractor_tables),
                "limitations": ["Synthetic project-authored development tasks, not an established benchmark or data lake",
                    "Related templates and alternate snapshots are not additional independent natural-language tasks",
                    "Role facts are supplied, not discovered; expected answers still need human interpretation review",
                    "Snapshot correctness alone does not prove query equivalence"],
                "sha256": {str(path.relative_to(output_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sorted(output_dir.rglob("*")) if path.is_file()}}
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--alternate-seeds", type=int, nargs="*", default=[20261107, 20261108])
    parser.add_argument("--distractor-tables", type=int, default=0)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    args = parser.parse_args()
    try:
        manifest = prepare(args.output_dir, seed=args.seed, alternate_seeds=args.alternate_seeds,
                           distractor_tables=args.distractor_tables, compiler_path=args.compiler)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2
    print(json.dumps({"ok": True, "output_dir": str(args.output_dir), "cases": 32,
                      "snapshots": len(manifest["snapshots"]), "model_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

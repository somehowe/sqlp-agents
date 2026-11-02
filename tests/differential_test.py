"""Check compiled SQL against an independent in-memory path evaluator.

No model is called. A fixed relational fixture contains typed keys, cycles,
competing roles, nullable terminal values, duplicates, and awkward identifiers.
The oracle follows Python dictionaries; it never derives answers from SQL.
"""

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
INTEGER_KEYS = (-(2**63), -9007199254740993, -1, 0, 1, 9007199254740993, 2**63 - 1)
STRING_KEYS = ("key 0", "O'Brien", "café", "line\r\nkey", "", 'a"key', "back\\slash")
VALUES = ("CA", "CA", None, "DK", "O'Brien", "line\r\nvalue", "café")


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def literal(value):
    return str(value) if type(value) is int else "'" + value.replace("'", "''") + "'"


def row_bag(rows):
    return Counter(tuple(row) for row in rows)


@dataclass
class Case:
    source: str
    alias: str
    projections: list
    conditions: list
    distinct: bool


class Fixture:
    def __init__(self, connection):
        self.connection = connection
        self.names = ("node A", 'N"B', "node.café", "AND", "source records")
        role_names = ("next", "author.university", 'mentor"role', "AND", "line\r\nbreak")
        self.tables, self.references = {}, {}
        for index, name in enumerate(self.names):
            columns = {"id": "integer" if index % 2 == 0 else "string",
                       "value": "string", 'n"umber': "integer"}
            self.tables[name] = {"columns": columns, "rows": [], "by_id": {}, "roles": []}
        for index, name in enumerate(self.names):
            table = self.tables[name]
            for edge in range(2):
                # The first edge forms a four-table cycle. One second edge is
                # a self-reference; others branch back through repeated tables.
                target = name if (index == 1 and edge == 1) else self.names[(index + edge + 1) % 4]
                role = role_names[(2 * index + edge) % len(role_names)]
                key = "fk" + str(edge)
                table["columns"][key] = self.tables[target]["columns"]["id"]
                table["roles"].append(role)
                self.references[name, role] = key, target, "id"
        self.arguments = []
        for name, table in self.tables.items():
            columns = table["columns"]
            for column, kind in columns.items():
                self.arguments += ["--column", name, column, kind,
                                   "unique" if column == "id" and name != "source records" else "nonunique"]
            definitions = [quoted(column) + (" INTEGER" if kind == "integer" else " TEXT") +
                           (" NOT NULL UNIQUE" if column == "id" and name != "source records" else "")
                           for column, kind in columns.items()]
            connection.execute("CREATE TABLE " + quoted(name) + "(" + ",".join(definitions) + ")")
            own_keys = self.keys(name)
            row_indices = [*range(7), 0, 0] if name == "source records" else range(7)
            for index in row_indices:
                key = own_keys[index]
                row = {"id": key, "value": VALUES[index],
                       'n"umber': None if index == 2 else INTEGER_KEYS[index]}
                for edge, role in enumerate(table["roles"]):
                    foreign_key, target, _ = self.references[name, role]
                    row[foreign_key] = self.keys(target)[(index + 1 + 2 * edge) % 7]
                table["rows"].append(row)
                table["by_id"][key] = row
                connection.execute("INSERT INTO " + quoted(name) + " VALUES (" +
                                   ",".join("?" for _ in columns) + ")", list(row.values()))
        for (source, role), (source_key, target, target_key) in self.references.items():
            self.arguments += ["--reference", source, role, source_key, target, target_key]

    def keys(self, table):
        return INTEGER_KEYS if self.tables[table]["columns"]["id"] == "integer" else STRING_KEYS

    def terminal(self, source, path):
        for role in path[:-1]:
            _, source, _ = self.references[source, role]
        return source

    def value(self, row, source, path):
        for role in path[:-1]:
            key, target, _ = self.references[source, role]
            row = self.tables[target]["by_id"][row[key]]
            source = target
        return row[path[-1]]

    def expected(self, case):
        rows = []
        for row in self.tables[case.source]["rows"]:
            if all(self.value(row, case.source, path) == value for path, value in case.conditions):
                rows.append(tuple(self.value(row, case.source, path) for path, _ in case.projections))
        return list(set(rows)) if case.distinct else rows

    def random_path(self, source, rng):
        parts = []
        for _ in range(rng.randrange(7)):
            role = rng.choice(self.tables[source]["roles"])
            parts.append(role)
            _, source, _ = self.references[source, role]
        return tuple([*parts, rng.choice(list(self.tables[source]["columns"]))])

    def case(self, index, rng):
        root = self.names[0]
        if index < 2:
            # Always exercise duplicate counts and their DISTINCT counterpart.
            return Case("source records", "w", [(("value",), None)], [], index == 1)
        if index in (2, 3):
            role = self.tables[root]["roles"][index - 2]
            return Case(root, "FROM", [((role, "value"), None)],
                        [(("id",), INTEGER_KEYS[0])], False)
        if index in (4, 5):
            return Case(root, 'a".x', [(("id",), "boundary")],
                        [(("id",), INTEGER_KEYS[0 if index == 4 else -1])], False)
        source = rng.choice(self.names)
        alias = rng.choice(("w", "W_REF0", "FROM", "café", 'a".x'))
        projections = []
        for _ in range(rng.randrange(1, 8)):
            path = rng.choice(projections)[0] if projections and rng.random() < 0.3 else self.random_path(source, rng)
            label = rng.choice(("label", "AS", "café", 'x"y')) if rng.random() < 0.2 else None
            projections.append((path, label))
        conditions = []
        for _ in range(rng.randrange(6)):
            path = self.random_path(source, rng)
            choices = [self.value(row, source, path) for row in self.tables[source]["rows"]]
            choices = [value for value in choices if value is not None]
            kind = self.tables[self.terminal(source, path)]["columns"][path[-1]]
            value = rng.choice(choices) if choices and rng.random() < 0.8 else (999 if kind == "integer" else "missing")
            conditions.append((path, value))
        return Case(source, alias, projections, conditions, rng.choice((True, False)))


def query_text(case, index):
    def path_text(path):
        return ".".join(quoted(component) for component in (case.alias, *path))
    projections = [path_text(path) + (" AS " + quoted(label) if label is not None else "")
                   for path, label in case.projections]
    query = "SELECT " + ("DISTINCT " if case.distinct else "") + ", ".join(projections)
    query += " FROM " + quoted(case.source) + " AS " + quoted(case.alias)
    if case.conditions:
        query += " WHERE " + " AND ".join(path_text(path) + " = " + literal(value)
                                         for path, value in case.conditions)
    query += ";"
    if index % 5 == 0:
        query = "/* preceding comment */" + query.replace("SELECT ", "sElEcT\t", 1) + "-- trailing\r\n"
    return query


def check_trace(fixture, case, trace):
    if not isinstance(trace, dict):
        raise AssertionError("Missing translation trace")
    prefixes, paths = {}, []
    for clause, entries in (("select", case.projections), ("where", case.conditions)):
        for index, (path, _) in enumerate(entries):
            ids = []
            for depth in range(1, len(path)):
                prefix = path[:depth]
                if prefix not in prefixes:
                    prefixes[prefix] = len(prefixes)
                ids.append(prefixes[prefix])
            paths.append((clause, index, path, ids))
    joins, occurrences = trace.get("joins"), trace.get("paths")
    if not isinstance(joins, list) or len(joins) != len(prefixes) or not isinstance(occurrences, list) or len(occurrences) != len(paths):
        raise AssertionError("Trace occurrence/join count disagrees with independent role prefixes")
    aliases = {(): case.alias}
    for prefix, join_id in prefixes.items():
        join = joins[join_id]
        source_table = fixture.terminal(case.source, (*prefix[:-1], "id"))
        source_key, target_table, target_key = fixture.references[source_table, prefix[-1]]
        target_alias = join["target"]["alias"]
        if not isinstance(target_alias, str) or not target_alias or target_alias in aliases.values():
            raise AssertionError("Different reference prefixes need distinct nonempty SQL bindings")
        expected_source = {"table": source_table, "alias": aliases[prefix[:-1]], "column": source_key}
        expected_target = {"table": target_table, "alias": target_alias, "column": target_key}
        if join.get("role") != prefix[-1] or join.get("source") != expected_source or join.get("target") != expected_target:
            raise AssertionError("Trace join metadata does not implement the supplied relationship")
        aliases[prefix] = target_alias
    for actual, (clause, index, path, ids) in zip(occurrences, paths):
        expected = {"clause": clause, "index": index, "root": case.alias, "features": list(path),
                    "join_ids": ids, "column": {"table": fixture.terminal(case.source, path),
                    "alias": aliases[path[:-1]], "column": path[-1]}}
        if any(actual.get(key) != value for key, value in expected.items()):
            raise AssertionError("Trace path does not describe its occurrence and terminal column")


def check_oracle_sensitivity(fixture):
    """Contrasts guard against an oracle that ignores multiplicity or roles."""
    root = fixture.names[0]
    case = Case(root, "w", [(("value",), None)], [], False)
    expected = fixture.expected(case)
    wrong_distinct = fixture.connection.execute("SELECT DISTINCT value FROM " + quoted(root)).fetchall()
    if row_bag(expected) == row_bag(wrong_distinct):
        raise AssertionError("Fixture does not distinguish accidental DISTINCT")
    roles = fixture.tables[root]["roles"]
    right = Case(root, "w", [((roles[0], "value"), None)], [(("id",), INTEGER_KEYS[0])], False)
    wrong = Case(root, "w", [((roles[1], "value"), None)], right.conditions, False)
    if row_bag(fixture.expected(right)) == row_bag(fixture.expected(wrong)):
        raise AssertionError("Fixture does not distinguish relationship roles")
    exact = [(key,) for key in INTEGER_KEYS]
    rounded = [(float(key),) for key in INTEGER_KEYS]
    if row_bag(exact) == row_bag(rounded):
        raise AssertionError("Oracle lost int64 precision")


def run(compiler, seed, cases, timeout):
    rng = random.Random(seed)
    with sqlite3.connect(":memory:") as connection:
        fixture = Fixture(connection)
        check_oracle_sensitivity(fixture)
        for index in range(cases):
            case = fixture.case(index, rng)
            query = query_text(case, index)
            try:
                process = subprocess.run([str(compiler), *fixture.arguments], input=query,
                                         text=True, encoding="utf-8", capture_output=True, timeout=timeout)
                if process.returncode != 0:
                    raise AssertionError("Compiler rejected a generated valid query: " + process.stdout + process.stderr)
                result = json.loads(process.stdout)
                if not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1 or result.get("ok") is not True:
                    raise AssertionError("Unsupported compiler response")
                expected_parameters = [{"type": "int64" if type(value) is int else "string", "value": str(value)}
                                       for _, value in case.conditions]
                if result.get("parameters") != expected_parameters:
                    raise AssertionError("Parameter values, types, or order changed")
                parameters = [value for _, value in case.conditions]
                cursor = connection.execute(result["sql"], parameters)
                actual, expected = cursor.fetchall(), fixture.expected(case)
                labels = [label if label is not None else path[-1] for path, label in case.projections]
                if [column[0] for column in cursor.description] != labels:
                    raise AssertionError("Projection names/order differ")
                if row_bag(actual) != row_bag(expected):
                    raise AssertionError("Row bags differ: " + repr({"actual": actual, "expected": expected}))
                check_trace(fixture, case, result.get("trace"))
            except (AssertionError, AttributeError, IndexError, KeyError, TypeError, ValueError, sqlite3.Error, OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError(f"seed={seed}, case={index}, reproduce with --seed {seed} --cases {index + 1}\n"
                                   f"SQLP: {query!r}\n{error}") from error
    print(f"PASS {cases} differential cases (seed {seed}): row bags, parameter types/order, and path/join traces")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, default=ROOT / "build/sqlpc")
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--cases", type=int, default=1200)
    parser.add_argument("--timeout", type=float, default=5.0, help="per compiler invocation")
    args = parser.parse_args()
    if args.cases <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("cases and timeout must be positive; timeout must be finite")
    compiler = args.compiler.resolve()
    if not compiler.is_file():
        parser.error("compiler unavailable; run make first")
    try:
        run(compiler, args.seed, args.cases, args.timeout)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

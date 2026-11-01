"""Check declared relationships against real SQLite rows before agent runs."""

import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.validate_dataset import compiler_arguments, load_catalog, validate_dataset


def catalog():
    return {
        "version": 1,
        "tables": [
            {"name": "PARENT", "columns": [
                {"name": "id", "type": "integer", "unique": True},
                {"name": "name", "type": "string", "unique": False},
            ]},
            {"name": "CHILD", "columns": [
                {"name": "parent_id", "type": "integer", "unique": False},
                {"name": "note", "type": "string", "unique": False},
            ]},
        ],
        "references": [{"source_table": "CHILD", "feature": "parent", "source_column": "parent_id",
                        "target_table": "PARENT", "target_column": "id"}],
    }


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


class DatasetCheckTests(unittest.TestCase):
    def setUp(self):
        self.database = sqlite3.connect(":memory:")
        self.addCleanup(self.database.close)
        self.database.executescript("""
            CREATE TABLE PARENT(id INTEGER, name TEXT);
            CREATE TABLE CHILD(parent_id INTEGER, note TEXT);
            INSERT INTO PARENT VALUES (1, 'one'), (2, NULL);
            INSERT INTO CHILD VALUES (1, 'first'), (1, 'second'), (2, NULL);
        """)

    def report(self, description=None):
        result = validate_dataset(self.database, catalog() if description is None else description)
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["ok"], not result["issues"])
        return result

    def issue(self, code, report=None):
        matches = [entry for entry in (self.report() if report is None else report)["issues"]
                   if entry["code"] == code]
        self.assertEqual(len(matches), 1, matches)
        return matches[0]

    def invoke_cli(self, database, description, code):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "validate_dataset.py"), str(database), str(description)],
            capture_output=True, text=True, encoding="utf-8", timeout=5,
        )
        self.assertEqual(result.returncode, code, result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        record = json.loads(result.stdout)
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["ok"], code == 0)
        return record

    def test_valid_duplicate_sources_and_nullable_attributes(self):
        before = list(self.database.iterdump())
        self.assertFalse(self.database.in_transaction)
        self.assertTrue(self.report()["ok"])
        self.assertFalse(self.database.in_transaction)
        self.assertEqual(list(self.database.iterdump()), before)

    def test_empty_reference_source_is_valid(self):
        self.database.execute("DELETE FROM CHILD")
        self.database.commit()
        self.assertTrue(self.report()["ok"])

    def test_actual_storage_types_are_checked(self):
        self.database.execute("UPDATE PARENT SET id = 'not an integer' WHERE id = 2")
        self.database.execute("UPDATE PARENT SET name = ? WHERE id = 1", (sqlite3.Binary(b"blob"),))
        issues = [entry for entry in self.report()["issues"] if entry["code"] == "invalid_type"]
        self.assertEqual({(entry["table"], entry["column"], entry["count"]) for entry in issues},
                         {("PARENT", "id", 1), ("PARENT", "name", 1)})

    def test_unique_keys_cannot_be_null(self):
        self.database.execute("UPDATE PARENT SET id = NULL WHERE id = 2")
        entry = self.issue("null_unique_key")
        self.assertEqual((entry["table"], entry["column"], entry["count"]), ("PARENT", "id", 1))

    def test_duplicate_targets_and_ambiguous_source_rows(self):
        self.database.execute("INSERT INTO PARENT VALUES (1, 'duplicate')")
        result = self.report()
        self.assertEqual(self.issue("duplicate_unique_key", result)["count"], 1)
        self.assertEqual(self.issue("ambiguous_reference_target", result)["count"], 2)

    def test_null_and_orphaned_source_keys_are_distinct_failures(self):
        self.database.executemany("INSERT INTO CHILD VALUES (?, ?)", [(None, 'null'), (99, 'missing')])
        result = self.report()
        self.assertEqual(self.issue("null_reference_key", result)["count"], 1)
        self.assertEqual(self.issue("missing_reference_target", result)["count"], 1)

    def test_missing_schema_names_do_not_become_quoted_literals(self):
        description = catalog()
        description["tables"][0]["columns"][1]["name"] = "nonexistent"
        entry = self.issue("missing_column", self.report(description))
        self.assertEqual((entry["table"], entry["column"]), ("PARENT", "nonexistent"))
        description = catalog()
        description["tables"].append({"name": "ABSENT", "columns": [
            {"name": "id", "type": "integer", "unique": False}]})
        self.assertEqual(self.issue("missing_table", self.report(description))["table"], "ABSENT")

    def test_physical_name_spelling_must_match_exactly(self):
        description = catalog()
        description["tables"][0]["name"] = "parent"
        description["references"][0]["target_table"] = "parent"
        self.assertEqual(self.issue("missing_table", self.report(description))["table"], "parent")

    def test_temporary_table_or_view_shadowing_is_rejected(self):
        for kind, definition in [("TABLE", "(fake TEXT)"), ("VIEW", "AS SELECT 1 AS fake")]:
            with self.subTest(kind=kind):
                self.database.execute(f"CREATE TEMP {kind} parent {definition}")
                entry = self.issue("shadowed_table")
                self.assertEqual((entry["table"], entry["count"]), ("PARENT", 1))
                self.database.execute(f"DROP {kind} temp.parent")

    def test_source_collation_can_make_unique_targets_ambiguous(self):
        self.database.executescript("""
            DROP TABLE CHILD;
            DROP TABLE PARENT;
            CREATE TABLE PARENT(id TEXT COLLATE BINARY, name TEXT);
            CREATE TABLE CHILD(parent_id TEXT COLLATE NOCASE, note TEXT);
            INSERT INTO PARENT VALUES ('A', 'upper'), ('a', 'lower');
            INSERT INTO CHILD VALUES ('A', 'one source row');
        """)
        description = catalog()
        for table in description["tables"]:
            table["columns"][0]["type"] = "string"
        result = self.report(description)
        self.assertEqual([entry["code"] for entry in result["issues"]], ["ambiguous_reference_target"])
        self.assertEqual(result["issues"][0]["count"], 1)

    def test_without_rowid_sources_do_not_require_a_row_identifier(self):
        self.database.executescript("""
            DROP TABLE CHILD;
            CREATE TABLE CHILD(parent_id INTEGER, note TEXT, uid INTEGER PRIMARY KEY) WITHOUT ROWID;
            INSERT INTO CHILD VALUES (1, 'first', 1), (1, 'second', 2);
        """)
        self.assertTrue(self.report()["ok"])

    def test_quoted_unicode_names_are_preserved(self):
        parent, child, key, foreign = 'parent" café', 'child\n🧠', 'id"key', 'parent.key\t'
        description = catalog()
        description["tables"][0]["name"] = parent
        description["tables"][0]["columns"][0]["name"] = key
        description["tables"][1]["name"] = child
        description["tables"][1]["columns"][0]["name"] = foreign
        description["references"] = [{"source_table": child, "feature": "friend.with.dot",
                                      "source_column": foreign, "target_table": parent, "target_column": key}]
        self.database.execute(f"CREATE TABLE {identifier(parent)}({identifier(key)} INTEGER, name TEXT)")
        self.database.execute(f"CREATE TABLE {identifier(child)}({identifier(foreign)} INTEGER, note TEXT)")
        self.database.execute(f"INSERT INTO {identifier(parent)} VALUES (1, 'one')")
        self.database.execute(f"INSERT INTO {identifier(child)} VALUES (1, NULL)")
        self.assertTrue(self.report(description)["ok"])

    def test_caller_transaction_is_preserved(self):
        self.database.execute("INSERT INTO CHILD VALUES (2, 'uncommitted')")
        changes = self.database.total_changes
        self.assertTrue(self.database.in_transaction)
        self.assertTrue(self.report()["ok"])
        self.assertTrue(self.database.in_transaction)
        self.assertEqual(self.database.total_changes, changes)
        self.assertEqual(self.database.execute("SELECT COUNT(*) FROM CHILD WHERE note = 'uncommitted'").fetchone(), (1,))
        self.database.rollback()
        self.assertEqual(self.database.execute("SELECT COUNT(*) FROM CHILD WHERE note = 'uncommitted'").fetchone(), (0,))

    def test_invalid_catalog_metadata_is_rejected(self):
        edits = [
            lambda c: c.update(version=True),
            lambda c: c.update(version=2),
            lambda c: c.update(tables=[]),
            lambda c: c["tables"][0]["columns"][0].update(type="blob"),
            lambda c: c["tables"][0]["columns"][0].update(unique=1),
            lambda c: c["tables"][0]["columns"][0].update(name="bad\0name"),
            lambda c: c["tables"][0]["columns"].append({"name": "ID", "type": "integer", "unique": True}),
            lambda c: c["references"][0].update(target_column="missing"),
            lambda c: c["tables"][0]["columns"][0].update(unique=False),
            lambda c: c["tables"][1]["columns"][0].update(type="string"),
        ]
        for edit in edits:
            description = copy.deepcopy(catalog())
            edit(description)
            with self.subTest(description=description), self.assertRaises(ValueError):
                validate_dataset(self.database, description)

    def test_compiler_arguments_use_the_same_catalog_facts(self):
        self.assertEqual(compiler_arguments(catalog()), [
            "--column", "PARENT", "id", "integer", "unique",
            "--column", "PARENT", "name", "string", "nonunique",
            "--column", "CHILD", "parent_id", "integer", "nonunique",
            "--column", "CHILD", "note", "string", "nonunique",
            "--reference", "CHILD", "parent", "parent_id", "PARENT", "id",
        ])

    def test_json_duplicate_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(catalog()), encoding="utf-8")
            self.assertEqual(load_catalog(path), catalog())
            path.write_text('{"version":1,"version":2,"tables":[],"references":[]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_catalog(path)

    def test_cli_checks_valid_and_invalid_files_without_modifying_them(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "lake # café.sqlite"
            catalog_path = Path(directory) / "catalog.json"
            catalog_path.write_text(json.dumps(catalog()), encoding="utf-8")
            saved = sqlite3.connect(database_path)
            try:
                self.database.backup(saved)
            finally:
                saved.close()
            before = database_path.read_bytes()
            self.assertEqual(self.invoke_cli(database_path, catalog_path, 0)["issues"], [])
            self.assertEqual(database_path.read_bytes(), before)

            saved = sqlite3.connect(database_path)
            try:
                saved.execute("INSERT INTO CHILD VALUES (99, 'orphan')")
                saved.commit()
            finally:
                saved.close()
            before = database_path.read_bytes()
            report = self.invoke_cli(database_path, catalog_path, 1)
            self.assertEqual(self.issue("missing_reference_target", report)["count"], 1)
            self.assertEqual(database_path.read_bytes(), before)

    def test_cli_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "missing.sqlite"
            catalog_path = Path(directory) / "catalog.json"
            catalog_path.write_text(json.dumps(catalog()), encoding="utf-8")
            report = self.invoke_cli(database_path, catalog_path, 2)
            self.assertEqual(report["error"]["stage"], "database")
            self.assertFalse(database_path.exists())

    def test_cli_malformed_catalog_is_a_catalog_error(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "missing.sqlite"
            catalog_path = Path(directory) / "catalog.json"
            catalog_path.write_text('{"version":1,"version":2}', encoding="utf-8")
            report = self.invoke_cli(database_path, catalog_path, 2)
            self.assertEqual(report["error"]["stage"], "catalog")
            self.assertFalse(database_path.exists())


if __name__ == "__main__":
    unittest.main()

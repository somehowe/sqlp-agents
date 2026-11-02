"""Offline provenance, snapshot, and independent-oracle checks for Chinook."""

import copy
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.prepare_chinook import (
    EXAMPLES, REFERENCES, SOURCE_SHA256, TABLE_COLUMNS, INTEGER_COLUMNS,
    build_workloads, copy_snapshot, load_records, make_catalog, oracle_rows, prepare,
    prepare_arm_variant, rewrite_table_bindings,
)
from tools.run_comparison import run_comparison
from tools.validate_dataset import validate_dataset


def fixture(*, foreign_keys=True):
    source = sqlite3.connect(":memory:")
    for table, columns in TABLE_COLUMNS.items():
        declarations = [f'"{col}" ' + ("INTEGER" if col in INTEGER_COLUMNS else "TEXT") +
                        (" PRIMARY KEY NOT NULL" if i == 0 else "")
                        for i, col in enumerate(columns)]
        if foreign_keys:
            declarations += [f'FOREIGN KEY ("{left}") REFERENCES "{target}" ("{right}")'
                             for owner, _, left, target, right in REFERENCES if owner == table]
        source.execute('CREATE TABLE "' + table + '" (' + ', '.join(declarations) + ')')
    data = {
        "Artist": [(1, "Alpha"), (2, "Beta")],
        "Album": [(1, "Red", 1), (2, "Blue", 2)],
        "Employee": [(1, "Jane", "A", "Agent", "Calgary", "Canada", "rep@example.org"),
                     (2, "Sam", "B", "Agent", "Boston", "USA", "other@example.org")],
        "Customer": [(1, "Jo", "C", "Paris", "France", "customer@example.org", 1),
                     (2, "Mo", "D", "Toronto", "Canada", "second@example.org", 2)],
        "Genre": [(1, "Rock"), (2, "Jazz")],
        "MediaType": [(1, "MP3"), (2, "FLAC")],
        "Track": [(1, "One", 1, 1, 1, None, 100), (2, "Two", 2, 2, 2, "Composer", 200)],
        "Invoice": [(1, 1, "2021-01-01", "Paris", "France"),
                    (2, 2, "2021-02-01", "Toronto", "Canada")],
        "InvoiceLine": [(1, 1, 1, 1), (2, 1, 1, 1), (3, 2, 2, 1)],
    }
    for table, rows in data.items():
        source.executemany('INSERT INTO "' + table + '" VALUES (' +
                           ','.join('?' for _ in rows[0]) + ')', rows)
    source.commit()
    source.execute("BEGIN")
    return source


class SnapshotTests(unittest.TestCase):
    def test_catalog_has_reviewed_total_roles_and_one_key_per_table(self):
        catalog = make_catalog()
        self.assertEqual(len(catalog["tables"]), 9)
        self.assertEqual(len(catalog["references"]), 8)
        for table in catalog["tables"]:
            self.assertEqual(sum(col["unique"] for col in table["columns"]), 1)
        with closing(fixture()) as source:
            self.assertTrue(validate_dataset(source, catalog)["ok"])

    def test_copy_preserves_explicit_record_keys_rows_duplicates_and_null_attributes(self):
        with closing(fixture()) as source, closing(sqlite3.connect(":memory:")) as target:
            catalog, counts = copy_snapshot(source, target)
            self.assertTrue(source.in_transaction)
            self.assertEqual(counts["InvoiceLine"], 3)
            self.assertEqual(target.execute("SELECT TrackId FROM InvoiceLine").fetchall(), [(1,), (1,), (2,)])
            self.assertIsNone(target.execute("SELECT Composer FROM Track WHERE TrackId=1").fetchone()[0])
            self.assertTrue(validate_dataset(target, catalog)["ok"])
            self.assertFalse(target.execute("PRAGMA foreign_key_check").fetchall())
            self.assertEqual(target.execute("PRAGMA table_info(Track)").fetchall()[2][3], 1)

    def test_copy_rejects_missing_declared_foreign_keys(self):
        with closing(fixture(foreign_keys=False)) as source, closing(sqlite3.connect(":memory:")) as target:
            with self.assertRaisesRegex(ValueError, "foreign key"):
                copy_snapshot(source, target)
            self.assertFalse(target.execute("SELECT 1 FROM sqlite_schema").fetchall())

    def test_copy_rejects_null_and_dangling_reference_without_dropping_rows(self):
        for value in (None, 999):
            with self.subTest(value=value), closing(fixture()) as source, closing(sqlite3.connect(":memory:")) as target:
                source.execute("UPDATE Track SET AlbumId=? WHERE TrackId=1", (value,))
                with self.assertRaisesRegex(ValueError, "reference assumptions"):
                    copy_snapshot(source, target)
                self.assertFalse(target.execute("SELECT 1 FROM sqlite_schema").fetchall())

    def test_copy_requires_snapshot_and_fresh_destination(self):
        with closing(fixture()) as source, closing(sqlite3.connect(":memory:")) as target:
            source.rollback()
            with self.assertRaisesRegex(ValueError, "active read snapshot"):
                copy_snapshot(source, target)
            source.execute("BEGIN")
            target.execute("CREATE TABLE existing(x)")
            with self.assertRaisesRegex(ValueError, "destination must be empty"):
                copy_snapshot(source, target)

    def test_changed_source_rejected_before_execution_or_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / "untrusted.sql"
            path.write_text("CREATE TABLE should_not_run(x);")
            with self.assertRaisesRegex(ValueError, "pinned upstream SHA256"):
                prepare(path, base / "output")
            self.assertFalse((base / "output").exists())


class OracleTests(unittest.TestCase):
    def setUp(self):
        self.connection = fixture()
        self.addCleanup(self.connection.close)
        self.records, self.indexes = load_records(self.connection)

    def plan(self, **kwargs):
        result = {"root": "InvoiceLine", "projections": ["track.Name"], "where": [], "distinct": False}
        result.update(kwargs)
        return result

    def test_row_bag_distinct_and_independent_keyed_paths(self):
        rows = oracle_rows(self.records, self.indexes, self.plan())
        self.assertEqual(Counter(map(tuple, rows)), Counter({("One",): 2, ("Two",): 1}))
        self.assertEqual(len(oracle_rows(self.records, self.indexes, self.plan(distinct=True))), 2)
        rows = oracle_rows(self.records, self.indexes, self.plan(
            projections=["track.album.artist.Name", "invoice.customer.supportRep.Country"],
            where=[["invoice.customer.Country", "France"]]))
        self.assertEqual(rows, [["Alpha", "Canada"], ["Alpha", "Canada"]])

    def test_nullable_terminal_is_preserved_and_empty_answer_is_not_error(self):
        self.assertEqual(oracle_rows(self.records, self.indexes, self.plan(
            projections=["track.Composer"], where=[["TrackId", 1]])), [[None], [None]])
        self.assertEqual(oracle_rows(self.records, self.indexes, self.plan(
            where=[["invoice.customer.supportRep.Country", "France"]])), [])

    def test_unknown_paths_rejected_even_when_root_has_no_rows(self):
        for path in ("track.missing.Name", "track.Unknown", "track..Name", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                oracle_rows({**self.records, "InvoiceLine": []}, self.indexes,
                            self.plan(projections=[path]))

    def test_predicate_types_must_match_supported_literal_profile(self):
        for where in ([["TrackId", True]], [["TrackId", 2**63]], [["track.Name", None]],
                      [["track.Name", 1]], [["TrackId", "1"]], [["TrackId"]]):
            with self.subTest(where=where), self.assertRaises(ValueError):
                oracle_rows(self.records, self.indexes, self.plan(where=where))

    def test_table_rewriter_changes_bindings_only(self):
        original = ("SELECT t.Name FROM Track t JOIN Album a ON t.AlbumId=a.AlbumId "
                    "WHERE t.Name='FROM Track JOIN Album' -- JOIN Track\n"
                    "AND a.Title='it''s FROM Album'; /* FROM Track */")
        actual = rewrite_table_bindings(original, {"Track": "ARM_Track", "Album": "ARM_Album"})
        self.assertIn('FROM "ARM_Track" t JOIN "ARM_Album" a', actual)
        self.assertIn("t.Name='FROM Track JOIN Album' -- JOIN Track", actual)
        self.assertIn("a.Title='it''s FROM Album'; /* FROM Track */", actual)
        self.assertEqual(rewrite_table_bindings('SELECT t.Name FROM "Track" t;', {"Track": "ARM_Track"}),
                         'SELECT t.Name FROM "ARM_Track" t;')
        for query in ("SELECT 1", "SELECT * FROM main.Track", "SELECT * FROM (SELECT 1)",
                      "SELECT * FROM Unknown", "SELECT * FROM"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                rewrite_table_bindings(query, {"Track": "ARM_Track"})

    def test_arm_variant_rejects_changed_column_semantics_before_output_creation(self):
        config = json.loads((EXAMPLES / "arm_mapping.json").read_text())
        config["entities"][0]["columns"][0]["name"] = "different_key"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "mapping.json"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "must preserve columns"):
                prepare_arm_variant(root / "unused", root / "output", mapping_path=path)
            self.assertFalse((root / "output").exists())

    def test_question_plan_scope_and_semantic_checks(self):
        plans = json.loads((EXAMPLES / "questions.json").read_text())
        self.assertEqual(Counter(task["scope"] for task in plans["tasks"]),
                         Counter(development=12, held_out=30))
        self.assertEqual(len({task["id"] for task in plans["tasks"]}), 42)
        self.assertEqual(len({task["question"] for task in plans["tasks"]}), 42)
        for task in plans["tasks"]:
            oracle_rows(self.records, self.indexes, task["oracle"])
        altered = copy.deepcopy(plans)
        altered["tasks"][0]["sql"] = "SELECT 999"
        with self.assertRaisesRegex(ValueError, "disagrees"):
            build_workloads(self.connection, altered)


SOURCE = ROOT / "build" / "chinook-source" / "Chinook_Sqlite.sql"


@unittest.skipUnless(SOURCE.is_file(), "Pinned upstream script not downloaded; unit checks remain offline")
class PinnedSourceIntegrationTests(unittest.TestCase):
    def test_all_42_questions_match_sql_sqlp_and_python_without_model_calls(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "prepared"
            manifest = prepare(SOURCE, base)
            self.assertEqual(sum(manifest["row_counts"].values()), 6874)
            self.assertEqual(manifest["human_answer_review"], "pending")
            self.assertEqual(manifest["model_calls"], 0)
            catalog = json.loads((base / "catalog.json").read_text())
            for scope, count in (("development", 12), ("held_out", 30)):
                workload = json.loads((base / (scope + ".json")).read_text())
                self.assertEqual(len(workload["cases"]), count)
                report = run_comparison(base / "snapshot.sqlite", catalog, workload)
                self.assertEqual(report["scope"], scope)
                self.assertTrue(report["ok"])
                self.assertTrue(all(r["correct"] for case in report["cases"] for r in case["runs"]))
            for filename, digest in manifest["files_sha256"].items():
                self.assertEqual(hashlib.sha256((base / filename).read_bytes()).hexdigest(), digest)
            with self.assertRaises(FileExistsError):
                prepare(SOURCE, base)

            arm = Path(directory) / "arm"
            arm_manifest = prepare_arm_variant(base, arm)
            self.assertEqual(arm_manifest["human_answer_review"], "pending")
            logical = json.loads((arm / "catalog.json").read_text())
            for scope in ("development", "held_out"):
                original = json.loads((base / (scope + ".json")).read_text())
                variant = json.loads((arm / (scope + ".json")).read_text())
                self.assertEqual([c["expected_rows"] for c in original["cases"]],
                                 [c["expected_rows"] for c in variant["cases"]])
                report = run_comparison(arm / "snapshot.sqlite", logical, variant)
                self.assertTrue(report["ok"])
                self.assertEqual(report["scope"], scope)
            with self.assertRaises(FileExistsError):
                prepare_arm_variant(base, arm)


if __name__ == "__main__":
    unittest.main()

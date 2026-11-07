"""Catalog-only view control: multiplicity, roles, limits, and snapshot safety."""

from collections import Counter
from copy import deepcopy
import inspect
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_join_views import prepare_join_views
from tools.run_comparison import SetupError, run_comparison
from tools.validate_dataset import load_catalog, validate_dataset


class JoinViewsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory/"source.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript((ROOT/"examples/academic/data.sql").read_text())
        self.catalog = load_catalog(ROOT/"examples/academic/catalog.json")
        self.number = 0

    def prepare(self, **options):
        self.number += 1
        output = options.pop("output_dir", self.directory/("views-"+str(self.number)))
        metadata = prepare_join_views(options.pop("database_path", self.database),
            options.pop("catalog", self.catalog), output, **options)
        return output, metadata

    def view(self, metadata, root):
        return next(view for view in metadata["views"] if view["root_table"] == root)

    def column(self, view, path):
        return next(column for column in view["columns"] if column["path"] == path)

    def test_all_roots_and_paths_are_catalog_only_and_source_is_unchanged(self):
        before = self.database.read_bytes()
        self.catalog["private_note"] = "PRIVATE_GOLD_SENTINEL"
        self.catalog["references"][0]["private_note"] = "PRIVATE_GOLD_SENTINEL"
        output, metadata = self.prepare()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(metadata["uses_workload"])
        self.assertEqual({view["root_table"] for view in metadata["views"]},
                         {table["name"] for table in self.catalog["tables"]})
        self.assertNotIn("workload", inspect.signature(prepare_join_views).parameters)
        for name in ("catalog.json", "metadata.json", "view_guide.md"):
            self.assertNotIn("PRIVATE_GOLD_SENTINEL", (output/name).read_text())
            self.assertEqual((output/name).stat().st_mode & 0o777, 0o400)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        exported = load_catalog(output/"catalog.json")
        self.assertEqual(len(exported["tables"]), 2*len(self.catalog["tables"]))
        self.assertEqual(exported["references"], [
            {key: reference[key] for key in ("source_table", "feature", "source_column", "target_table", "target_column")}
            for reference in self.catalog["references"]])
        with sqlite3.connect(output/"snapshot.sqlite") as connection:
            self.assertTrue(validate_dataset(connection, exported)["ok"])

    def test_keyless_root_duplicates_and_separate_roles_are_preserved(self):
        output, metadata = self.prepare(depth=2)
        view = self.view(metadata, "WROTE")
        name = self.column(view, ["author", "name"])["name"]
        affiliation = self.column(view, ["author", "university", "country"])["name"]
        publisher = self.column(view, ["paper", "publisher", "country"])["name"]
        with sqlite3.connect(output/"snapshot.sqlite") as connection:
            rows = connection.execute('SELECT "'+name+'", "'+affiliation+'", "'+publisher+'" FROM "'+view["name"]+'"').fetchall()
        self.assertEqual(len(rows), 5)
        self.assertEqual(Counter(rows)[("Alice", "CA", "DK")], 2)
        self.assertFalse(any(column["unique"] for column in view["columns"]))

    def test_only_direct_root_keys_keep_unique_flags(self):
        _, metadata = self.prepare()
        view = self.view(metadata, "AUTHOR")
        self.assertTrue(self.column(view, ["aid"])["unique"])
        self.assertFalse(self.column(view, ["mentor", "aid"])["unique"])
        self.assertFalse(self.column(view, ["university", "uid"])["unique"])

    def test_cycles_are_unrolled_only_to_selected_depth(self):
        _, metadata = self.prepare(depth=3)
        view = self.view(metadata, "AUTHOR")
        paths = [column["path"] for column in view["columns"]]
        self.assertIn(["mentor", "mentor", "mentor", "aid"], paths)
        self.assertNotIn(["mentor", "mentor", "mentor", "mentor", "aid"], paths)
        self.assertTrue(all(len(prefix["roles"]) <= 3 for prefix in view["prefixes"]))
        self.assertEqual(view["join_count"], len(view["trace"]["joins"]))

    def test_flattened_view_can_be_queried_by_both_sql_and_sqlp(self):
        output, metadata = self.prepare()
        view = self.view(metadata, "WROTE")
        name = self.column(view, ["author", "name"])["name"]
        country = self.column(view, ["author", "university", "country"])["name"]
        workload = {"version": 1, "scope": "development", "cases": [{
            "id": "flat", "question": "Find author names for Canadian affiliation authorship records.",
            "sql": 'SELECT v."'+name+'" FROM "'+view["name"]+'" AS v WHERE v."'+country+'" = \'CA\';',
            "sqlp": 'SELECT v."'+name+'" FROM "'+view["name"]+'" AS v WHERE v."'+country+'" = \'CA\';',
            "column_count": 1, "expected_rows": [["Alice"], ["Alice"], ["Caro"]]}]}
        result = run_comparison(output/"snapshot.sqlite", load_catalog(output/"catalog.json"), workload)
        self.assertTrue(result["ok"])
        guide = (output/"view_guide.md").read_text()
        self.assertIn("either SQL or SQLP", guide)
        self.assertIn("without questions or answers", guide)

    def test_escaped_columns_keep_direct_and_role_paths_distinct(self):
        database = self.directory/"escaped.sqlite"
        with sqlite3.connect(database) as connection:
            connection.executescript('CREATE TABLE TARGET(id INTEGER PRIMARY KEY,b INTEGER); CREATE TABLE NODE(id INTEGER PRIMARY KEY,a__b INTEGER,a_id INTEGER NOT NULL); INSERT INTO TARGET VALUES(1,17); INSERT INTO NODE VALUES(1,42,1);')
        catalog = {"version": 1, "tables": [
            {"name": "TARGET", "columns": [{"name": "id", "type": "integer", "unique": True}, {"name": "b", "type": "integer", "unique": False}]},
            {"name": "NODE", "columns": [{"name": "id", "type": "integer", "unique": True}, {"name": "a__b", "type": "integer", "unique": False}, {"name": "a_id", "type": "integer", "unique": False}]}],
            "references": [{"source_table": "NODE", "feature": "a", "source_column": "a_id", "target_table": "TARGET", "target_column": "id"}]}
        output, metadata = self.prepare(database_path=database, catalog=catalog)
        view = self.view(metadata, "NODE")
        direct = self.column(view, ["a__b"])["name"]
        reached = self.column(view, ["a", "b"])["name"]
        self.assertNotEqual(direct.lower(), reached.lower())
        with sqlite3.connect(output/"snapshot.sqlite") as connection:
            self.assertEqual(connection.execute('SELECT "'+direct+'","'+reached+'" FROM "'+view["name"]+'"').fetchall(), [(42,17)])

    def test_caps_and_unsupported_profile_fail_without_dropping_roots(self):
        for options in ({"depth": 0}, {"depth": True}, {"depth": 4}, {"max_joins": 0},
                        {"max_columns": 2}, {"max_joins": 64}, {"max_columns": 2001}):
            with self.subTest(options=options):
                output = self.directory/("bad-"+str(len(list(self.directory.iterdir()))))
                with self.assertRaises(ValueError):
                    self.prepare(output_dir=output, **options)
                self.assertFalse(output.exists())
        catalog = deepcopy(self.catalog)
        catalog["references"][0]["feature"] = "author role"
        with self.assertRaisesRegex(ValueError, "ASCII"):
            self.prepare(catalog=catalog)

    def test_bad_source_relationship_is_rejected_before_publication(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE AUTHOR SET uni_id=999 WHERE aid=1")
        output = self.directory/"bad-data"
        before = self.database.read_bytes()
        with self.assertRaises(SetupError):
            self.prepare(output_dir=output)
        self.assertFalse(output.exists())
        self.assertEqual(before, self.database.read_bytes())

    def test_existing_output_refused_without_overwrite(self):
        output, _ = self.prepare()
        before = (output/"metadata.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "must be new"):
            self.prepare(output_dir=output)
        self.assertEqual(before, (output/"metadata.json").read_bytes())

    def test_uncheckpointed_wal_rows_are_included_in_copy(self):
        with sqlite3.connect(self.database) as keeper:
            keeper.execute("PRAGMA journal_mode=WAL")
            keeper.execute("PRAGMA wal_autocheckpoint=0")
            keeper.execute("UPDATE AUTHOR SET name='WAL author' WHERE aid=1")
            keeper.commit()
            output, metadata = self.prepare()
            view = self.view(metadata, "AUTHOR")
            with sqlite3.connect(output/"snapshot.sqlite") as connection:
                self.assertEqual(connection.execute('SELECT name FROM "'+view["name"]+'" WHERE aid=1').fetchall(), [("WAL author",)])


if __name__ == "__main__":
    unittest.main()

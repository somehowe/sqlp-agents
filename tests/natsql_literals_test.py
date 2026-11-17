"""Lossless literal transport regressions; no converter or model calls."""

from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.natsql_literals import (protect_literals, restore_literals,
                                   transport_metadata)


class NatSQLLiteralTests(unittest.TestCase):
    def test_apostrophe_regression_survives_upstream_quote_replacement(self):
        query = "SELECT t.Name FROM t WHERE t.Country = 'Côte d''Ivoire';"
        protected, mapping = protect_literals(query)
        self.assertNotIn("Côte", protected)
        converted = protected.replace("'", '"')
        self.assertEqual(restore_literals(converted, mapping), query)
        self.assertEqual(mapping["literals"][0]["value"], "Côte d'Ivoire")

    def test_values_roundtrip_through_sqlite_without_unicode_or_escape_changes(self):
        values = ["", "'", "a''b", "Côte d'Ivoire", 'she said "hi"',
                  "back\\slash\\", "first\nsecond\rthird\tlast", "😀漢字é\u0301",
                  "-- /*not comments*/", '%_O\'Neil', '"\\\'']
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        for value in values:
            query = "SELECT '" + value.replace("'", "''") + "';"
            protected, mapping = protect_literals(query)
            restored = restore_literals(protected.replace("'", '"'), mapping)
            with self.subTest(value=value):
                self.assertEqual(restored, query)
                self.assertEqual(connection.execute(restored).fetchone(), (value,))

    def test_repeated_values_keep_distinct_occurrences_and_stable_mapping(self):
        query = "SELECT 'same', 'same', 'different';"
        protected, mapping = protect_literals(query)
        self.assertEqual(len(mapping["literals"]), 3)
        self.assertEqual(len({item["marker"] for item in mapping["literals"]}), 3)
        self.assertEqual(protect_literals(query), (protected, mapping))
        self.assertEqual(restore_literals(protected, mapping), query)

    def test_comments_quoted_identifiers_and_numeric_literals_are_not_protected(self):
        query = ('SELECT "apostrophe\'column", `tick\'name`, [bracket\'name], '
                 "X'0041', -42, 12.5 FROM t -- 'comment'\r\n"
                 "WHERE v='O''Neil' /* 'also comment' */;")
        protected, mapping = protect_literals(query)
        self.assertEqual(len(mapping["literals"]), 1)
        self.assertEqual(restore_literals(protected, mapping), query)
        self.assertIn("X'0041', -42, 12.5", protected)
        self.assertIn("-- 'comment'\r\n", protected)

    def test_restore_matches_only_complete_literal_tokens(self):
        _, mapping = protect_literals("SELECT 'O''Neil';")
        marker = mapping["literals"][0]["marker"]
        sql = ("SELECT 'prefix" + marker + "suffix', \"" + marker + "\".x, "
               "x.\"" + marker + "\", `" + marker + "`, [" + marker + "] "
               "FROM \"" + marker + "\" AS \"" + marker + "\" "
               "WHERE x = \"" + marker + "\" -- '" + marker + "'\n"
               "/* \"" + marker + "\" */;")
        restored = restore_literals(sql, mapping)
        self.assertEqual(restored, sql.replace('WHERE x = "' + marker + '"', "WHERE x = 'O''Neil'"))

    def test_literal_duplicated_by_converter_is_restored_at_every_occurrence(self):
        _, mapping = protect_literals("SELECT 'same';")
        marker = mapping["literals"][0]["marker"]
        self.assertEqual(restore_literals("SELECT '" + marker + "', \"" + marker + '\";', mapping),
                         "SELECT 'same', 'same';")
        self.assertEqual(restore_literals("SELECT 1;", mapping), "SELECT 1;")

    def test_single_quoted_identifier_positions_are_preserved_as_identifiers(self):
        query = ("SELECT t.'Name' AS 'alias' FROM 'records' AS t "
                 "WHERE t.'Country' = 'Côte d''Ivoire' COLLATE 'binary';")
        protected, mapping = protect_literals(query)
        self.assertEqual(len(mapping["literals"]), 1)
        self.assertEqual(mapping["literals"][0]["value"], "Côte d'Ivoire")
        self.assertIn("t.'Name' AS 'alias' FROM 'records'", protected)
        self.assertIn("t.'Country'", protected)
        self.assertIn("COLLATE 'binary'", protected)
        self.assertEqual(restore_literals(protected, mapping), query)
        marker = mapping["literals"][0]["marker"]
        identifiers = "SELECT t.'" + marker + "' AS '" + marker + "' FROM '" + marker + "';"
        self.assertEqual(restore_literals(identifiers, mapping), identifiers)

    def test_unterminated_tokens_fail_without_repairing_the_input(self):
        for query in ("SELECT 'unclosed", "SELECT 'escaped''", 'SELECT "unclosed',
                      "SELECT `unclosed", "SELECT [unclosed", "SELECT 1 /* unclosed"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                protect_literals(query)
        _, mapping = protect_literals("SELECT 'value';")
        with self.assertRaises(ValueError):
            restore_literals("SELECT 'unclosed", mapping)

    def test_mapping_corruption_and_unknown_exact_markers_fail(self):
        _, mapping = protect_literals("SELECT 'value';")
        for field, value in (("version", 2), ("version", True), ("source_sha256", "bad"),
                             ("literals", None)):
            broken = deepcopy(mapping)
            broken[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                restore_literals("SELECT 1;", broken)
        broken = deepcopy(mapping)
        broken["literals"][0]["value"] = "different"
        with self.assertRaises(ValueError):
            restore_literals("SELECT 1;", broken)
        broken = deepcopy(mapping)
        broken["literals"].append(deepcopy(broken["literals"][0]))
        with self.assertRaises(ValueError):
            restore_literals("SELECT 1;", broken)
        unknown = "sqlp-literal-" + "0" * 64 + "-0000"
        with self.assertRaises(ValueError):
            restore_literals("SELECT '" + unknown + "';", mapping)

    def test_metadata_and_query_hashes_bind_the_actual_sources(self):
        query = "SELECT 'é';"
        _, mapping = protect_literals(query)
        self.assertEqual(mapping["source_sha256"], hashlib.sha256(query.encode()).hexdigest())
        metadata = transport_metadata()
        self.assertEqual(metadata["version"], 1)
        self.assertEqual(metadata["source_sha256"],
                         hashlib.sha256((ROOT / "tools/natsql_literals.py").read_bytes()).hexdigest())

    def test_nontext_and_invalid_unicode_are_rejected(self):
        for query in (None, 7, "SELECT '\ud800';"):
            with self.subTest(query=repr(query)), self.assertRaises(ValueError):
                protect_literals(query)


if __name__ == "__main__":
    unittest.main()

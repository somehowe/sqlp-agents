"""Public benchmark extraction, leakage, coverage and execution semantics."""

from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.public_textsql_benchmark import _matching_rows, prepare, query_features, score_case, score_generated


def ast():
    return {"select": [False, [[0, [0, [0, 1, False], None]]]],
            "from": {"table_units": [["table_unit", 0]], "conds": []},
            "where": [], "groupBy": [], "having": [], "orderBy": [], "limit": None,
            "union": None, "intersect": None, "except": None}


def case(rows, *, width=1, ordered=False):
    return {"expected_rows": rows, "column_count": width, "gold_status": "ok",
            "gold_error": None, "metadata": {"ordered_result": ordered}}


class FeatureTests(unittest.TestCase):
    def test_simple_projection_only_a_candidate_not_claimed_supported(self):
        result = query_features(ast())
        self.assertEqual(result["sqlp_profile"]["status"], "unverified_syntactic_candidate")
        self.assertFalse(result["sqlp_profile"]["evaluated_as_sqlp"])

    def test_nested_aggregation_set_operators_and_predicates_are_retained(self):
        value = ast()
        value["select"][1][0][0] = 3
        value["where"] = [[False, 8, [0, [0, 1, False], None], ast(), None], "or",
                          [True, 3, [0, [0, 1, False], None], 1, None]]
        value["groupBy"] = [[0, 1, False]]
        value["having"] = [[False, 3, [0, [3, 1, False], None], 2, None]]
        value["union"] = ast()
        value["orderBy"] = ["asc", [[0, [0, 1, False], None]]]
        value["limit"] = 1
        result = query_features(value)
        self.assertTrue({"aggregate", "subquery", "in", "or", "negation", "greater_than",
                         "group_by", "having", "union", "order_by", "limit"}.issubset(result["features"]))
        self.assertEqual(result["sql_scopes"], 3)
        self.assertEqual(result["sqlp_profile"]["status"], "outside_current_fragment")
        self.assertTrue(result["ordered_result"])

    def test_nested_order_does_not_make_outer_result_order_sensitive(self):
        nested = ast()
        nested["orderBy"] = ["desc", [[0, [0, 1, False], None]]]
        outer = ast()
        outer["where"] = [[False, 8, [0, [0, 1, False], None], nested, None]]
        result = query_features(outer)
        self.assertIn("order_by", result["features"])
        self.assertFalse(result["ordered_result"])

    def test_aggregate_used_only_in_ordering_is_retained(self):
        value = ast()
        value["groupBy"] = [[0, 1, False]]
        value["orderBy"] = ["desc", [[0, [3, 0, False], None]]]
        result = query_features(value)
        self.assertIn("aggregate", result["features"])
        self.assertIn("star_projection_or_operand", result["features"])
        self.assertEqual(result["sqlp_profile"]["status"], "outside_current_fragment")


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.executescript("CREATE TABLE sample(value REAL); INSERT INTO sample VALUES(1),(1),(2);")

    def score(self, expected, query, **options):
        return score_case(self.connection, expected, query, **options)

    def test_duplicate_counts_and_empty_width_matter(self):
        self.assertTrue(self.score(case([[1], [1], [2]]), "SELECT value FROM sample")["correct"])
        self.assertFalse(self.score(case([[1], [1], [2]]), "SELECT DISTINCT value FROM sample")["correct"])
        self.assertFalse(self.score(case([], width=2), "SELECT value FROM sample WHERE 0")["correct"])
        self.assertTrue(self.score(case([], width=1), "SELECT value FROM sample WHERE 0")["correct"])

    def test_only_ordered_gold_requires_sequence(self):
        query = "SELECT value FROM sample ORDER BY value DESC"
        self.assertTrue(self.score(case([[1], [1], [2]]), query)["correct"])
        self.assertFalse(self.score(case([[1], [1], [2]], ordered=True), query)["correct"])

    def test_numeric_equivalence_and_tolerance_but_exact_strings_nulls(self):
        self.assertTrue(self.score(case([[1]]), "SELECT 1.0")["correct"])
        self.assertTrue(self.score(case([[1 / 3]]), "SELECT 0.333333333")["correct"])
        self.assertFalse(self.score(case([[1 / 3]]), "SELECT 0.334")["correct"])
        self.assertFalse(self.score(case([["A"]]), "SELECT 'a'")["correct"])
        self.assertFalse(self.score(case([[None]]), "SELECT ''")["correct"])

    def test_numeric_bag_matching_keeps_nonnumeric_groups_and_multiplicity(self):
        expected = case([["a", 1 / 3], ["b", 0.5], ["a", 1 / 3]], width=2)
        query = "SELECT 'b',0.5 UNION ALL SELECT 'a',0.333333333 UNION ALL SELECT 'a',0.333333333"
        self.assertTrue(self.score(expected, query)["correct"])
        self.assertFalse(self.score(expected, "SELECT 'a',0.333333333 UNION ALL SELECT 'b',0.5")["correct"])

    def test_close_numeric_coordinates_can_require_cross_pairing(self):
        actual = [[0.0, 2], [5e-9, 1]]
        expected = [[0.0, 1], [5e-9, 2]]
        self.assertTrue(_matching_rows(actual, expected, False))
        self.assertFalse(_matching_rows(actual, expected, True))
        self.assertFalse(_matching_rows(actual, [[0.0, 1], [5e-9, 1]], False))

    def test_tolerance_matching_must_preserve_numeric_row_correlations(self):
        actual = [[0.0, 0.0], [5e-9, 1.0], [1.0, 2.0]]
        expected = [[0.0, 1.0], [5e-9, 2.0], [1.0, 0.0]]
        self.assertFalse(_matching_rows(actual, expected, False))
        self.assertTrue(_matching_rows(actual + actual, actual + actual, False))

    def test_failed_gold_stays_unscorable_and_read_only_write_is_rejected(self):
        bad = {"gold_status": "error", "gold_error": {"code": "text_decoding"}}
        result = self.score(bad, "SELECT 1")
        self.assertIsNone(result["correct"])
        self.assertEqual(result["status"], "unscorable_gold")
        result = self.score(case([]), "DELETE FROM sample RETURNING value")
        self.assertFalse(result["correct"])
        self.assertEqual(result["error"]["stage"], "execute")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM sample").fetchone(), (3,))

    def test_invalid_and_excessive_queries_remain_failures(self):
        self.assertEqual(self.score(case([[1]]), "SELECT missing")["error"]["code"], "sqlite")
        self.assertEqual(self.score(case([[1]]), "SELECT value FROM sample", max_rows=1)["error"]["code"], "row_limit")


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source"
        database = self.source / "database" / "sample"
        database.mkdir(parents=True)
        with closing(sqlite3.connect(database / "sample.sqlite")) as connection:
            connection.executescript("CREATE TABLE sample(value REAL, label TEXT);"
                                     "INSERT INTO sample VALUES(1.5,'secret_answer');")
        questions = [{"db_id": "sample", "question": "Which labels are recorded?", "query": "SELECT label FROM sample", "sql": ast()},
                     {"db_id": "sample", "question": "What labels appear in the table?", "query": "SELECT label FROM sample", "sql": ast()},
                     {"db_id": "sample", "question": "Which missing thing is recorded?", "query": "SELECT missing FROM sample", "sql": ast()}]
        schema = {"db_id": "sample", "table_names_original": ["sample"], "column_names_original": [[-1, "*"], [0, "value"], [0, "label"]], "foreign_keys": []}
        (self.source / "spider-dev.json").write_text(json.dumps(questions))
        (self.source / "spider-tables.json").write_text(json.dumps([schema]))

    def test_export_public_allowlist_private_answers_preserve_failed_gold_and_real_types(self):
        output = self.root / "output"
        result = prepare(output, source_root=self.source, verify_pinned=False)
        self.assertEqual((result["question_count"], result["database_count"], result["gold_executable"], result["gold_errors"]), (3, 1, 2, 1))
        self.assertEqual(result["unique_database_query_count"], 2)
        public = json.loads((output / "sample/public/tasks.json").read_text())
        self.assertTrue(all(set(item) == {"id", "question"} for item in public["cases"]))
        self.assertNotIn("secret_answer", json.dumps(public))
        self.assertNotIn("SELECT label", json.dumps(public))
        schema = json.loads((output / "sample/public/schema.json").read_text())
        self.assertEqual(schema["tables"][0]["columns"][0]["declared_type"], "REAL")
        self.assertFalse(schema["tables"][0]["columns"][0]["not_null"])
        cases = json.loads((output / "sample/private/workload.json").read_text())["cases"]
        self.assertEqual(cases[0]["expected_rows"], [["secret_answer"]])
        self.assertEqual(cases[2]["gold_status"], "error")
        self.assertIsNone(cases[2]["expected_rows"])
        self.assertTrue(score_generated(output / "sample/database.sqlite", cases[0], cases[0]["sql"])["correct"])

    def test_pinned_source_mismatch_and_existing_output_stop_before_mutation(self):
        output = self.root / "output"
        with self.assertRaisesRegex(ValueError, "Pinned source"):
            prepare(output, source_root=self.source)
        self.assertFalse(output.exists())
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(ValueError, "fresh"):
            prepare(output, source_root=self.source, verify_pinned=False)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_traversal_database_id_rejected_before_copy(self):
        questions = json.loads((self.source / "spider-dev.json").read_text())
        questions[0]["db_id"] = "../escape"
        (self.source / "spider-dev.json").write_text(json.dumps(questions))
        with self.assertRaisesRegex(ValueError, "database ID"):
            prepare(self.root / "output", source_root=self.source, verify_pinned=False)
        self.assertFalse((self.root / "output").exists())


if __name__ == "__main__":
    unittest.main()

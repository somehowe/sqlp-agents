"""Exercise manual paired query runs, answer checking, and execution limits."""

import copy
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_comparison as comparison_module
from tools.run_comparison import SetupError, run_comparison
from tools.validate_dataset import load_catalog


SQL = "SELECT name FROM AUTHOR WHERE aid = 1"
SQLP = "SELECT a.name FROM AUTHOR a WHERE a.aid = 1;"


def workload(sql=SQL, sqlp=SQLP, rows=None, width=1):
    return {"version": 1, "scope": "development", "cases": [{
        "id": "authors", "question": "Which author has identifier 1?",
        "column_count": width, "sql": sql, "sqlp": sqlp,
        "expected_rows": [["Alice"]] if rows is None else rows,
    }]}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "academic.sqlite"
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript((ROOT / "examples/academic/data.sql").read_text())
        finally:
            connection.close()
        self.catalog = load_catalog(ROOT / "examples/academic/catalog.json")

    def compare(self, description=None, **options):
        return run_comparison(self.database, self.catalog,
                              workload() if description is None else description, **options)

    def runs(self, description=None, **options):
        report = self.compare(description, **options)
        return {run["language"]: run for run in report["cases"][0]["runs"]}

    def execute(self, sql):
        with sqlite3.connect(self.database) as connection:
            return connection.execute(sql).fetchall()

    def test_real_paired_paths_keep_parameters_trace_and_manual_provenance(self):
        description = workload(
            "SELECT a.name FROM WROTE w JOIN AUTHOR a ON w.author_aid=a.aid "
            "JOIN UNIVERSITY u ON a.uni_id=u.uid JOIN PAPER p ON w.paper_pid=p.pid "
            "JOIN UNIVERSITY pub ON p.publisher_uid=pub.uid WHERE u.country='CA' "
            "AND pub.country='DK'",
            "SELECT w.author.name FROM WROTE w WHERE w.author.university.country='CA' "
            "AND w.paper.publisher.country='DK';", [["Alice"], ["Alice"]])
        report = self.compare(description)
        self.assertTrue(report["ok"])
        self.assertEqual((report["version"], report["origin"], report["scope"]),
                         (1, "manual", "development"))
        runs = {run["language"]: run for run in report["cases"][0]["runs"]}
        self.assertEqual(runs["sql"]["rows"], runs["sqlp"]["rows"])
        self.assertEqual(runs["sqlp"]["rows"], [
            [{"type": "string", "value": "Alice"}],
            [{"type": "string", "value": "Alice"}],
        ])
        self.assertEqual(len(runs["sqlp"]["parameters"]), 2)
        self.assertEqual(len(runs["sqlp"]["trace"]["joins"]), 4)
        self.assertTrue(runs["sqlp"]["trace"]["paths"])
        for run in runs.values():
            self.assertEqual(run["status"], "ok")
            self.assertTrue(run["correct"])
            self.assertIsNone(run["error"])
            self.assertIsNone(run["token_usage"])

    def test_duplicate_counts_matter_but_row_order_does_not(self):
        description = workload(
            "SELECT DISTINCT a.name FROM WROTE w JOIN AUTHOR a ON w.author_aid=a.aid "
            "WHERE a.uni_id=1",
            "SELECT w.author.name FROM WROTE w WHERE w.author.uni_id=1;",
            [["Caro"], ["Alice"], ["Alice"]])
        runs = self.runs(description)
        self.assertFalse(runs["sql"]["correct"])
        self.assertTrue(runs["sqlp"]["correct"])
        self.assertEqual(runs["sql"]["status"], "ok")

    def test_empty_answers_still_require_the_right_projection_width(self):
        runs = self.runs(workload("SELECT name, aid FROM AUTHOR WHERE 0",
                                  "SELECT a.name FROM AUTHOR a WHERE a.aid=99;", []))
        self.assertFalse(runs["sql"]["correct"])
        self.assertTrue(runs["sqlp"]["correct"])
        self.assertEqual(runs["sql"]["rows"], [])
        self.assertEqual(len(runs["sql"]["columns"]), 2)

    def test_numeric_values_compare_equally_without_coercing_text(self):
        for expression, correct in [("1.0", True), ("'1'", False)]:
            with self.subTest(expression=expression):
                runs = self.runs(workload("SELECT " + expression,
                                          "SELECT a.aid FROM AUTHOR a WHERE a.aid=1;", [[1]]))
                self.assertEqual(runs["sql"]["correct"], correct)
                self.assertTrue(runs["sqlp"]["correct"])

    def test_int64_precision_survives_json_records_and_float_comparison(self):
        self.execute("CREATE TABLE NUMBERS(value INTEGER NOT NULL)")
        self.execute("INSERT INTO NUMBERS VALUES (9007199254740993), (9223372036854775807)")
        self.catalog["tables"].append({"name": "NUMBERS", "columns": [
            {"name": "value", "type": "integer", "unique": False}]})
        description = workload("SELECT value FROM NUMBERS", "SELECT n.value FROM NUMBERS n;",
                               [[9007199254740993], [9223372036854775807]])
        report = self.compare(description)
        self.assertTrue(report["ok"])
        expected = [[{"type": "int64", "value": "9007199254740993"}],
                    [{"type": "int64", "value": "9223372036854775807"}]]
        self.assertEqual(report["cases"][0]["expected_rows"], expected)
        self.assertEqual(report["cases"][0]["runs"][0]["rows"], expected)
        description["cases"][0]["sql"] = "SELECT CAST(value AS REAL) FROM NUMBERS"
        self.assertFalse(self.runs(description)["sql"]["correct"])

    def test_query_compile_errors_do_not_discard_the_other_condition(self):
        runs = self.runs(workload(sqlp="SELECT a.missing.name FROM AUTHOR a;"))
        self.assertTrue(runs["sql"]["correct"])
        self.assertEqual(runs["sqlp"]["status"], "error")
        self.assertFalse(runs["sqlp"]["correct"])
        self.assertEqual(runs["sqlp"]["error"]["stage"], "compile")

    def test_empty_queries_are_recorded_as_query_failures(self):
        runs = self.runs(workload(sql="", sqlp=""))
        for run in runs.values():
            self.assertEqual(run["status"], "error")
            self.assertFalse(run["correct"])
            self.assertIsNone(run["rows"])
        self.assertEqual(runs["sql"]["error"]["stage"], "execute")
        self.assertEqual(runs["sqlp"]["error"]["stage"], "compile")

    def test_sql_execution_errors_do_not_discard_the_other_condition(self):
        runs = self.runs(workload(sql="SELECT nonexistent FROM AUTHOR"))
        self.assertTrue(runs["sqlp"]["correct"])
        self.assertEqual(runs["sql"]["status"], "error")
        self.assertEqual(runs["sql"]["error"]["stage"], "execute")
        self.assertEqual(runs["sql"]["error"]["code"], "sqlite")

    def test_generated_statements_cannot_modify_or_reconfigure_the_database(self):
        statements = ["DELETE FROM AUTHOR", "PRAGMA table_info(AUTHOR)",
                      "CREATE TEMP TABLE AUTHOR(name TEXT)", "ATTACH ':memory:' AS other",
                      "COMMIT", "SELECT load_extension('untrusted')"]
        before = self.execute("SELECT * FROM AUTHOR ORDER BY aid")
        for sql in statements:
            with self.subTest(sql=sql):
                runs = self.runs(workload(sql=sql))
                self.assertEqual(runs["sql"]["status"], "error")
                self.assertEqual(runs["sql"]["error"]["stage"], "execute")
                self.assertTrue(runs["sqlp"]["correct"])
                self.assertEqual(self.execute("SELECT * FROM AUTHOR ORDER BY aid"), before)

    def test_row_limit_is_an_error_not_a_scored_truncated_answer(self):
        runs = self.runs(workload(sql="SELECT name FROM AUTHOR"), max_rows=1)
        self.assertEqual(runs["sql"]["status"], "error")
        self.assertEqual(runs["sql"]["error"]["code"], "row_limit")
        self.assertFalse(runs["sql"]["correct"])
        self.assertTrue(runs["sqlp"]["correct"])

    def test_nonterminating_sql_is_stopped_and_the_next_condition_still_runs(self):
        sql = ("WITH RECURSIVE n(v) AS (VALUES(1) UNION ALL SELECT v+1 FROM n) "
               "SELECT sum(v) FROM n")
        runs = self.runs(workload(sql=sql), timeout_seconds=0.2)
        self.assertEqual(runs["sql"]["error"]["code"], "timeout")
        self.assertTrue(runs["sqlp"]["correct"])

    def test_invalid_gold_or_workload_is_a_setup_failure(self):
        descriptions = []
        for rows in [[[True]], [[float("inf")]], [[2**63]], [["Alice", "extra"]]]:
            descriptions.append(workload(rows=rows))
        descriptions.append({"version": 1, "scope": "development", "cases": []})
        duplicate = workload()
        duplicate["cases"].append(copy.deepcopy(duplicate["cases"][0]))
        descriptions.append(duplicate)
        for description in descriptions:
            with self.subTest(description=description):
                with self.assertRaises(SetupError) as failure:
                    self.compare(description)
                self.assertEqual(failure.exception.stage, "workload")

    def test_bad_catalog_missing_compiler_and_failed_preflight_abort_the_run(self):
        invalid_catalog = copy.deepcopy(self.catalog)
        invalid_catalog["tables"][0]["columns"][0]["type"] = "unknown"
        with self.assertRaises(SetupError) as failure:
            run_comparison(self.database, invalid_catalog, workload())
        self.assertEqual(failure.exception.stage, "catalog")
        with self.assertRaises(SetupError) as failure:
            run_comparison(self.database, self.catalog, workload(), self.database.parent / "absent")
        self.assertEqual(failure.exception.stage, "compiler")
        self.execute("UPDATE WROTE SET author_aid=99 WHERE author_aid=1")
        with self.assertRaises(SetupError) as failure:
            self.compare()
        self.assertEqual(failure.exception.stage, "preflight")

    def test_concurrent_commit_cannot_change_the_comparison_snapshot(self):
        self.assertEqual(self.execute("PRAGMA journal_mode=WAL"), [("wal",)])
        real_compile = comparison_module._compile

        def compile_after_external_commit(*args):
            self.execute("UPDATE AUTHOR SET name='Changed after SQL' WHERE aid=1")
            return real_compile(*args)

        with patch.object(comparison_module, "_compile", side_effect=compile_after_external_commit):
            report = self.compare()
        self.assertTrue(report["ok"])
        for run in report["cases"][0]["runs"]:
            self.assertEqual(run["rows"], [[{"type": "string", "value": "Alice"}]])
        self.assertEqual(self.execute("SELECT name FROM AUTHOR WHERE aid=1"),
                         [("Changed after SQL",)])

    def test_malformed_compiler_output_is_a_setup_failure_not_a_query_error(self):
        responses = ["not JSON", '{"version":1,"ok":true,"sql":"SELECT ?",'
                     '"trace":{"joins":[],"paths":[]},'
                     '"parameters":[{"type":"int64","value":"not an integer"}]}']
        for output in responses:
            with self.subTest(output=output):
                process = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
                with patch.object(comparison_module.subprocess, "run", return_value=process):
                    with self.assertRaises(SetupError) as failure:
                        self.compare()
                self.assertEqual(failure.exception.stage, "compiler")

    def test_invalid_compiled_sql_is_an_infrastructure_failure(self):
        compiled = {"ok": True, "sql": "SELECT nonexistent FROM AUTHOR", "parameters": [],
                    "trace": {"joins": [], "paths": []}}
        with patch.object(comparison_module, "_compile", return_value=(compiled, [])):
            with self.assertRaises(SetupError) as failure:
                self.compare()
        self.assertEqual(failure.exception.stage, "compiled_sql")

    def test_invalid_execution_limits_are_setup_failures(self):
        for options in [{"max_rows": 0}, {"max_rows": sys.maxsize},
                        {"timeout_seconds": float("inf")}, {"timeout_seconds": True}]:
            with self.subTest(options=options), self.assertRaises(SetupError) as failure:
                self.compare(**options)
            self.assertEqual(failure.exception.stage, "workload")


if __name__ == "__main__":
    unittest.main()

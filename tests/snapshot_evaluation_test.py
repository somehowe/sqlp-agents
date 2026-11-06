"""Local semantic diagnostics using dummy replay responses and real SQLite."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import evaluate_snapshots as scoring_module
from tools.batch_pilot import run_batch_pilot
from tools.evaluate_snapshots import evaluate_snapshots
from tools.run_comparison import SetupError
from tools.run_pilot import ReplayProvider, run_pilot
from tools.validate_dataset import load_catalog

SETTINGS = {"model": "offline-snapshot-model", "max_output_tokens": 512}


def response(text):
    return {"model": SETTINGS["model"], "id": "offline-response", "status": "completed",
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27}}


def authors_workload():
    return {"version": 1, "scope": "development", "cases": [{
        "id": "author-names", "question": "List author names at CA universities, one row per author; keep repeats.",
        "sql": "SELECT a.name FROM AUTHOR a JOIN UNIVERSITY u ON a.uni_id=u.uid WHERE u.country='CA';",
        "sqlp": "SELECT a.name FROM AUTHOR a WHERE a.university.country='CA';",
        "column_count": 1, "expected_rows": [["Alice"], ["Caro"]]}]}


class SnapshotEvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source_db = self.directory / "source.sqlite"
        self.alternate_db = self.directory / "alternate.sqlite"
        with sqlite3.connect(self.source_db) as connection:
            connection.executescript((ROOT / "examples/academic/data.sql").read_text())
        self.copy_database()
        self.catalog = load_catalog(ROOT / "examples/academic/catalog.json")
        self.guides = {language: (ROOT / "prompts" / (language + ".md")).read_text()
                       for language in ("sql", "sqlp")}
        self.journal_number = 0

    def copy_database(self):
        with sqlite3.connect(self.source_db) as source, sqlite3.connect(self.alternate_db) as target:
            source.backup(target)

    def native_report(self, workload=None, queries=None):
        workload = workload or authors_workload()
        plan = run_pilot(self.source_db, self.catalog, workload, guides=self.guides, settings=SETTINGS)
        by_id = {case["id"]: case for case in workload["cases"]}
        entries = [{"request_sha256": request["request_sha256"], "response": response(
            queries[request["language"]] if queries else by_id[request["case_id"]][request["language"]])}
            for request in plan["requests"]]
        self.journal_number += 1
        return run_pilot(self.source_db, self.catalog, workload, guides=self.guides, settings=SETTINGS,
                         origin="replay", provider=ReplayProvider(entries), max_requests=len(entries),
                         journal_path=self.directory / ("source-" + str(self.journal_number) + ".jsonl"))

    def evaluate(self, report, workload=None, **options):
        return evaluate_snapshots(report, self.catalog, {"contrast": {
            "database_path": self.alternate_db, "workload": workload or authors_workload()}}, **options)

    def test_same_query_is_robust_when_valid_alternate_data_preserve_answers(self):
        report = self.native_report()
        result = self.evaluate(report)
        self.assertEqual((result["kind"], result["origin"]), ("cross_snapshot_scoring", "local_rescoring"))
        self.assertTrue(result["completed"] and result["ok"])
        for language in ("sql", "sqlp"):
            self.assertEqual(result["summary"][language]["robust_on_tested_snapshots"], 1)
        for run in result["cases"][0]["runs"]:
            self.assertTrue(run["source_correct"] and run["robust_on_tested_snapshots"])
            self.assertEqual(run["source_generation"]["response_id"], "offline-response")
            self.assertEqual(len(run["source_generation"]["request_sha256"]), 64)
            self.assertIsNone(run["snapshots"]["contrast"]["token_usage"])
            self.assertNotIn("usage", run["source_generation"])

    def test_duplicate_value_counterexample_exposes_accidental_distinct_success(self):
        source = authors_workload()
        queries = {key: source["cases"][0][key].replace("SELECT ", "SELECT DISTINCT ", 1)
                   for key in ("sql", "sqlp")}
        report = self.native_report(source, queries)
        self.assertTrue(report["ok"])
        with sqlite3.connect(self.alternate_db) as connection:
            connection.execute("INSERT INTO AUTHOR VALUES (4,'Alice',1,2)")
        alternate = deepcopy(source)
        alternate["cases"][0]["expected_rows"].append(["Alice"])
        result = self.evaluate(report, alternate)
        self.assertFalse(result["ok"])
        for run in result["cases"][0]["runs"]:
            self.assertTrue(run["source_correct"])
            self.assertFalse(run["snapshots"]["contrast"]["correct"])
            self.assertFalse(run["robust_on_tested_snapshots"])
            self.assertEqual(len(run["snapshots"]["contrast"]["rows"]), 2)
            self.assertEqual(len(run["snapshots"]["contrast"]["expected_rows"]), 3)

    def test_role_collision_matches_primary_but_fails_contrast(self):
        with sqlite3.connect(self.source_db) as connection:
            connection.execute("UPDATE AUTHOR SET uni_id=1")
            connection.execute("UPDATE PAPER SET publisher_uid=1")
        self.copy_database()
        workload = {"version": 1, "scope": "development", "cases": [{
            "id": "role", "question": "List author names on authorships at CA universities, keeping repeats.",
            "sql": "SELECT a.name FROM WROTE w JOIN AUTHOR a ON w.author_aid=a.aid JOIN UNIVERSITY u ON a.uni_id=u.uid WHERE u.country='CA';",
            "sqlp": "SELECT w.author.name FROM WROTE w WHERE w.author.university.country='CA';",
            "column_count": 1, "expected_rows": [["Alice"], ["Alice"], ["Bob"], ["Caro"], ["Bob"]]}]}
        wrong = {
            "sql": "SELECT a.name FROM WROTE w JOIN AUTHOR a ON w.author_aid=a.aid JOIN PAPER p ON w.paper_pid=p.pid JOIN UNIVERSITY u ON p.publisher_uid=u.uid WHERE u.country='CA';",
            "sqlp": "SELECT w.author.name FROM WROTE w WHERE w.paper.publisher.country='CA';"}
        report = self.native_report(workload, wrong)
        self.assertTrue(report["ok"])
        with sqlite3.connect(self.alternate_db) as connection:
            connection.execute("UPDATE PAPER SET publisher_uid=2 WHERE pid=10")
        result = self.evaluate(report, workload)
        self.assertFalse(result["ok"])
        self.assertTrue(all(run["source_correct"] for run in result["cases"][0]["runs"]))
        self.assertTrue(all(not run["robust_on_tested_snapshots"] for run in result["cases"][0]["runs"]))

    def test_original_compile_failure_is_not_retried(self):
        queries = {"sql": authors_workload()["cases"][0]["sql"], "sqlp": "SELECT invalid SQLP;"}
        report = self.native_report(queries=queries)
        self.assertFalse(report["ok"])
        original = deepcopy(report)
        with patch.object(scoring_module, "_condition", wraps=scoring_module._condition) as scorer:
            result = self.evaluate(report)
        self.assertEqual(scorer.call_count, 1)
        run = next(run for run in result["cases"][0]["runs"] if run["language"] == "sqlp")
        self.assertEqual(run["snapshots"]["contrast"]["status"], "skipped")
        self.assertFalse(run["robust_on_tested_snapshots"])
        self.assertEqual(report, original)

    def test_wrong_executable_source_is_rescored_but_never_promoted_to_robust(self):
        workload = {"version": 1, "scope": "development", "cases": [{
            "id": "author", "question": "Name author identifier1.", "column_count": 1,
            "sql": "SELECT a.name FROM AUTHOR a WHERE a.aid=1;",
            "sqlp": "SELECT a.name FROM AUTHOR a WHERE a.aid=1;", "expected_rows": [["Alice"]]}]}
        wrong = {key: "SELECT a.name FROM AUTHOR a WHERE a.aid=2;" for key in ("sql", "sqlp")}
        report = self.native_report(workload, wrong)
        with sqlite3.connect(self.alternate_db) as connection:
            connection.execute("UPDATE AUTHOR SET name='Bob' WHERE aid=1")
        alternate = deepcopy(workload)
        alternate["cases"][0]["expected_rows"] = [["Bob"]]
        result = self.evaluate(report, alternate)
        for run in result["cases"][0]["runs"]:
            self.assertFalse(run["source_correct"])
            self.assertTrue(run["snapshots"]["contrast"]["correct"])
            self.assertFalse(run["robust_on_tested_snapshots"])

    def test_batch_missing_output_is_preserved_and_batch_usage_is_not_copied(self):
        workload = authors_workload()
        second = deepcopy(workload["cases"][0])
        second["id"] = "second"
        second["question"] += " This is a second independently answered task."
        workload["cases"].append(second)
        plan = run_batch_pilot(self.source_db, self.catalog, workload, guides=self.guides,
                               settings=SETTINGS, batch_size=4)
        entries = [{"request_sha256": request["request_sha256"], "response": response(json.dumps([
            {"id": workload["cases"][0]["id"], "query": workload["cases"][0][request["language"]]}]))}
            for request in plan["requests"]]
        report = run_batch_pilot(self.source_db, self.catalog, workload, guides=self.guides,
                                 settings=SETTINGS, batch_size=4, origin="replay",
                                 provider=ReplayProvider(entries), max_requests=2,
                                 journal_path=self.directory / "batch-source.jsonl")
        with patch.object(scoring_module, "_condition", wraps=scoring_module._condition) as scorer:
            result = self.evaluate(report, workload)
        self.assertEqual(scorer.call_count, 2)
        second = next(case for case in result["cases"] if case["id"] == "second")
        for run in second["runs"]:
            self.assertEqual(run["query"], "")
            self.assertEqual(run["snapshots"]["contrast"]["status"], "skipped")
            self.assertNotIn("usage", run["source_generation"])
        for language in ("sql", "sqlp"):
            self.assertEqual(result["summary"][language]["skipped_original_failures"], 1)

    def test_changed_source_batch_query_is_rejected(self):
        workload = authors_workload()
        plan = run_batch_pilot(self.source_db, self.catalog, workload, guides=self.guides, settings=SETTINGS)
        entries = [{"request_sha256": request["request_sha256"], "response": response(json.dumps([
            {"id": workload["cases"][0]["id"], "query": workload["cases"][0][request["language"]]}]))}
            for request in plan["requests"]]
        report = run_batch_pilot(self.source_db, self.catalog, workload, guides=self.guides, settings=SETTINGS,
                                 origin="replay", provider=ReplayProvider(entries), max_requests=2,
                                 journal_path=self.directory / "changed-batch.jsonl")
        report["cases"][0]["runs"][0]["query"] = "SELECT 1;"
        with self.assertRaisesRegex(SetupError, "differs from batch output"):
            self.evaluate(report)

    def test_misaligned_ids_questions_widths_catalog_and_incomplete_source_rejected(self):
        report = self.native_report()
        for change in ("missing", "extra", "question", "width", "duplicate"):
            alternate = authors_workload()
            if change == "missing":
                alternate["cases"][0]["id"] = "unknown"
            elif change == "extra":
                extra = deepcopy(alternate["cases"][0]); extra["id"] = "extra"
                alternate["cases"].append(extra)
            elif change == "duplicate":
                alternate["cases"].append(deepcopy(alternate["cases"][0]))
            elif change == "question":
                alternate["cases"][0]["question"] += " Changed."
            else:
                alternate["cases"][0].update(column_count=2, expected_rows=[["Alice", "Caro"]])
            with self.subTest(change=change), self.assertRaises(SetupError):
                self.evaluate(report, alternate)
        changed_catalog = deepcopy(self.catalog)
        changed_catalog["references"][0]["feature"] = "different_role"
        with self.assertRaisesRegex(SetupError, "catalog facts differ"):
            evaluate_snapshots(report, changed_catalog, {"contrast": {
                "database_path": self.alternate_db, "workload": authors_workload()}})
        report["completed"] = False
        with self.assertRaisesRegex(SetupError, "completed"):
            self.evaluate(report)

    def test_source_files_and_input_objects_are_unchanged(self):
        report, workload = self.native_report(), authors_workload()
        before = deepcopy([report, self.catalog, workload])
        files = [self.source_db, self.alternate_db]
        hashes = [path.read_bytes() for path in files]
        result = self.evaluate(report, workload)
        self.assertTrue(result["ok"])
        self.assertEqual([report, self.catalog, workload], before)
        self.assertEqual([path.read_bytes() for path in files], hashes)
        self.assertTrue(result["snapshots"]["contrast"]["preflight"]["ok"])

    def test_changed_original_question_cannot_be_rebound_to_existing_model_response(self):
        report = self.native_report()
        report["cases"][0]["question"] += " Changed after generation."
        alternate = authors_workload()
        alternate["cases"][0]["question"] = report["cases"][0]["question"]
        with self.assertRaisesRegex(SetupError, "Source request question differs"):
            self.evaluate(report, alternate)

    def test_changed_compiler_and_invalid_alternate_data_are_setup_failures(self):
        report = self.native_report()
        changed = self.directory / "changed-sqlpc"
        shutil.copyfile(ROOT / "build/sqlpc", changed)
        with changed.open("ab") as output:
            output.write(b"changed-build")
        changed.chmod(0o700)
        with self.assertRaisesRegex(SetupError, "original compiler"):
            self.evaluate(report, compiler_path=changed)
        with sqlite3.connect(self.alternate_db) as connection:
            connection.execute("UPDATE AUTHOR SET uni_id=999 WHERE aid=1")
        with self.assertRaisesRegex(SetupError, "failed catalog checks"):
            self.evaluate(report)


if __name__ == "__main__":
    unittest.main()

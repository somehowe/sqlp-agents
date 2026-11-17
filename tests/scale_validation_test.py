"""Saved-response integrity and contrast rescoring checks; no model calls."""

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_scale_study as runner
from tools.validate_scale_study import validate


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class ScaleValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = self.root / "ledger"
        write(self.ledger / "budget.json", {"calls": [], "authorized_micro_usd": 100000000,
                                          "prior_upper_micro_usd": 1000000})
        self.source = self.root / "source"
        domain = self.source / "items"
        domain.mkdir(parents=True)
        catalog = {"version": 1, "tables": [{"name": "Item", "columns": [
            {"name": "Id", "type": "integer", "unique": True},
            {"name": "Name", "type": "string", "unique": False}]}], "references": []}
        write(domain / "catalog.json", catalog)
        for folder, name in ((domain, "Original"), (domain / "contrast-1", "Different")):
            folder.mkdir(exist_ok=True)
            with sqlite3.connect(folder / "database.sqlite") as connection:
                connection.execute("CREATE TABLE Item(Id INTEGER PRIMARY KEY NOT NULL, Name TEXT NOT NULL)")
                connection.execute("INSERT INTO Item VALUES(1,?)", (name,))
            case = {"id": "item-1", "question": "Return the stored Name field of item1, keeping duplicates.",
                    "sql": "SELECT Name FROM Item WHERE Id=1;", "sqlp": "SELECT r.Name FROM Item AS r WHERE r.Id=1;",
                    "column_count": 1, "expected_rows": [[name]], "design": {"family": "direct"}}
            write(folder / "workload.json", {"version": 1, "scope": "development", "cases": [case]})
        write(self.source / "manifest.json", {"datasets": [{"id": "items", "directory": "items", "snapshots": [
            {"id": "primary", "database": "items/database.sqlite", "workload": "items/workload.json"},
            {"id": "contrast-1", "database": "items/contrast-1/database.sqlite", "workload": "items/contrast-1/workload.json"}]}]})
        self.study = self.root / "study"
        runner.prepare(self.study, supported=self.source, ledger=self.ledger)
        self.calls = 0

    def provider(self, payload):
        self.calls += 1
        query = ("SELECT r.Name FROM Item AS r WHERE r.Id=1;" if "SQLP" in payload["instructions"]
                 else "SELECT Name FROM Item WHERE Id=1;")
        return {"id": "response-" + str(self.calls), "model": "gpt-6-luna", "status": "completed",
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "output": [{"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": json.dumps([{"id": "item-1", "query": query}])}]}]}

    def run_fixture(self, provider=None):
        result = runner.run(self.study, provider=provider or self.provider, ledger=self.ledger, workers=1)
        self.assertTrue(result["completed"])

    def test_binds_raw_requests_and_counts_whole_response_usage_once(self):
        self.run_fixture()
        result = validate(self.study, require_complete=True)
        self.assertEqual(result["generated_outputs"], 2)
        self.assertEqual(result["completed_calls"], 2)
        self.assertEqual(result["reexecuted_primary_outputs"], 2)
        self.assertEqual(sum(v["total_tokens"] for v in result["token_usage_by_language"].values()), 30)
        for value in result["contrast_rescoring"].values():
            self.assertEqual(value["attempts"], 1)
            self.assertEqual(value["correct"], 1)

    def test_incomplete_task_coverage_is_explicit(self):
        self.assertFalse(validate(self.study)["complete"])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            validate(self.study, require_complete=True)

    def test_changed_frozen_data_is_rejected(self):
        path = self.study / "inputs/supported/items/catalog.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "Frozen input changed"):
            validate(self.study)

    def test_changed_scored_query_is_rejected(self):
        self.run_fixture()
        path = self.study / "runs/request-00000/report.json"
        data = json.loads(path.read_text())
        data["cases"][0]["query"] = "SELECT 'fabricated';"
        write(path, data)
        with self.assertRaisesRegex(ValueError, "Scored query differs"):
            validate(self.study)

    def test_wrong_saved_correctness_is_rejected_by_reexecution(self):
        self.run_fixture()
        path = self.study / "runs/request-00000/report.json"
        data = json.loads(path.read_text())
        data["cases"][0]["correct"] = False
        write(path, data)
        with self.assertRaisesRegex(ValueError, "Reexecution changed primary score"):
            validate(self.study)

    def test_unknown_usage_stays_unknown(self):
        def unknown(payload):
            response = self.provider(payload)
            response["usage"] = None
            return response
        self.run_fixture(unknown)
        result = validate(self.study)
        self.assertEqual(result["token_usage_by_language"], {"supported/sql": None, "supported/sqlp": None})
        self.assertEqual(sum(result["unknown_usage_calls"].values()), 2)

    def test_generation_failures_remain_in_contrast_denominator(self):
        def malformed(payload):
            response = self.provider(payload)
            response["output"][0]["content"][0]["text"] = "not json"
            return response
        self.run_fixture(malformed)
        result = validate(self.study)
        for value in result["contrast_rescoring"].values():
            self.assertEqual(value["attempts"], 1)
            self.assertEqual(value["correct"], 0)
            self.assertEqual(value["generation_failures"], 1)

    def test_public_reexecution_uses_its_larger_declared_row_cap(self):
        source = self.root / "public-source"
        folder = source / "big"
        folder.mkdir(parents=True)
        with sqlite3.connect(folder / "database.sqlite") as connection:
            connection.execute("CREATE TABLE Big(Name TEXT NOT NULL)")
            connection.executemany("INSERT INTO Big VALUES(?)", [("Original",)] * 10001)
        case = {"id": "public-1", "question": "Return one original name.",
                "sql": "SELECT Name FROM Big LIMIT 1;", "expected_rows": [["Original"]],
                "column_count": 1, "gold_status": "ok", "gold_error": None,
                "metadata": {"ordered_result": False}}
        write(folder / "workload.json", {"version": 1, "scope": "development", "cases": [case]})
        write(folder / "schema.json", {"tables": [{"name": "Big", "columns": [{"name": "Name"}],
                                                    "foreign_keys": []}], "published_foreign_keys": []})
        write(folder / "tasks.json", {"cases": [{"id": "public-1", "question": case["question"]}]})
        write(source / "manifest.json", {"limits": {"max_rows": 100000}, "datasets": [
            {"id": "big", "directory": "big", "database": "big/database.sqlite", "schema": "big/schema.json",
             "tasks": "big/tasks.json", "workload": "big/workload.json"}]})
        study = self.root / "public-study"
        runner.prepare(study, public=source, ledger=self.ledger)
        def many_rows(payload):
            response = self.provider(payload)
            response["output"][0]["content"][0]["text"] = json.dumps([
                {"id": "public-1", "query": "SELECT Name FROM Big;"}])
            return response
        result = runner.run(study, provider=many_rows, ledger=self.ledger, workers=1)
        self.assertTrue(result["completed"])
        report = json.loads((study / "runs/request-00000/report.json").read_text())
        self.assertEqual(report["cases"][0]["status"], "ok")
        self.assertFalse(report["cases"][0]["correct"])
        audited = validate(study, require_complete=True)
        self.assertEqual(audited["reexecuted_primary_outputs"], 1)


if __name__ == "__main__":
    unittest.main()

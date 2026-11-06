"""Optional NatSQL model-condition plumbing, using offline responses only."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import natsql_pilot as pilot
from tools.run_comparison import SetupError
from tools.run_pilot import ReplayProvider, digest
from tools.validate_dataset import load_catalog

NATSQL = "select AUTHOR.name from AUTHOR where AUTHOR.aid = 1;"
SQL = "SELECT name FROM AUTHOR WHERE aid=1;"
SETTINGS = {"model": "offline-fixture-model", "max_output_tokens": 128}


def response(text=NATSQL, *, model="offline-fixture-model", status="completed", usage=True):
    return {"model": model, "id": "offline-natsql-fixture", "status": status,
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27} if usage else None}


class NatSQLPilotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / "source.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript((ROOT/"examples/academic/data.sql").read_text())
        self.catalog = load_catalog(ROOT/"examples/academic/catalog.json")
        self.guide = (ROOT/"prompts/natsql.md").read_text()
        self.workload = {"version": 1, "scope": "development", "cases": [{
            "id": "dev-author", "question": "Which author has identifier 1?",
            "sql": SQL, "sqlp": "SELECT a.name FROM AUTHOR a WHERE a.aid=1;",
            "column_count": 1, "expected_rows": [["Alice"]]}]}

    def converted(self, query, database, catalog, **options):
        return {"version": 1, "ok": True, "backend": "natsql", "sql": SQL,
                "environment": {"python": "offline", "nltk": "offline"},
                "compiler_options": {"use_from_info": False, "remove_values": False, "sq": None}}

    def execute(self, provider=None, converter=None, **options):
        options.setdefault("origin", "live")
        options.setdefault("journal_path", self.directory/"journal.jsonl")
        options.setdefault("max_requests", len(self.workload["cases"]))
        with patch.object(pilot.natsql_adapter, "convert", side_effect=converter or self.converted):
            return pilot.run_natsql_pilot(self.database, self.catalog, self.workload,
                guide=self.guide, settings=SETTINGS, provider=provider or (lambda payload: response()),
                converter_options={"upstream": self.directory}, **options)

    def events(self):
        return [json.loads(line) for line in (self.directory/"journal.jsonl").read_text().splitlines()]

    def test_plan_contains_only_public_facts_with_true_language_and_no_converter_call(self):
        self.catalog["private_note"] = "GOLD_SECRET_SENTINEL"
        self.workload["cases"][0]["private_note"] = "GOLD_SECRET_SENTINEL"
        with patch.object(pilot.natsql_adapter, "convert") as converter:
            plan = pilot.run_natsql_pilot(self.database, self.catalog, self.workload,
                                          guide=self.guide, settings=SETTINGS)
        converter.assert_not_called()
        request = plan["requests"][0]
        self.assertEqual(request["language"], "natsql")
        self.assertEqual(request["request_sha256"], digest(request["payload"]))
        payload = json.dumps(request["payload"])
        self.assertNotIn("GOLD_SECRET_SENTINEL", payload)
        self.assertNotIn("expected_rows", payload)
        self.assertNotIn(SQL, payload)
        self.assertIs(request["payload"]["store"], False)
        self.assertEqual(request["payload"]["tools"], [])
        self.assertEqual(plan["cases"], [])

    def test_generated_source_converted_sql_and_usage_are_separate(self):
        report = self.execute()
        run = report["cases"][0]["runs"][0]
        self.assertTrue(report["completed"] and report["ok"])
        self.assertEqual(run["language"], "natsql")
        self.assertEqual(run["query"], NATSQL)
        self.assertEqual(run["sql"], SQL)
        self.assertEqual(report["summary"]["natsql"]["token_usage"]["total_tokens"], 27)
        kinds = [event["event"] for event in self.events()]
        self.assertLess(kinds.index("response_received"), kinds.index("query_converted"))
        self.assertLess(kinds.index("query_converted"), kinds.index("run_scored"))
        self.assertEqual((self.directory/"journal.jsonl").stat().st_mode & 0o777, 0o600)
        self.assertIn("adapter_sha256", report["manifest"])
        self.assertNotIn("compiler_sha256", report["manifest"])

    def test_converter_rejection_remains_scored_attempt_with_usage(self):
        def convert(query, *arguments, **options):
            if query == NATSQL:
                return {"version": 1, "ok": False, "backend": "natsql",
                        "error": {"kind": "conversion_rejected", "message": "not executable"}}
            return self.converted(query, *arguments, **options)
        report = self.execute(converter=convert)
        run = report["cases"][0]["runs"][0]
        self.assertTrue(report["completed"])
        self.assertFalse(report["ok"])
        self.assertEqual(run["error"], {"stage": "convert", "code": "conversion_rejected"})
        self.assertEqual(run["token_usage"]["total_tokens"], 27)
        self.assertEqual(report["summary"]["natsql"]["attempts"], 1)

    def test_query_wrong_answer_and_execution_failure_are_retained(self):
        for sql in ("SELECT name FROM AUTHOR WHERE aid=999;", "SELECT missing FROM AUTHOR;"):
            with self.subTest(sql=sql):
                journal = self.directory/("journal-"+str(len(sql))+".jsonl")
                def convert(query, *arguments, **options):
                    result = self.converted(query, *arguments, **options)
                    if query == NATSQL:
                        result["sql"] = sql
                    return result
                report = self.execute(converter=convert, journal_path=journal)
                self.assertTrue(report["completed"])
                self.assertFalse(report["cases"][0]["runs"][0]["correct"])

    def test_missing_usage_stays_unknown(self):
        report = self.execute(lambda payload: response(usage=False))
        self.assertIsNone(report["summary"]["natsql"]["token_usage"])
        self.assertEqual(report["summary"]["natsql"]["usage_known_attempts"], 0)

    def test_incomplete_empty_and_nul_generations_are_failures_not_setup_errors(self):
        cases = ((response(status="incomplete"), "generation"), (response(text=""), "generation"),
                 (response(text="bad\0query"), "convert"))
        for index, (raw, stage) in enumerate(cases):
            with self.subTest(stage=stage, index=index):
                report = self.execute(lambda payload: raw, journal_path=self.directory/(f"j{index}.jsonl"))
                run = report["cases"][0]["runs"][0]
                self.assertTrue(report["completed"])
                self.assertFalse(run["correct"])
                self.assertEqual(run["error"]["stage"], stage)

    def test_setup_failure_happens_before_provider(self):
        provider = Mock()
        def reject(*arguments, **options):
            return {"ok": False, "error": {"kind": "dependency_setup", "message": "not installed"}}
        with self.assertRaisesRegex(SetupError, "not installed"):
            self.execute(provider, converter=reject)
        provider.assert_not_called()
        self.assertFalse((self.directory/"journal.jsonl").exists())

    def test_insufficient_cap_or_existing_journal_prevents_provider(self):
        provider = Mock()
        with self.assertRaises(SetupError):
            self.execute(provider, max_requests=0)
        provider.assert_not_called()
        (self.directory/"journal.jsonl").write_text("prior run")
        with self.assertRaises(SetupError):
            self.execute(provider)
        provider.assert_not_called()
        self.assertEqual((self.directory/"journal.jsonl").read_text(), "prior run")

    def test_frozen_snapshot_survives_provider_mutation_of_original(self):
        def provider(payload):
            with sqlite3.connect(self.database) as changed:
                changed.execute("UPDATE AUTHOR SET name='Changed' WHERE aid=1")
            return response()
        report = self.execute(provider)
        self.assertTrue(report["ok"])
        self.assertEqual(report["cases"][0]["runs"][0]["rows"], [[{"type": "string", "value": "Alice"}]])

    def test_replay_is_bound_to_exact_nat_request_and_labeled_separately(self):
        plan = pilot.plan_natsql(self.catalog, self.workload, guide=self.guide, settings=SETTINGS)
        provider = ReplayProvider([{"request_sha256": plan["requests"][0]["request_sha256"],
                                    "response": response()}])
        report = self.execute(provider, origin="replay")
        self.assertEqual(report["origin"], "replay")
        self.assertTrue(report["ok"])


@unittest.skipUnless(os.environ.get("SQLP_NATSQL_UPSTREAM") and os.environ.get("SQLP_NATSQL_PYTHON")
                     and os.environ.get("SQLP_NATSQL_DATA"), "External pinned NatSQL environment not configured")
class ExternalNatSQLPilotTests(unittest.TestCase):
    def test_complete_official_converter_scores_offline_provider_output(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)/"data.sqlite"
            with sqlite3.connect(database) as connection:
                connection.executescript((ROOT/"examples/academic/data.sql").read_text())
            workload = {"version": 1, "scope": "development", "cases": [{
                "id": "a", "question": "Which author has identifier 1?", "sql": SQL,
                "sqlp": "SELECT a.name FROM AUTHOR a WHERE a.aid=1;", "column_count": 1,
                "expected_rows": [["Alice"]]}]}
            report = pilot.run_natsql_pilot(database, load_catalog(ROOT/"examples/academic/catalog.json"),
                workload, guide=(ROOT/"prompts/natsql.md").read_text(), settings=SETTINGS,
                origin="live", provider=lambda payload: response(), max_requests=1,
                journal_path=Path(directory)/"journal.jsonl", converter_options={
                    "upstream": os.environ["SQLP_NATSQL_UPSTREAM"], "python": os.environ["SQLP_NATSQL_PYTHON"],
                    "nltk_data": os.environ["SQLP_NATSQL_DATA"]})
            self.assertTrue(report["ok"])
            self.assertEqual(report["cases"][0]["runs"][0]["language"], "natsql")


if __name__ == "__main__":
    unittest.main()

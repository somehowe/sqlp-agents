"""Batch accounting, strict extraction, and snapshot checks without API access."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import batch_pilot as batch_module
from tools.batch_pilot import build_batch_request, parse_batch_queries, run_batch_pilot
from tools.model_provider import ProviderError
from tools.run_comparison import SetupError
from tools.run_pilot import ReplayProvider, digest
from tools.validate_dataset import load_catalog

SETTINGS = {"model": "offline-batch-model", "max_output_tokens": 512,
            "reasoning": {"effort": "high"}}
SQL = "SELECT a.name FROM AUTHOR a WHERE a.aid = 1;"
SQLP = SQL


def workload(count=2):
    return {"version": 1, "scope": "development", "cases": [
        {"id": "author-" + str(index), "question": "Name author identifier 1, task " + str(index),
         "sql": SQL, "sqlp": SQLP, "column_count": 1, "expected_rows": [["Alice"]]}
        for index in range(count)]}


def response(text, **changes):
    raw = {"model": SETTINGS["model"], "status": "completed", "id": "offline-response",
           "output": [{"type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": text}]}],
           "usage": {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27}}
    raw.update(changes)
    return raw


def answer(payload):
    public = json.loads(payload["input"][0]["content"])
    return response(json.dumps([{"id": task["id"], "query": SQL}
                                for task in public["tasks"]]))


class BatchPilotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / "academic.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript((ROOT / "examples/academic/data.sql").read_text())
        self.catalog = load_catalog(ROOT / "examples/academic/catalog.json")
        self.guides = {language: (ROOT / "prompts" / (language + ".md")).read_text()
                       for language in ("sql", "sqlp")}
        self.journal = self.directory / "journal.jsonl"

    def run_batch(self, **options):
        return run_batch_pilot(self.database, self.catalog, options.pop("workload", workload()),
                               options.pop("compiler_path", ROOT / "build/sqlpc"),
                               guides=self.guides, settings=SETTINGS, **options)

    def execute(self, provider=answer, **options):
        return self.run_batch(origin=options.pop("origin", "live"), provider=provider,
                              journal_path=self.journal, max_requests=options.pop("max_requests", 24),
                              **options)

    def events(self):
        return [json.loads(line) for line in self.journal.read_text().splitlines()]

    def test_public_requests_have_equivalent_facts_and_no_gold_metadata(self):
        catalog = deepcopy(self.catalog)
        catalog["private"] = "NEVER_SEND_PRIVATE_GOLD"
        tasks = workload()["cases"]
        tasks[0]["private"] = "NEVER_SEND_PRIVATE_GOLD"
        requests = [build_batch_request(tasks, catalog, language, self.guides[language], SETTINGS)
                    for language in ("sql", "sqlp")]
        public = [json.loads(request["input"][0]["content"]) for request in requests]
        self.assertEqual(public[0], public[1])
        for request in requests:
            encoded = json.dumps(request)
            self.assertNotIn("NEVER_SEND_PRIVATE_GOLD", encoded)
            self.assertNotIn("expected_rows", encoded)
            self.assertNotIn("Alice", encoded)
            self.assertEqual(request["reasoning"], {"effort": "high"})
            self.assertFalse(request["store"])
            self.assertEqual(request["tools"], [])

    def test_strict_extraction_rejects_json_errors_markdown_and_unknown_ids(self):
        for text in ("```json\n[]\n```", "[] trailing", "NaN", "{}",
                     '[{"id":"a","id":"b","query":"SELECT 1;"}]',
                     '[{"id":"extra","query":"SELECT 1;"}]',
                     '[{"id":"a","query":"SELECT 1;","note":"extra"}]'):
            with self.subTest(text=text):
                parsed = parse_batch_queries(text, ["a", "b"])
                self.assertEqual(set(parsed["errors"]), {"a", "b"})
                self.assertEqual(parsed["queries"], {})

    def test_extraction_handles_reordering_missing_and_duplicate_ids_without_repair(self):
        parsed = parse_batch_queries('[{"id":"b","query":" SELECT 2; "},'
                                     '{"id":"a","query":"SELECT 1;"}]', ["a", "b"])
        self.assertEqual(parsed["queries"], {"a": "SELECT 1;", "b": " SELECT 2; "})
        self.assertEqual(parsed["errors"], {})
        parsed = parse_batch_queries('[{"id":"a","query":"SELECT 1;"},'
                                     '{"id":"a","query":"SELECT 1;"},'
                                     '{"id":"c","query":"SELECT 3;"}]', ["a", "b", "c"])
        self.assertEqual(parsed["queries"], {"c": "SELECT 3;"})
        self.assertEqual(parsed["errors"], {"a": "duplicate_batch_id", "b": "missing_batch_id"})

    def test_dry_plan_group_sizes_and_default_never_call_provider_or_score(self):
        for size, count in ((1, 24), (4, 6), (12, 2)):
            with self.subTest(size=size), patch.object(batch_module, "_condition") as scorer:
                report = self.run_batch(workload=workload(12), batch_size=size)
            scorer.assert_not_called()
            self.assertEqual((report["origin"], report["completed"]), ("dry_run", True))
            self.assertEqual(len(report["requests"]), count)
            self.assertEqual(report["manifest"]["protocol"], "batch_first_attempt_v1")
            self.assertEqual(report["cases"], [])
            for request in report["requests"]:
                self.assertEqual(request["request_sha256"], digest(request["payload"]))
        self.assertFalse(self.journal.exists())

    def test_exact_replay_scores_queries_but_usage_remains_per_batch(self):
        plan = self.run_batch(batch_size=4)
        provider = ReplayProvider([{"request_sha256": request["request_sha256"],
                                    "response": answer(request["payload"])} for request in plan["requests"]])
        report = self.execute(provider, origin="replay", batch_size=4)
        self.assertTrue(report["ok"])
        self.assertEqual(report["kind"], "batch_pilot")
        self.assertEqual(len(report["batches"]), 2)
        for language in ("sql", "sqlp"):
            summary = report["summary"][language]
            self.assertEqual((summary["correct"], summary["attempts"]), (2, 2))
            self.assertEqual(summary["token_usage"]["total_tokens"], 27)
            self.assertEqual(summary["amortized_batch_tokens_per_question"], 13.5)
        for case in report["cases"]:
            for run in case["runs"]:
                self.assertIsNone(run["token_usage"])
                self.assertNotIn("generation", run)
                self.assertEqual(run["usage_accounting"], "whole_batch_only")
        self.assertEqual(stat.S_IMODE(self.journal.stat().st_mode), 0o600)
        self.assertEqual(sum(event["event"] == "response_received" for event in self.events()), 2)

    def test_malformed_generated_batch_is_failed_attempts_with_usage_retained(self):
        provider = Mock(side_effect=[response("```json\n[]\n```"),
                                     answer(self.run_batch()["requests"][1]["payload"])])
        report = self.execute(provider)
        self.assertTrue(report["completed"])
        self.assertFalse(report["ok"])
        self.assertEqual(sorted(summary["correct"] for summary in report["summary"].values()), [0, 2])
        self.assertTrue(all(summary["token_usage"]["total_tokens"] == 27
                            for summary in report["summary"].values()))

    def test_duplicate_and_missing_case_outputs_stay_in_denominator(self):
        def missing(payload):
            raw = answer(payload)
            entries = json.loads(raw["output"][0]["content"][0]["text"])
            raw["output"][0]["content"][0]["text"] = json.dumps([entries[0], entries[0]])
            return raw
        report = self.execute(missing)
        for summary in report["summary"].values():
            self.assertEqual((summary["correct"], summary["attempts"]), (0, 2))
        self.assertEqual({run["error"]["code"] for case in report["cases"] for run in case["runs"]},
                         {"missing_batch_id", "duplicate_batch_id"})

    def test_incomplete_or_refused_response_fails_every_task_in_its_batch(self):
        provider = Mock(side_effect=[response("[]", status="incomplete"),
                                     response("", output=[{"type": "message", "role": "assistant",
                                         "content": [{"type": "refusal", "refusal": "offline refusal"}]}])])
        report = self.execute(provider)
        self.assertTrue(report["completed"])
        self.assertEqual([value["correct"] for value in report["summary"].values()], [0, 0])
        self.assertEqual({run["error"]["code"] for case in report["cases"] for run in case["runs"]},
                         {"incomplete", "refused"})

    def test_provider_model_drift_aborts_after_received_response_is_journaled(self):
        calls = 0
        def drifting(payload):
            nonlocal calls
            raw = answer(payload)
            calls += 1
            if calls == 2:
                raw["model"] = "different-model"
            return raw
        with self.assertRaisesRegex(SetupError, "model changed"):
            self.execute(drifting)
        events = self.events()
        self.assertEqual(sum(event["event"] == "response_received" for event in events), 2)
        self.assertEqual(events[-1]["event"], "infrastructure_error")

    def test_source_changes_during_generation_do_not_change_private_snapshot_or_compiler(self):
        compiler = self.directory / "source-sqlpc"
        shutil.copyfile(ROOT / "build/sqlpc", compiler)
        compiler.chmod(0o700)
        calls = 0
        def changing(payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                with sqlite3.connect(self.database) as connection:
                    connection.execute("UPDATE AUTHOR SET name='Changed' WHERE aid=1")
                compiler.write_text("not an executable compiler anymore")
            return answer(payload)
        report = self.execute(changing, compiler_path=compiler)
        self.assertTrue(report["ok"])
        self.assertEqual(calls, 2)

    def test_missing_usage_makes_entire_condition_total_unknown(self):
        calls = 0
        def partial(payload):
            nonlocal calls
            raw = answer(payload)
            calls += 1
            if calls == 1:
                raw["usage"] = {"input_tokens": 20}
            return raw
        report = self.execute(partial, batch_size=1)
        self.assertTrue(report["ok"])
        self.assertEqual(sum(summary["token_usage"] is None for summary in report["summary"].values()), 1)

    def test_bad_caps_catalog_and_existing_journal_stop_before_provider(self):
        provider = Mock(side_effect=AssertionError("must never call provider"))
        with self.assertRaises(SetupError):
            self.execute(provider, max_requests=1)
        bad = deepcopy(self.catalog)
        bad["tables"][0]["columns"][0]["name"] = "missing_column"
        with self.assertRaises(SetupError):
            run_batch_pilot(self.database, bad, workload(), guides=self.guides,
                            settings=SETTINGS, origin="live", provider=provider,
                            journal_path=self.journal, max_requests=2)
        self.journal.write_text("retain existing journal")
        with self.assertRaises(SetupError):
            self.execute(provider)
        self.assertEqual(self.journal.read_text(), "retain existing journal")
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()

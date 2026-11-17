"""Three-language batching checks with real SQLite and offline model responses."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import natsql_batch_pilot as pilot
from tools.model_provider import ProviderError
from tools.run_comparison import SetupError
from tools.run_pilot import ReplayProvider, digest
from tools.validate_dataset import load_catalog

SETTINGS = {"model": "offline-three-language-model", "max_output_tokens": 512,
            "reasoning": {"effort": "high"}}
SQL = "SELECT a.name FROM AUTHOR a WHERE a.aid = 1;"
NATSQL = "select AUTHOR.name from AUTHOR where AUTHOR.aid = 1;"


def workload(count=2):
    return {"version": 1, "scope": "development", "cases": [
        {"id": "author-" + str(i), "question": "Name author identifier 1, task " + str(i),
         "sql": SQL, "sqlp": SQL, "column_count": 1, "expected_rows": [["Alice"]]}
        for i in range(count)]}


def response(text, **changes):
    result = {"model": SETTINGS["model"], "id": "offline-response", "status": "completed",
              "output": [{"type": "message", "role": "assistant", "status": "completed",
                          "content": [{"type": "output_text", "text": text}]}],
              "usage": {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27,
                        "output_tokens_details": {"reasoning_tokens": 2}}}
    result.update(changes)
    return result


def answer(payload):
    public = json.loads(payload["input"][0]["content"])
    query = NATSQL if "GUIDE_NATSQL" in payload["instructions"] else SQL
    return response(json.dumps([{"id": task["id"], "query": query}
                                for task in public["tasks"]]))


class NatSQLBatchPilotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.database = self.root / "data.sqlite"
        with sqlite3.connect(self.database) as c:
            c.executescript((ROOT / "examples/academic/data.sql").read_text())
        self.catalog = load_catalog(ROOT / "examples/academic/catalog.json")
        self.guides = {l: "GUIDE_" + l.upper() for l in pilot.LANGUAGES}
        self.journal = self.root / "journal.jsonl"

    def converted(self, query, database, catalog, **options):
        self.assertTrue(Path(database).exists())
        return {"version": 1, "ok": True, "backend": "natsql", "sql": SQL,
                "environment": {"python": "offline", "nltk": "offline"},
                "compiler_options": {"use_from_info": False, "remove_values": False, "sq": None}}

    def plan(self, **options):
        return pilot.run_batch_pilot(self.database, self.catalog, options.pop("workload", workload()),
            options.pop("compiler_path", ROOT / "build/sqlpc"), guides=self.guides,
            settings=SETTINGS, **options)

    def execute(self, provider=answer, converter=None, **options):
        with patch.object(pilot.natsql_adapter, "convert", side_effect=converter or self.converted):
            return self.plan(origin=options.pop("origin", "live"), provider=provider,
                journal_path=self.journal, max_requests=options.pop("max_requests", 100),
                converter_options={"upstream": self.root}, **options)

    def events(self):
        return [json.loads(line) for line in self.journal.read_text().splitlines()]

    def test_all_three_languages_receive_identical_public_facts_without_gold(self):
        catalog = deepcopy(self.catalog)
        catalog["private"] = "PRIVATE_GOLD_SENTINEL"
        tasks = workload()["cases"]
        tasks[0]["private"] = "PRIVATE_GOLD_SENTINEL"
        payloads = [pilot.build_batch_request(tasks, catalog, l, self.guides[l], SETTINGS)
                    for l in pilot.LANGUAGES]
        bodies = [p["input"] for p in payloads]
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[1], bodies[2])
        for p in payloads:
            encoded = json.dumps(p)
            self.assertNotIn("PRIVATE_GOLD_SENTINEL", encoded)
            self.assertNotIn("expected_rows", encoded)
            self.assertNotIn("Alice", encoded)
            self.assertIs(p["store"], False)
            self.assertEqual(p["tools"], [])
            self.assertEqual(p["reasoning"], {"effort": "high"})
        with self.assertRaises(ValueError):
            pilot.build_batch_request(tasks, catalog, "other", "guide", SETTINGS)

    def test_dry_plan_counts_and_hashes_never_invoke_converter_or_scorer(self):
        for size, expected in [(1, 36), (4, 9), (12, 3)]:
            with self.subTest(size=size), patch.object(pilot.natsql_adapter, "convert") as conv, patch.object(pilot, "_condition") as score:
                plan = self.plan(workload=workload(12), batch_size=size)
                conv.assert_not_called()
                score.assert_not_called()
            self.assertEqual(len(plan["requests"]), expected)
            self.assertEqual(set(r["language"] for r in plan["requests"]), set(pilot.LANGUAGES))
            self.assertEqual(plan["manifest"]["protocol"], "three_language_batch_first_attempt_v1")
            self.assertEqual(set(plan["manifest"]["all_guides_sha256"]), set(pilot.LANGUAGES))
            self.assertEqual(plan["cases"], [])
            self.assertTrue(all(r["request_sha256"] == digest(r["payload"]) for r in plan["requests"]))
        self.assertFalse(self.journal.exists())

    def test_exact_replay_scores_all_languages_and_counts_usage_only_once(self):
        plan = self.plan(batch_size=4)
        provider = ReplayProvider([{"request_sha256": r["request_sha256"], "response": answer(r["payload"])}
                                   for r in plan["requests"]])
        report = self.execute(provider, origin="replay", batch_size=4)
        self.assertTrue(report["ok"])
        self.assertEqual(report["kind"], "three_language_batch_pilot")
        self.assertEqual(len(report["batches"]), 3)
        for language in pilot.LANGUAGES:
            value = report["summary"][language]
            self.assertEqual((value["correct"], value["attempts"]), (2, 2))
            self.assertEqual(value["token_usage"]["total_tokens"], 27)
            self.assertEqual(value["amortized_batch_tokens_per_question"], 13.5)
        for case in report["cases"]:
            for run in case["runs"]:
                self.assertIsNone(run["token_usage"])
                self.assertNotIn("generation", run)
                self.assertEqual(run["usage_accounting"], "whole_batch_only")
                if run["language"] == "natsql":
                    self.assertEqual(run["query"], NATSQL)
                    self.assertEqual(run["sql"], SQL)
                    self.assertTrue(run["conversion"]["ok"])
        self.assertEqual(stat.S_IMODE(self.journal.stat().st_mode), 0o600)
        events = self.events()
        self.assertEqual(sum(e["event"] == "response_received" for e in events), 3)
        self.assertEqual(sum(e["event"] == "query_converted" for e in events), 2)

    def test_generated_nat_conversion_failure_remains_an_attempt_with_full_batch_usage(self):
        def rejected(query, *args, **options):
            if query == NATSQL:
                return {"version": 1, "ok": False, "backend": "natsql", "error": {"kind": "conversion_rejected", "message": "rejected"}}
            return self.converted(query, *args, **options)
        report = self.execute(converter=rejected)
        self.assertFalse(report["ok"])
        self.assertTrue(report["completed"])
        nat = report["summary"]["natsql"]
        self.assertEqual((nat["correct"], nat["attempts"]), (0, 2))
        self.assertEqual(nat["token_usage"]["total_tokens"], 27)
        self.assertEqual(report["summary"]["sqlp"]["correct"], 2)
        for c in report["cases"]:
            r = next(r for r in c["runs"] if r["language"] == "natsql")
            self.assertEqual(r["error"], {"stage": "convert", "code": "conversion_rejected"})

    def test_missing_batch_member_is_scored_without_repair_or_duplicated_tokens(self):
        def missing(payload):
            result = answer(payload)
            entries = json.loads(result["output"][0]["content"][0]["text"])
            result["output"][0]["content"][0]["text"] = json.dumps(entries[:1])
            return result
        report = self.execute(missing)
        for language in pilot.LANGUAGES:
            self.assertEqual((report["summary"][language]["correct"], report["summary"][language]["attempts"]), (1, 2))
            self.assertEqual(report["summary"][language]["token_usage"]["total_tokens"], 27)
        errors = [r["error"]["code"] for c in report["cases"] for r in c["runs"] if r["error"]]
        self.assertEqual(errors, ["missing_batch_id"] * 3)

    def test_converter_dependency_or_smoke_failure_stops_before_paid_provider(self):
        provider = Mock(side_effect=AssertionError("must not call"))
        def failed(*args, **kwargs):
            return {"version": 1, "ok": False, "backend": "natsql", "error": {"kind": "dependency_setup", "message": "not installed"}}
        with self.assertRaisesRegex(SetupError, "not installed"):
            self.execute(provider, converter=failed)
        provider.assert_not_called()

    def test_bad_caps_or_existing_journal_stop_before_provider(self):
        provider = Mock(side_effect=AssertionError("must not call"))
        with self.assertRaises(SetupError):
            self.execute(provider, max_requests=2)
        self.journal.write_text("prior record")
        with self.assertRaises(SetupError):
            self.execute(provider)
        self.assertEqual(self.journal.read_text(), "prior record")
        provider.assert_not_called()

    def test_model_drift_aborts_after_response_was_durably_saved(self):
        count = 0
        def changing(payload):
            nonlocal count
            result = answer(payload)
            count += 1
            if count == 2:
                result["model"] = "changed-model"
            return result
        with self.assertRaisesRegex(SetupError, "model changed"):
            self.execute(changing)
        self.assertEqual(sum(e["event"] == "response_received" for e in self.events()), 2)
        self.assertEqual(self.events()[-1]["event"], "infrastructure_error")

    def test_source_mutation_does_not_change_private_sql_and_nat_snapshots(self):
        def mutating(payload):
            with sqlite3.connect(self.database) as c:
                c.execute("UPDATE AUTHOR SET name='Changed' WHERE aid=1")
            return answer(payload)
        report = self.execute(mutating)
        self.assertTrue(report["ok"])
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o644)

    def test_unknown_usage_remains_unknown_for_only_affected_condition(self):
        count = 0
        def partial(payload):
            nonlocal count
            result = answer(payload)
            count += 1
            if count == 1:
                result["usage"] = None
            return result
        report = self.execute(partial)
        self.assertTrue(report["ok"])
        self.assertEqual(sum(v["token_usage"] is None for v in report["summary"].values()), 1)


if __name__ == "__main__":
    unittest.main()

"""Offline orchestration checks; fixture responses are never study evidence."""

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_scale_study as study
from tools.model_provider import ProviderError
from tools.run_pilot import digest


class ScaleRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "supported"
        dataset = self.source / "academic"
        dataset.mkdir(parents=True)
        with sqlite3.connect(dataset / "database.sqlite") as connection:
            connection.executescript((ROOT / "examples/academic/data.sql").read_text())
        (dataset / "catalog.json").write_text((ROOT / "examples/academic/catalog.json").read_text())
        self.cases = [{"id": "task-" + str(i), "question": "Return each author's name, retaining duplicates. " + str(i),
                       "sql": "SELECT name FROM AUTHOR;", "sqlp": "SELECT a.name FROM AUTHOR a;",
                       "column_count": 1, "expected_rows": [["Alice"], ["Bob"], ["Cara"]],
                       "private": "PRIVATE_GOLD_SENTINEL",
                       "design": {"domain": "academic", "family": "names", "depth": 0}}
                      for i in range(3)]
        # Derive fixture rows directly; tests focus on orchestration rather than semantics.
        with sqlite3.connect(dataset / "database.sqlite") as connection:
            rows = [list(row) for row in connection.execute("SELECT name FROM AUTHOR")]
        for case in self.cases:
            case["expected_rows"] = rows
        study.write_new(dataset / "workload.json", {"version": 1, "scope": "development", "cases": self.cases})
        study.write_new(self.source / "manifest.json", {"datasets": [{"id": "academic", "directory": "academic",
                                                                      "case_count": 3, "snapshots": []}]})
        self.ledger = self.root / "ledger"
        self.ledger.mkdir()
        study.write_new(self.ledger / "budget.json", {"version": 1, "authorized_micro_usd": 100_000_000,
            "prior_upper_micro_usd": 1_000_000, "calls": []})
        self.output = self.root / "study"

    def prepare(self):
        return study.prepare(self.output, supported=self.source, compiler_path=ROOT / "build/sqlpc",
                             ledger=self.ledger, batch_size=2)

    def answer(self, payload, usage=True):
        tasks = json.loads(payload["input"][0]["content"])["tasks"]
        language = "sqlp" if "SQLP" in payload["instructions"] else "sql"
        query = self.cases[0][language]
        return {"id": "fixture", "model": "gpt-6-luna", "status": "completed",
                "output": [{"type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": json.dumps([
                                {"id": task["id"], "query": query} for task in tasks])}]}],
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30} if usage else None}

    def test_prepare_freezes_sources_and_never_leaks_gold_into_requests(self):
        result = self.prepare()
        self.assertEqual((result["planned_unique_questions"], result["planned_calls"], result["planned_outputs"]), (3, 4, 6))
        design = study.verify(self.output)
        groups = {}
        for request in design["requests"]:
            encoded = json.dumps(request["payload"])
            self.assertNotIn("PRIVATE_GOLD_SENTINEL", encoded)
            self.assertNotIn("expected_rows", encoded)
            self.assertNotIn(self.cases[0]["sql"], encoded)
            key = tuple(request["case_ids"])
            previous = groups.setdefault(key, request["payload"]["input"])
            self.assertEqual(previous, request["payload"]["input"])
        (self.source / "academic/workload.json").write_text("changed source")
        study.verify(self.output)
        with self.assertRaisesRegex(ValueError, "new directory"):
            self.prepare()
        (self.output / "inputs/supported/academic/workload.json").write_text("changed frozen")
        with self.assertRaisesRegex(ValueError, "Frozen input changed"):
            study.verify(self.output)

    def test_run_and_resume_count_usage_once_per_batch_without_new_calls(self):
        self.prepare()
        count = 0
        def provider(payload):
            nonlocal count
            count += 1
            return self.answer(payload)
        first = study.run(self.output, provider=provider, ledger=self.ledger, workers=2)
        self.assertTrue(first["completed"])
        self.assertEqual((count, first["generated_outputs"], first["observed_unique_questions"]), (4, 6, 3))
        for language in ("sql", "sqlp"):
            summary = first["by_language"]["supported/" + language]
            self.assertEqual((summary["correct"], summary["scoreable"]), (3, 3))
            self.assertEqual(summary["token_usage"]["total_tokens"], 60)
        second = study.run(self.output, provider=lambda _: self.fail("Must not call again"), ledger=self.ledger)
        self.assertEqual(second["completed_calls"], 4)
        self.assertEqual(second["token_usage"]["total_tokens"], 120)

    def test_raw_response_retained_before_scoring_and_reused_after_report_loss(self):
        self.prepare()
        study.run(self.output, provider=self.answer, ledger=self.ledger, workers=1, max_new_requests=1)
        request = study.read(self.output / "design.json")["requests"][0]
        folder = self.output / "runs" / request["id"]
        (folder / "report.json").unlink()
        result = study.run(self.output, provider=lambda _: self.fail("Retained response must be reused"),
                           ledger=self.ledger, workers=1, max_new_requests=1)
        self.assertEqual(result["completed_calls"], 1)
        self.assertTrue(study.read(folder / "report.json")["recovered_response"])

    def test_ledger_recovers_response_when_process_stopped_before_local_copy(self):
        self.prepare()
        request = study.read(self.output / "design.json")["requests"][0]
        label = "scale-study/" + digest({"design": study.sha(self.output / "design.json")})[:20]
        state = study.read(self.ledger / "budget.json")
        state["calls"].append({"label": label, "request_sha256": request["request_sha256"],
                               "response_file": "response-00000.json", "upper_micro_usd": 100})
        (self.ledger / "budget.json").write_text(json.dumps(state))
        study.write_new(self.ledger / "response-00000.json", self.answer(request["payload"]))
        result = study.run(self.output, provider=lambda _: self.fail("Ledger response must be reused"),
                           ledger=self.ledger, workers=1, max_new_requests=1)
        self.assertEqual(result["completed_calls"], 1)

    def test_uncertain_transport_is_not_retried_and_other_requests_are_not_queued(self):
        self.prepare()
        count = 0
        def provider(payload):
            nonlocal count
            count += 1
            raise ProviderError("fixture transport uncertainty")
        first = study.run(self.output, provider=provider, ledger=self.ledger, workers=1)
        self.assertEqual(count, 1)
        self.assertEqual(first["completed_calls"], 0)
        self.assertEqual(len(first["run_errors"]), 1)
        second = study.run(self.output, provider=provider, ledger=self.ledger, workers=1)
        self.assertEqual(count, 1)
        self.assertIn("Prior started", second["run_errors"][0]["message"])

    def test_incomplete_and_missing_outputs_remain_failed_attempts_with_usage(self):
        self.prepare()
        def incomplete(payload):
            response = self.answer(payload)
            response["status"] = "incomplete"
            return response
        result = study.run(self.output, provider=incomplete, ledger=self.ledger, workers=2)
        self.assertTrue(result["completed"])
        self.assertEqual(result["generated_outputs"], 6)
        self.assertEqual(result["token_usage"]["total_tokens"], 120)
        self.assertEqual(result["by_language"]["supported/sql"]["correct"], 0)
        self.assertEqual(result["by_language"]["supported/sql"]["errors"], {"incomplete": 3})

    def test_unknown_usage_remains_unknown(self):
        self.prepare()
        result = study.run(self.output, provider=lambda p: self.answer(p, usage=False), ledger=self.ledger)
        self.assertIsNone(result["token_usage"])
        self.assertEqual(result["usage_known_calls"], 0)

    def test_frozen_natsql_network_profile_mismatch_stops_before_provider(self):
        study.prepare(self.output, supported=self.source, compiler_path=ROOT / "build/sqlpc",
                      ledger=self.ledger, batch_size=2, languages=("natsql",),
                      natsql_network_profile="base")
        self.assertEqual(study.verify(self.output)["converter_profile"], {"network_profile": "base"})
        with self.assertRaisesRegex(ValueError, "frozen design"):
            study.run(self.output, provider=lambda _: self.fail("must not call"), ledger=self.ledger,
                      converter_options={"upstream": self.root, "network_profile": "enlarged"})

    def test_frozen_natsql_cache_and_literal_profile_are_forwarded_without_public_leakage(self):
        from unittest.mock import patch
        cache = self.root / "cache"
        cache.mkdir()
        (cache / "schema.json").write_text('{"schema":"PRIVATE_SCHEMA_CACHE_SENTINEL"}')
        study.write_new(cache / "manifest.json", {"version": 1, "kind": "natsql_prepared_schema_cache", "entries": []})
        with patch("tools.natsql_cache.resolve_manifest") as validate:
            study.prepare(self.output, supported=self.source, compiler_path=ROOT / "build/sqlpc",
                          ledger=self.ledger, batch_size=2, languages=("natsql",),
                          natsql_network_profile="enlarged-indexed", natsql_literal_profile="preserved",
                          natsql_schema_cache=cache / "manifest.json")
        validate.assert_called_once()
        design = study.verify(self.output)
        self.assertIn("converter-cache/schema.json", design["frozen_sha256"])
        self.assertNotIn("PRIVATE_SCHEMA_CACHE_SENTINEL", json.dumps(design["requests"]))
        expected_cache = {"manifest": str(self.output / "converter-cache/manifest.json"),
                          "sha256": study.sha(self.output / "converter-cache/manifest.json")}
        seen = []
        def converted(query, database, catalog, **options):
            seen.append(options)
            return {"version": 1, "backend": "natsql", "ok": True,
                    "sql": "SELECT name FROM AUTHOR;", "compiler_options": {
                        "network_profile": "enlarged-indexed", "literal_profile": "preserved"}}
        with patch("tools.natsql_pilot.natsql_adapter.convert", side_effect=converted):
            report = study.run(self.output, provider=self.answer, ledger=self.ledger,
                converter_options={"upstream": self.root, "network_profile": "enlarged-indexed",
                                   "literal_profile": "preserved"})
        self.assertTrue(report["completed"])
        self.assertTrue(all(options["schema_cache"] == expected_cache and
                            options["literal_profile"] == "preserved" for options in seen))
        (self.output / "converter-cache/schema.json").write_text("changed")
        with self.assertRaisesRegex(ValueError, "Frozen input changed"):
            study.verify(self.output)

    def test_public_request_allowlists_tasks_and_nested_schema(self):
        schema = {"version": 1, "database_id": "fixture", "private": "PRIVATE_GOLD_SENTINEL",
                  "tables": [{"name": "t", "private": "PRIVATE_GOLD_SENTINEL", "columns": [
                      {"name": "x", "declared_type": "REAL", "not_null": False, "primary_key_position": 0,
                       "expected_rows": "PRIVATE_GOLD_SENTINEL"}], "foreign_keys": []}]}
        request = study.build_public_request(self.cases[:1], schema, "SQL guide",
                                            {"model": "gpt-6-luna", "max_output_tokens": 8192})
        encoded = json.dumps(request)
        self.assertNotIn("PRIVATE_GOLD_SENTINEL", encoded)
        self.assertNotIn("expected_rows", encoded)
        self.assertIn("REAL", encoded)
        with self.assertRaises(ValueError):
            study.build_public_request(self.cases[:1] * 2, schema, "guide",
                                       {"model": "gpt-6-luna", "max_output_tokens": 8192})

    def test_public_end_to_end_uses_ordered_scorer_and_keeps_gold_failures(self):
        source = self.root / "public"
        dataset = source / "fixture"
        (dataset / "public").mkdir(parents=True)
        (dataset / "private").mkdir()
        with sqlite3.connect(dataset / "database.sqlite") as connection:
            connection.executescript("CREATE TABLE t(x INTEGER); INSERT INTO t VALUES(1),(2);")
        schema = {"version": 1, "database_id": "fixture", "tables": [{"name": "t", "columns": [
            {"name": "x", "declared_type": "INTEGER", "not_null": False, "primary_key_position": 0}],
            "foreign_keys": [], "create_sql": "CREATE TABLE t(x INTEGER)"}], "published_foreign_keys": []}
        cases = [{"id": "ordered", "question": "List x in ascending order.", "sql": "SELECT x FROM t ORDER BY x;",
                  "sqlp": "", "column_count": 1, "expected_rows": [[1], [2]], "gold_status": "ok",
                  "metadata": {"ordered_result": True, "features": ["order_by"]}},
                 {"id": "bad-gold", "question": "This published task has malformed gold text.",
                  "sql": "SELECT malformed FROM t;", "sqlp": "", "column_count": None,
                  "expected_rows": None, "gold_status": "error", "gold_error": {"code": "text_decoding"},
                  "metadata": {"ordered_result": False}}]
        study.write_new(dataset / "public/schema.json", schema)
        study.write_new(dataset / "public/tasks.json", {"version": 1, "cases": [
            {"id": c["id"], "question": c["question"]} for c in cases]})
        study.write_new(dataset / "private/workload.json", {"version": 1, "scope": "development", "cases": cases})
        study.write_new(source / "manifest.json", {"datasets": [{"id": "fixture", "directory": "fixture",
            "database": "fixture/database.sqlite", "schema": "fixture/public/schema.json",
            "tasks": "fixture/public/tasks.json", "workload": "fixture/private/workload.json", "case_count": 2}]})
        study.prepare(self.output, public=source, ledger=self.ledger, compiler_path=ROOT / "build/sqlpc", batch_size=4)
        def provider(payload):
            response = self.answer(payload)
            response["output"][0]["content"][0]["text"] = json.dumps([
                {"id": c["id"], "query": "SELECT x FROM t ORDER BY x DESC;"} for c in cases])
            return response
        result = study.run(self.output, provider=provider, ledger=self.ledger, workers=1)
        summary = result["by_language"]["public/sql"]
        self.assertTrue(result["completed"])
        self.assertEqual((summary["generated"], summary["scoreable"], summary["gold_errors"], summary["correct"]),
                         (2, 1, 1, 0))
        self.assertEqual(summary["token_usage"]["total_tokens"], 30)
        self.assertEqual(summary["errors"], {"wrong_answer": 1})


if __name__ == "__main__":
    unittest.main()

"""Saved-response integrity and contrast rescoring checks; no model calls."""

import json
from copy import deepcopy
import hashlib
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

    def recovery_fixture(self):
        """Declare an unobserved first send plus one retained manual response."""
        self.run_fixture()
        design = json.loads((self.study / "design.json").read_text())
        label = "scale-study/" + runner.digest({"design": runner.sha(self.study / "design.json")})[:20]
        calls = []
        for request in design["requests"]:
            folder = self.study / "runs" / request["id"]
            raw = json.loads((folder / "response.json").read_text())["response"]
            index = len(calls)
            response_file = "response-%05d.json" % index
            write(self.ledger / response_file, raw)
            calls.append({"index": index, "label": label, "model": design["settings"]["model"],
                "request_sha256": request["request_sha256"], "status": "completed",
                "response_file": response_file, "upper_micro_usd": 10, "estimated_micro_usd": 9})
        request = design["requests"][0]
        folder = self.study / "runs" / request["id"]
        raw = json.loads((folder / "response.json").read_text())["response"]
        recovery_index = len(calls)
        retained = deepcopy(calls[0])
        retained.update(index=recovery_index, label=label + "/transport-recovery-1",
                        response_file="response-%05d.json" % recovery_index)
        write(self.ledger / retained["response_file"], raw)
        calls.append(retained)
        calls[0].update(status="transport_or_http_error_usage_unknown", upper_micro_usd=100000,
                        estimated_micro_usd=None)
        calls[0].pop("response_file")
        write(self.ledger / "budget.json", {"calls": calls})
        original_journal = (json.dumps({"event": "request_started",
                            "request_sha256": request["request_sha256"]}) + "\n").encode()
        (folder / "journal.jsonl").write_bytes(original_journal)
        plan = {"version": 1, "kind": "explicit_scale_transport_recovery_plan",
            "study_design_sha256": runner.sha(self.study / "design.json"),
            "request_id": request["id"], "request_sha256": request["request_sha256"],
            "original_label": label, "original_ledger_index": 0,
            "original_status": calls[0]["status"], "original_upper_micro_usd": 100000,
            "original_call_sha256": runner.digest(calls[0]),
            "original_journal_bytes": len(original_journal),
            "original_journal_sha256": hashlib.sha256(original_journal).hexdigest(),
            "recovery_label": retained["label"], "authorization": "Explicit fixture authorization",
            "original_unknown_usage": True, "whole_study_api_token_usage": None,
            "policy": "One manual identical-payload transport resubmission; preserve the unknown reservation; no query repair."}
        write(folder / "transport-recovery-plan.json", plan)
        manifest = {**plan, "kind": "explicit_scale_transport_recovery",
            "plan_file": "transport-recovery-plan.json", "plan_sha256": runner.sha(folder / "transport-recovery-plan.json"),
            "recovery_ledger_index": recovery_index, "recovery_response_file": retained["response_file"],
            "response_sha256": runner.digest(raw),
            "ledger_response_file_sha256": runner.sha(self.ledger / retained["response_file"]),
            "known_retained_response_usage": raw["usage"], "paid_call_count_for_frozen_request": 2,
            "budget_guard_sha256": "0" * 64}
        path = folder / "transport-recovery.json"
        write(path, manifest)
        config = {"manifest": path, "sha256": runner.sha(path)}
        saved = json.loads((folder / "response.json").read_text())
        saved["transport_recovery"] = {"manifest": path.name, "manifest_sha256": config["sha256"]}
        write(folder / "response.json", saved)
        return config, request, calls

    def two_recoveries_fixture(self):
        first, _, calls = self.recovery_fixture()
        design = json.loads((self.study / "design.json").read_text())
        request = design["requests"][1]
        folder = self.study / "runs" / request["id"]
        saved = json.loads((folder / "response.json").read_text())
        raw = saved["response"]
        retained = deepcopy(calls[1])
        retained.update(index=len(calls), label=calls[1]["label"] + "/transport-recovery-1",
                        response_file="response-%05d.json" % len(calls))
        write(self.ledger / retained["response_file"], raw)
        calls.append(retained)
        calls[1].update(status="transport_or_http_error_usage_unknown", upper_micro_usd=100000,
                        estimated_micro_usd=None)
        calls[1].pop("response_file")
        write(self.ledger / "budget.json", {"calls": calls})
        journal = (json.dumps({"event": "request_started", "request_sha256": request["request_sha256"]}) + "\n").encode()
        (folder / "journal.jsonl").write_bytes(journal)
        plan = json.loads(Path(first["manifest"]).with_name("transport-recovery-plan.json").read_text())
        plan.update(request_id=request["id"], request_sha256=request["request_sha256"],
                    original_ledger_index=1, original_call_sha256=runner.digest(calls[1]),
                    original_journal_bytes=len(journal), original_journal_sha256=hashlib.sha256(journal).hexdigest())
        write(folder / "transport-recovery-plan.json", plan)
        manifest = {**plan, "kind": "explicit_scale_transport_recovery", "plan_file": "transport-recovery-plan.json",
            "plan_sha256": runner.sha(folder / "transport-recovery-plan.json"),
            "recovery_ledger_index": retained["index"], "recovery_response_file": retained["response_file"],
            "response_sha256": runner.digest(raw),
            "ledger_response_file_sha256": runner.sha(self.ledger / retained["response_file"]),
            "known_retained_response_usage": raw["usage"], "paid_call_count_for_frozen_request": 2,
            "budget_guard_sha256": "0" * 64}
        path = folder / "transport-recovery.json"
        write(path, manifest)
        second = {"manifest": path, "sha256": runner.sha(path)}
        saved["transport_recovery"] = {"manifest": path.name, "manifest_sha256": second["sha256"]}
        write(folder / "response.json", saved)
        return [first, second], calls

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

    def test_explicit_transport_recovery_keeps_total_usage_unknown_and_known_sums_separate(self):
        config, request, _ = self.recovery_fixture()
        result = validate(self.study, require_complete=True, ledger=self.ledger, recovery_manifest=config)
        self.assertTrue(result["complete"])
        self.assertFalse(result["api_token_usage_complete"])
        self.assertIsNone(result["token_usage"])
        self.assertEqual(result["unobserved_transport_calls"], 1)
        self.assertEqual(result["total_ledger_attempts_accounted"], 3)
        affected = "supported/" + request["language"]
        other = "supported/" + ("sqlp" if request["language"] == "sql" else "sql")
        self.assertIsNone(result["token_usage_by_language"][affected])
        self.assertEqual(result["token_usage_by_language"][other]["total_tokens"], 15)
        self.assertEqual(result["known_response_token_sums"]["total_tokens"], 30)
        self.assertEqual(result["known_response_token_sums_by_language"][affected]["total_tokens"], 15)

    def test_transport_duplicates_require_explicit_manifest_and_ledger(self):
        config, _, _ = self.recovery_fixture()
        with self.assertRaisesRegex(ValueError, "explicitly supplied"):
            validate(self.study, ledger=self.ledger)
        with self.assertRaisesRegex(ValueError, "explicit ledger"):
            validate(self.study, recovery_manifest=config)

    def test_recovery_cannot_release_the_original_unknown_reservation_or_hide_a_response(self):
        config, _, calls = self.recovery_fixture()
        calls[0]["upper_micro_usd"] -= 1
        write(self.ledger / "budget.json", {"calls": calls})
        with self.assertRaisesRegex(ValueError, "reservation was changed"):
            validate(self.study, ledger=self.ledger, recovery_manifest=config)
        calls[0]["upper_micro_usd"] += 1
        calls[0]["response_file"] = calls[-1]["response_file"]
        write(self.ledger / "budget.json", {"calls": calls})
        with self.assertRaisesRegex(ValueError, "unobserved"):
            validate(self.study, ledger=self.ledger, recovery_manifest=config)

    def test_recovery_rejects_additional_paid_attempts_or_changed_plan(self):
        config, _, calls = self.recovery_fixture()
        extra = {**calls[-1], "index": len(calls), "label": "undeclared-third-attempt"}
        write(self.ledger / "budget.json", {"calls": [*calls, extra]})
        with self.assertRaisesRegex(ValueError, "Undisclosed extra"):
            validate(self.study, ledger=self.ledger, recovery_manifest=config)
        write(self.ledger / "budget.json", {"calls": calls})
        plan_path = Path(config["manifest"]).with_name("transport-recovery-plan.json")
        plan_path.write_text(plan_path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "authorization plan hash"):
            validate(self.study, ledger=self.ledger, recovery_manifest=config)

    def test_recovery_rejects_manifest_tampering_and_ledger_raw_byte_changes(self):
        config, _, calls = self.recovery_fixture()
        path = Path(config["manifest"])
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "manifest SHA256"):
            validate(self.study, ledger=self.ledger, recovery_manifest=config)
        path.write_bytes(original)
        response = self.ledger / calls[-1]["response_file"]
        response.write_text(response.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "response byte hash"):
            validate(self.study, ledger=self.ledger, recovery_manifest=config)

    def test_recovery_preserves_the_entire_unknown_entry_and_original_journal_prefix(self):
        config, request, calls = self.recovery_fixture()
        calls[0]["unexpected_change"] = "not part of the authorized original entry"
        write(self.ledger / "budget.json", {"calls": calls})
        with self.assertRaisesRegex(ValueError, "unknown ledger entry changed"):
            validate(self.study, ledger=self.ledger, recovery_manifest=config)
        calls[0].pop("unexpected_change")
        write(self.ledger / "budget.json", {"calls": calls})
        path = self.study / "runs" / request["id"] / "journal.jsonl"
        path.write_bytes(b" " + path.read_bytes())
        with self.assertRaisesRegex(ValueError, "journal prefix changed"):
            validate(self.study, ledger=self.ledger, recovery_manifest=config)

    def test_partial_study_already_discloses_unobserved_transport_usage_before_recovery(self):
        _, request, calls = self.recovery_fixture()
        folder = self.study / "runs" / request["id"]
        (folder / "report.json").unlink()
        (folder / "response.json").unlink()
        write(self.ledger / "budget.json", {"calls": calls[:-1]})
        result = validate(self.study, ledger=self.ledger)
        self.assertFalse(result["complete"])
        self.assertFalse(result["api_token_usage_complete"])
        self.assertIsNone(result["token_usage"])
        self.assertEqual(result["unobserved_transport_calls"], 1)
        self.assertEqual(result["known_response_token_sums"]["total_tokens"], 15)

    def test_two_independent_manifest_bindings_keep_both_unknown_attempts(self):
        configs, _ = self.two_recoveries_fixture()
        result = validate(self.study, require_complete=True, ledger=self.ledger, recovery_manifest=configs)
        self.assertTrue(result["complete"])
        self.assertEqual(result["unobserved_transport_calls"], 2)
        self.assertEqual(result["total_ledger_attempts_accounted"], 4)
        self.assertIsNone(result["token_usage"])
        self.assertEqual(result["known_response_token_sums"]["total_tokens"], 30)
        self.assertEqual(result["token_usage_by_language"], {"supported/sql": None, "supported/sqlp": None})
        self.assertEqual(len(result["transport_recoveries"]), 2)
        self.assertIsNone(result["transport_recovery"])

    def test_repeated_manifests_or_missing_request_exceptions_are_rejected(self):
        configs, _ = self.two_recoveries_fixture()
        with self.assertRaisesRegex(ValueError, "Repeated recovery"):
            validate(self.study, ledger=self.ledger, recovery_manifest=[configs[0], configs[0]])
        with self.assertRaisesRegex(ValueError, "explicitly supplied"):
            validate(self.study, ledger=self.ledger, recovery_manifest=configs[:1])

    def test_manifest_hashes_and_local_markers_cannot_be_cross_mapped_between_requests(self):
        configs, _ = self.two_recoveries_fixture()
        wrong = [{**configs[0], "sha256": configs[1]["sha256"]}, configs[1]]
        with self.assertRaisesRegex(ValueError, "manifest SHA256"):
            validate(self.study, ledger=self.ledger, recovery_manifest=wrong)
        path = Path(configs[1]["manifest"]).with_name("response.json")
        saved = json.loads(path.read_text())
        saved["transport_recovery"]["manifest_sha256"] = configs[0]["sha256"]
        write(path, saved)
        with self.assertRaisesRegex(ValueError, "does not disclose"):
            validate(self.study, ledger=self.ledger, recovery_manifest=configs)


if __name__ == "__main__":
    unittest.main()

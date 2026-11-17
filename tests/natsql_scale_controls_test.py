"""Small real SQLite control fixtures with mocked providers and conversion."""

import json
import hashlib
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import natsql_scale_controls as controls
from tools import run_scale_study as study
from tools import scale_model_matrix as matrix

SQL = "SELECT name FROM AUTHOR WHERE aid=1;"
SQLP = "SELECT a.name FROM AUTHOR a WHERE a.aid=1;"
NATSQL = "SELECT AUTHOR.name FROM AUTHOR WHERE AUTHOR.aid=1;"


class NatSQLScaleControlsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.supported = self.root / "supported"
        dataset = self.supported / "academic"
        dataset.mkdir(parents=True)
        with sqlite3.connect(dataset / "database.sqlite") as connection:
            connection.executescript((ROOT / "examples/academic/data.sql").read_text())
        shutil.copy2(ROOT / "examples/academic/catalog.json", dataset / "catalog.json")
        self.cases = [{"id": "q" + str(i), "question": "Return the author name for identifier 1. Task " + str(i),
                      "sql": SQL, "sqlp": SQLP, "column_count": 1, "expected_rows": [["Alice"]],
                      "design": {"depth": 1, "family": "author", "expected_empty": False}}
                     for i in range(4)]
        study.write_new(dataset / "workload.json", {"version": 1, "scope": "development", "cases": self.cases})
        alternate = dataset / "contrast-1"
        alternate.mkdir()
        shutil.copy2(dataset / "database.sqlite", alternate / "database.sqlite")
        with sqlite3.connect(alternate / "database.sqlite") as connection:
            connection.execute("UPDATE AUTHOR SET name='Zora' WHERE aid=1")
        other_cases = json.loads(json.dumps(self.cases))
        for case in other_cases:
            case["expected_rows"] = [["Zora"]]
        study.write_new(alternate / "workload.json", {"version": 1, "scope": "development", "cases": other_cases})
        study.write_new(self.supported / "manifest.json", {"case_count": 4,
                "datasets": [{"id": "academic", "directory": "academic", "case_count": 4,
                              "snapshots": [{"id": "contrast-1", "database": "academic/contrast-1/database.sqlite",
                                             "workload": "academic/contrast-1/workload.json"}]}]})
        self.ledger = self.root / "ledger"
        self.ledger.mkdir()
        study.write_new(self.ledger / "budget.json", {"authorized_micro_usd": 100_000_000,
                         "prior_upper_micro_usd": 1_000_000, "calls": []})
        self.luna = self.root / "luna"
        study.prepare(self.luna, supported=self.supported, compiler_path=ROOT / "build/sqlpc",
                      ledger=self.ledger, batch_size=4)
        self.matrix = self.root / "matrix"
        matrix.prepare(self.luna, self.matrix, ledger=self.ledger)
        self.subset = self.root / "subset"
        shutil.copytree(self.supported, self.subset)
        study.write_new(self.subset / "selection.json", {"original_design_sha256": study.sha(self.luna / "design.json"),
                         "model_outcomes_read": False})
        manifest = study.read(self.subset / "manifest.json")
        manifest["files_sha256"] = {str(p.relative_to(self.subset)): study.sha(p)
                for p in self.subset.rglob("*") if p.is_file() and p.name != "manifest.json"}
        (self.subset / "manifest.json").write_text(json.dumps(manifest))
        self.guide = self.root / "natsql.md"
        self.guide.write_text("NatSQL GUIDE: use qualified physical columns; return JSON.")
        self.output = self.root / "controls"

    def prepare(self):
        return controls.prepare(self.matrix, self.subset, self.output, guide=self.guide, ledger=self.ledger,
                                network_profile="base", literal_profile="native")

    @staticmethod
    def response(payload):
        instructions = payload["instructions"]
        query = NATSQL if "NatSQL GUIDE" in instructions else SQLP if "SQLP" in instructions else SQL
        tasks = json.loads(payload["input"][0]["content"])["tasks"]
        return {"id": "fixture-only", "model": payload["model"], "status": "completed",
                "output": [{"type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": json.dumps([
                                {"id": c["id"], "query": query} for c in tasks])}]}],
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}}

    def run_fixtures(self):
        manifest = controls.read(self.output / "controls.json")
        for member in manifest["members"]:
            main = (self.output / member["main_directory"]).resolve()
            study.run(main, provider=self.response, ledger=self.ledger, workers=1)
            def converted(query, database, catalog, **options):
                self.assertEqual(options["network_profile"], "base")
                return {"version": 1, "backend": "natsql", "ok": True, "sql": SQL,
                        "compiler_options": {"network_profile": "base"}}
            with patch("tools.natsql_pilot.natsql_adapter.convert", side_effect=converted):
                study.run(self.output / member["directory"], provider=self.response, ledger=self.ledger,
                          workers=1, converter_options={"upstream": self.root, "network_profile": "base"})

    def test_prepare_matches_original_groups_facts_settings_and_records_profile(self):
        manifest = self.prepare()
        self.assertEqual((manifest["unique_questions"], manifest["natsql_planned_calls"],
                          manifest["natsql_planned_outputs"]), (4, 3, 12))
        self.assertEqual(manifest["questions_added_to_parent_distinct_count"], 0)
        for member in manifest["members"]:
            design = controls.verify(self.output / member["directory"])
            self.assertEqual(design["supported_languages"], ["natsql"])
            self.assertEqual(design["converter_profile"], {"network_profile": "base", "literal_profile": "native"})
            request = design["requests"][0]
            self.assertNotIn("expected_rows", json.dumps(request["payload"]))
            self.assertNotIn("Alice", json.dumps(request["payload"]))
            self.assertEqual(len(member["matched_batches"]), 1)
            self.assertEqual(member["matched_batches"][0]["case_ids"], ["q0", "q1", "q2", "q3"])

    def test_incomplete_and_existing_control_outputs_are_explicit(self):
        self.prepare()
        result = controls.analyze(self.output, rescore=False)
        self.assertFalse(result["completed"])
        self.assertEqual(result["completed_new_natsql_batches"], 0)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            controls.analyze(self.output, require_complete=True, rescore=False)
        before = (self.output / "controls.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "new directory"):
            self.prepare()
        self.assertEqual((self.output / "controls.json").read_bytes(), before)

    def test_indexed_profile_is_explicitly_frozen_without_changing_public_facts(self):
        manifest = controls.prepare(self.matrix, self.subset, self.output, guide=self.guide,
                                    ledger=self.ledger, network_profile="enlarged-indexed")
        self.assertEqual(manifest["converter_profile"]["network_profile"], "enlarged-indexed")
        self.assertEqual(controls.verify(self.output / "luna")["converter_profile"],
                         {"network_profile": "enlarged-indexed", "literal_profile": "preserved"})
        self.assertTrue(any("exact official-network equivalence" in item for item in manifest["limitations"]))

    def test_modified_subset_fails_before_creating_requests(self):
        path = self.subset / "academic/catalog.json"
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "Subset input changed"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_analysis_reuses_whole_batches_once_and_reexecutes_generated_sql(self):
        self.prepare()
        self.run_fixtures()
        result = controls.analyze(self.output, require_complete=True)
        self.assertTrue(result["completed"])
        self.assertEqual((result["completed_new_natsql_batches"], result["generated_new_natsql_outputs"],
                          result["reexecuted_converted_primary_outputs"]), (3, 12, 12))
        for model in result["by_model"].values():
            self.assertEqual(model["natsql_contrast_rescoring"],
                             {"attempts": 4, "correct": 4, "primary_match_contrast_mismatch": 0})
            for language in ("sql", "sqlp", "natsql"):
                value = model["languages"][language]
                self.assertEqual((value["generated"], value["correct"], value["batches"]), (4, 4, 1))
                self.assertEqual(value["token_usage"]["total_tokens"], 30)

    def test_reference_scopes_keep_all_targets_and_exclude_partial_batches_from_tokens(self):
        reference = self.root / "reference"
        reference.mkdir()
        study.write_new(reference / "report.json", {"model_calls": 0, "purpose": "offline reference fixture"})
        study.write_new(reference / "coverage-summary.json", {
            "model_outcomes_used_for_coverage": False,
            "fixed_model_target_ids": ["q0", "q1", "q2", "q3"],
            "common_reference_ids_after_serial_review": ["q0", "q1", "q2"]})
        manifest = controls.prepare(self.matrix, self.subset, self.output, guide=self.guide,
            ledger=self.ledger, network_profile="base", literal_profile="native",
            reference_report=reference / "report.json")
        self.assertEqual(manifest["unique_questions"], 4)
        self.assertEqual(manifest["reference_coverage"]["common_reference_case_count"], 3)
        self.assertEqual(manifest["reference_coverage"]["clean_whole_group_count"], 0)
        for member in manifest["members"]:
            design = study.verify(self.output / member["directory"])
            self.assertIn("reference-preflight/report.json", design["frozen_sha256"])
            self.assertIn("reference-preflight/coverage-summary.json", design["frozen_sha256"])
            self.assertEqual(design["planned_unique_questions"], 4)
        self.run_fixtures()
        summary = controls.analyze(self.output, require_complete=True)
        for model in summary["by_model"].values():
            for value in model["languages"].values():
                self.assertEqual((value["generated"], value["correct"]), (4, 4))
                self.assertEqual(value["common_reference_passed"]["correct"], 3)
                self.assertEqual(value["reference_passed_whole_batches"]["batches"], 0)
                self.assertIsNone(value["reference_passed_whole_batches"]["token_usage"])
                self.assertEqual(value["reference_preflight_failed_targets"], {"generated": 1, "correct": 1})

    def test_changed_saved_query_is_rejected_without_rescoring_or_model_calls(self):
        self.prepare()
        self.run_fixtures()
        path = self.output / "luna/runs/request-00000/report.json"
        report = controls.read(path)
        report["cases"][0]["query"] = "SELECT hacked FROM missing;"
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "Scored query changed"):
            controls.analyze(self.output, require_complete=True, rescore=False)

    def test_transport_recovery_leaves_full_and_clean_natsql_api_usage_unknown(self):
        self.prepare()
        self.run_fixtures()
        study_path = self.output / "luna"
        design = study.read(study_path / "design.json")
        request = design["requests"][0]
        folder = study_path / "runs" / request["id"]
        saved = study.read(folder / "response.json")
        journal = (json.dumps({"event": "request_started", "request_sha256": request["request_sha256"]}) + "\n").encode()
        (folder / "journal.jsonl").write_bytes(journal)
        plan = {"version": 1, "kind": "explicit_scale_transport_recovery_plan",
            "study_design_sha256": study.sha(study_path / "design.json"),
            "request_id": request["id"], "request_sha256": request["request_sha256"],
            "original_status": "transport_or_http_error_usage_unknown", "original_unknown_usage": True,
            "whole_study_api_token_usage": None, "authorization": "Explicit fixture transport-only recovery",
            "policy": "Retain unknown original send; no query feedback", "original_journal_bytes": len(journal),
            "original_journal_sha256": hashlib.sha256(journal).hexdigest()}
        study.write_new(folder / "transport-recovery-plan.json", plan)
        recovery = {**plan, "kind": "explicit_scale_transport_recovery",
            "plan_sha256": study.sha(folder / "transport-recovery-plan.json"),
            "response_sha256": study.digest(saved["response"]),
            "known_retained_response_usage": saved["response"]["usage"], "paid_call_count_for_frozen_request": 2}
        study.write_new(folder / "transport-recovery.json", recovery)
        saved["transport_recovery"] = {"manifest": "transport-recovery.json",
                                        "manifest_sha256": study.sha(folder / "transport-recovery.json")}
        (folder / "response.json").write_text(json.dumps(saved))
        summary = controls.analyze(self.output, require_complete=True, rescore=False)
        self.assertEqual(summary["new_natsql_unobserved_api_attempts"], 1)
        self.assertIsNone(summary["new_natsql_api_token_usage"])
        self.assertEqual(summary["known_new_natsql_response_token_usage"]["total_tokens"], 90)
        lunar = summary["by_model"]["gpt-6-luna"]["languages"]
        self.assertEqual(lunar["natsql"]["correct"], 4)
        self.assertEqual(lunar["natsql"]["known_response_token_usage"]["total_tokens"], 30)
        self.assertIsNone(lunar["natsql"]["token_usage"])
        self.assertIsNone(lunar["natsql"]["reference_passed_whole_batches"]["token_usage"])
        self.assertEqual(lunar["sql"]["token_usage"]["total_tokens"], 30)
        self.assertEqual(summary["by_model"]["gpt-6-astra"]["languages"]["natsql"]["token_usage"]["total_tokens"], 30)


if __name__ == "__main__":
    unittest.main()

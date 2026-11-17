"""Offline matrix/parity fixtures; no provider calls or research scores."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import scale_model_matrix as matrix
from tools.run_pilot import digest


class ScaleModelMatrixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "luna"
        self.source.mkdir()
        self.output = self.root / "matrix"
        self.ledger = self.root / "ledger"
        self.ledger.mkdir()
        matrix.write_new(self.ledger / "budget.json", {"authorized_micro_usd": 100_000_000,
                         "prior_upper_micro_usd": 1_000_000, "calls": []})
        files = {"inputs/supported/private.json": '{"private":"PRIVATE_GOLD_SENTINEL"}',
                 "inputs/supported/database.sqlite": "fixture data bytes",
                 "guides/sql.md": "SQL guide", "guides/sqlp.md": "SQLP guide",
                 "runtime/tools/run_scale_study.py": "# unchanged frozen execution fixture\n",
                 "sqlpc": "fixture executable bytes"}
        for name, content in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (self.source / "runs").mkdir()
        requests = []
        for i, language in enumerate(("sql", "sqlp")):
            payload = {"model": "gpt-6-luna", "max_output_tokens": 16384,
                       "reasoning": {"effort": "high"}, "instructions": language + " guide",
                       "store": False, "tools": [], "input": [{"role": "user", "content":
                           json.dumps({"tasks": [{"id": "q1", "question": "Return a name."},
                                                  {"id": "q2", "question": "Return absent names."}]})}]}
            requests.append({"id": "request-%05d" % i, "dataset_id": "supported/fixture",
                             "stratum": "supported", "language": language,
                             "case_ids": ["q1", "q2"], "payload": payload,
                             "request_sha256": digest(payload)})
        self.design = {"version": 1, "kind": "scale_textsql_study",
                       "settings": {"model": "gpt-6-luna", "max_output_tokens": 16384,
                                    "reasoning": {"effort": "high"}},
                       "batch_size": 4, "seed": 20261116, "supported_languages": ["sql", "sqlp"],
                       "datasets": [{"id": "supported/fixture", "stratum": "supported", "case_count": 2}],
                       "limits": {"max_rows": 10000, "public_max_rows": 100000,
                                  "sqlite_timeout_seconds": 5, "api_timeout_seconds": 180},
                       "planned_unique_questions": 2, "planned_calls": 2, "planned_outputs": 4,
                       "attempt_policy": "one first attempt", "requests": requests,
                       "budget_before": matrix.budget_summary(self.ledger),
                       "frozen_sha256": {name: matrix.sha(self.source / name) for name in files}}
        matrix.write_new(self.source / "design.json", self.design)
        self.saved_results(self.source, "gpt-6-luna")

    def saved_results(self, directory, model, *, known_usage=True):
        by_language = {}
        for i, language in enumerate(("sql", "sqlp")):
            usage = {"input_tokens": 10, "output_tokens": 5 if language == "sql" else 3,
                     "total_tokens": 15 if language == "sql" else 13}
            by_language["supported/" + language] = {"generated": 2, "scoreable": 2,
                    "correct": 1 if language == "sql" else 2, "gold_errors": 0,
                    "token_usage": usage if known_usage else None}
            path = directory / "runs" / ("request-%05d" % i) / "report.json"
            matrix.write_new(path, {"cases": [
                {"correct": language == "sqlp", "metadata": {"expected_empty": False}},
                {"correct": True, "metadata": {"expected_empty": True, "deliberate_empty_control": True}}]})
        matrix.write_new(directory / "summary.json", {"model": model, "completed": True,
                "planned_calls": 2, "completed_calls": 2, "generated_outputs": 4,
                "observed_unique_questions": 2, "by_language": by_language,
                "token_usage": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}
                               if known_usage else None,
                "breakdowns": {"depth": {"supported/sql/1": {"correct": 1}}},
                "supported_paired_correctness": {"complete_pairs": 2, "both_correct": 1,
                    "sql_only_correct": 0, "sqlp_only_correct": 1, "both_incorrect": 0}})

    def prepare(self):
        return matrix.prepare(self.source, self.output, ledger=self.ledger)

    def test_clones_change_only_models_and_digests_preserving_all_frozen_files(self):
        prepared = self.prepare()
        self.assertEqual((prepared["unique_questions"], prepared["planned_calls"],
                          prepared["planned_new_calls"], prepared["planned_outputs"]), (2, 6, 4, 12))
        for name, model in (("sol", "gpt-6.1-sol"), ("astra", "gpt-6-astra")):
            folder = self.output / name
            design = matrix.verify_model_parity(self.source, folder)
            self.assertEqual(design["settings"]["model"], model)
            self.assertEqual(design["frozen_sha256"], self.design["frozen_sha256"])
            self.assertFalse((folder / "summary.json").exists())
            self.assertEqual(list((folder / "runs").iterdir()), [])
            for before, after in zip(self.design["requests"], design["requests"]):
                self.assertEqual(before["payload"]["input"], after["payload"]["input"])
                self.assertNotEqual(before["request_sha256"], after["request_sha256"])
                self.assertNotIn("PRIVATE_GOLD_SENTINEL", json.dumps(after["payload"]))
        self.assertEqual(matrix.read(self.source / "design.json"), self.design)

    def test_invalid_models_and_existing_destination_do_not_replace_work(self):
        for models in (("gpt-6-luna",), ("unknown",), ("gpt-6-astra", "gpt-6-astra")):
            with self.assertRaises(ValueError):
                matrix.prepare(self.source, self.output, ledger=self.ledger, models=models)
            self.assertFalse(self.output.exists())
        self.prepare()
        original = (self.output / "matrix.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "new directory"):
            self.prepare()
        self.assertEqual((self.output / "matrix.json").read_bytes(), original)

    def test_tampered_query_or_settings_cannot_claim_model_only_parity(self):
        self.prepare()
        path = self.output / "sol/design.json"
        original = matrix.read(path)
        for field in ("instructions", "reasoning"):
            changed = deepcopy(original)
            changed["requests"][0]["payload"][field] = "changed"
            changed["requests"][0]["request_sha256"] = digest(changed["requests"][0]["payload"])
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "beyond its model"):
                matrix.verify_model_parity(self.source, path.parent)
        path.write_text(json.dumps(original))
        changed = deepcopy(original)
        changed["settings"]["reasoning"]["effort"] = "low"
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "settings"):
            matrix.verify_model_parity(self.source, path.parent)

    def test_changed_frozen_runtime_and_parent_design_are_rejected(self):
        self.prepare()
        runtime = self.output / "astra/runtime/tools/run_scale_study.py"
        runtime.write_text("changed")
        with self.assertRaisesRegex(ValueError, "Frozen input changed"):
            matrix.verify_matrix(self.output)
        path = self.source / "design.json"
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "Parent study design changed"):
            matrix.verify_matrix(self.output)

    def test_incomplete_matrix_reuses_luna_without_inventing_new_results(self):
        self.prepare()
        summary = matrix.aggregate(self.output)
        self.assertFalse(summary["completed"])
        self.assertEqual((summary["unique_questions"], summary["completed_calls"], summary["generated_outputs"]), (2, 2, 4))
        self.assertEqual(summary["models"]["gpt-6-astra"]["status"], "not_started")
        self.assertIsNone(summary["token_usage"])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            matrix.aggregate(self.output, require_complete=True)

    def test_complete_matrix_keeps_questions_distinct_from_model_repetitions(self):
        self.prepare()
        self.saved_results(self.output / "sol", "gpt-6.1-sol")
        self.saved_results(self.output / "astra", "gpt-6-astra")
        summary = matrix.aggregate(self.output, require_complete=True)
        self.assertTrue(summary["completed"])
        self.assertEqual((summary["unique_questions"], summary["observed_question_model_pairs"],
                          summary["completed_calls"], summary["generated_outputs"]), (2, 6, 6, 12))
        self.assertEqual(summary["token_usage"]["total_tokens"], 84)
        for model in matrix.MODELS:
            record = summary["models"][model]
            self.assertAlmostEqual(record["sqlp_total_token_change_percent"], -13.3333333333)
            self.assertEqual(record["controls"]["sql/nonempty"], {"generated": 1, "correct": 0})
            self.assertEqual(record["controls"]["sqlp/deliberate_empty_control"], {"generated": 1, "correct": 1})

    def test_unknown_usage_and_inconsistent_completed_coverage_stay_explicit(self):
        self.prepare()
        self.saved_results(self.output / "sol", "gpt-6.1-sol", known_usage=False)
        self.saved_results(self.output / "astra", "gpt-6-astra")
        summary = matrix.aggregate(self.output, require_complete=True)
        self.assertIsNone(summary["token_usage"])
        self.assertIsNone(summary["models"]["gpt-6.1-sol"]["sqlp_total_token_change_percent"])
        path = self.output / "sol/summary.json"
        changed = matrix.read(path)
        changed["generated_outputs"] = 3
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "incomplete coverage"):
            matrix.aggregate(self.output)

    def test_live_wrapper_uses_frozen_runtime_and_binds_guard_without_executing_it(self):
        self.prepare()
        with patch.object(matrix.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as execute:
            status = matrix.run_model(self.output, "gpt-6.1-sol", api_key_file=self.root / "key",
                                      ledger=self.ledger, workers=3, max_new_requests=2)
        self.assertEqual(status, 0)
        args, kwargs = execute.call_args
        self.assertEqual(kwargs["cwd"], self.output / "sol/runtime")
        self.assertIn(str(self.root / "key"), args[0])
        self.assertEqual(args[0][-2:], ["3", "2"])
        self.assertTrue(matrix.read(self.output / "sol/execution-budget.json")["frozen_runtime_unchanged"])
        with self.assertRaisesRegex(ValueError, "parent must be reused"):
            matrix.run_model(self.output, "gpt-6-luna", api_key_file=self.root / "key", ledger=self.ledger)


if __name__ == "__main__":
    unittest.main()

"""Prepared-query timing/text checks, with no model calls or implicit downloads."""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import benchmark_prepared as bench
from tools.validate_dataset import load_catalog


def workload():
    return {"version": 1, "scope": "development", "cases": [{
        "id": "authorships", "question": "List Canadian authorship names, including repeated records.",
        "column_count": 1,
        "sql": "SELECT a.name FROM WROTE w JOIN AUTHOR a ON w.author_aid=a.aid JOIN UNIVERSITY u ON a.uni_id=u.uid WHERE u.country='CA';",
        "sqlp": "SELECT w.author.name FROM WROTE w WHERE w.author.university.country='CA';",
        "expected_rows": [["Alice"], ["Alice"], ["Caro"]]}]}


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.database = self.folder / "academic.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript((ROOT / "examples/academic/data.sql").read_text())
        self.catalog = load_catalog(ROOT / "examples/academic/catalog.json")

    def benchmark(self, description=None, **options):
        options = {"repetitions": 2, "warmups": 1, **options}
        return bench.benchmark_prepared(self.database, self.catalog,
                                       workload() if description is None else description, **options)

    def test_actual_compilation_execution_raw_samples_and_explicit_origin(self):
        before = self.database.read_bytes()
        report = self.benchmark()
        self.assertTrue(report["ok"])
        self.assertEqual(report["origin"], "prepared_microbenchmark")
        self.assertEqual(report["timing_status"], "completed")
        self.assertEqual(len(report["samples"]), 4)
        self.assertEqual(len(report["warmup_samples"]), 2)
        self.assertEqual(report["summary"]["prepared_correct"], {"sql": 1, "sqlp": 1})
        self.assertIsNone(report["provider_token_usage"])
        self.assertIsNone(report["summary"]["static_visible_text_sum"])
        self.assertIsNone(report["summary"]["timings"]["sql"]["compile_subprocess"])
        self.assertEqual(report["cases"][0]["max_path_depth"], 2)
        self.assertEqual(report["cases"][0]["join_count"], 2)
        self.assertEqual(report["cases"][0]["validation"][0]["rows"],
                         report["cases"][0]["validation"][1]["rows"])
        for sample in report["samples"]:
            self.assertTrue(sample["correct"])
            self.assertGreater(sample["execute_ns"], 0)
            self.assertEqual(sample["row_count"], 3)
            if sample["language"] == "sqlp":
                self.assertGreater(sample["compile_subprocess_ns"], 0)
                self.assertEqual(sample["preprocess_and_execute_ns"],
                                 sample["execute_ns"] + sample["compile_subprocess_ns"])
        self.assertEqual(before, self.database.read_bytes())

    def test_failed_query_retains_both_conditions_and_skips_all_timing(self):
        description = workload()
        description["cases"][0]["sql"] = "SELECT missing FROM AUTHOR"
        report = self.benchmark(description)
        self.assertFalse(report["ok"])
        self.assertEqual(report["timing_status"], "skipped_validation_failed")
        self.assertEqual(report["samples"], [])
        self.assertEqual(report["warmup_samples"], [])
        self.assertIsNone(report["summary"]["timings"])
        runs = report["cases"][0]["validation"]
        self.assertFalse(runs[0]["correct"])
        self.assertTrue(runs[1]["correct"])
        self.assertEqual(runs[0]["error"]["stage"], "execute")

    def test_duplicate_counts_prevent_timing_acceptance(self):
        description = workload()
        description["cases"][0]["sql"] = description["cases"][0]["sql"].replace("SELECT", "SELECT DISTINCT", 1)
        report = self.benchmark(description)
        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"]["prepared_correct"], {"sql": 0, "sqlp": 1})
        self.assertIsNone(report["summary"]["timings"])

    def test_writes_rejected_without_mutating_source(self):
        description = workload()
        description["cases"][0]["sql"] = "DELETE FROM AUTHOR"
        before = self.database.read_bytes()
        report = self.benchmark(description)
        self.assertFalse(report["ok"])
        self.assertEqual(before, self.database.read_bytes())

    def test_timing_answer_drift_aborts_and_does_not_summarize_partial_winner(self):
        with patch.object(bench, "_execute", return_value=(["name"], [["wrong"]])):
            report = self.benchmark(warmups=0)
        self.assertEqual(report["timing_status"], "aborted")
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["samples"]), 1)
        self.assertEqual(report["samples"][0]["error"]["code"], "answer_drift")
        self.assertIsNone(report["summary"]["timings"])

    def test_repeated_compiler_output_drift_aborts(self):
        original = bench._compile

        def altered(*args):
            compiled, parameters = original(*args)
            compiled["sql"] += " -- changed"
            return compiled, parameters

        with patch.object(bench, "_compile", side_effect=altered):
            report = self.benchmark(warmups=0)
        self.assertEqual(report["timing_status"], "aborted")
        failed = [sample for sample in report["samples"] if "error" in sample]
        self.assertEqual(failed[0]["error"]["code"], "compiler_drift")
        self.assertIsNone(report["summary"]["timings"])

    def test_seeded_schedule_counterbalances_each_case_within_one_attempt(self):
        cases = [{"id": str(index)} for index in range(5)]
        first = bench._schedule(cases, 7, 2, 10)
        self.assertEqual(first, bench._schedule(cases, 7, 2, 10))
        self.assertNotEqual(first, bench._schedule(cases, 7, 2, 11))
        for case in cases:
            orders = [step["languages"][0] for step in first
                      if step["phase"] == "measure" and step["case_id"] == case["id"]]
            self.assertEqual(len(orders), 7)
            self.assertEqual(abs(orders.count("sql") - orders.count("sqlp")), 1)
        self.assertEqual(len(first), 45)

    def test_invalid_numeric_settings_rejected_before_execution(self):
        invalid = {"repetitions": [False, 0, 101, 1.5], "warmups": [True, -1, 21],
                   "seed": [True, -1, 2**63], "max_rows": [False, 0, 100001],
                   "timeout_seconds": [True, 0, -1, float("nan"), float("inf"), 301],
                   "preflight_timeout_seconds": [False, 0], "max_snapshot_bytes": [0, True]}
        with patch.object(bench, "_condition", side_effect=AssertionError("Should not execute")):
            for name, values in invalid.items():
                for value in values:
                    with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                        self.benchmark(**{name: value})

    def test_byte_lengths_are_utf8_and_null_tokens_remain_unknown(self):
        guides = {"sql": "SQL guide café", "sqlp": "SQLP guide café"}
        report = self.benchmark(guides=guides, repetitions=1, warmups=0)
        text = report["cases"][0]["text"]
        self.assertEqual(text["sql"]["guide_utf8_bytes"], len(guides["sql"].encode("utf-8")))
        self.assertGreater(text["sql"]["guide_utf8_bytes"], len(guides["sql"]))
        self.assertEqual(text["sql"]["task_utf8_bytes"], text["sqlp"]["task_utf8_bytes"])
        self.assertIsNone(text["sql"]["static_text_tokens"]["prepared_query"])
        self.assertIsNone(text["delta_sqlp_minus_sql"]["static_visible_text_sum"])
        with self.assertRaises(ValueError):
            self.benchmark(guides={"sql": "only one"})

    def test_tokenizer_never_downloads_and_rejects_wrong_local_hash(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("No implicit HTTP permitted")):
            encoding, metadata = bench.load_local_encoding(None)
            self.assertIsNone(encoding)
            self.assertFalse(metadata["available"])
            bad = self.folder / "vocab"
            bad.write_text("not the pinned vocabulary")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                bench.load_local_encoding(bad)
            self.assertTrue(self.benchmark(repetitions=1, warmups=0)["ok"])

    def test_optional_pinned_local_tokenizer_counts_text_without_http(self):
        path = ROOT / "resources/tokenizers/cl100k_base.tiktoken"
        if not path.exists():
            self.skipTest("Optional explicitly downloaded local vocabulary is absent")
        try:
            import tiktoken
        except ImportError:
            self.skipTest("Optional tiktoken package is absent")
        with patch("urllib.request.urlopen", side_effect=AssertionError("No HTTP permitted")):
            report = self.benchmark(encoding_path=path, repetitions=1, warmups=0)
        self.assertTrue(report["tokenizer"]["available"])
        self.assertEqual(report["tokenizer"]["vocabulary_sha256"], bench.VOCAB_SHA256)
        self.assertGreater(report["summary"]["static_query_tokens"]["sql"], 0)
        self.assertIsNone(report["provider_token_usage"])
        text = report["cases"][0]["text"]["sql"]
        self.assertEqual(text["static_visible_text_sum"], sum(text["static_text_tokens"].values()))

    def test_snapshot_size_cap_and_missing_compiler_are_setup_errors(self):
        with self.assertRaisesRegex(ValueError, "snapshot.*limit"):
            self.benchmark(max_snapshot_bytes=1)
        with self.assertRaisesRegex(ValueError, "Compiler executable unavailable"):
            self.benchmark(compiler_path=self.folder / "missing")

    def test_snapshot_digest_includes_committed_wal_pages(self):
        held = sqlite3.connect(self.database)
        self.addCleanup(held.close)
        held.execute("PRAGMA journal_mode=WAL")
        held.execute("PRAGMA wal_autocheckpoint=0")
        held.commit()
        first = self.benchmark(repetitions=1, warmups=0)
        disk_before = self.database.read_bytes()
        held.execute("INSERT INTO UNIVERSITY(uid,name,country) VALUES (900,'Extra','ZZ')")
        held.commit()
        self.assertEqual(disk_before, self.database.read_bytes())
        second = self.benchmark(repetitions=1, warmups=0)
        self.assertNotEqual(first["hashes"]["snapshot_sha256"], second["hashes"]["snapshot_sha256"])

    def test_held_out_label_is_preparation_not_new_model_evidence(self):
        description = workload()
        description["scope"] = "held_out"
        report = self.benchmark(description, repetitions=1, warmups=0)
        self.assertEqual(report["scope"], "held_out")
        self.assertEqual(report["origin"], "prepared_microbenchmark")
        self.assertIsNone(report["summary"]["provider_token_usage"])

    def test_cli_status_one_for_wrong_answers_two_for_setup(self):
        catalog, description = self.folder / "catalog.json", self.folder / "workload.json"
        catalog.write_text(json.dumps(self.catalog))
        task = workload()
        task["cases"][0]["expected_rows"] = []
        description.write_text(json.dumps(task))
        command = [sys.executable, "-m", "tools.benchmark_prepared", str(self.database), str(catalog),
                   str(description), "--repetitions", "1", "--warmups", "0"]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["timing_status"], "skipped_validation_failed")
        result = subprocess.run(command + ["--compiler", str(self.folder / "missing")],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["origin"], "prepared_microbenchmark")


if __name__ == "__main__":
    unittest.main()

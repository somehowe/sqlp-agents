"""Check paired analysis and evidence labels using offline reports only."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_runs import analyze_report, render_markdown
from tools.model_provider import parse_response
from tools.run_comparison import run_comparison
from tools.run_pilot import digest
from tools.validate_dataset import load_catalog


def condition(language, *, correct=True, error=None, usage=(20, 10), generation="completed"):
    query = "SELECT a.name FROM AUTHOR a;"
    raw = {"id": "offline_" + language, "model": "offline-model", "status":
           "incomplete" if generation == "incomplete" else "completed",
           "output": [{"type": "message", "role": "assistant", "content":
                       [{"type": "refusal", "refusal": "Declined"}] if generation == "refused" else
                       [{"type": "output_text", "text": " " if generation == "empty" else query}]}]}
    if usage is not None:
        raw["usage"] = {"input_tokens": usage[0], "output_tokens": usage[1], "total_tokens": sum(usage),
                        "input_tokens_details": {"cached_tokens": usage[0]},
                        "output_tokens_details": {"reasoning_tokens": usage[1]}}
    generated = parse_response(raw)
    if generation != "completed":
        error = {"stage": "generation", "code": generation}
    failed = error is not None
    return {"language": language, "query": generated["raw_text"], "correct": False if failed else correct,
            "status": "error" if failed else "ok", "error": error, "columns": None if failed else ["name"],
            "rows": None if failed else [[{"type": "string", "value": "Alice"}]],
            "token_usage": generated["usage"], "generation": generated}


def report(case_runs=None):
    if case_runs is None:
        case_runs = [(condition("sql"), condition("sqlp"))]
    result = {"version": 1, "origin": "replay", "scope": "development", "ok": True, "completed": True,
              "returned_model": "offline-model", "manifest": {"protocol": "first_attempt_v1",
              "settings": {"model": "offline-model", "max_output_tokens": 128},
              "planned_requests": 2 * len(case_runs)}, "requests": [], "cases": []}
    for index, runs in enumerate(case_runs):
        case_id = "task-" + str(index)
        for run in runs:
            payload = {"model": "offline-model", "max_output_tokens": 128,
                       "store": False, "tools": [], "truncation": "disabled",
                       "input": case_id + " " + run["language"]}
            run["request_sha256"] = digest(payload)
            result["requests"].append({"case_id": case_id, "language": run["language"],
                                      "payload": payload, "request_sha256": digest(payload)})
        result["cases"].append({"id": case_id, "column_count": 1, "runs": list(runs)})
    result["ok"] = all(run["correct"] for case in result["cases"] for run in case["runs"])
    return result


class AnalysisTests(unittest.TestCase):
    def test_real_prepared_fixture_is_summarized_as_manual_without_model_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fixture.sqlite"
            with sqlite3.connect(database) as connection:
                connection.executescript((ROOT / "examples/academic/data.sql").read_text())
            raw = run_comparison(database, load_catalog(ROOT / "examples/academic/catalog.json"),
                                 json.loads((ROOT / "examples/academic/workload.json").read_text()))
        summary = analyze_report(raw)
        self.assertEqual(summary["origin"], "manual")
        self.assertEqual(summary["case_count"], 6)
        self.assertEqual(summary["paired_correctness"]["both_correct"], 6)
        for language in ("sql", "sqlp"):
            self.assertIsNone(summary["conditions"][language]["token_usage"])
            self.assertEqual(summary["conditions"][language]["missing_usage_attempts"], 6)

    def test_pairs_are_matched_by_language_not_run_order_and_keep_all_outcomes(self):
        raw = report([
            (condition("sql"), condition("sqlp")),
            (condition("sqlp", correct=False), condition("sql")),
            (condition("sql", correct=False), condition("sqlp")),
            (condition("sqlp", correct=False), condition("sql", correct=False)),
        ])
        raw["summary"] = {"sql": "untrusted cached summary"}
        summary = analyze_report(raw)
        self.assertEqual(summary["paired_correctness"], {"both_correct": 1, "sql_only_correct": 1,
                                                        "sqlp_only_correct": 1, "neither_correct": 1})
        for language in ("sql", "sqlp"):
            self.assertEqual(summary["conditions"][language]["correct"], 2)
            self.assertEqual(summary["conditions"][language]["accuracy"], 0.5)
        self.assertEqual(summary["origin"], "replay")
        self.assertIn("recorded responses", " ".join(summary["notes"]))

    def test_failed_attempts_still_contribute_tokens_and_tokens_per_correct(self):
        summary = analyze_report(report([
            (condition("sql", usage=(10, 5)), condition("sqlp", usage=(6, 4))),
            (condition("sql", error={"stage": "execute", "code": "sqlite"}, usage=(10, 10)),
             condition("sqlp", error={"stage": "compile", "diagnostic": {}}, usage=(8, 7))),
        ]))
        sql, sqlp = summary["conditions"]["sql"], summary["conditions"]["sqlp"]
        self.assertEqual(sql["token_usage"], {"input_tokens": 20, "output_tokens": 15, "total_tokens": 35})
        self.assertEqual(sql["mean_total_tokens"], 17.5)
        self.assertEqual(sql["median_total_tokens"], 17.5)
        self.assertEqual(sql["tokens_per_correct"], 35)
        self.assertEqual(sqlp["tokens_per_correct"], 25)
        self.assertEqual(sql["errors_by_stage"], {"execute": 1})
        self.assertEqual(sqlp["errors_by_stage"], {"compile": 1})
        self.assertEqual(summary["paired_token_usage"]["known_pair_delta_sum"], -10)

    def test_mixed_unknown_usage_keeps_language_total_unknown_and_labels_known_pairs(self):
        summary = analyze_report(report([
            (condition("sql", usage=(10, 5)), condition("sqlp", usage=(4, 6))),
            (condition("sql", usage=None), condition("sqlp", usage=(8, 7))),
        ]))
        sql, sqlp = summary["conditions"]["sql"], summary["conditions"]["sqlp"]
        self.assertIsNone(sql["token_usage"])
        self.assertIsNone(sql["mean_total_tokens"])
        self.assertIsNone(sql["median_total_tokens"])
        self.assertIsNone(sql["tokens_per_correct"])
        self.assertEqual((sql["known_usage_attempts"], sql["missing_usage_attempts"]), (1, 1))
        self.assertEqual(sqlp["token_usage"]["total_tokens"], 25)
        paired = summary["paired_token_usage"]
        self.assertEqual((paired["known_pairs"], paired["missing_pairs"]), (1, 1))
        self.assertEqual(paired["known_pair_delta_sum"], -5)
        self.assertEqual(paired["per_case"][1]["sqlp_minus_sql_total_tokens"], None)
        self.assertEqual(paired["coverage"], "known_pairs_only")

    def test_partial_provider_usage_remains_unknown_without_fabricating_counts(self):
        raw = report()
        run = raw["cases"][0]["runs"][0]
        run["generation"]["raw_response"]["usage"] = {"input_tokens": 20}
        run["generation"]["usage"] = run["token_usage"] = None
        summary = analyze_report(raw)
        self.assertIsNone(summary["conditions"]["sql"]["token_usage"])
        self.assertIsNone(summary["paired_token_usage"]["known_pair_delta_sum"])

    def test_no_correct_answers_makes_tokens_per_correct_undefined_but_keeps_cost(self):
        summary = analyze_report(report([(condition("sql", correct=False), condition("sqlp", correct=False))]))
        for language in ("sql", "sqlp"):
            self.assertEqual(summary["conditions"][language]["token_usage"]["total_tokens"], 30)
            self.assertIsNone(summary["conditions"][language]["tokens_per_correct"])
        self.assertEqual(summary["paired_correctness"]["neither_correct"], 1)

    def test_known_zero_usage_is_zero_and_refusal_truncation_still_count(self):
        summary = analyze_report(report([(condition("sql", generation="refused", usage=(0, 0)),
                                          condition("sqlp", generation="incomplete", usage=(20, 10)))]))
        self.assertEqual(summary["conditions"]["sql"]["token_usage"]["total_tokens"], 0)
        for language in ("sql", "sqlp"):
            self.assertEqual(summary["conditions"][language]["attempts"], 1)
            self.assertEqual(summary["conditions"][language]["errors_by_stage"], {"generation": 1})
        self.assertEqual(summary["paired_token_usage"]["known_pair_delta_sum"], 30)

    def test_incomplete_setup_dry_run_and_empty_reports_are_rejected(self):
        variants = []
        for key, value in (("completed", False), ("origin", "dry_run"), ("cases", []),
                           ("error", {"stage": "compiler"}), ("version", True)):
            raw = report()
            raw[key] = value
            variants.append(raw)
        for raw in variants:
            with self.subTest(keys=raw.keys()), self.assertRaises(ValueError):
                analyze_report(raw)

    def test_duplicate_case_missing_arm_extra_attempt_and_wrong_request_pairs_are_rejected(self):
        variants = []
        raw = report()
        raw["cases"].append(deepcopy(raw["cases"][0]))
        variants.append(raw)
        for runs in ([condition("sql")], [condition("sql"), condition("sql")],
                     [condition("sql"), condition("sqlp"), condition("sql")]):
            raw = report()
            raw["cases"][0]["runs"] = runs
            variants.append(raw)
        raw = report()
        raw["requests"][0]["case_id"] = "unrelated"
        variants.append(raw)
        for raw in variants:
            with self.subTest(cases=len(raw["cases"])), self.assertRaises(ValueError):
                analyze_report(raw)

    def test_failed_condition_cannot_be_labeled_correct_or_return_scored_rows(self):
        for field, value in (("correct", True), ("rows", []), ("columns", ["name"]),
                             ("error", {"stage": "provider"}), ("error", {"stage": "compile"})):
            raw = report([(condition("sql", error={"stage": "execute"}), condition("sqlp"))])
            raw["cases"][0]["runs"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                analyze_report(raw)
        raw = report()
        raw["ok"] = False
        with self.assertRaises(ValueError):
            analyze_report(raw)

    def test_invalid_and_mismatched_token_usage_is_rejected(self):
        variants = [True, {"input_tokens": 2, "output_tokens": 3, "total_tokens": 4},
                    {"input_tokens": -1, "output_tokens": 2, "total_tokens": 1},
                    {"input_tokens": True, "output_tokens": 2, "total_tokens": 3},
                    {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}]
        for usage in variants:
            raw = report()
            raw["cases"][0]["runs"][0]["token_usage"] = usage
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                analyze_report(raw)

    def test_generation_query_model_status_or_request_hash_drift_is_rejected(self):
        variants = []
        for section, field, value in (("run", "query", "a different query"),
                                      ("generation", "model", "other-model"),
                                      ("generation", "status", "refused"),
                                      ("run", "request_sha256", "0" * 64)):
            raw = report()
            run = raw["cases"][0]["runs"][0]
            (run if section == "run" else run["generation"])[field] = value
            variants.append(raw)
        raw = report()
        raw["requests"][0]["payload"]["max_output_tokens"] = 256
        raw["requests"][0]["request_sha256"] = digest(raw["requests"][0]["payload"])
        variants.append(raw)
        for raw in variants:
            with self.subTest(), self.assertRaises(ValueError):
                analyze_report(raw)

    def test_manual_evidence_cannot_gain_model_tokens_by_adding_fields(self):
        raw = report()
        raw["origin"] = "manual"
        with self.assertRaises(ValueError):
            analyze_report(raw)
        for run in raw["cases"][0]["runs"]:
            run.pop("generation")
            run["token_usage"] = None
        summary = analyze_report(raw)
        self.assertIsNone(summary["requested_model"])
        self.assertIsNone(summary["returned_model"])

    def test_optional_settings_and_conversation_cannot_differ_in_one_condition(self):
        changes = [("temperature", 2), ("reasoning", {"effort": "high"}),
                   ("conversation", "some-prior-conversation"), ("previous_response_id", "prior-response"),
                   ("store", True), ("tools", [{"type": "web_search"}]), ("stream", True),
                   ("background", True), ("truncation", "auto")]
        for field, value in changes:
            raw = report()
            request = raw["requests"][0]
            request["payload"][field] = value
            request["request_sha256"] = digest(request["payload"])
            run = next(run for run in raw["cases"][0]["runs"] if run["language"] == request["language"])
            run["request_sha256"] = request["request_sha256"]
            with self.subTest(field=field), self.assertRaises(ValueError):
                analyze_report(raw)

    def test_correct_projection_width_and_success_error_consistency_are_checked(self):
        for action in ("width", "error", "rows"):
            raw = report()
            run = raw["cases"][0]["runs"][0]
            if action == "width":
                raw["cases"][0]["column_count"] = 2
            elif action == "error":
                run["error"] = {"stage": "execute"}
            else:
                run["rows"] = [[{"type": "string", "value": "Alice"}, {"type": "null", "value": None}]]
            with self.subTest(action=action), self.assertRaises(ValueError):
                analyze_report(raw)

    def test_markdown_labels_unknown_usage_and_escapes_returned_model(self):
        raw = report([(condition("sql", usage=None), condition("sqlp", usage=None))])
        name = "<script>alert(1)</script>|\nmodel ![image](https://example.invalid)"
        raw["returned_model"] = name
        for run in raw["cases"][0]["runs"]:
            run["generation"]["model"] = name
            run["generation"]["raw_response"]["model"] = name
        markdown = render_markdown(analyze_report(raw))
        self.assertNotIn("<script>", markdown)
        self.assertIn("&lt;script&gt;", markdown)
        self.assertIn("\\| model", markdown)
        self.assertNotIn("![image]", markdown)
        self.assertIn("\\!\\[image\\]", markdown)
        self.assertIn("unknown", markdown)
        self.assertIn("recorded responses", markdown)

    def test_cli_json_and_markdown_and_duplicate_key_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report()))
            base = [sys.executable, "-m", "tools.analyze_runs", str(path)]
            output = subprocess.run(base, cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(output.stdout)["paired_correctness"]["both_correct"], 1)
            output = subprocess.run([*base, "--markdown"], cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertIn("| SQLP | 1 / 1 |", output.stdout)
            path.write_text('{"version":1,"version":1}')
            output = subprocess.run(base, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(output.returncode, 2)
            self.assertEqual(json.loads(output.stdout)["error"]["stage"], "analysis")


if __name__ == "__main__":
    unittest.main()

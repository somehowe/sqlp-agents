"""Describe one completed paired SQL/SQLP report without rerunning any queries."""

import argparse
from collections import Counter
import html
import json
from pathlib import Path
import statistics
import sys

from .model_provider import ProviderError, parse_response
from .run_pilot import digest
from .validate_dataset import _unique_object

LANGUAGES = ("sql", "sqlp")
USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
SETTING_FIELDS = ("model", "max_output_tokens", "temperature", "reasoning")
MAX_REPORT_BYTES = 64 * 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _usage(value):
    if value is None:
        return None
    _require(isinstance(value, dict) and set(value) == set(USAGE_FIELDS),
             "Normalized usage needs input_tokens, output_tokens, and total_tokens")
    _require(all(type(value[key]) is int and 0 <= value[key] <= 2**63 - 1 for key in USAGE_FIELDS),
             "Token counts must be nonnegative int64 numbers")
    _require(value["input_tokens"] + value["output_tokens"] == value["total_tokens"],
             "Token counts are inconsistent")
    return value


def _validate_run(run, case, origin, returned_model, request):
    _require(isinstance(run, dict) and run.get("language") in LANGUAGES, "Invalid condition record")
    _require(type(run.get("correct")) is bool and run.get("status") in ("ok", "error"),
             "Each condition needs Boolean correctness and status ok|error")
    _require(isinstance(run.get("query"), str), "Each condition needs original query text")
    _require("token_usage" in run, "Missing normalized token usage; use null for unknown")
    usage = _usage(run["token_usage"])
    _require("error" in run, "Condition must explicitly contain its error or null")
    if run["status"] == "error":
        _require(not run["correct"] and run.get("columns") is None and run.get("rows") is None,
                 "A failed condition cannot have a scored answer")
        error = run.get("error")
        _require(isinstance(error, dict) and error.get("stage") in ("generation", "compile", "execute"),
                 "Condition error needs a generation, compile, or execute stage")
        _require(error["stage"] != "compile" or run["language"] == "sqlp", "Only SQLP has a compiler stage")
    else:
        _require(run.get("error") is None, "Successful execution cannot have an error")
        columns, rows = run.get("columns"), run.get("rows")
        _require(isinstance(columns, list) and columns and all(isinstance(c, str) for c in columns),
                 "Successful execution needs column names")
        _require(isinstance(rows, list) and all(isinstance(row, list) and len(row) == len(columns) for row in rows),
                 "Successful execution needs consistently shaped rows")
        if run["correct"]:
            _require(len(columns) == case["column_count"], "Correct answer has wrong projection width")
    if origin == "manual":
        _require(usage is None and "generation" not in run,
                 "Manual reports cannot contain model generation or token usage")
        _require(run.get("error", {}) is None or run["error"]["stage"] != "generation",
                 "Manual reports cannot have generation failures")
        return
    generated = run.get("generation")
    _require(isinstance(generated, dict) and "raw_response" in generated,
             "Pilot conditions need their saved provider response")
    try:
        parsed = parse_response(generated["raw_response"])
    except ProviderError as error:
        raise ValueError("Pilot contains an invalid provider response: " + str(error)) from error
    _require(parsed["status"] != "failed", "Provider failures cannot enter completed paired analysis")
    _require(all(generated.get(key) == parsed[key] for key in ("raw_text", "status", "model", "request_id", "usage")),
             "Saved generation fields do not match the raw provider response")
    _require(parsed["model"] == returned_model and run["query"] == parsed["raw_text"],
             "Condition model/query differs from its saved generation")
    _require(usage == parsed["usage"], "Condition usage differs from its provider response")
    _require(run.get("request_sha256") == request["request_sha256"], "Condition is not bound to its request")
    if parsed["status"] == "completed":
        _require(run.get("error") is None or run["error"]["stage"] != "generation",
                 "Completed generation cannot have a generation failure")
    else:
        _require(run["status"] == "error" and run["error"].get("stage") == "generation" and
                 run["error"].get("code") == parsed["status"], "Generation failure was scored or mislabeled")


def _pairs(report):
    _require(isinstance(report, dict) and type(report.get("version")) is int and report["version"] == 1,
             "Expected a version-1 report")
    origin = report.get("origin")
    _require(origin in ("manual", "replay", "live"), "Analyze completed manual, replay, or live reports only")
    _require(report.get("scope") in ("development", "held_out"), "Report needs an explicit development|held_out scope")
    _require(report.get("error") is None and type(report.get("ok")) is bool,
             "Setup failures cannot enter paired analysis")
    cases = report.get("cases")
    _require(isinstance(cases, list) and cases, "A report needs nonempty completed cases")
    requests = {}
    if origin != "manual":
        _require(report.get("completed") is True, "Incomplete pilots cannot enter paired analysis")
        manifest = report.get("manifest")
        _require(isinstance(manifest, dict) and manifest.get("protocol") == "first_attempt_v1",
                 "Only the first-attempt pilot protocol is supported")
        _require(type(manifest.get("planned_requests")) is int and manifest["planned_requests"] == 2 * len(cases),
                 "Planned request count differs from completed pairs")
        settings = manifest.get("settings")
        _require(isinstance(settings, dict) and not set(settings) - set(SETTING_FIELDS) and
                 _nonempty(settings.get("model")) and type(settings.get("max_output_tokens")) is int and
                 settings["max_output_tokens"] > 0, "Missing or unsupported requested model settings")
        _require(_nonempty(report.get("returned_model")), "Missing returned model identifier")
        planned = report.get("requests")
        _require(isinstance(planned, list) and len(planned) == 2 * len(cases), "Missing planned requests")
        for request in planned:
            _require(isinstance(request, dict) and _nonempty(request.get("case_id")) and
                     request.get("language") in LANGUAGES and isinstance(request.get("payload"), dict),
                     "Malformed planned request")
            identity = request["case_id"], request["language"]
            _require(identity not in requests, "Duplicate request condition")
            _require(request.get("request_sha256") == digest(request["payload"]), "Request payload hash differs")
            payload = request["payload"]
            _require(digest({key: payload[key] for key in SETTING_FIELDS if key in payload}) == digest(settings),
                     "Condition settings differ from manifest settings")
            _require(payload.get("store") is False and payload.get("tools") == [] and
                     payload.get("truncation") == "disabled" and
                     payload.get("stream", False) is False and payload.get("background", False) is False and
                     "conversation" not in payload and "previous_response_id" not in payload,
                     "First-attempt requests must be synchronous, stateless, and tool-free")
            requests[identity] = request
    elif "completed" in report:
        _require(report["completed"] is True, "Incomplete manual reports cannot enter analysis")
    seen, pairs = set(), []
    for case in cases:
        _require(isinstance(case, dict) and _nonempty(case.get("id")) and case["id"] not in seen,
                 "Case identifiers must be nonempty and unique")
        seen.add(case["id"])
        _require(type(case.get("column_count")) is int and case["column_count"] > 0,
                 "Each case needs a positive projection width")
        runs = case.get("runs")
        _require(isinstance(runs, list) and len(runs) == 2, "Each case needs exactly one SQL and one SQLP attempt")
        mapped = {}
        for run in runs:
            _require(isinstance(run, dict) and run.get("language") in LANGUAGES and run["language"] not in mapped,
                     "Each case needs distinct SQL and SQLP conditions")
            language = run["language"]
            request = requests.get((case["id"], language))
            _require(origin == "manual" or request is not None, "No matching request for condition")
            _validate_run(run, case, origin, report.get("returned_model"), request)
            mapped[language] = run
        pairs.append((case["id"], mapped))
    _require(not requests or set(requests) == {(case_id, language) for case_id in seen for language in LANGUAGES},
             "Planned requests do not match analyzed case identifiers")
    _require(report["ok"] == all(run["correct"] for _, pair in pairs for run in pair.values()),
             "Root correctness flag differs from condition records")
    return pairs


def analyze_report(report):
    """Recompute descriptive metrics from one complete batch, retaining failures.

    Correctness comes from the saved oracle labels, not agreement between arms.
    This does not rerun queries, independently review gold, or establish any
    causal/statistical claim. Never combine different reports implicitly.
    """
    pairs = _pairs(report)
    counts = {"both_correct": 0, "sql_only_correct": 0, "sqlp_only_correct": 0, "neither_correct": 0}
    differences = []
    for case_id, pair in pairs:
        sql, sqlp = pair["sql"], pair["sqlp"]
        category = ("both_correct" if sql["correct"] and sqlp["correct"] else
                    "sql_only_correct" if sql["correct"] else
                    "sqlp_only_correct" if sqlp["correct"] else "neither_correct")
        counts[category] += 1
        known = sql["token_usage"] is not None and sqlp["token_usage"] is not None
        differences.append({"case_id": case_id, "sqlp_minus_sql_total_tokens":
                            sqlp["token_usage"]["total_tokens"] - sql["token_usage"]["total_tokens"] if known else None})
    conditions = {}
    for language in LANGUAGES:
        runs = [pair[language] for _, pair in pairs]
        correct = sum(run["correct"] for run in runs)
        usage = [run["token_usage"] for run in runs if run["token_usage"] is not None]
        all_known = len(usage) == len(runs)
        totals = {field: sum(value[field] for value in usage) for field in USAGE_FIELDS} if all_known else None
        tokens = [value["total_tokens"] for value in usage]
        conditions[language] = {"attempts": len(runs), "correct": correct, "accuracy": correct / len(runs),
                                "execution_ok": sum(run["status"] == "ok" for run in runs),
                                "errors_by_stage": dict(sorted(Counter(run["error"]["stage"] for run in runs
                                                                        if run["status"] == "error").items())),
                                "known_usage_attempts": len(usage), "missing_usage_attempts": len(runs) - len(usage),
                                "token_usage": totals, "mean_total_tokens": statistics.mean(tokens) if all_known else None,
                                "median_total_tokens": statistics.median(tokens) if all_known else None,
                                "tokens_per_correct": totals["total_tokens"] / correct if all_known and correct else None}
    known_deltas = [item["sqlp_minus_sql_total_tokens"] for item in differences if item["sqlp_minus_sql_total_tokens"] is not None]
    return {"version": 1, "kind": "paired_run_analysis", "origin": report["origin"], "scope": report["scope"],
            "protocol": "prepared_pairs_v1" if report["origin"] == "manual" else "first_attempt_v1",
            "case_count": len(pairs), "requested_model": None if report["origin"] == "manual" else report["manifest"]["settings"]["model"],
            "returned_model": None if report["origin"] == "manual" else report["returned_model"], "paired_correctness": counts, "conditions": conditions,
            "paired_token_usage": {"known_pairs": len(known_deltas), "missing_pairs": len(pairs) - len(known_deltas),
                                   "known_pair_delta_sum": sum(known_deltas) if known_deltas else None,
                                   "known_pair_delta_mean": statistics.mean(known_deltas) if known_deltas else None,
                                   "known_pair_delta_median": statistics.median(known_deltas) if known_deltas else None,
                                   "coverage": "known_pairs_only", "per_case": differences},
            "notes": ["Descriptive metrics for this single saved batch; correctness uses its saved oracle labels.",
                      "No statistical significance, generalization, API spending, or human-productivity claim is inferred.",
                      "Replay token figures describe recorded responses, not new billed calls." if report["origin"] == "replay" else
                      "Manual development reports have no model token evidence." if report["origin"] == "manual" else
                      "All scored first attempts, including generation and execution failures, remain in denominators."]}


def render_markdown(summary):
    """Render an analysis produced by analyze_report as a compact table."""
    def cell(value):
        if value is None:
            return "unknown"
        escaped = html.escape(str(value)).replace("\r", " ").replace("\n", " ")
        for character in "\\`*_[]!|":
            escaped = escaped.replace(character, "\\" + character)
        return escaped

    lines = ["# Paired run summary", "", "Origin: **" + cell(summary["origin"]) + "**. Scope: **" + cell(summary["scope"]) + "**.",
             "Cases: " + str(summary["case_count"]) + ". One attempt per condition and case.", ""]
    if summary["returned_model"] is not None:
        lines.extend(["Returned model: " + cell(summary["returned_model"]) + ".", ""])
    lines.extend(["| Condition | Correct / attempts | Accuracy | Total tokens | Mean tokens | Median tokens | Tokens per correct |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for language in LANGUAGES:
        condition = summary["conditions"][language]
        tokens = condition["token_usage"]
        values = [language.upper(), str(condition["correct"]) + " / " + str(condition["attempts"]),
                  format(100 * condition["accuracy"], ".1f") + "%", tokens["total_tokens"] if tokens is not None else None,
                  condition["mean_total_tokens"], condition["median_total_tokens"], condition["tokens_per_correct"]]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    paired = summary["paired_correctness"]
    lines.extend(["", "Paired correctness: both correct " + str(paired["both_correct"]) +
                  "; SQL only " + str(paired["sql_only_correct"]) + "; SQLP only " + str(paired["sqlp_only_correct"]) +
                  "; neither " + str(paired["neither_correct"]) + "."])
    usage = summary["paired_token_usage"]
    lines.extend(["", "Paired token coverage: " + str(usage["known_pairs"]) + " known, " + str(usage["missing_pairs"]) + " unknown.",
                  "SQLP minus SQL token sum over known pairs only: " + cell(usage["known_pair_delta_sum"]) + ".", ""])
    for language in LANGUAGES:
        condition = summary["conditions"][language]
        stages = condition["errors_by_stage"]
        lines.append(language.upper() + " errors by stage: " + (", ".join(stage + "=" + str(count) for stage, count in stages.items()) or "none") + ". " +
                     "Usage known for " + str(condition["known_usage_attempts"]) + "/" + str(condition["attempts"]) + " attempts.")
    lines.extend(["", *summary["notes"], ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--markdown", action="store_true", help="Print a descriptive Markdown table instead of JSON")
    args = parser.parse_args()
    try:
        with args.report.open("rb") as source:
            encoded = source.read(MAX_REPORT_BYTES + 1)
        _require(len(encoded) <= MAX_REPORT_BYTES, "Report exceeds the 64 MiB analysis limit")
        def reject_constant(value):
            raise ValueError("Nonfinite JSON value: " + value)
        report = json.loads(encoded, object_pairs_hook=_unique_object, parse_constant=reject_constant)
        summary = analyze_report(report)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
        print(json.dumps({"version": 1, "ok": False, "error": {"stage": "analysis", "message": str(error)}}, allow_nan=False))
        return 2
    print(render_markdown(summary) if args.markdown else json.dumps(summary, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Rescore saved generated queries on supplied alternate valid SQLite snapshots.

This performs local execution only. It neither calls a model nor assigns new
provider tokens, and passing these instances is not a SQL-equivalence proof.
"""

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

from .analyze_runs import analyze_report
from .batch_pilot import parse_batch_queries
from .compiler_catalog import catalog_argument_file
from .model_provider import ProviderError, parse_response
from .run_comparison import (DEFAULT_COMPILER, SQLITE_BYTES, SetupError,
                             _check_workload, _condition, _rows)
from .run_pilot import LANGUAGES, _public_catalog, _settings, _snapshot_hash, digest
from .validate_dataset import PreflightTimeoutError, _unique_object, validate_dataset

MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_CASES = 256
MAX_SNAPSHOTS = 8


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _batch_bindings(report):
    """Validate source batch/query associations without allocating usage."""
    requests, batches = report.get("requests"), report.get("batches")
    _require(isinstance(requests, list) and requests and isinstance(batches, list),
             "Batch source needs request and generation arrays")
    settings = _settings(report["manifest"]["settings"])
    requests_by_hash = {}
    for request in requests:
        _require(isinstance(request, dict) and isinstance(request.get("payload"), dict),
                 "Invalid source batch request")
        key = digest(request["payload"])
        _require(key == request.get("request_sha256") and key not in requests_by_hash,
                 "Changed or duplicate source batch request")
        payload = request["payload"]
        _require(payload.get("store") is False and payload.get("tools") == [] and
                 payload.get("truncation") == "disabled" and
                 "previous_response_id" not in payload and "conversation" not in payload,
                 "Source batch requests must be stateless and tool-free")
        actual_settings = {key: payload[key] for key in ("model", "max_output_tokens", "temperature", "reasoning")
                           if key in payload}
        _require(actual_settings == settings, "Source batch settings differ from manifest")
        requests_by_hash[key] = request
    _require(len(batches) == len(requests), "Source batch generations do not cover requests")
    bindings, seen = {}, set()
    for batch in batches:
        _require(isinstance(batch, dict), "Invalid source batch generation")
        key = batch.get("request_sha256")
        _require(key in requests_by_hash and key not in seen, "Unbound or duplicate batch generation")
        seen.add(key)
        request = requests_by_hash[key]
        for field in ("batch_id", "language", "case_ids"):
            _require(batch.get(field) == request.get(field), "Batch grouping differs from source request")
        ids, language = request.get("case_ids"), request.get("language")
        _require(language in LANGUAGES and isinstance(ids, list) and ids and
                 all(isinstance(value, str) and value for value in ids) and len(ids) == len(set(ids)),
                 "Invalid source batch grouping")
        generated = batch.get("generation")
        _require(isinstance(generated, dict) and "raw_response" in generated,
                 "Source batch needs original provider response")
        parsed = parse_response(generated["raw_response"])
        _require(parsed["status"] != "failed" and parsed["model"] == report.get("returned_model"),
                 "Source batch failed or changed returned model")
        _require(all(generated.get(field) == parsed[field] for field in
                     ("model", "request_id", "raw_text", "status", "usage")),
                 "Source batch generation differs from raw response")
        extracted = (parse_batch_queries(parsed["raw_text"], ids) if parsed["status"] == "completed"
                     else {"queries": {}, "errors": {value: parsed["status"] for value in ids}})
        for case_id in ids:
            identity = case_id, language
            _require(identity not in bindings, "Task appears in multiple source batches")
            bindings[identity] = {"request_sha256": key, "response_id": parsed["request_id"],
                                   "model": parsed["model"], "batch_id": batch["batch_id"],
                                   "query": extracted["queries"].get(case_id),
                                   "extraction_error": extracted["errors"].get(case_id)}
    return bindings


def _source_cases(report):
    _require(isinstance(report, dict) and type(report.get("version")) is int and report["version"] == 1 and
             report.get("completed") is True and type(report.get("ok")) is bool,
             "Source must be a completed version-1 pilot report")
    _require(report.get("origin") in ("live", "replay") and
             report.get("scope") in ("development", "held_out"),
             "Source must retain live/replay origin and workload scope")
    manifest = report.get("manifest")
    _require(isinstance(manifest, dict), "Source needs its experiment manifest")
    batch = report.get("kind") == "batch_pilot"
    if batch:
        bindings = _batch_bindings(report)
    else:
        analyze_report(report)  # Existing native protocol/settings/response validation.
        bindings = {}
    cases = report.get("cases")
    _require(isinstance(cases, list) and 1 <= len(cases) <= MAX_CASES,
             "Source case count must be from one to 256")
    result = {}
    for case in cases:
        _require(isinstance(case, dict), "Source cases must be objects")
        identifier, question, width = case.get("id"), case.get("question"), case.get("column_count")
        _require(isinstance(identifier, str) and identifier and identifier not in result and
                 isinstance(question, str) and question and type(width) is int and width > 0,
                 "Invalid or duplicate source case")
        runs = case.get("runs")
        _require(isinstance(runs, list) and len(runs) == 2 and
                 all(isinstance(run, dict) for run in runs) and
                 {run.get("language") for run in runs} == set(LANGUAGES),
                 "Every source case needs exactly one SQL and one SQLP attempt")
        for run in runs:
            _require(type(run.get("correct")) is bool and run.get("status") in ("ok", "error") and
                     isinstance(run.get("query"), str), "Invalid source attempt")
            _require(run["status"] == "ok" or run["correct"] is False,
                     "Failed source attempt cannot be correct")
            if run["status"] == "error":
                _require(isinstance(run.get("error"), dict) and
                         run["error"].get("stage") in ("generation", "compile", "execute") and
                         run.get("rows") is None and run.get("columns") is None,
                         "Failed source attempt needs an error and no answer")
            else:
                columns, rows = run.get("columns"), run.get("rows")
                _require(run.get("error") is None and isinstance(columns, list) and columns and
                         all(isinstance(value, str) for value in columns) and isinstance(rows, list) and
                         all(isinstance(row, list) and len(row) == len(columns) for row in rows),
                         "Executed source attempt needs consistent columns and rows")
                _require(not run["correct"] or len(columns) == width,
                         "Correct source attempt has wrong projection width")
            if batch:
                binding = bindings.get((identifier, run["language"]))
                _require(binding is not None and binding["request_sha256"] == run.get("request_sha256") and
                         binding["batch_id"] == run.get("batch_id"), "Unbound source task attempt")
                if binding["query"] is None:
                    _require(run["status"] == "error" and run["query"] == "" and
                             isinstance(run.get("error"), dict) and
                             run["error"].get("stage") == "generation" and
                             run["error"].get("code") == binding["extraction_error"],
                             "Failed batch extraction was not preserved")
                else:
                    _require(run["query"] == binding["query"], "Source query differs from batch output")
            else:
                generated = run["generation"]
                bindings[identifier, run["language"]] = {
                    "request_sha256": run["request_sha256"], "response_id": generated["request_id"],
                    "model": generated["model"], "batch_id": None}
        result[identifier] = case
    _require(set(bindings) == {(identifier, language) for identifier in result for language in LANGUAGES},
             "Source batches contain extra or missing tasks")
    _require(report["ok"] == all(run["correct"] for case in result.values() for run in case["runs"]),
             "Source correctness flag differs from attempts")
    for request in report["requests"]:
        payload = request["payload"]
        messages = payload.get("input")
        _require(isinstance(messages, list) and len(messages) == 1 and
                 isinstance(messages[0], dict) and messages[0].get("role") == "user" and
                 isinstance(messages[0].get("content"), str), "Invalid source public task payload")
        public = json.loads(messages[0]["content"], object_pairs_hook=_unique_object)
        _require(isinstance(public, dict) and digest(public.get("schema_and_relationships")) ==
                 manifest.get("public_catalog_sha256"), "Source request catalog differs from manifest")
        if batch:
            tasks = public.get("tasks")
            _require(set(public) == {"tasks", "schema_and_relationships"} and isinstance(tasks, list) and
                     all(isinstance(task, dict) and set(task) == {"id", "question"} for task in tasks) and
                     [task["id"] for task in tasks] == request["case_ids"] and
                     all(task["id"] in result and task["question"] == result[task["id"]]["question"]
                         for task in tasks), "Source batch questions differ from case records")
        else:
            _require(set(public) == {"question", "schema_and_relationships"} and
                     public["question"] == result[request["case_id"]]["question"],
                     "Source request question differs from case record")
    return result, bindings


def _skip(run):
    return {"language": run["language"], "query": run["query"], "sql": None,
            "parameters": [], "trace": None, "status": "skipped", "correct": False,
            "columns": None, "rows": None, "token_usage": None,
            "error": {"stage": "source_attempt", "code": "original_attempt_failed"},
            "original_error": run.get("error")}


def evaluate_snapshots(report, catalog, snapshots, compiler_path=DEFAULT_COMPILER, *,
                       max_rows=1000, timeout_seconds=5.0, preflight_timeout_seconds=30.0,
                       max_snapshot_bytes=64 * 1024 * 1024):
    """Execute the same saved query text on alternate supplied row-bag oracles.

    ``snapshots`` maps names to ``{"database_path": Path, "workload": dict}``.
    Case IDs, questions, widths, and catalog facts must match the source. Original
    error-status attempts are never rerun; wrong but executable queries are.
    """
    try:
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()
        _require(len(encoded) <= MAX_REPORT_BYTES, "Source report exceeds 64 MiB")
        report = json.loads(encoded)
        catalog = json.loads(json.dumps(catalog, allow_nan=False))
        cases, bindings = _source_cases(report)
        _require(type(max_rows) is int and 1 <= max_rows < sys.maxsize, "Invalid max_rows")
        _require(type(max_snapshot_bytes) is int and max_snapshot_bytes > 0, "Invalid snapshot limit")
        for limit in (timeout_seconds, preflight_timeout_seconds):
            _require(type(limit) in (int, float) and math.isfinite(limit) and limit > 0,
                     "Timeouts must be finite and positive")
        public_hash = digest(_public_catalog(catalog))
        _require(report["manifest"].get("public_catalog_sha256") == public_hash,
                 "Supplied catalog facts differ from original model condition")
        _require(isinstance(snapshots, dict) and 1 <= len(snapshots) <= MAX_SNAPSHOTS,
                 "Supply from one to eight alternate snapshots")
        alternate = {}
        for name, snapshot in snapshots.items():
            _require(isinstance(name, str) and name.strip() and isinstance(snapshot, dict) and
                     set(snapshot) == {"database_path", "workload"}, "Invalid named snapshot input")
            workload = json.loads(json.dumps(snapshot["workload"], allow_nan=False))
            _check_workload(workload, max_rows)
            _require(workload["scope"] == report["scope"], "Snapshot scope differs from source")
            actual = {case["id"]: case for case in workload["cases"]}
            _require(set(actual) == set(cases), "Snapshot task IDs differ from source")
            for identifier, case in actual.items():
                _require(case["question"] == cases[identifier]["question"] and
                         case["column_count"] == cases[identifier]["column_count"],
                         "Snapshot question or projection width differs from source")
            alternate[name] = {"database_path": Path(snapshot["database_path"]).resolve(),
                               "workload": workload, "cases": actual}
    except (ValueError, TypeError, KeyError, OverflowError, ProviderError) as error:
        raise SetupError("snapshot_input", str(error)) from error

    original_compiler = Path(compiler_path).resolve()
    if not original_compiler.is_file() or not os.access(original_compiler, os.X_OK):
        raise SetupError("compiler", "Compiler executable unavailable; run make first")
    result = {"version": 1, "kind": "cross_snapshot_scoring", "origin": "local_rescoring",
              "scope": report["scope"], "completed": False,
              "source": {"report_sha256": hashlib.sha256(encoded).hexdigest(),
                         "report_origin": report["origin"], "report_kind": report.get("kind", "first_attempt"),
                         "returned_model": report["returned_model"], "manifest": report["manifest"]},
              "catalog_sha256": digest(catalog), "public_catalog_sha256": public_hash,
              "limits": {"max_rows": max_rows, "timeout_seconds": timeout_seconds,
                         "preflight_timeout_seconds": preflight_timeout_seconds,
                         "max_snapshot_bytes": max_snapshot_bytes}, "snapshots": {}, "cases": [],
              "notes": ["Same generated source text; local execution only, no new model call or usage.",
                        "Original correctness labels are preserved, not re-reviewed.",
                        "Passing supplied snapshots is not general semantic equivalence.",
                        "Alternate snapshots are not additional independent natural-language tasks."]}
    for identifier, case in cases.items():
        runs = []
        for run in case["runs"]:
            binding = bindings[identifier, run["language"]]
            runs.append({"language": run["language"], "query": run["query"],
                         "query_sha256": hashlib.sha256(run["query"].encode()).hexdigest(),
                         "source_correct": run["correct"], "source_status": run["status"],
                         "source_error": run.get("error"), "source_rows": run.get("rows"),
                         "source_generation": {key: binding[key] for key in
                                               ("request_sha256", "response_id", "model", "batch_id")},
                         "snapshots": {}, "robust_on_tested_snapshots": run["correct"]})
        result["cases"].append({"id": identifier, "question": case["question"],
                                "column_count": case["column_count"], "runs": runs})
    outputs = {case["id"]: {run["language"]: run for run in case["runs"]}
               for case in result["cases"]}
    try:
        with tempfile.TemporaryDirectory(prefix="sqlp-rescore-") as temporary:
            compiler = Path(temporary) / "sqlpc"
            compiler_hash = hashlib.sha256(original_compiler.read_bytes()).hexdigest()
            shutil.copyfile(original_compiler, compiler)
            compiler.chmod(0o500)
            if (hashlib.sha256(compiler.read_bytes()).hexdigest() != compiler_hash or
                    hashlib.sha256(original_compiler.read_bytes()).hexdigest() != compiler_hash):
                raise SetupError("compiler", "Compiler changed while freezing rescoring artifact")
            if report["manifest"].get("compiler_sha256") != compiler_hash:
                raise SetupError("compiler", "Rescoring must use the original compiler binary")
            result["compiler_sha256"] = compiler_hash
            with catalog_argument_file(catalog) as arguments:
                for name, snapshot in alternate.items():
                    with closing(sqlite3.connect(snapshot["database_path"].as_uri() + "?mode=ro", uri=True,
                                                 cached_statements=0)) as connection:
                        connection.execute("BEGIN")
                        try:
                            preflight = validate_dataset(connection, catalog,
                                                         timeout_seconds=preflight_timeout_seconds)
                        except PreflightTimeoutError as error:
                            raise SetupError("preflight_timeout", str(error)) from error
                        if not preflight["ok"]:
                            raise SetupError("preflight", "Alternate snapshot failed catalog checks", preflight)
                        result["snapshots"][name] = {
                            "data_snapshot_sha256": _snapshot_hash(connection, max_snapshot_bytes),
                            "workload_sha256": digest(snapshot["workload"]), "preflight": preflight}
                        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQLITE_BYTES)
                        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQLITE_BYTES)
                        for identifier, case in cases.items():
                            expected = snapshot["cases"][identifier]
                            for run in case["runs"]:
                                local = (_skip(run) if run["status"] == "error" else _condition(
                                    connection, {**expected, run["language"]: run["query"]}, run["language"],
                                    arguments, compiler, max_rows, timeout_seconds))
                                local["expected_rows"] = _rows(expected["expected_rows"])
                                local["token_usage"] = None
                                target = outputs[identifier][run["language"]]
                                target["snapshots"][name] = local
                                target["robust_on_tested_snapshots"] &= local["correct"]
                                if not connection.in_transaction:
                                    raise SetupError("database", "Read snapshot ended before rescoring completed")
            result["summary"] = {}
            for language in LANGUAGES:
                runs = [outputs[key][language] for key in outputs]
                result["summary"][language] = {
                    "attempts": len(runs), "source_correct": sum(run["source_correct"] for run in runs),
                    "skipped_original_failures": sum(run["source_status"] == "error" for run in runs),
                    "correct_by_snapshot": {name: sum(run["snapshots"][name]["correct"] for run in runs)
                                            for name in alternate},
                    "robust_on_tested_snapshots": sum(run["robust_on_tested_snapshots"] for run in runs)}
            result["completed"] = True
            result["ok"] = all(run["robust_on_tested_snapshots"]
                               for case in result["cases"] for run in case["runs"])
            return result
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        if isinstance(error, SetupError):
            raise
        raise SetupError("snapshot_scoring", str(error)) from error

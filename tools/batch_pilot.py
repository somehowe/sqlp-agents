"""Compare SQL/SQLP generation with shared context across a batch of questions.

Provider usage belongs to an entire batch response. It is never copied into
each extracted query or presented as measured per-question reasoning usage.
"""

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sqlite3
import sys
import tempfile
import time

from .compiler_catalog import catalog_argument_file
from .model_provider import ProviderError, parse_response
from .run_comparison import (DEFAULT_COMPILER, SQLITE_BYTES, SetupError,
                             _check_workload, _condition, _rows)
from .run_pilot import (LANGUAGES, ReplayProvider, _Journal, _public_catalog,
                        _settings, _snapshot_hash, digest, run_pilot)
from .validate_dataset import _unique_object

_FORMAT = """Batch output contract (replaces the guide's single-query output format):
Answer every supplied task independently using the language guide below.
Return only one JSON array, with exactly one object per supplied task ID.
Each object must have exactly two fields: {"id": "supplied ID", "query": "query text"}.
Each query must be a complete statement ending in a semicolon. Preserve every
task ID exactly. Do not add Markdown, explanations, or additional fields.
"""
_REMINDER = "\n\nFinal output format: the JSON array specified in the batch output contract."


def build_batch_request(tasks, catalog, language, guide, settings):
    """Allowlist public IDs/questions; never serialize evaluation cases."""
    if language not in LANGUAGES:
        raise ValueError("Language must be sql or sqlp")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Batch requires at least one public task")
    public, seen = [], set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Each task must be an object")
        item = {key: task.get(key) for key in ("id", "question")}
        for value in item.values():
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Tasks need nonempty ID and question text")
            value.encode("utf-8")
        if item["id"] in seen:
            raise ValueError("Duplicate task ID")
        seen.add(item["id"])
        public.append(item)
    if not isinstance(guide, str) or not guide.strip():
        raise ValueError("Guide must be nonempty text")
    guide.encode("utf-8")
    data = {"tasks": public, "schema_and_relationships": _public_catalog(catalog)}
    return {**_settings(settings), "store": False, "tools": [], "truncation": "disabled",
            "instructions": _FORMAT + "\nLanguage guide:\n" + guide + _REMINDER,
            "input": [{"role": "user", "content": json.dumps(data, ensure_ascii=True,
                                                                    sort_keys=True, allow_nan=False)}]}


def _reject_constant(_):
    raise ValueError("Nonfinite JSON value")


def parse_batch_queries(text, case_ids):
    """Strict JSON extraction, with missing/duplicate outputs scored as failures.

    Invalid JSON, malformed entries, and unknown IDs invalidate the whole batch.
    Missing or duplicate known IDs invalidate those tasks; other unambiguous
    entries remain scoreable. Reordering a valid array is permitted by its IDs.
    """
    expected = set(case_ids)
    if not expected or len(expected) != len(case_ids):
        raise ValueError("Expected task IDs must be nonempty and unique")
    def invalid(code):
        return {"queries": {}, "errors": {key: code for key in case_ids},
                "protocol_errors": [code]}
    if not isinstance(text, str):
        return invalid("invalid_batch_json")
    try:
        values = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError):
        return invalid("invalid_batch_json")
    if not isinstance(values, list):
        return invalid("invalid_batch_shape")
    by_id = {}
    for entry in values:
        if (not isinstance(entry, dict) or set(entry) != {"id", "query"} or
                not isinstance(entry["id"], str) or not isinstance(entry["query"], str) or
                not entry["query"].strip()):
            return invalid("invalid_batch_shape")
        if entry["id"] not in expected:
            return invalid("unknown_batch_id")
        try:
            entry["query"].encode("utf-8")
        except UnicodeError:
            return invalid("invalid_batch_shape")
        by_id.setdefault(entry["id"], []).append(entry["query"])
    queries, errors = {}, {}
    for key in case_ids:
        items = by_id.get(key, [])
        if len(items) == 1:
            queries[key] = items[0]
        else:
            errors[key] = "missing_batch_id" if not items else "duplicate_batch_id"
    return {"queries": queries, "errors": errors,
            "protocol_errors": sorted(set(errors.values()))}


def _failed(language, code):
    return {"language": language, "query": "", "sql": None, "parameters": [],
            "trace": None, "status": "error", "correct": False, "columns": None,
            "rows": None, "error": {"stage": "generation", "code": code},
            "token_usage": None}


def summarize_batch_report(report):
    """Describe scored tasks and whole-batch usage; no per-query allocation."""
    result = {}
    for language in LANGUAGES:
        runs = [run for case in report["cases"] for run in case["runs"]
                if run["language"] == language]
        batches = [batch for batch in report["batches"] if batch["language"] == language]
        known = [batch["generation"]["usage"] for batch in batches
                 if batch["generation"]["usage"] is not None]
        complete_usage = bool(batches) and len(known) == len(batches)
        usage = ({key: sum(value[key] for value in known)
                  for key in ("input_tokens", "output_tokens", "total_tokens")}
                 if complete_usage else None)
        result[language] = {"attempts": len(runs), "correct": sum(run["correct"] for run in runs),
                            "batches": len(batches), "usage_known_batches": len(known),
                            "token_usage": usage,
                            "amortized_batch_tokens_per_question":
                                usage["total_tokens"] / len(runs) if usage and runs else None}
    return result


def run_batch_pilot(database_path, catalog, workload, compiler_path=DEFAULT_COMPILER, *,
                    guides, settings, batch_size=4, origin="dry_run", provider=None,
                    journal_path=None, max_requests=0, seed=0, max_rows=1000,
                    timeout_seconds=5.0, preflight_timeout_seconds=30.0,
                    max_snapshot_bytes=64 * 1024 * 1024):
    """Plan or execute paired batches on one private, preflighted snapshot.

    Use batch_size=1 as the JSON-output control. The original first-attempt
    protocol returns raw query text and is a different response-format condition.
    Transport/protocol-provider failures abort; generated output-format errors
    remain failed task attempts. The caller supplies any paid provider/budget.
    """
    try:
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be an integer from one to 1000")
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        if origin not in ("dry_run", "replay", "live"):
            raise ValueError("Origin must be dry_run, replay, or live")
        if not isinstance(guides, dict) or set(guides) != set(LANGUAGES):
            raise ValueError("Supply corresponding SQL and SQLP guides")
        if type(max_rows) is not int or not 1 <= max_rows < sys.maxsize:
            raise ValueError("max_rows must be a positive integer below sys.maxsize")
        for limit in (timeout_seconds, preflight_timeout_seconds):
            if type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0:
                raise ValueError("Timeouts must be finite and positive")
        if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0:
            raise ValueError("Snapshot limit must be positive")
        _check_workload(workload, max_rows)
        # Freeze caller-owned configuration before exposing requests to adapters.
        catalog, workload, guides, settings = json.loads(json.dumps(
            [catalog, workload, guides, _settings(settings)], allow_nan=False))
        groups = [workload["cases"][start:start + batch_size]
                  for start in range(0, len(workload["cases"]), batch_size)]
        requests, rng = [], random.Random(seed)
        for index, group in enumerate(groups):
            order = list(LANGUAGES)
            rng.shuffle(order)
            for language in order:
                payload = build_batch_request(group, catalog, language, guides[language], settings)
                requests.append({"batch_id": "batch-" + str(index + 1),
                                 "case_ids": [case["id"] for case in group], "language": language,
                                 "request_sha256": digest(payload), "payload": payload})
        if origin == "dry_run":
            if provider is not None or journal_path is not None:
                raise ValueError("Dry runs accept neither provider nor journal")
        elif (not callable(provider) or journal_path is None or type(max_requests) is not int or
              max_requests < len(requests)):
            raise ValueError("Execution needs provider, fresh journal, and a sufficient request cap")
        if origin == "replay":
            if not isinstance(provider, ReplayProvider):
                raise ValueError("Replay origin requires ReplayProvider")
            provider.validate_requests(requests)
        elif isinstance(provider, ReplayProvider):
            raise ValueError("ReplayProvider must be labeled replay")
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise SetupError("plan", str(error)) from error

    journal = None
    try:
        with tempfile.TemporaryDirectory(prefix="sqlp-batch-") as temporary:
            directory = Path(temporary)
            database, compiler = directory / "data.sqlite", directory / "sqlpc"
            original_compiler = Path(compiler_path).resolve()
            if not original_compiler.is_file() or not os.access(original_compiler, os.X_OK):
                raise SetupError("compiler", "Compiler executable unavailable; run make first")
            compiler_hash = hashlib.sha256(original_compiler.read_bytes()).hexdigest()
            shutil.copyfile(original_compiler, compiler)
            if (hashlib.sha256(compiler.read_bytes()).hexdigest() != compiler_hash or
                    hashlib.sha256(original_compiler.read_bytes()).hexdigest() != compiler_hash):
                raise SetupError("compiler", "Compiler changed while freezing batch inputs")
            compiler.chmod(0o500)
            with closing(sqlite3.connect(Path(database_path).resolve().as_uri() + "?mode=ro",
                                         uri=True, cached_statements=0)) as source:
                source.execute("BEGIN")
                source_hash = _snapshot_hash(source, max_snapshot_bytes)
                deadline = time.monotonic() + preflight_timeout_seconds
                def progress(_status, _remaining, _total):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Batch snapshot copy timed out")
                with closing(sqlite3.connect(database)) as destination:
                    source.backup(destination, pages=128, progress=progress, sleep=0.0)
            database.chmod(0o400)
            # Reuse the established full preflight, compiler smoke query, source
            # hashes, and resource checks on the exact private execution inputs.
            checked = run_pilot(database, catalog, workload, compiler, guides=guides,
                                settings=settings, seed=seed, max_rows=max_rows,
                                timeout_seconds=timeout_seconds,
                                preflight_timeout_seconds=preflight_timeout_seconds,
                                max_snapshot_bytes=max_snapshot_bytes)
            manifest = {**checked["manifest"], "protocol": "batch_first_attempt_v1",
                        "source_read_snapshot_sha256": source_hash, "batch_size": batch_size,
                        "grouping": [[case["id"] for case in group] for group in groups],
                        "planned_requests": len(requests), "usage_accounting": "whole_batch_only",
                        "batch_instructions_sha256": {language: hashlib.sha256(
                            build_batch_request(groups[0], catalog, language, guides[language], settings)
                            ["instructions"].encode()).hexdigest() for language in LANGUAGES}}
            manifest["limits"]["max_requests"] = max_requests
            report = {"version": 1, "kind": "batch_pilot", "origin": origin, "scope": workload["scope"],
                      "ok": True, "completed": False, "manifest": manifest,
                      "preflight": checked["preflight"], "requests": requests, "batches": [], "cases": []}
            if origin == "dry_run":
                report.update(completed=True, summary=summarize_batch_report(report))
                return report
            journal = _Journal(journal_path)
            journal.event("manifest", version=1, report_kind="batch_pilot", origin=origin,
                          scope=workload["scope"], manifest=manifest)
            report["cases"] = [{"id": case["id"], "question": case["question"],
                                 "column_count": case["column_count"],
                                 "expected_rows": _rows(case["expected_rows"]), "runs": []}
                                for case in workload["cases"]]
            cases = {case["id"]: case for case in workload["cases"]}
            outputs = {case["id"]: case for case in report["cases"]}
            returned_model = None
            with catalog_argument_file(catalog) as arguments, closing(sqlite3.connect(
                    database.resolve().as_uri() + "?mode=ro", uri=True, cached_statements=0)) as connection:
                connection.execute("BEGIN")
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQLITE_BYTES)
                connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQLITE_BYTES)
                for request in requests:
                    journal.event("request_started", **request)
                    started = time.monotonic()
                    try:
                        raw = provider(json.loads(json.dumps(request["payload"], allow_nan=False)))
                        elapsed = time.monotonic() - started
                        journal.event("response_received", batch_id=request["batch_id"],
                                      language=request["language"], request_sha256=request["request_sha256"],
                                      generation_seconds=elapsed, response=raw)
                        generated = parse_response(raw)
                        if generated["status"] == "failed":
                            raise ProviderError("Provider reported failed or cancelled response", raw_response=raw)
                        if returned_model is not None and generated["model"] != returned_model:
                            raise ProviderError("Returned model changed within the paired batch pilot", raw_response=raw)
                        returned_model = generated["model"]
                    except ProviderError as error:
                        journal.event("infrastructure_error", stage="provider", message=str(error),
                                      request_sha256=request["request_sha256"], raw_response=error.raw_response)
                        raise SetupError("provider", str(error)) from error
                    parsed = (parse_batch_queries(generated["raw_text"], request["case_ids"])
                              if generated["status"] == "completed" else
                              {"queries": {}, "errors": {key: generated["status"] for key in request["case_ids"]},
                               "protocol_errors": [generated["status"]]})
                    batch = {"batch_id": request["batch_id"], "case_ids": request["case_ids"],
                             "language": request["language"], "request_sha256": request["request_sha256"],
                             "generation": generated, "generation_seconds": elapsed,
                             "protocol_errors": parsed["protocol_errors"], "extraction_errors": parsed["errors"]}
                    report["batches"].append(batch)
                    for key in request["case_ids"]:
                        language = request["language"]
                        if key in parsed["errors"]:
                            record = _failed(language, parsed["errors"][key])
                        else:
                            record = _condition(connection, {**cases[key], language: parsed["queries"][key]},
                                                language, arguments, compiler, max_rows, timeout_seconds)
                        record.update(batch_id=request["batch_id"], request_sha256=request["request_sha256"],
                                      token_usage=None, usage_accounting="whole_batch_only")
                        outputs[key]["runs"].append(record)
                        journal.event("run_scored", case_id=key, run=record)
                        if not connection.in_transaction:
                            raise SetupError("database", "Read snapshot ended before batch pilot completed")
            report.update(completed=True, returned_model=returned_model,
                          ok=all(run["correct"] for case in report["cases"] for run in case["runs"]),
                          summary=summarize_batch_report(report))
            journal.event("completed", ok=report["ok"], summary=report["summary"])
            return report
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        if isinstance(error, SetupError):
            raise
        raise SetupError("batch_pilot", str(error)) from error
    finally:
        if journal is not None:
            journal.close()

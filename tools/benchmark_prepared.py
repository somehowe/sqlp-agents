"""Offline prepared-query microbenchmarks, never model generation or API usage."""

import argparse
import base64
from collections import Counter
from contextlib import ExitStack, closing
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

from .compiler_catalog import catalog_argument_file
from .run_comparison import (DEFAULT_COMPILER, SQLITE_BYTES, QueryError, SetupError,
                             _check_workload, _compile, _condition, _execute)
from .run_pilot import build_request, digest
from .validate_dataset import (PreflightTimeoutError, _unique_object, compiler_arguments,
                               validate_dataset)


ROOT = Path(__file__).resolve().parents[1]
VOCAB_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
VOCAB_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
MAX_INPUT_BYTES = 64 * 1024 * 1024
PATTERN = r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"


def _bytes(text):
    return len(text.encode("utf-8"))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")


def _seconds(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 300:
        raise ValueError(name + " must be finite and in (0, 300]")


def load_local_encoding(path):
    """Load an explicit, hash-pinned local vocabulary; never fetch or use a cache."""
    if path is None:
        return None, {"available": False, "name": None, "reason": "No local vocabulary supplied"}
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Tokenizer vocabulary must be a local file at most 5 MiB")
    data = path.read_bytes()
    if _sha(data) != VOCAB_SHA256:
        raise ValueError("Local cl100k_base vocabulary SHA-256 mismatch")
    try:
        import tiktoken
    except ImportError as error:
        raise ValueError("Optional local tokenizer requires an installed tiktoken package") from error
    ranks = {}
    try:
        for line in data.splitlines():
            token, rank = line.split()
            decoded = base64.b64decode(token, validate=True)
            if decoded in ranks:
                raise ValueError("Duplicate vocabulary token")
            ranks[decoded] = int(rank)
        if len(ranks) != 100256 or set(ranks.values()) != set(range(100256)):
            raise ValueError("Unexpected vocabulary ranks")
    except (ValueError, TypeError) as error:
        raise ValueError("Malformed local cl100k_base vocabulary") from error
    encoding = tiktoken.Encoding(
        name="cl100k_base_local", pat_str=PATTERN, mergeable_ranks=ranks,
        special_tokens={"<|endoftext|>": 100257, "<|fim_prefix|>": 100258,
                        "<|fim_middle|>": 100259, "<|fim_suffix|>": 100260,
                        "<|endofprompt|>": 100276})
    return encoding, {"available": True, "name": "cl100k_base", "vocabulary_sha256": VOCAB_SHA256,
                      "source_url": VOCAB_URL, "tiktoken_version": importlib.metadata.version("tiktoken"),
                      "ordinary_text_only": True,
                      "excludes": ["API framing", "model reasoning", "unobserved model output", "retries"]}


def _tokens(encoding, text):
    return None if encoding is None else len(encoding.encode_ordinary(text))


def _stats(values):
    if not values:
        return None
    ordered = sorted(values)
    return {"count": len(values), "minimum_ns": min(values), "median_ns": statistics.median(values),
            "mean_ns": statistics.mean(values), "p95_ns": ordered[math.ceil(.95 * len(ordered)) - 1],
            "maximum_ns": max(values)}


def _text_metrics(case, catalog, guides, encoding):
    result = {}
    for language in ("sql", "sqlp"):
        request = build_request(case["question"], catalog, language, guides[language],
                                {"model": "static-text-count-only", "max_output_tokens": 1})
        guide, task, query = request["instructions"], request["input"][0]["content"], case[language]
        counted = {"guide": _tokens(encoding, guide), "task": _tokens(encoding, task),
                   "prepared_query": _tokens(encoding, query)}
        result[language] = {"source_utf8_bytes": _bytes(query), "source_characters": len(query),
                            "guide_utf8_bytes": _bytes(guide), "task_utf8_bytes": _bytes(task),
                            "static_text_tokens": counted,
                            "static_visible_text_sum": None if encoding is None else sum(counted.values()),
                            "request_sha256": digest(request)}
    sql, sqlp = result["sql"], result["sqlp"]
    result["delta_sqlp_minus_sql"] = {
        "source_utf8_bytes": sqlp["source_utf8_bytes"] - sql["source_utf8_bytes"],
        "guide_utf8_bytes": sqlp["guide_utf8_bytes"] - sql["guide_utf8_bytes"],
        "static_query_tokens": None if encoding is None else (
            sqlp["static_text_tokens"]["prepared_query"] - sql["static_text_tokens"]["prepared_query"]),
        "static_guide_tokens": None if encoding is None else (
            sqlp["static_text_tokens"]["guide"] - sql["static_text_tokens"]["guide"]),
        "static_visible_text_sum": None if encoding is None else (
            sqlp["static_visible_text_sum"] - sql["static_visible_text_sum"])}
    return result


def _schedule(cases, repetitions, warmups, seed):
    rng, rows = random.Random(seed), []
    offsets = {case["id"]: rng.randrange(2) for case in cases}
    for phase, count in (("warmup", warmups), ("measure", repetitions)):
        for iteration in range(count):
            order = list(cases)
            rng.shuffle(order)
            for case in order:
                languages = ["sql", "sqlp"]
                if (iteration + offsets[case["id"]]) % 2:
                    languages.reverse()
                rows.append({"phase": phase, "iteration": iteration,
                             "case_id": case["id"], "languages": languages})
    return rows


def _summary(report):
    cases, samples = report["cases"], report["samples"]
    result = {"case_count": len(cases), "prepared_correct": {
        language: sum(run["correct"] for case in cases for run in case["validation"]
                      if run["language"] == language) for language in ("sql", "sqlp")},
              "timings": None, "provider_token_usage": None}
    result["source_utf8_bytes"] = {language: sum(case["text"][language]["source_utf8_bytes"] for case in cases)
                                   for language in ("sql", "sqlp")}
    baseline, path = result["source_utf8_bytes"]["sql"], result["source_utf8_bytes"]["sqlp"]
    result["source_byte_change_fraction"] = None if baseline == 0 else (path - baseline) / baseline
    if report["tokenizer"]["available"]:
        result["static_visible_text_sum"] = {
            language: sum(case["text"][language]["static_visible_text_sum"] for case in cases)
            for language in ("sql", "sqlp")}
        result["static_query_tokens"] = {
            language: sum(case["text"][language]["static_text_tokens"]["prepared_query"] for case in cases)
            for language in ("sql", "sqlp")}
        result["static_guide_tokens_per_call"] = {
            language: cases[0]["text"][language]["static_text_tokens"]["guide"] for language in ("sql", "sqlp")}
    else:
        result["static_visible_text_sum"] = result["static_query_tokens"] = result["static_guide_tokens_per_call"] = None
    if report["timing_status"] == "completed":
        result["timings"] = {language: {
            "execute": _stats([sample["execute_ns"] for sample in samples if sample["language"] == language]),
            "compile_subprocess": _stats([sample["compile_subprocess_ns"] for sample in samples
                                           if sample["language"] == language and sample["compile_subprocess_ns"] is not None]),
            "preprocess_and_execute": _stats([sample["preprocess_and_execute_ns"] for sample in samples
                                               if sample["language"] == language])}
            for language in ("sql", "sqlp")}
    grouped = {}
    for case in cases:
        depth = str(case["max_path_depth"])
        group = grouped.setdefault(depth, {"cases": 0, "source_byte_delta": 0,
                                          "static_query_token_delta": 0 if report["tokenizer"]["available"] else None,
                                          "static_visible_token_delta": 0 if report["tokenizer"]["available"] else None})
        group["cases"] += 1
        delta = case["text"]["delta_sqlp_minus_sql"]
        group["source_byte_delta"] += delta["source_utf8_bytes"]
        if report["tokenizer"]["available"]:
            group["static_query_token_delta"] += delta["static_query_tokens"]
            group["static_visible_token_delta"] += delta["static_visible_text_sum"]
    result["by_max_path_depth"] = grouped
    return result


def benchmark_prepared(database_path, catalog, workload, compiler_path=DEFAULT_COMPILER, *,
                       repetitions=7, warmups=1, seed=20261101, max_rows=1000,
                       timeout_seconds=5.0, preflight_timeout_seconds=30.0,
                       encoding_path=None, max_snapshot_bytes=MAX_INPUT_BYTES, guides=None):
    """Validate every pair before timing; return complete raw samples and honest origins."""
    _integer(repetitions, "repetitions", 1, 100)
    _integer(warmups, "warmups", 0, 20)
    _integer(seed, "seed", 0, 2**63 - 1)
    _integer(max_rows, "max_rows", 1, 100000)
    _integer(max_snapshot_bytes, "max_snapshot_bytes", 1, 256 * 1024 * 1024)
    _seconds(timeout_seconds, "timeout_seconds")
    _seconds(preflight_timeout_seconds, "preflight_timeout_seconds")
    _check_workload(workload, max_rows)
    if len(workload["cases"]) > 1000:
        raise ValueError("At most 1000 prepared cases are supported")
    compiler_arguments(catalog)
    if guides is None:
        guides = {language: (ROOT / "prompts" / (language + ".md")).read_text(encoding="utf-8")
                  for language in ("sql", "sqlp")}
    if not isinstance(guides, dict) or set(guides) != {"sql", "sqlp"}:
        raise ValueError("Guides must contain exactly sql and sqlp")
    for guide in guides.values():
        if not isinstance(guide, str) or not guide.strip() or _bytes(guide) > SQLITE_BYTES:
            raise ValueError("Guides must be nonempty UTF-8 text within the size limit")
    encoding, tokenizer = load_local_encoding(encoding_path)
    compiler = Path(compiler_path).resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise SetupError("compiler", "Compiler executable unavailable; run make first")
    report = {"version": 1, "origin": "prepared_microbenchmark", "scope": workload["scope"],
              "ok": False, "timing_status": "skipped_validation_failed", "provider_token_usage": None,
              "repetitions": repetitions, "warmups": warmups, "seed": seed, "tokenizer": tokenizer,
              "limits": {"max_rows": max_rows, "timeout_seconds": timeout_seconds,
                         "preflight_timeout_seconds": preflight_timeout_seconds, "sqlite_bytes": SQLITE_BYTES,
                         "max_snapshot_bytes": max_snapshot_bytes},
              "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                              "platform": platform.platform(), "machine": platform.machine(),
                              "clock": "perf_counter_ns", "sqlite_statement_cache": 0,
                              "cache_protocol": "warm process/page cache; fresh SQL preparation, not cold I/O",
                              "compile_protocol": "new compiler subprocess including startup/IPC/JSON validation; catalog transport prepared once",
                              "execute_protocol": "same read transaction; execute plus bounded fetch; answer checking outside measured interval"},
              "hashes": {"catalog_sha256": digest(catalog), "workload_sha256": digest(workload),
                         "guides_sha256": {language: _sha(guide.encode("utf-8")) for language, guide in guides.items()}},
              "cases": [], "schedule": [], "warmup_samples": [], "samples": [], "error": None}
    report["hashes"]["sources_sha256"] = {str(path.relative_to(ROOT)): _sha(path.read_bytes()) for path in (
        Path(__file__), ROOT / "tools/run_comparison.py", ROOT / "tools/run_pilot.py",
        ROOT / "tools/validate_dataset.py", ROOT / "tools/compiler_catalog.py")}
    by_id = {case["id"]: case for case in workload["cases"]}
    try:
        with ExitStack() as stack:
            temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="sqlp-microbench-")))
            frozen_compiler = temporary / "sqlpc"
            shutil.copy2(compiler, frozen_compiler)
            frozen_compiler.chmod(0o700)
            report["hashes"]["compiler_sha256"] = _sha(frozen_compiler.read_bytes())
            arguments = stack.enter_context(catalog_argument_file(catalog))
            uri = Path(database_path).resolve().as_uri() + "?mode=ro"
            connection = stack.enter_context(closing(sqlite3.connect(uri, uri=True, cached_statements=0)))
            connection.execute("BEGIN")
            preflight = validate_dataset(connection, catalog, timeout_seconds=preflight_timeout_seconds)
            report["preflight"] = preflight
            if not preflight["ok"]:
                raise SetupError("preflight", "Dataset failed preflight", preflight)
            size = connection.execute("PRAGMA main.page_count").fetchone()[0] * connection.execute("PRAGMA main.page_size").fetchone()[0]
            if size > max_snapshot_bytes:
                raise SetupError("database", "Database snapshot exceeds the benchmark byte limit")
            snapshot = connection.serialize()
            if len(snapshot) > max_snapshot_bytes:
                raise SetupError("database", "Serialized snapshot exceeds the benchmark byte limit")
            report["hashes"]["snapshot_sha256"] = _sha(snapshot)
            report["snapshot_bytes"] = len(snapshot)
            del snapshot
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQLITE_BYTES)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQLITE_BYTES)
            validated = {}
            for case in workload["cases"]:
                runs = [_condition(connection, case, language, arguments, frozen_compiler, max_rows, timeout_seconds)
                        for language in ("sql", "sqlp")]
                path_run = runs[1]
                trace = path_run["trace"]
                item = {"id": case["id"], "question": case["question"], "validation": runs,
                        "text": _text_metrics(case, catalog, guides, encoding),
                        "join_count": None if trace is None else len(trace["joins"]),
                        "max_path_depth": None if trace is None else max(
                            (len(path["join_ids"]) for path in trace["paths"]), default=0)}
                report["cases"].append(item)
                validated[case["id"]] = {run["language"]: run for run in runs}
            if not all(run["correct"] for case in report["cases"] for run in case["validation"]):
                report["summary"] = _summary(report)
                return report
            report["timing_status"] = "running"
            report["schedule"] = _schedule(workload["cases"], repetitions, warmups, seed)
            for step in report["schedule"]:
                case = by_id[step["case_id"]]
                for language in step["languages"]:
                    sample = {"case_id": case["id"], "language": language, "iteration": step["iteration"],
                              "compile_subprocess_ns": None, "execute_ns": None,
                              "preprocess_and_execute_ns": None, "correct": False}
                    collection = report["warmup_samples"] if step["phase"] == "warmup" else report["samples"]
                    collection.append(sample)
                    expected = validated[case["id"]][language]
                    sql, parameters = case["sql"], []
                    try:
                        if language == "sqlp":
                            started = time.perf_counter_ns()
                            compiled, parameters = _compile(case[language], arguments, frozen_compiler, timeout_seconds)
                            sample["compile_subprocess_ns"] = time.perf_counter_ns() - started
                            if not compiled["ok"] or any(compiled.get(field) != expected.get(field)
                                                        for field in ("sql", "parameters", "trace")):
                                raise QueryError("compiler_drift", "Repeated compilation differs from validated output")
                            sql = compiled["sql"]
                        started = time.perf_counter_ns()
                        columns, rows = _execute(connection, sql, parameters, max_rows, timeout_seconds)
                        sample["execute_ns"] = time.perf_counter_ns() - started
                        sample["preprocess_and_execute_ns"] = sample["execute_ns"] + (sample["compile_subprocess_ns"] or 0)
                        sample["row_count"] = len(rows)
                        sample["correct"] = len(columns) == case["column_count"] and Counter(map(tuple, rows)) == Counter(map(tuple, case["expected_rows"]))
                        if not sample["correct"]:
                            raise QueryError("answer_drift", "Timed query no longer matches the expected row bag")
                        if not connection.in_transaction:
                            raise QueryError("snapshot_ended", "Read snapshot ended during benchmarking")
                    except (QueryError, SetupError) as error:
                        sample["error"] = {"code": getattr(error, "code", getattr(error, "stage", "timing")),
                                           "message": str(error)}
                        report["timing_status"] = "aborted"
                        report["error"] = {"stage": "timing", "case_id": case["id"], "language": language,
                                           "message": str(error)}
                        report["summary"] = _summary(report)
                        return report
            report["ok"] = True
            report["timing_status"] = "completed"
    except PreflightTimeoutError as error:
        raise SetupError("preflight_timeout", str(error)) from error
    except sqlite3.Error as error:
        raise SetupError("database", str(error)) from error
    report["summary"] = _summary(report)
    return report


def _load(path):
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("JSON input exceeds 64 MiB")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("workload", type=Path)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--max-rows", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--preflight-timeout", type=float, default=30.0)
    parser.add_argument("--encoding", type=Path, help="explicit local hash-pinned cl100k_base vocabulary; never downloaded")
    args = parser.parse_args()
    try:
        report = benchmark_prepared(args.database, _load(args.catalog), _load(args.workload), args.compiler,
                                    repetitions=args.repetitions, warmups=args.warmups, seed=args.seed,
                                    max_rows=args.max_rows, timeout_seconds=args.timeout,
                                    preflight_timeout_seconds=args.preflight_timeout, encoding_path=args.encoding)
    except (ValueError, OSError) as error:
        report = {"version": 1, "ok": False, "origin": "prepared_microbenchmark", "cases": [],
                  "timing_status": "setup_error", "provider_token_usage": None,
                  "error": {"stage": getattr(error, "stage", "setup"), "message": str(error)}}
        print(json.dumps(report, allow_nan=False))
        return 2
    print(json.dumps(report, allow_nan=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

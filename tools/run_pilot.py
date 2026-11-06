"""Plan or replay paired first-attempt model queries; live calls are opt-in."""

import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from .compiler_catalog import catalog_argument_file
from .credentials import load_api_key
from .model_provider import OpenAIResponsesClient, ProviderError, parse_response
from .run_comparison import (DEFAULT_COMPILER, SQLITE_BYTES, SetupError,
                             _check_workload, _compile, _condition, _rows)
from .validate_dataset import (PreflightTimeoutError, _unique_object,
                               compiler_arguments, load_catalog, validate_dataset)

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("sql", "sqlp")
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def digest(value):
    """Hash exactly the canonical JSON payload retained in the record."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public_catalog(catalog):
    # Deliberately do not copy descriptions or arbitrary extra metadata. The
    # experiment input is an allowlist, not a serialization of an evaluation case.
    compiler_arguments(catalog)
    return {"version": 1, "tables": [
        {"name": table["name"], "columns": [
            {key: column[key] for key in ("name", "type", "unique")}
            for column in table["columns"]]} for table in catalog["tables"]],
        "references": [{key: reference[key] for key in
                         ("source_table", "feature", "source_column", "target_table", "target_column")}
                        for reference in catalog["references"]]}


def _settings(settings):
    if not isinstance(settings, dict) or set(settings) - {"model", "max_output_tokens", "temperature", "reasoning"}:
        raise ValueError("Settings allow model, max_output_tokens, temperature, and reasoning only")
    model, maximum = settings.get("model"), settings.get("max_output_tokens")
    if not isinstance(model, str) or not model.strip() or any(ord(char) < 32 for char in model):
        raise ValueError("Set an explicit nonempty model identifier")
    model.encode("utf-8")
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("max_output_tokens must be a positive integer")
    if "temperature" in settings:
        value = settings["temperature"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError("temperature must be finite and between zero and two")
    if "reasoning" in settings:
        value = settings["reasoning"]
        if (not isinstance(value, dict) or set(value) != {"effort"} or
                value["effort"] not in REASONING_EFFORTS):
            raise ValueError("reasoning must specify an explicit supported effort")
    # A fresh JSON object prevents a provider mutating settings shared by arms.
    return json.loads(json.dumps(settings, allow_nan=False))


def build_request(question, catalog, language, guide, settings):
    """Construct the entire public request, accepting no gold-answer object."""
    if language not in LANGUAGES:
        raise ValueError("Language must be sql or sqlp")
    if not isinstance(question, str) or not question.strip() or not isinstance(guide, str) or not guide.strip():
        raise ValueError("Question and guide must be nonempty text")
    question.encode("utf-8")
    guide.encode("utf-8")
    task = {"question": question, "schema_and_relationships": _public_catalog(catalog)}
    return {**_settings(settings), "store": False, "tools": [], "truncation": "disabled",
            "instructions": guide, "input": [{"role": "user", "content":
                json.dumps(task, ensure_ascii=True, sort_keys=True, allow_nan=False)}]}


class ReplayProvider:
    """Recorded provider responses bound to exact requests, never new AI evidence."""

    def __init__(self, entries):
        if not isinstance(entries, list):
            raise ValueError("Replay entries must be an array")
        self.entries = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"request_sha256", "response"}:
                raise ValueError("Each replay entry needs request_sha256 and response")
            key = entry["request_sha256"]
            if (not isinstance(key, str) or len(key) != 64 or
                    any(char not in "0123456789abcdef" for char in key) or key in self.entries):
                raise ValueError("Invalid or duplicate replay request hash")
            self.entries[key] = entry["response"]
        self.consumed = set()

    def validate_requests(self, requests):
        hashes = [request["request_sha256"] for request in requests]
        if len(set(hashes)) != len(hashes):
            raise ValueError("Replay requires unique requests; distinguish duplicate questions before running")
        if self.consumed or set(hashes) != set(self.entries):
            raise ValueError("Replay does not match the exact planned requests")

    def __call__(self, payload):
        key = digest(payload)
        if key not in self.entries or key in self.consumed:
            raise ProviderError("Replay request unavailable or already consumed")
        self.consumed.add(key)
        return self.entries[key]


class _Journal:
    def __init__(self, path):
        # Exclusive creation avoids overwriting earlier paid responses. Restrict
        # access to the current user: questions/results may contain private data.
        self.file = os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8")

    def event(self, kind, **fields):
        self.file.write(json.dumps({"event": kind, **fields}, allow_nan=False, ensure_ascii=True) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self):
        self.file.close()


def _source_manifest(compiler):
    files = [*sorted((ROOT / "tools").glob("*.py")), *sorted((ROOT / "src").glob("*"))]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in files if path.is_file()}
    try:
        repository_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT,
                                         capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        if Path(repository_root).resolve() != ROOT.resolve():
            raise OSError("Frozen source is outside the repository root")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=5, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                                    text=True, timeout=5, check=True).stdout)
    except (OSError, subprocess.SubprocessError):
        head, dirty = None, None
    return {"git_head": head, "git_dirty": dirty, "source_sha256": digest(hashes),
            "source_files": hashes, "compiler_sha256": hashlib.sha256(compiler.read_bytes()).hexdigest(),
            "python_version": sys.version, "sqlite_version": sqlite3.sqlite_version}


def _snapshot_hash(connection, maximum):
    if not hasattr(connection, "serialize"):
        raise SetupError("database", "Pilot snapshot hashes require SQLite serialization support")
    size = connection.execute("PRAGMA main.page_count").fetchone()[0] * connection.execute("PRAGMA main.page_size").fetchone()[0]
    if size > maximum:
        raise SetupError("database", "Database exceeds max_snapshot_bytes; freeze a smaller dataset or raise the explicit limit")
    # serialize observes this read transaction, including committed WAL pages;
    # hashing the main file alone would miss them.
    snapshot = connection.serialize(name="main")
    if len(snapshot) > maximum:
        raise SetupError("database", "Serialized database exceeds max_snapshot_bytes")
    return hashlib.sha256(snapshot).hexdigest()


def _summary(cases):
    result = {}
    for language in LANGUAGES:
        runs = [run for case in cases for run in case["runs"] if run["language"] == language]
        known = [run["token_usage"] for run in runs if run["token_usage"] is not None]
        all_known = bool(runs) and len(known) == len(runs)
        result[language] = {"attempts": len(runs), "correct": sum(run["correct"] for run in runs),
                            "usage_known_attempts": len(known), "token_usage":
                            {field: sum(usage[field] for usage in known) for field in
                             ("input_tokens", "output_tokens", "total_tokens")} if all_known else None}
    return result


def run_pilot(database_path, catalog, workload, compiler_path=DEFAULT_COMPILER, *, guides, settings,
              origin="dry_run", provider=None, journal_path=None, max_requests=0, seed=0,
              max_rows=1000, timeout_seconds=5.0, preflight_timeout_seconds=30.0,
              max_snapshot_bytes=64 * 1024 * 1024):
    """One independent first attempt per language and case, on one read snapshot.

    Default dry-run mode does no model calls or query execution. Replay/live
    modes require an explicit provider, request cap, and a fresh journal path.
    Setup/transport failures abort rather than silently changing denominators;
    already received responses remain in the durable journal.
    """
    try:
        if type(max_rows) is not int or not 1 <= max_rows < sys.maxsize:
            raise ValueError("max_rows must be a positive integer below sys.maxsize")
        for value in (timeout_seconds, preflight_timeout_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Timeouts must be finite and positive")
        if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0 or type(seed) is not int:
            raise ValueError("Invalid snapshot size or random seed")
        if origin not in ("dry_run", "replay", "live"):
            raise ValueError("Origin must be dry_run, replay, or live")
        if not isinstance(guides, dict) or set(guides) != set(LANGUAGES):
            raise ValueError("Supply one corresponding guide for each language")
        _check_workload(workload, max_rows)
        arguments = compiler_arguments(catalog)
        requests = []
        rng = random.Random(seed)
        for case in workload["cases"]:
            order = list(LANGUAGES)
            rng.shuffle(order)
            for language in order:
                payload = build_request(case["question"], catalog, language, guides[language], settings)
                requests.append({"case_id": case["id"], "language": language,
                                 "request_sha256": digest(payload), "payload": payload})
        if origin == "dry_run":
            if provider is not None or journal_path is not None:
                raise ValueError("Dry runs accept neither provider nor journal")
        elif not callable(provider) or journal_path is None or type(max_requests) is not int or max_requests < len(requests):
            raise ValueError("Execution needs provider, fresh journal, and max_requests covering the plan")
        if origin == "replay":
            if not isinstance(provider, ReplayProvider):
                raise ValueError("Replay origin requires ReplayProvider")
            provider.validate_requests(requests)
        elif isinstance(provider, ReplayProvider):
            raise ValueError("ReplayProvider must be labeled replay")
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise SetupError("plan", str(error)) from error
    compiler = Path(compiler_path).resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise SetupError("compiler", "Compiler executable unavailable; run make first")
    report = {"version": 1, "origin": origin, "scope": workload["scope"], "ok": True,
              "completed": False, "requests": requests, "cases": []}
    journal = None
    compiler_copy = None
    catalog_stack = ExitStack()
    try:
        try:
            arguments = catalog_stack.enter_context(catalog_argument_file(catalog))
        except ValueError as error:
            raise SetupError("catalog", str(error)) from error
        with closing(sqlite3.connect(Path(database_path).resolve().as_uri() + "?mode=ro", uri=True,
                                     cached_statements=0)) as connection:
            connection.execute("BEGIN")
            try:
                preflight = validate_dataset(connection, catalog, timeout_seconds=preflight_timeout_seconds)
            except PreflightTimeoutError as error:
                raise SetupError("preflight_timeout", str(error)) from error
            if not preflight["ok"]:
                raise SetupError("preflight", "Dataset failed preflight", preflight)
            if origin != "dry_run":
                # A rebuild of build/sqlpc during a long model call must not
                # change the binary used by later conditions. Hash and execute
                # a private frozen copy, checking for a change during copying.
                original_hash = hashlib.sha256(compiler.read_bytes()).digest()
                compiler_copy = tempfile.TemporaryDirectory(prefix="sqlp-pilot-")
                frozen = Path(compiler_copy.name) / "sqlpc"
                shutil.copyfile(compiler, frozen)
                if (hashlib.sha256(frozen.read_bytes()).digest() != original_hash or
                        hashlib.sha256(compiler.read_bytes()).digest() != original_hash):
                    raise SetupError("compiler", "Compiler changed while freezing the pilot artifact")
                frozen.chmod(0o500)
                compiler = frozen
            # Fail an incompatible binary/catalog before generating any paid
            # response. This direct-column query is valid for every catalog.
            first_table = catalog["tables"][0]
            quote = lambda name: '"' + name.replace('"', '""') + '"'
            smoke_query = ("SELECT r." + quote(first_table["columns"][0]["name"]) +
                           " FROM " + quote(first_table["name"]) + " r;")
            smoke, _ = _compile(smoke_query, arguments, compiler, timeout_seconds)
            if not smoke["ok"]:
                raise SetupError("compiler", "Compiler rejected the catalog smoke query", smoke["error"])
            manifest = {"protocol": "first_attempt_v1", "data_snapshot_sha256": _snapshot_hash(connection, max_snapshot_bytes),
                        "catalog_sha256": digest(catalog), "public_catalog_sha256": digest(_public_catalog(catalog)),
                        "workload_sha256": digest(workload), "guides_sha256":
                        {key: hashlib.sha256(value.encode("utf-8")).hexdigest() for key, value in guides.items()},
                        "settings": _settings(settings), "seed": seed, "planned_requests": len(requests),
                        "limits": {"max_rows": max_rows, "timeout_seconds": timeout_seconds,
                                   "preflight_timeout_seconds": preflight_timeout_seconds,
                                   "max_snapshot_bytes": max_snapshot_bytes, "max_requests": max_requests,
                                   "sqlite_bytes": SQLITE_BYTES},
                        **_source_manifest(compiler)}
            report.update(manifest=manifest, preflight=preflight)
            if origin == "dry_run":
                report.update(completed=True, summary=_summary([]))
                return report
            journal = _Journal(journal_path)
            journal.event("manifest", version=1, origin=origin, scope=workload["scope"], manifest=manifest)
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQLITE_BYTES)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQLITE_BYTES)
            cases = {case["id"]: case for case in workload["cases"]}
            for case in workload["cases"]:
                report["cases"].append({"id": case["id"], "question": case["question"],
                                        "column_count": case["column_count"],
                                        "expected_rows": _rows(case["expected_rows"]), "runs": []})
            outputs = {case["id"]: case for case in report["cases"]}
            returned_model = None
            for request in requests:
                journal.event("request_started", **request)
                started = time.monotonic()
                try:
                    # Copy: adapters must not be able to alter retained prompts.
                    raw = provider(json.loads(json.dumps(request["payload"], allow_nan=False)))
                    elapsed = time.monotonic() - started
                    journal.event("response_received", case_id=request["case_id"], language=request["language"],
                                  request_sha256=request["request_sha256"], generation_seconds=elapsed, response=raw)
                    generated = parse_response(raw)
                    if generated["status"] == "failed":
                        raise ProviderError("Provider reported a failed or cancelled response", raw_response=raw)
                    if returned_model is not None and generated["model"] != returned_model:
                        raise ProviderError("Returned model changed within the paired pilot", raw_response=raw)
                    returned_model = generated["model"]
                except ProviderError as error:
                    journal.event("infrastructure_error", stage="provider", message=str(error),
                                  raw_response=error.raw_response, request_sha256=request["request_sha256"])
                    raise SetupError("provider", str(error)) from error
                language, case = request["language"], cases[request["case_id"]]
                if generated["status"] == "completed":
                    model_case = {**case, language: generated["raw_text"]}
                    record = _condition(connection, model_case, language, arguments, compiler, max_rows, timeout_seconds)
                else:
                    record = {"language": language, "query": generated["raw_text"], "sql": None,
                              "parameters": [], "trace": None, "status": "error", "correct": False,
                              "columns": None, "rows": None, "error":
                              {"stage": "generation", "code": generated["status"]}}
                record.update(token_usage=generated["usage"], generation=generated,
                              generation_seconds=elapsed, request_sha256=request["request_sha256"])
                outputs[case["id"]]["runs"].append(record)
                journal.event("run_scored", case_id=case["id"], run=record)
                if not connection.in_transaction:
                    raise SetupError("database", "Read snapshot ended before the pilot completed")
            report.update(completed=True, ok=all(run["correct"] for case in report["cases"] for run in case["runs"]),
                          summary=_summary(report["cases"]), returned_model=returned_model)
            journal.event("completed", ok=report["ok"], summary=report["summary"])
            return report
    except (sqlite3.Error, OSError, ValueError, TypeError) as error:
        if isinstance(error, SetupError):
            raise
        raise SetupError("pilot", str(error)) from error
    finally:
        if journal is not None:
            journal.close()
        if compiler_copy is not None:
            compiler_copy.cleanup()
        catalog_stack.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("workload", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--reasoning-effort", choices=REASONING_EFFORTS,
                        help="Explicit provider reasoning effort; omitted leaves the provider default unchanged")
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--sql-guide", type=Path, default=ROOT / "prompts/sql.md")
    parser.add_argument("--sqlp-guide", type=Path, default=ROOT / "prompts/sqlp.md")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replay", type=Path)
    mode.add_argument("--live", action="store_true", help="Make paid API calls; requires explicit request cap and journal")
    parser.add_argument("--api-key-file", type=Path,
                        help="Read a private raw key or OPENAI_API_KEY/AI_API_KEY assignment; live mode only")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--max-requests", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-rows", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--preflight-timeout", type=float, default=30.0)
    parser.add_argument("--provider-timeout", type=float, default=60.0)
    args = parser.parse_args()
    origin = "replay" if args.replay else "live" if args.live else "dry_run"
    try:
        if args.api_key_file is not None and not args.live:
            raise ValueError("--api-key-file requires --live")
        settings = {"model": args.model, "max_output_tokens": args.max_output_tokens}
        if args.reasoning_effort is not None:
            settings["reasoning"] = {"effort": args.reasoning_effort}
        settings = _settings(settings)
        provider = None
        if args.replay:
            replay = json.loads(args.replay.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
            if not isinstance(replay, dict) or type(replay.get("version")) is not int or replay["version"] != 1:
                raise ValueError("Replay needs version 1 and an entries array")
            provider = ReplayProvider(replay.get("entries"))
        elif args.live:
            # Read a secret only after the operator explicitly selects live mode.
            key = load_api_key(args.api_key_file) if args.api_key_file is not None else os.environ.get("OPENAI_API_KEY")
            provider = OpenAIResponsesClient(key, args.provider_timeout)
        report = run_pilot(args.database, load_catalog(args.catalog),
                           json.loads(args.workload.read_text(encoding="utf-8"), object_pairs_hook=_unique_object),
                           args.compiler, guides={"sql": args.sql_guide.read_text(encoding="utf-8"),
                                                  "sqlp": args.sqlp_guide.read_text(encoding="utf-8")},
                           settings=settings,
                           origin=origin, provider=provider, journal_path=args.journal,
                           max_requests=args.max_requests, seed=args.seed, max_rows=args.max_rows,
                           timeout_seconds=args.timeout, preflight_timeout_seconds=args.preflight_timeout)
    except (OSError, ValueError, ProviderError) as error:
        print(json.dumps({"version": 1, "origin": origin, "ok": False, "completed": False,
                          "error": {"stage": getattr(error, "stage", "input"), "message": str(error)}}, allow_nan=False))
        return 2
    print(json.dumps(report, allow_nan=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

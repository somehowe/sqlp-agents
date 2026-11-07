"""Optional first-attempt NatSQL condition using the pinned official converter.

This is a separate report schema: it never labels NatSQL queries as SQL/SQLP
or assigns their provider usage to another language. Callers supply a provider;
this module never reads credentials or constructs a network client.
"""

from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import tempfile
import time

from . import natsql_adapter
from .model_provider import ProviderError, parse_response
from .run_comparison import SQLITE_BYTES, SetupError, _check_workload, _condition, _rows
from .run_pilot import (ReplayProvider, _Journal, _public_catalog, _settings,
                        _snapshot_hash, _source_manifest, build_request, digest)
from .validate_dataset import PreflightTimeoutError, validate_dataset


def plan_natsql(catalog, workload, *, guide, settings, seed=0, max_rows=1000):
    """Plan public requests only; no database, converter, or model is accessed."""
    _check_workload(workload, max_rows)
    natsql_adapter.schema_input(catalog)
    if type(seed) is not int:
        raise ValueError("NatSQL ordering seed must be an integer")
    cases = list(workload["cases"])
    random.Random(seed).shuffle(cases)
    requests = []
    for case in cases:
        # build_request's language argument selects validation only. The guide
        # specifies NatSQL; the public task/fact JSON is identical to SQL/SQLP.
        payload = build_request(case["question"], catalog, "sql", guide, settings)
        requests.append({"case_id": case["id"], "language": "natsql",
                         "request_sha256": digest(payload), "payload": payload})
    return {"version": 1, "kind": "natsql_first_attempt", "origin": "dry_run",
            "scope": workload["scope"], "completed": True, "ok": True,
            "requests": requests, "settings": _settings(settings), "seed": seed,
            "upstream_commit": natsql_adapter.UPSTREAM_COMMIT,
            "schema_profile": "reviewed-structural-v1", "cases": []}


def _conversion(query, snapshot, catalog, options):
    if (not query.strip() or "\0" in query or
            len(query.encode("utf-8")) > natsql_adapter.MAX_QUERY_BYTES):
        return natsql_adapter._failure("conversion_input", "Generated NatSQL exceeds the supported text profile")
    try:
        response = natsql_adapter.convert(query, snapshot, catalog, **options)
    except (OSError, ValueError, sqlite3.Error) as error:
        raise SetupError("natsql_converter", str(error)) from error
    if not response["ok"] and response["error"]["kind"] in ("dependency_setup", "converter_protocol"):
        raise SetupError("natsql_converter", response["error"]["message"], response)
    return response


def _empty_run(text, stage, code):
    return {"language": "natsql", "query": text, "sql": None, "parameters": [],
            "trace": None, "status": "error", "correct": False, "columns": None,
            "rows": None, "error": {"stage": stage, "code": code}, "token_usage": None}


def _summary(cases):
    runs = [run for case in cases for run in case["runs"]]
    known = [run["token_usage"] for run in runs if run["token_usage"] is not None]
    return {"natsql": {"attempts": len(runs), "correct": sum(run["correct"] for run in runs),
                       "usage_known_attempts": len(known), "token_usage":
                       {field: sum(usage[field] for usage in known)
                        for field in ("input_tokens", "output_tokens", "total_tokens")}
                       if runs and len(runs) == len(known) else None}}


def run_natsql_pilot(database_path, catalog, workload, *, guide, settings,
                     converter_options=None, origin="dry_run", provider=None,
                     journal_path=None, max_requests=0, seed=0, max_rows=1000,
                     timeout_seconds=5.0, preflight_timeout_seconds=30.0,
                     max_snapshot_bytes=64 * 1024 * 1024):
    """Score every supplied case; conversion/query failures remain attempts.

    A successful converter smoke test and dataset preflight precede paid calls.
    One private frozen snapshot supplies converter input and read-only scoring.
    The converter's structural schema adapter omits upstream NLP preprocessing;
    results describe this declared profile, not NatSQL's full published system.
    """
    try:
        if type(max_rows) is not int or max_rows < 1:
            raise ValueError("max_rows must be a positive integer")
        if type(max_snapshot_bytes) is not int or max_snapshot_bytes < 1:
            raise ValueError("max_snapshot_bytes must be positive")
        for value in (timeout_seconds, preflight_timeout_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Timeouts must be finite and positive")
        if origin not in ("dry_run", "replay", "live"):
            raise ValueError("Origin must be dry_run, replay, or live")
        report = plan_natsql(catalog, workload, guide=guide, settings=settings,
                             seed=seed, max_rows=max_rows)
        if origin == "dry_run":
            if provider is not None or journal_path is not None:
                raise ValueError("Dry runs accept neither provider nor journal")
            return report
        if (not callable(provider) or journal_path is None or type(max_requests) is not int
                or max_requests < len(report["requests"])):
            raise ValueError("Execution needs provider, fresh journal, and a sufficient request cap")
        if origin == "replay":
            if not isinstance(provider, ReplayProvider):
                raise ValueError("Replay origin requires ReplayProvider")
            provider.validate_requests(report["requests"])
        elif isinstance(provider, ReplayProvider):
            raise ValueError("ReplayProvider must be labeled replay")
        if not isinstance(converter_options, dict) or "upstream" not in converter_options:
            raise ValueError("Supply explicit pinned converter options including upstream")
        if set(converter_options) - {"upstream", "python", "nltk_data", "timeout_seconds", "use_from_info"}:
            raise ValueError("Unknown NatSQL converter option")
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        if isinstance(error, SetupError):
            raise
        raise SetupError("plan", str(error)) from error

    report.update(origin=origin, completed=False, ok=False)
    journal = None
    try:
        with tempfile.TemporaryDirectory(prefix="sqlp-natsql-pilot-") as temporary:
            snapshot = Path(temporary) / "snapshot.sqlite"
            uri = Path(database_path).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, cached_statements=0)) as original:
                original.execute("BEGIN")
                preflight = validate_dataset(original, catalog, timeout_seconds=preflight_timeout_seconds)
                if not preflight["ok"]:
                    raise SetupError("preflight", "Dataset failed preflight", preflight)
                original_hash = _snapshot_hash(original, max_snapshot_bytes)
                with closing(sqlite3.connect(snapshot)) as target:
                    original.backup(target)
            snapshot.chmod(0o400)
            first_table = catalog["tables"][0]
            smoke_query = "select " + first_table["name"] + "." + first_table["columns"][0]["name"] + " from " + first_table["name"]
            smoke = _conversion(smoke_query, snapshot, catalog, converter_options)
            if not smoke["ok"]:
                raise SetupError("natsql_converter", "NatSQL converter rejected its direct-column smoke query", smoke)
            source = _source_manifest(Path(natsql_adapter.__file__).resolve())
            source["adapter_sha256"] = source.pop("compiler_sha256")
            with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True,
                                         cached_statements=0)) as connection:
                connection.execute("BEGIN")
                manifest = {"protocol": "natsql_first_attempt_v1", "settings": _settings(settings),
                            "data_snapshot_sha256": _snapshot_hash(connection, max_snapshot_bytes),
                            "source_snapshot_sha256": original_hash,
                            "catalog_sha256": digest(catalog), "public_catalog_sha256": digest(_public_catalog(catalog)),
                            "workload_sha256": digest(workload),
                            "guide_sha256": hashlib.sha256(guide.encode()).hexdigest(),
                            "upstream_commit": natsql_adapter.UPSTREAM_COMMIT,
                            "upstream_source_blobs": dict(natsql_adapter.SOURCE_BLOBS),
                            "converter_environment": smoke.get("environment"),
                            "converter_options": smoke.get("compiler_options"),
                            "schema_profile": "reviewed-structural-v1", "seed": seed,
                            "planned_requests": len(report["requests"]),
                            "limits": {"max_rows": max_rows, "timeout_seconds": timeout_seconds,
                                       "preflight_timeout_seconds": preflight_timeout_seconds,
                                       "max_requests": max_requests, "max_snapshot_bytes": max_snapshot_bytes},
                            **source}
                report.update(manifest=manifest, preflight=preflight)
                journal = _Journal(journal_path)
                journal.event("manifest", version=1, origin=origin, scope=workload["scope"], manifest=manifest)
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQLITE_BYTES)
                connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQLITE_BYTES)
                cases = {case["id"]: case for case in workload["cases"]}
                records = {}
                for case in workload["cases"]:
                    record = {"id": case["id"], "question": case["question"],
                              "column_count": case["column_count"],
                              "expected_rows": _rows(case["expected_rows"]), "runs": []}
                    report["cases"].append(record)
                    records[case["id"]] = record
                returned_model = None
                for request in report["requests"]:
                    journal.event("request_started", **request)
                    started = time.monotonic()
                    try:
                        raw = provider(json.loads(json.dumps(request["payload"], allow_nan=False)))
                        elapsed = time.monotonic() - started
                        journal.event("response_received", case_id=request["case_id"], language="natsql",
                                      request_sha256=request["request_sha256"], generation_seconds=elapsed, response=raw)
                        generated = parse_response(raw)
                        if generated["status"] == "failed":
                            raise ProviderError("Provider reported a failed or cancelled response", raw_response=raw)
                        if returned_model is not None and returned_model != generated["model"]:
                            raise ProviderError("Returned model changed within the NatSQL pilot", raw_response=raw)
                        returned_model = generated["model"]
                    except ProviderError as error:
                        journal.event("infrastructure_error", stage="provider", message=str(error),
                                      raw_response=error.raw_response, request_sha256=request["request_sha256"])
                        raise SetupError("provider", str(error)) from error
                    case = cases[request["case_id"]]
                    if generated["status"] != "completed":
                        run = _empty_run(generated["raw_text"], "generation", generated["status"])
                    else:
                        converted = _conversion(generated["raw_text"], snapshot, catalog, converter_options)
                        journal.event("query_converted", case_id=case["id"], request_sha256=request["request_sha256"], conversion=converted)
                        if converted["ok"]:
                            if converted.get("environment") != smoke.get("environment"):
                                raise SetupError("natsql_converter", "Converter environment changed during the pilot")
                            run = _condition(connection, {**case, "sql": converted["sql"]}, "sql", (), None,
                                             max_rows, timeout_seconds)
                            run.update(language="natsql", query=generated["raw_text"])
                        else:
                            run = _empty_run(generated["raw_text"], "convert", converted["error"]["kind"])
                        run["conversion"] = converted
                    run.update(generation=generated, token_usage=generated["usage"],
                               generation_seconds=elapsed, request_sha256=request["request_sha256"])
                    records[case["id"]]["runs"].append(run)
                    journal.event("run_scored", case_id=case["id"], run=run)
                    if not connection.in_transaction:
                        raise SetupError("database", "Read snapshot ended before the pilot completed")
                report.update(completed=True, ok=all(run["correct"] for case in report["cases"] for run in case["runs"]),
                              summary=_summary(report["cases"]), returned_model=returned_model)
                journal.event("completed", ok=report["ok"], summary=report["summary"])
                return report
    except PreflightTimeoutError as error:
        raise SetupError("preflight_timeout", str(error)) from error
    except (sqlite3.Error, OSError, ValueError, TypeError) as error:
        if isinstance(error, SetupError):
            raise
        raise SetupError("natsql_pilot", str(error)) from error
    finally:
        if journal is not None:
            journal.close()

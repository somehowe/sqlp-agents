"""Freeze, run, resume and analyze a larger matched text-to-SQL study.

Every paid request is bound to a frozen payload. Completed raw responses are
reused on resume; an uncertain prior send is never automatically repeated.
Prepared validation and public SQL-only challenges remain separate strata.
"""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import threading
import time

from .batch_pilot import _FORMAT, _REMINDER, _failed, parse_batch_queries
from .compiler_catalog import catalog_argument_file
from .credentials import load_api_key
from .model_provider import OpenAIResponsesClient, ProviderError, parse_response
from .natsql_batch_pilot import build_batch_request
from .natsql_pilot import _checked_converter_options, _conversion, _empty_run
from .research_budget import BudgetedProvider
from .run_comparison import DEFAULT_COMPILER, SQLITE_BYTES, _condition
from .run_pilot import _settings, digest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "resources/experiments/demo-study-2026-11-06/spending"
PUBLIC_GUIDE = """Generate a read-only SQLite SELECT query for each English task.
Use the supplied actual schema and foreign-key facts. Follow requested filters,
aggregation, nesting, ordering, duplicate handling and limits. Preserve requested
output column order. Use SQLite syntax. Supply exact literal values from each
question; do not invent records. Return the required JSON batch array only.
"""


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _replace_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def budget_summary(directory=DEFAULT_LEDGER):
    state = read(Path(directory) / "budget.json")
    upper = state["prior_upper_micro_usd"] + sum(c["upper_micro_usd"] for c in state["calls"])
    return {"calls": len(state["calls"]), "authorized_usd": state["authorized_micro_usd"] / 1e6,
            "upper_usd": upper / 1e6,
            "remaining_upper_usd": (state["authorized_micro_usd"] - upper) / 1e6,
            "estimated_usd": sum(c.get("estimated_micro_usd") or 0 for c in state["calls"]) / 1e6}


def build_public_request(tasks, schema, guide, settings):
    """Only separately exported public schema and task fields enter the prompt."""
    public = [{"id": item["id"], "question": item["question"]} for item in tasks]
    if not public or len({c["id"] for c in public}) != len(public):
        raise ValueError("Public batch IDs must be nonempty and unique")
    schema = {key: schema[key] for key in ("version", "database_id", "tables", "published_foreign_keys")
              if key in schema}
    schema["tables"] = [{key: table[key] for key in ("name", "columns", "foreign_keys", "create_sql")
                         if key in table} for table in schema.get("tables", [])]
    for table in schema["tables"]:
        table["columns"] = [{key: column[key] for key in
                              ("name", "declared_type", "not_null", "primary_key_position") if key in column}
                             for column in table.get("columns", [])]
        table["foreign_keys"] = [{key: foreign[key] for key in
                                   ("id", "sequence", "target_table", "source_column", "target_column",
                                    "on_update", "on_delete", "match") if key in foreign}
                                  for foreign in table.get("foreign_keys", [])]
    schema["published_foreign_keys"] = [{key: foreign[key] for key in
                                          ("source_table", "source_column", "target_table", "target_column")
                                         if key in foreign}
                                        for foreign in schema.get("published_foreign_keys", [])]
    return {**_settings(settings), "store": False, "tools": [], "truncation": "disabled",
            "instructions": _FORMAT + "\nLanguage guide:\n" + guide + _REMINDER,
            "input": [{"role": "user", "content": json.dumps(
                {"tasks": public, "schema_and_relationships": schema},
                ensure_ascii=True, sort_keys=True, allow_nan=False)}]}


def _copy_dataset(source, destination):
    if destination.exists():
        raise ValueError("Frozen destination already exists")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def prepare(output, *, supported=None, public=None, compiler_path=DEFAULT_COMPILER,
            model="gpt-6-luna", batch_size=4, languages=("sql", "sqlp"),
            max_output_tokens=8192, reasoning_effort="high", seed=20261116,
            ledger=DEFAULT_LEDGER, guides=None, natsql_network_profile=None,
            natsql_literal_profile=None, natsql_schema_cache=None, reference_files=()):
    """Freeze all inputs and exact public requests without invoking any provider."""
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Study output must be a new directory")
    if not supported and not public:
        raise ValueError("Supply a supported workload, a public workload, or both")
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise ValueError("Batch size must be between 1 and 100")
    if not languages or set(languages) - {"sql", "sqlp", "natsql"} or len(set(languages)) != len(languages):
        raise ValueError("Choose distinct sql/sqlp/natsql languages")
    if natsql_network_profile is not None and ("natsql" not in languages or
            natsql_network_profile not in ("enlarged", "base", "enlarged-indexed")):
        raise ValueError("An explicit NatSQL network profile requires the NatSQL condition")
    if natsql_literal_profile is not None and ("natsql" not in languages or
            natsql_literal_profile not in ("native", "preserved")):
        raise ValueError("An explicit NatSQL literal profile requires the NatSQL condition")
    if natsql_schema_cache is not None and natsql_network_profile != "enlarged-indexed":
        raise ValueError("Prepared NatSQL schemas require the indexed profile")
    settings = _settings({"model": model, "max_output_tokens": max_output_tokens,
                          "reasoning": {"effort": reasoning_effort}})
    output.mkdir(parents=True, mode=0o700)
    (output / "runs").mkdir()
    (output / "guides").mkdir()
    shutil.copy2(compiler_path, output / "sqlpc")
    supplied_guides = guides or {}
    for language in languages:
        source = supplied_guides.get(language, ROOT / "prompts" / (language + ".md"))
        shutil.copy2(source, output / "guides" / (language + ".md"))
    (output / "guides/public-sql.md").write_text(PUBLIC_GUIDE, encoding="utf-8")
    # Retain a runnable copy of Python orchestration/scoring, alongside its hashes.
    shutil.copytree(ROOT / "tools", output / "runtime/tools",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cache_binding = None
    if natsql_schema_cache is not None:
        cache_source = Path(natsql_schema_cache).resolve()
        if cache_source.is_dir():
            cache_source = cache_source / "manifest.json"
        cache_manifest = read(cache_source)
        if cache_manifest.get("kind") != "natsql_prepared_schema_cache":
            raise ValueError("Expected a NatSQL prepared-schema manifest")
        shutil.copytree(cache_source.parent, output / "converter-cache")
        cache_binding = {"manifest": "converter-cache/" + cache_source.name,
                         "sha256": sha(output / "converter-cache" / cache_source.name)}
    if reference_files:
        (output / "reference-preflight").mkdir()
        for reference in reference_files:
            shutil.copy2(reference, output / "reference-preflight" / Path(reference).name)
    datasets, requests, rng = [], [], random.Random(seed)
    for stratum, source in (("supported", supported), ("public", public)):
        if source is None:
            continue
        source = Path(source).resolve()
        _copy_dataset(source, output / "inputs" / stratum)
        source_manifest = read(output / "inputs" / stratum / "manifest.json")
        for entry in source_manifest["datasets"]:
            base = Path("inputs") / stratum
            directory = base / entry["directory"]
            dataset = {"id": stratum + "/" + entry["id"], "stratum": stratum,
                       "directory": str(directory)}
            if stratum == "supported":
                dataset.update(database=str(directory / "database.sqlite"),
                               catalog=str(directory / "catalog.json"),
                               workload=str(directory / "workload.json"),
                               snapshots=[{**snapshot, "database": str(base / snapshot["database"]),
                                           "workload": str(base / snapshot["workload"])}
                                          for snapshot in entry.get("snapshots", [])])
            else:
                dataset.update({key: str(base / entry[key])
                                for key in ("database", "schema", "tasks", "workload")})
            workload = read(output / dataset["workload"])
            if cache_binding is not None and stratum == "supported":
                from . import natsql_adapter, natsql_cache
                key = natsql_adapter._schema_cache_key(
                    natsql_adapter.schema_input(read(output / dataset["catalog"])), natsql_network_profile)
                natsql_cache.resolve_manifest({"manifest": output / cache_binding["manifest"],
                                               "sha256": cache_binding["sha256"]}, key)
            cases = workload["cases"]
            dataset["case_count"] = len(cases)
            if stratum == "public":
                public_cases = read(output / dataset["tasks"])["cases"]
                if [(c["id"], c["question"]) for c in public_cases] != [(c["id"], c["question"]) for c in cases]:
                    raise ValueError("Public and private task IDs/questions disagree")
            elif any(c.get("expected_rows") is None for c in cases):
                raise ValueError("Supported tasks require validated independent expected rows")
            datasets.append(dataset)
            for start in range(0, len(cases), batch_size):
                group = cases[start:start + batch_size]
                order = list(languages) if stratum == "supported" else ["sql"]
                rng.shuffle(order)
                for language in order:
                    if stratum == "supported":
                        payload = build_batch_request(group, read(output / dataset["catalog"]), language,
                            (output / "guides" / (language + ".md")).read_text(), settings)
                    else:
                        payload = build_public_request(group, read(output / dataset["schema"]), PUBLIC_GUIDE, settings)
                    request = {"id": "request-%05d" % len(requests), "dataset_id": dataset["id"],
                               "stratum": stratum, "language": language,
                               "case_ids": [c["id"] for c in group],
                               "request_sha256": digest(payload), "payload": payload}
                    requests.append(request)
    if len({r["request_sha256"] for r in requests}) != len(requests):
        raise ValueError("Duplicate exact requests prevent safe recovery")
    frozen = {str(p.relative_to(output)): sha(p) for parent in ("inputs", "guides", "runtime", "converter-cache", "reference-preflight")
              for p in sorted((output / parent).rglob("*")) if p.is_file()}
    frozen["sqlpc"] = sha(output / "sqlpc")
    design = {"version": 1, "kind": "scale_textsql_study", "settings": settings,
              "batch_size": batch_size, "seed": seed, "supported_languages": list(languages),
              "datasets": datasets, "requests": requests, "frozen_sha256": frozen,
              "limits": {"max_rows": 10000, "public_max_rows": 100000, "sqlite_timeout_seconds": 5,
                         "api_timeout_seconds": 180},
              "budget_before": budget_summary(ledger),
              "attempt_policy": "one attempt; no repair, output cleanup or automatic retry",
              "human_semantic_review": "pending",
              "caveats": ["Constructed questions share templates and authored interpretations",
                          "Public SQL-only challenge is outside the paired language comparison",
                          "Public benchmark may appear in model training data",
                          "Whole response token usage counted once including failures"],
              "planned_unique_questions": sum(d["case_count"] for d in datasets),
              "planned_calls": len(requests),
              "planned_outputs": sum(len(r["case_ids"]) for r in requests)}
    if natsql_network_profile is not None:
        design["converter_profile"] = {"network_profile": natsql_network_profile}
    if natsql_literal_profile is not None:
        design.setdefault("converter_profile", {})["literal_profile"] = natsql_literal_profile
    if cache_binding is not None:
        design["converter_schema_cache"] = cache_binding
    write_new(output / "design.json", design)
    return {key: design[key] for key in ("planned_unique_questions", "planned_calls", "planned_outputs", "budget_before")}


def verify(directory):
    directory = Path(directory)
    design = read(directory / "design.json")
    for relative, expected in design["frozen_sha256"].items():
        if sha(directory / relative) != expected:
            raise ValueError("Frozen input changed: " + relative)
    for request in design["requests"]:
        if digest(request["payload"]) != request["request_sha256"]:
            raise ValueError("Frozen request hash mismatch")
    return design


def _event(path, event, **fields):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **fields}, ensure_ascii=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _recover_response(ledger, request, label):
    """Bind ledger recovery to exact experiment label and exact public request."""
    state = read(Path(ledger) / "budget.json")
    calls = [c for c in state["calls"] if c.get("label") == label and
             c["request_sha256"] == request["request_sha256"]]
    if len(calls) > 1:
        raise ValueError("Duplicate paid request in ledger; inspect before continuing")
    if not calls:
        return None
    call = calls[0]
    if not call.get("response_file"):
        raise ProviderError("Prior send has unknown usage; refusing an automatic duplicate")
    return read(Path(ledger) / call["response_file"])


def _score_supported(directory, dataset, request, parsed, design, converter_options):
    workload = read(directory / dataset["workload"])
    cases = {c["id"]: c for c in workload["cases"]}
    catalog = read(directory / dataset["catalog"])
    database = directory / dataset["database"]
    language, records = request["language"], []
    with catalog_argument_file(catalog) as arguments, closing(sqlite3.connect(
            database.resolve().as_uri() + "?mode=ro", uri=True, cached_statements=0)) as connection:
        connection.execute("BEGIN")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQLITE_BYTES)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQLITE_BYTES)
        for key in request["case_ids"]:
            case = cases[key]
            if key in parsed["errors"]:
                record = _failed(language, parsed["errors"][key])
            else:
                query = parsed["queries"][key]
                if language == "natsql":
                    converted = _conversion(query, database, catalog, converter_options)
                    if converted["ok"]:
                        record = _condition(connection, {**case, "sql": converted["sql"]}, "sql", (), None,
                                            design["limits"]["max_rows"], design["limits"]["sqlite_timeout_seconds"])
                        record.update(language="natsql", query=query)
                    else:
                        record = _empty_run(query, "convert", converted["error"]["kind"])
                    record["conversion"] = converted
                else:
                    record = _condition(connection, {**case, language: query}, language, arguments,
                                        directory / "sqlpc", design["limits"]["max_rows"],
                                        design["limits"]["sqlite_timeout_seconds"])
            record.update(case_id=key, question=case["question"], metadata=case.get("design", case.get("metadata", {})),
                          scoreable=True, usage_accounting="whole_batch_only", token_usage=None)
            records.append(record)
    return records


def _score_public(directory, dataset, request, parsed, design):
    # The public exporter provides its own declared bag/ordered-result scorer.
    from .public_textsql_benchmark import score_generated
    workload = read(directory / dataset["workload"])
    cases = {c["id"]: c for c in workload["cases"]}
    records = []
    for key in request["case_ids"]:
        case = cases[key]
        if case.get("gold_status", "ok") != "ok":
            record = {"language": "sql", "query": parsed["queries"].get(key, ""), "correct": None,
                      "scoreable": False, "status": "gold_error", "error": case.get("gold_error")}
        elif key in parsed["errors"]:
            record = {**_failed("sql", parsed["errors"][key]), "scoreable": True}
        else:
            record = score_generated(directory / dataset["database"], case, parsed["queries"][key],
                                     max_rows=design["limits"].get("public_max_rows", 100000),
                                     timeout_seconds=design["limits"]["sqlite_timeout_seconds"])
            record.update(language="sql", scoreable=True)
        record.update(case_id=key, question=case["question"], metadata=case.get("metadata", {}),
                      usage_accounting="whole_batch_only", token_usage=None)
        records.append(record)
    return records


def run(directory, **options):
    """One active writer per study prevents concurrent duplicate paid sends."""
    directory = Path(directory).resolve()
    with (directory / "run.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another process is already running this frozen study") from None
        return _run(directory, **options)


def _run(directory, *, provider=None, api_key_file=None, ledger=DEFAULT_LEDGER,
        workers=4, converter_options=None, max_new_requests=None, progress=None):
    """Run uncompleted frozen requests; recover retained paid outputs on resume."""
    directory = Path(directory).resolve()
    design = verify(directory)
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("Workers must be between 1 and 16")
    if max_new_requests is not None and (type(max_new_requests) is not int or max_new_requests < 0):
        raise ValueError("Request cap must be nonnegative")
    if "natsql" in design["supported_languages"] and not converter_options:
        raise ValueError("NatSQL requires explicit pinned converter options")
    if "natsql" in design["supported_languages"]:
        converter_options = _checked_converter_options(converter_options)
        profile = design.get("converter_profile", {}).get("network_profile")
        if profile is not None and converter_options.get("network_profile", "enlarged") != profile:
            raise ValueError("NatSQL network profile differs from the frozen design")
        literal_profile = design.get("converter_profile", {}).get("literal_profile")
        if literal_profile is not None and converter_options.get("literal_profile", "native") != literal_profile:
            raise ValueError("NatSQL literal profile differs from the frozen design")
        if design.get("converter_schema_cache"):
            cache = design["converter_schema_cache"]
            frozen_cache = {"manifest": str(directory / cache["manifest"]), "sha256": cache["sha256"]}
            if "schema_cache" in converter_options and converter_options["schema_cache"] != frozen_cache:
                raise ValueError("NatSQL schema cache differs from the frozen design")
            converter_options["schema_cache"] = frozen_cache
    label = "scale-study/" + digest({"design": sha(directory / "design.json")})[:20]
    if provider is None:
        if api_key_file is None:
            raise ValueError("Live execution requires an explicit credential file")
        client = OpenAIResponsesClient(load_api_key(api_key_file), design["limits"]["api_timeout_seconds"])
        provider = BudgetedProvider(client, ledger, label=label)
    datasets = {d["id"]: d for d in design["datasets"]}
    pending = [r for r in design["requests"] if not (directory / "runs" / r["id"] / "report.json").exists()]
    if max_new_requests is not None:
        pending = pending[:max_new_requests]
    stop = threading.Event()
    errors = []

    def one(request):
        if stop.is_set():
            return {"request_id": request["id"], "skipped": True}
        folder = directory / "runs" / request["id"]
        folder.mkdir(exist_ok=True)
        response_path = folder / "response.json"
        journal = folder / "journal.jsonl"
        raw, recovered, elapsed = None, False, None
        if response_path.exists():
            saved = read(response_path)
            if saved["request_sha256"] != request["request_sha256"]:
                raise ValueError("Saved response belongs to a different request")
            raw, elapsed, recovered = saved["response"], saved.get("generation_seconds"), True
        else:
            raw = _recover_response(ledger, request, label)
            recovered = raw is not None
            if raw is None:
                # A local started event without ledger evidence could be a custom
                # provider's interrupted send. Never automatically repeat it.
                if journal.exists() and any(e["event"] == "request_started" for e in
                                             (json.loads(line) for line in journal.read_text().splitlines())):
                    raise ProviderError("Prior started request has no retained response; inspect before retry")
                _event(journal, "request_started", request_sha256=request["request_sha256"])
                started = time.monotonic()
                raw = provider(json.loads(json.dumps(request["payload"])))
                elapsed = time.monotonic() - started
            write_new(response_path, {"request_sha256": request["request_sha256"],
                                     "generation_seconds": elapsed, "response": raw})
            _event(journal, "response_retained", recovered_from_ledger=recovered)
        generated = parse_response(raw)
        if generated["model"] != design["settings"]["model"]:
            raise ProviderError("Returned model differs from frozen model", raw_response=raw)
        if generated["status"] == "failed":
            raise ProviderError("Provider failed/cancelled; raw output retained", raw_response=raw)
        parsed = (parse_batch_queries(generated["raw_text"], request["case_ids"])
                  if generated["status"] == "completed" else
                  {"queries": {}, "errors": {k: generated["status"] for k in request["case_ids"]},
                   "protocol_errors": [generated["status"]]})
        scorer = _score_supported if request["stratum"] == "supported" else _score_public
        options = (converter_options,) if request["stratum"] == "supported" else ()
        records = scorer(directory, datasets[request["dataset_id"]], request, parsed, design, *options)
        report = {"version": 1, "origin": "live", "completed": True,
                  "request_id": request["id"], "request_sha256": request["request_sha256"],
                  "dataset_id": request["dataset_id"], "stratum": request["stratum"],
                  "language": request["language"], "case_ids": request["case_ids"],
                  "generation": generated, "generation_seconds": elapsed,
                  "recovered_response": recovered, "protocol_errors": parsed["protocol_errors"],
                  "cases": records}
        write_new(folder / "report.json", report)
        _event(journal, "completed", scored=len(records), correct=sum(r.get("correct") is True for r in records))
        result = {"request_id": request["id"], "dataset": request["dataset_id"],
                  "language": request["language"], "cases": len(records),
                  "correct": sum(r.get("correct") is True for r in records), "recovered": recovered}
        if progress:
            progress(result)
        return result

    # Executor submission is bounded to workers: a budget stop does not enqueue
    # the remaining hundreds of requests, and no output is silently retried.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        iterator = iter(pending)
        active = {}
        for _ in range(workers):
            request = next(iterator, None)
            if request is not None:
                active[executor.submit(one, request)] = request
        while active:
            future = next(as_completed(active))
            request = active.pop(future)
            try:
                future.result()
            except Exception as error:
                stop.set()
                safe = {"request_id": request["id"], "error_type": type(error).__name__, "message": str(error)}
                errors.append(safe)
                if progress:
                    progress({"stopped": safe})
            if not stop.is_set():
                request = next(iterator, None)
                if request is not None:
                    active[executor.submit(one, request)] = request
    summary = analyze(directory)
    summary["run_errors"] = errors
    _replace_json(directory / "latest-run.json", summary)
    return summary


def analyze(directory):
    directory = Path(directory)
    design = read(directory / "design.json")
    groups, observed = defaultdict(list), set()
    reports, known_usage = [], []
    for request in design["requests"]:
        path = directory / "runs" / request["id"] / "report.json"
        if not path.exists():
            continue
        report = read(path)
        if report["request_sha256"] != request["request_sha256"] or not report["completed"]:
            raise ValueError("Report disagrees with frozen request")
        reports.append(report)
        for record in report["cases"]:
            key = (report["dataset_id"], record["case_id"])
            observed.add(key)
            for dimension, value in (("language", report["stratum"] + "/" + report["language"]),
                                     ("dataset_language", report["dataset_id"] + "/" + report["language"])):
                groups[(dimension, str(value))].append(record)
            metadata = record.get("metadata", {})
            for field in ("family", "depth", "difficulty", "sqlp_profile"):
                if field in metadata:
                    value = metadata[field]
                    if isinstance(value, dict):
                        value = value.get("status", "unknown")
                    groups[(field, report["stratum"] + "/" + report["language"] + "/" + str(value))].append(record)
            for feature in metadata.get("features", []):
                groups[("feature", report["stratum"] + "/" + report["language"] + "/" + feature)].append(record)
        if report["generation"]["usage"] is not None:
            known_usage.append(report["generation"]["usage"])
    def error_code(record):
        error = record.get("error")
        if not error:
            return "wrong_answer"
        return error.get("code", error.get("kind", error.get("stage", "unknown")))
    def totals(records):
        scoreable = [r for r in records if r.get("scoreable", True)]
        return {"generated": len(records), "scoreable": len(scoreable),
                "correct": sum(r.get("correct") is True for r in scoreable),
                "gold_errors": len(records) - len(scoreable),
                "errors": dict(Counter(error_code(r) for r in scoreable if not r.get("correct")))}
    by_language = {}
    for (dimension, key), records in sorted(groups.items()):
        if dimension != "language":
            continue
        matching = [r for r in reports if r["stratum"] + "/" + r["language"] == key]
        known = [r["generation"]["usage"] for r in matching if r["generation"]["usage"] is not None]
        by_language[key] = {**totals(records), "batches": len(matching), "usage_known_batches": len(known),
                           "token_usage": {f: sum(v[f] for v in known) for f in
                                           ("input_tokens", "output_tokens", "total_tokens")}
                           if len(known) == len(matching) else None}
    paired = defaultdict(dict)
    for report in reports:
        if report["stratum"] == "supported" and report["language"] in ("sql", "sqlp"):
            for record in report["cases"]:
                paired[(report["dataset_id"], record["case_id"])][report["language"]] = record.get("correct") is True
    complete_pairs = [pair for pair in paired.values() if set(pair) == {"sql", "sqlp"}]
    result = {"version": 1, "kind": "scale_textsql_summary", "origin": "live",
              "planned_unique_questions": design["planned_unique_questions"],
              "planned_calls": design["planned_calls"], "planned_outputs": design["planned_outputs"],
              "completed": len(reports) == len(design["requests"]), "completed_calls": len(reports),
              "observed_unique_questions": len(observed),
              "generated_outputs": sum(len(r["cases"]) for r in reports),
              "supported_paired_correctness": {
                  "complete_pairs": len(complete_pairs),
                  "both_correct": sum(p["sql"] and p["sqlp"] for p in complete_pairs),
                  "sql_only_correct": sum(p["sql"] and not p["sqlp"] for p in complete_pairs),
                  "sqlp_only_correct": sum(p["sqlp"] and not p["sql"] for p in complete_pairs),
                  "both_incorrect": sum(not p["sql"] and not p["sqlp"] for p in complete_pairs)},
              "model": design["settings"]["model"], "by_language": by_language,
              "breakdowns": {dimension: {key: totals(records) for (d, key), records in sorted(groups.items())
                                         if d == dimension}
                             for dimension in sorted({d for d, _ in groups} - {"language"})},
              "usage_known_calls": len(known_usage),
              "token_usage": {f: sum(v[f] for v in known_usage) for f in
                              ("input_tokens", "output_tokens", "total_tokens")}
                             if len(known_usage) == len(reports) and reports else None,
              "caveats": design["caveats"]}
    _replace_json(directory / "summary.json", result)
    return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "analyze", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--supported", type=Path)
    parser.add_argument("--public", type=Path)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--languages", nargs="+", default=["sql", "sqlp"])
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-new-requests", type=int)
    parser.add_argument("--natsql-upstream", type=Path)
    parser.add_argument("--natsql-python", type=Path)
    parser.add_argument("--natsql-nltk-data", type=Path)
    parser.add_argument("--natsql-network-profile", choices=("enlarged", "base", "enlarged-indexed"))
    parser.add_argument("--natsql-literal-profile", choices=("native", "preserved"))
    parser.add_argument("--natsql-schema-cache", type=Path)
    parser.add_argument("--sql-guide", type=Path)
    parser.add_argument("--sqlp-guide", type=Path)
    parser.add_argument("--natsql-guide", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.directory, supported=args.supported, public=args.public,
                         compiler_path=args.compiler, model=args.model, batch_size=args.batch_size,
                         languages=args.languages, max_output_tokens=args.max_output_tokens,
                         reasoning_effort=args.reasoning_effort, ledger=args.ledger,
                         guides={l: p for l in args.languages if (p := getattr(args, l + "_guide"))},
                         natsql_network_profile=args.natsql_network_profile,
                         natsql_literal_profile=args.natsql_literal_profile,
                         natsql_schema_cache=args.natsql_schema_cache)
    elif args.action == "run":
        converter = None
        if args.natsql_upstream:
            converter = {"upstream": args.natsql_upstream, "use_from_info": False}
            if args.natsql_network_profile is not None:
                converter["network_profile"] = args.natsql_network_profile
            if args.natsql_literal_profile is not None:
                converter["literal_profile"] = args.natsql_literal_profile
            if args.natsql_schema_cache is not None:
                cache_manifest = args.natsql_schema_cache / "manifest.json" if args.natsql_schema_cache.is_dir() else args.natsql_schema_cache
                converter["schema_cache"] = {"manifest": str(cache_manifest.resolve()), "sha256": sha(cache_manifest)}
            for key, value in (("python", args.natsql_python), ("nltk_data", args.natsql_nltk_data)):
                if value:
                    converter[key] = value
        result = run(args.directory, api_key_file=args.api_key_file, ledger=args.ledger,
                     workers=args.workers, converter_options=converter, max_new_requests=args.max_new_requests,
                     progress=lambda record: print(json.dumps(record), flush=True))
    elif args.action == "verify":
        design = verify(args.directory)
        result = {"verified": True, "requests": len(design["requests"])}
    else:
        result = analyze(args.directory)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()

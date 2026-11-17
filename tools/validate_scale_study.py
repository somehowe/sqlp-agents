"""Audit a frozen scale study and rescore its saved outputs without model calls.

This separately checks artifact bindings, public-only prompts, completeness and
whole-response accounting. Execution uses the established bounded read-only
primitives and public benchmark metric; it is not independent human review.
"""

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from .batch_pilot import parse_batch_queries
from .compiler_catalog import catalog_argument_file
from .model_provider import parse_response
from .public_textsql_benchmark import score_generated
from .run_comparison import QueryError, _compile, _execute, _rows


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _public_catalog(catalog):
    return {"version": 1, "tables": [
        {"name": table["name"], "columns": [
            {key: column[key] for key in ("name", "type", "unique")}
            for column in table["columns"]]} for table in catalog["tables"]],
        "references": [{key: reference[key] for key in
                        ("source_table", "feature", "source_column", "target_table", "target_column")}
                       for reference in catalog["references"]]}


def _rescore_supported(directory, dataset, case, query, language, design, snapshot=None):
    database = directory / (snapshot["database"] if snapshot else dataset["database"])
    catalog = _read(directory / dataset["catalog"])
    with catalog_argument_file(catalog) as arguments:
        sql, parameters = query, []
        if language == "sqlp":
            compiled, parameters = _compile(query, arguments, directory / "sqlpc", 10)
            if not compiled["ok"]:
                return {"status": "error", "correct": False, "error_stage": "compile"}
            sql = compiled["sql"]
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True,
                                     cached_statements=0)) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1_000_000)
            try:
                columns, rows = _execute(connection, sql, parameters, design["limits"]["max_rows"],
                                         design["limits"]["sqlite_timeout_seconds"])
            except QueryError as error:
                return {"status": "error", "correct": False, "error_stage": "execute", "error_code": error.code}
    correct = (len(columns) == case["column_count"] and
               Counter(map(tuple, rows)) == Counter(map(tuple, case["expected_rows"])))
    return {"status": "ok", "correct": correct, "columns": columns, "rows": _rows(rows), "sql": sql}


def validate(directory, *, require_complete=False, rescore=True, contrasts=True, ledger=None):
    """Return an audit summary; any changed/unbound evidence raises ValueError."""
    directory = Path(directory).resolve()
    design = _read(directory / "design.json")
    _require(design.get("kind") == "scale_textsql_study", "Unexpected study kind")
    for relative, expected in design["frozen_sha256"].items():
        path = (directory / relative).resolve()
        _require(path.is_relative_to(directory), "Frozen path escapes study directory")
        _require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "Frozen input changed: " + relative)
    datasets = {d["id"]: d for d in design["datasets"]}
    _require(len(datasets) == len(design["datasets"]), "Duplicate dataset IDs")
    cases = {identifier: {c["id"]: c for c in _read(directory / d["workload"])["cases"]}
             for identifier, d in datasets.items()}
    for identifier, dataset in datasets.items():
        _require(len(cases[identifier]) == dataset["case_count"], "Workload case count mismatch")
    requests = design["requests"]
    _require(len({r["id"] for r in requests}) == len(requests), "Duplicate request IDs")
    _require(len({r["request_sha256"] for r in requests}) == len(requests), "Duplicate request hashes")
    expected_coverage = {(identifier, language, key)
                         for identifier, dataset in datasets.items()
                         for language in (design["supported_languages"] if dataset["stratum"] == "supported" else ["sql"])
                         for key in cases[identifier]}
    planned, observed = set(), set()
    usage_by_language, unknown_usage = defaultdict(Counter), Counter()
    results, contrast_results = defaultdict(Counter), defaultdict(Counter)
    completed = scored = reexecuted = 0
    raw_response_ids = set()
    ledger_state = _read(Path(ledger) / "budget.json") if ledger else None
    for request in requests:
        identifier, language = request["dataset_id"], request["language"]
        dataset = datasets[identifier]
        _require(request["stratum"] == dataset["stratum"], "Request stratum mismatch")
        _require(_digest(request["payload"]) == request["request_sha256"], "Request hash mismatch")
        payload = request["payload"]
        _require(payload.get("store") is False and payload.get("tools") == [] and
                 payload.get("truncation") == "disabled" and
                 "previous_response_id" not in payload and "conversation" not in payload,
                 "Request is not stateless and tool-free")
        _require({k: payload[k] for k in design["settings"]} == design["settings"], "Request settings mismatch")
        _require(len(payload["input"]) == 1 and payload["input"][0]["role"] == "user", "Unexpected prompt messages")
        public = json.loads(payload["input"][0]["content"])
        expected_tasks = [{"id": key, "question": cases[identifier][key]["question"]} for key in request["case_ids"]]
        expected_schema = (_public_catalog(_read(directory / dataset["catalog"]))
                           if dataset["stratum"] == "supported" else _read(directory / dataset["schema"]))
        _require(public == {"tasks": expected_tasks, "schema_and_relationships": expected_schema},
                 "Prompt contains changed tasks, schema, or private evaluation material")
        _require(len(set(request["case_ids"])) == len(request["case_ids"]), "Repeated task within request")
        for key in request["case_ids"]:
            identity = identifier, language, key
            _require(identity in expected_coverage and identity not in planned, "Repeated or unplanned task")
            planned.add(identity)
        folder = directory / "runs" / request["id"]
        report_path = folder / "report.json"
        if not report_path.exists():
            continue
        report = _read(report_path)
        saved = _read(folder / "response.json")
        _require(saved["request_sha256"] == request["request_sha256"], "Raw response request mismatch")
        generated = parse_response(saved["response"])
        _require(generated["model"] == design["settings"]["model"], "Returned model mismatch")
        _require(generated["status"] != "failed", "Provider failure cannot be a completed report")
        _require(report.get("origin") == "live" and report.get("completed") is True, "Invalid report origin/status")
        for field in ("request_sha256", "dataset_id", "stratum", "language", "case_ids"):
            _require(report[field] == request[field], "Report/request binding mismatch: " + field)
        _require(report["request_id"] == request["id"], "Report ID mismatch")
        _require(report["generation"] == generated, "Generation differs from retained raw response")
        response_id = generated["request_id"]
        if response_id is not None:
            _require(response_id not in raw_response_ids, "Raw response reused across distinct requests")
            raw_response_ids.add(response_id)
        if ledger_state is not None:
            bindings = [c for c in ledger_state["calls"] if c["request_sha256"] == request["request_sha256"]]
            _require(len(bindings) == 1 and bindings[0].get("response_file"), "Missing or duplicate paid ledger binding")
            _require(_read(Path(ledger) / bindings[0]["response_file"]) == saved["response"], "Ledger raw response mismatch")
        parsed = (parse_batch_queries(generated["raw_text"], request["case_ids"])
                  if generated["status"] == "completed" else
                  {"queries": {}, "errors": {key: generated["status"] for key in request["case_ids"]},
                   "protocol_errors": [generated["status"]]})
        _require(report["protocol_errors"] == parsed["protocol_errors"], "Protocol errors changed")
        _require([r["case_id"] for r in report["cases"]] == request["case_ids"], "Scored task order/coverage changed")
        label = dataset["stratum"] + "/" + language
        completed += 1
        if generated["usage"] is None:
            unknown_usage[label] += 1
        else:
            usage_by_language[label].update(generated["usage"])
        for record in report["cases"]:
            key = record["case_id"]
            case = cases[identifier][key]
            observed.add((identifier, language, key))
            _require(record["question"] == case["question"], "Scored question changed")
            _require(record.get("token_usage") is None and record.get("usage_accounting") == "whole_batch_only",
                     "Per-query response usage must remain unallocated")
            _require(record["query"] == parsed["queries"].get(key, ""), "Scored query differs from model output")
            expected_scoreable = dataset["stratum"] == "supported" or case.get("gold_status") == "ok"
            _require(record.get("scoreable", True) == expected_scoreable, "Gold eligibility changed")
            if not expected_scoreable:
                _require(record["correct"] is None, "Unscorable gold was assigned correctness")
            elif key in parsed["errors"]:
                _require(record["correct"] is not True and record.get("error", {}).get("code") == parsed["errors"][key],
                         "Generation failure was discarded or changed")
            elif rescore and record.get("scoreable", True):
                if language == "natsql":
                    raise ValueError("NatSQL rescore requires a pinned converter; use --no-rescore for integrity only")
                rescored = (_rescore_supported(directory, dataset, case, record["query"], language, design)
                            if dataset["stratum"] == "supported" else
                            score_generated(directory / dataset["database"], case, record["query"],
                                            max_rows=design["limits"].get("public_max_rows", design["limits"]["max_rows"]),
                                            timeout_seconds=design["limits"]["sqlite_timeout_seconds"]))
                _require(rescored["correct"] == record["correct"] and rescored["status"] == record["status"],
                         "Reexecution changed primary score: " + request["id"] + "/" + key)
                if rescored["status"] == "ok":
                    _require(rescored["rows"] == record["rows"] and rescored["columns"] == record["columns"],
                             "Reexecution changed recorded answer: " + request["id"] + "/" + key)
                reexecuted += 1
                if contrasts and dataset["stratum"] == "supported":
                    for snapshot in dataset.get("snapshots", []):
                        if snapshot["database"] == dataset["database"]:
                            continue
                        alternate = {c["id"]: c for c in _read(directory / snapshot["workload"])["cases"]}[key]
                        check = _rescore_supported(directory, dataset, alternate, record["query"], language, design, snapshot)
                        contrast_key = language + "/" + snapshot["id"]
                        contrast_results[contrast_key]["attempts"] += 1
                        contrast_results[contrast_key]["correct"] += check["correct"]
                        contrast_results[contrast_key]["primary_match_contrast_mismatch"] += bool(record["correct"] and not check["correct"])
            if rescore and contrasts and dataset["stratum"] == "supported" and key in parsed["errors"]:
                for snapshot in dataset.get("snapshots", []):
                    if snapshot["database"] == dataset["database"]:
                        continue
                    contrast_key = language + "/" + snapshot["id"]
                    contrast_results[contrast_key]["attempts"] += 1
                    contrast_results[contrast_key]["correct"] += 0
                    contrast_results[contrast_key]["generation_failures"] += 1
            scored += 1
            results[label]["generated"] += 1
            results[label]["correct"] += record["correct"] is True
            results[label]["unscorable"] += not record.get("scoreable", True)
    _require(planned == expected_coverage, "Frozen plan does not cover every eligible task exactly once")
    _require(len(requests) == design["planned_calls"], "Planned call count mismatch")
    _require(sum(len(r["case_ids"]) for r in requests) == design["planned_outputs"], "Planned output count mismatch")
    complete = len(observed) == len(expected_coverage)
    _require(not require_complete or complete, "Study has incomplete generated-task coverage")
    usage = {key: (None if unknown_usage[key] else dict(value)) for key, value in usage_by_language.items()}
    for key in unknown_usage:
        usage[key] = None
    return {"version": 1, "kind": "scale_textsql_validation", "ok": True, "complete": complete,
            "validator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "frozen_validator_sha256": design["frozen_sha256"].get("runtime/tools/validate_scale_study.py"),
            "completed_calls": completed, "generated_outputs": scored, "reexecuted_primary_outputs": reexecuted,
            "planned_questions": design["planned_unique_questions"], "by_language": dict(results),
            "token_usage_by_language": usage, "unknown_usage_calls": {k: v for k, v in unknown_usage.items() if v},
            "contrast_rescoring": dict(contrast_results),
            "model_calls": 0, "human_semantic_review": "pending",
            "scope": "artifact/accounting audit and bounded saved-query reexecution; shared execution/scoring primitives"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--no-rescore", action="store_true")
    parser.add_argument("--no-contrasts", action="store_true")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.directory, require_complete=args.require_complete, rescore=not args.no_rescore,
                      contrasts=not args.no_contrasts, ledger=args.ledger)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

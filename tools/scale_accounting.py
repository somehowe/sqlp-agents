"""Read-only accounting overlays for frozen studies with lost API responses.

A received response has known reported tokens. A transport failure without a
response has unknown tokens even if a separately authorized identical request
later succeeds. Never promote known-response subtotals to exact API totals.
"""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .model_provider import parse_response
from .run_pilot import digest
from .run_scale_study import read, sha, verify
from .scale_model_matrix import aggregate, verify_matrix
from .validate_scale_study import validate


FIELDS = ("input_tokens", "output_tokens", "total_tokens")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def select_recovery_configs(directory, configs):
    """Accept a global list while preserving explicit path/hash bindings."""
    if configs is None:
        return None
    directory = Path(directory).resolve()
    if isinstance(configs, dict):
        configs = [configs]
    _require(isinstance(configs, (list, tuple)), "Recovery configs must be a list of manifest/SHA bindings")
    return [config for config in configs if Path(config["manifest"]).resolve().is_relative_to(directory)]


def _local_recovery(directory, request, saved, config):
    """Validate the local hash chain; ledger verification is explicitly separate."""
    folder = directory / "runs" / request["id"]
    path = Path(config["manifest"]).resolve()
    _require(path == folder / "transport-recovery.json" and not path.is_symlink(), "Recovery path is not the request's manifest")
    _require(sha(path) == config["sha256"], "Recovery manifest SHA256 mismatch")
    recovery = read(path)
    _require(recovery.get("kind") == "explicit_scale_transport_recovery", "Unexpected recovery kind")
    _require(recovery.get("study_design_sha256") == sha(directory / "design.json") and
             recovery.get("request_id") == request["id"] and
             recovery.get("request_sha256") == request["request_sha256"], "Recovery changed the frozen study/request")
    plan_path = folder / "transport-recovery-plan.json"
    _require(not plan_path.is_symlink() and sha(plan_path) == recovery.get("plan_sha256"), "Recovery plan hash mismatch")
    plan = read(plan_path)
    _require(plan.get("kind") == "explicit_scale_transport_recovery_plan", "Unexpected recovery authorization plan")
    for key, value in plan.items():
        if key != "kind":
            _require(recovery.get(key) == value, "Recovery changed its authorization plan: " + key)
    _require(plan.get("original_unknown_usage") is True and plan.get("whole_study_api_token_usage") is None and
             plan.get("original_status") == "transport_or_http_error_usage_unknown" and
             recovery.get("paid_call_count_for_frozen_request") == 2,
             "Recovery must preserve the unknown original attempt")
    _require(bool(plan.get("authorization")) and bool(plan.get("policy")), "Recovery lacks explicit authorization")
    journal = (folder / "journal.jsonl").read_bytes()
    count = plan.get("original_journal_bytes")
    _require(type(count) is int and 0 < count <= len(journal) and
             hashlib.sha256(journal[:count]).hexdigest() == plan.get("original_journal_sha256"),
             "Original unresolved journal prefix changed")
    if saved is not None:
        _require(saved.get("transport_recovery") == {"manifest": "transport-recovery.json",
                 "manifest_sha256": config["sha256"]}, "Retained response omits its recovery binding")
        _require(digest(saved["response"]) == recovery.get("response_sha256"), "Recovery response hash mismatch")
        usage = parse_response(saved["response"])["usage"]
        _require(recovery.get("known_retained_response_usage") == usage, "Recovery reported usage changed")
    return recovery


def study_accounting(directory, *, ledger=None, recovery_configs=None, request_ids=None,
                     strict_validation=True):
    """Account one full study or an exact request subset without making calls."""
    directory = Path(directory).resolve()
    design = verify(directory)
    selected = set(request_ids) if request_ids is not None else {r["id"] for r in design["requests"]}
    requests = {r["id"]: r for r in design["requests"]}
    _require(selected <= set(requests), "Accounting scope contains an unknown request")
    explicit = select_recovery_configs(directory, recovery_configs)
    configs = {} if explicit is None else {Path(c["manifest"]).resolve().parent.name: c for c in explicit}
    if explicit is not None:
        _require(len(configs) == len(explicit), "Duplicate recovery configuration")
    state = read(Path(ledger) / "budget.json") if ledger is not None else None
    label = "scale-study/" + digest({"design": sha(directory / "design.json")})[:20]
    calls_by_request = defaultdict(list)
    if state is not None:
        by_hash = {r["request_sha256"]: r["id"] for r in design["requests"]}
        for call in state["calls"]:
            if call.get("label") in (label, label + "/transport-recovery-1") and call.get("request_sha256") in by_hash:
                calls_by_request[by_hash[call["request_sha256"]]].append(call)
    recovered = {}
    by_request = {}
    for identifier, request in requests.items():
        folder = directory / "runs" / identifier
        saved = read(folder / "response.json") if (folder / "response.json").exists() else None
        if saved is not None:
            _require(saved["request_sha256"] == request["request_sha256"], "Retained response belongs to another request")
            binding = saved.get("transport_recovery")
            if binding is not None:
                _require(binding.get("manifest") == "transport-recovery.json", "Unexpected recovery binding path")
                config = {"manifest": folder / "transport-recovery.json", "sha256": binding["manifest_sha256"]}
                if explicit is None:
                    configs[identifier] = config
                else:
                    _require(identifier in configs and Path(configs[identifier]["manifest"]).resolve() == Path(config["manifest"]).resolve()
                             and configs[identifier]["sha256"] == config["sha256"], "Recovered response requires its explicit manifest")
        if identifier in configs:
            recovered[identifier] = _local_recovery(directory, request, saved, configs[identifier])
        elif (folder / "transport-recovery.json").exists():
            raise ValueError("Undisclosed local recovery manifest")
        if identifier not in selected:
            continue
        known = Counter({field: 0 for field in FIELDS})
        received = unknown_received = 0
        if saved is not None:
            parsed = parse_response(saved["response"])
            _require(parsed["model"] == design["settings"]["model"], "Received model differs from frozen settings")
            usage = parsed["usage"]
            received = 1
            if usage is None:
                unknown_received = 1
            else:
                known.update(usage)
        unknown_calls = [call for call in calls_by_request[identifier] if not call.get("response_file") and
                         call.get("status") in ("transport_or_http_error_usage_unknown", "sending")]
        unknown = len(unknown_calls) if state is not None else int(identifier in recovered)
        if identifier in recovered:
            _require(unknown >= 1, "Recovered request lost its unknown original API attempt")
        if state is not None and saved is None:
            # A provider response retained by the spending guard remains usage
            # evidence even if local scoring has not yet copied that response.
            for call in calls_by_request[identifier]:
                if call.get("response_file"):
                    parsed = parse_response(read(Path(ledger) / call["response_file"]))
                    received += 1
                    if parsed["usage"] is None:
                        unknown_received += 1
                    else:
                        known.update(parsed["usage"])
        by_request[identifier] = {"stratum": request["stratum"], "language": request["language"],
            "known_response_token_usage": dict(known), "received_response_calls": received,
            "unknown_received_usage_calls": unknown_received, "unobserved_api_attempts": unknown,
            "token_usage": dict(known) if received and not unknown and not unknown_received else None,
            "transport_recovery_manifest_sha256": configs[identifier]["sha256"] if identifier in configs else None}
    if state is not None and strict_validation:
        validate(directory, rescore=False, contrasts=False, ledger=ledger,
                 recovery_manifest=list(configs.values()) or None)
    elif state is None:
        # Without a ledger, a started journal with no response cannot be called
        # complete usage, even when no completed recovery manifest exists yet.
        for identifier, record in by_request.items():
            journal = directory / "runs" / identifier / "journal.jsonl"
            if not record["received_response_calls"] and journal.exists() and not record["unobserved_api_attempts"]:
                events = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
                if any(event.get("event") == "request_started" for event in events):
                    record["unobserved_api_attempts"] = 1
    by_language = {}
    for key in sorted({r["stratum"] + "/" + r["language"] for r in by_request.values()}):
        values = [r for r in by_request.values() if r["stratum"] + "/" + r["language"] == key]
        known = Counter({field: 0 for field in FIELDS})
        for value in values:
            known.update(value["known_response_token_usage"])
        unknown = sum(v["unobserved_api_attempts"] for v in values)
        missing = sum(v["unknown_received_usage_calls"] for v in values)
        by_language[key] = {"known_response_token_usage": dict(known),
                            "token_usage": dict(known) if not unknown and not missing else None,
                            "unobserved_api_attempts": unknown, "unknown_received_usage_calls": missing,
                            "received_response_calls": sum(v["received_response_calls"] for v in values)}
    known = Counter({field: 0 for field in FIELDS})
    for value in by_language.values():
        known.update(value["known_response_token_usage"])
    unknown = sum(value["unobserved_api_attempts"] for value in by_language.values())
    missing = sum(value["unknown_received_usage_calls"] for value in by_language.values())
    return {"model": design["settings"]["model"], "known_response_token_usage": dict(known),
            "token_usage": dict(known) if not unknown and not missing else None,
            "api_token_usage_complete": not unknown and not missing,
            "unobserved_api_attempts": unknown, "unknown_received_usage_calls": missing,
            "received_response_calls": sum(value["received_response_calls"] for value in by_language.values()),
            "by_language": by_language, "by_request": by_request,
            "recovery_manifest_bindings": [{"manifest": str(config["manifest"]), "sha256": config["sha256"]}
                                           for config in configs.values()],
            "binding_scope": "strict ledger and artifact validation" if state is not None and strict_validation else
                             "ledger snapshot plus local recovery hash chains" if state is not None else
                             "local retained-response/recovery hash chains; ledger not supplied",
            "model_calls_during_analysis": 0}


def matrix_accounting(directory, *, ledger=None, recovery_configs=None, require_complete=False):
    """Overlay exact/unknown API accounting without editing the frozen wrapper."""
    directory = Path(directory).resolve()
    matrix = verify_matrix(directory)
    result = deepcopy(aggregate(directory, require_complete=require_complete))
    known = Counter({field: 0 for field in FIELDS})
    unknown = missing = received = 0
    for member in matrix["members"]:
        folder = (directory / member["directory"]).resolve()
        accounting = study_accounting(folder, ledger=ledger, recovery_configs=recovery_configs)
        record = result["models"][member["model"]]
        record["known_response_token_usage"] = accounting["known_response_token_usage"]
        record["token_usage"] = accounting["token_usage"]
        record["api_token_usage_complete"] = accounting["api_token_usage_complete"]
        record["unobserved_api_attempts"] = accounting["unobserved_api_attempts"]
        record["received_response_calls"] = accounting["received_response_calls"]
        record["recovery_manifest_bindings"] = accounting["recovery_manifest_bindings"]
        record["accounting_binding_scope"] = accounting["binding_scope"]
        for language, scoped in accounting["by_language"].items():
            if language in record.get("by_language", {}):
                record["by_language"][language].update({key: scoped[key] for key in
                    ("known_response_token_usage", "token_usage", "unobserved_api_attempts", "unknown_received_usage_calls")})
        record["known_response_sqlp_token_change_percent"] = record.get("sqlp_total_token_change_percent")
        supported = record.get("by_language", {})
        sql = supported.get("supported/sql", {}).get("token_usage")
        sqlp = supported.get("supported/sqlp", {}).get("token_usage")
        record["sqlp_total_token_change_percent"] = ((sqlp["total_tokens"] / sql["total_tokens"] - 1) * 100
            if sql and sqlp and sql["total_tokens"] else None)
        known.update(accounting["known_response_token_usage"])
        unknown += accounting["unobserved_api_attempts"]
        missing += accounting["unknown_received_usage_calls"]
        received += accounting["received_response_calls"]
    result.update(kind="scale_model_matrix_accounting", known_response_token_usage=dict(known),
                  token_usage=dict(known) if not unknown and not missing else None,
                  api_token_usage_complete=not unknown and not missing,
                  unobserved_api_attempts=unknown, unknown_received_usage_calls=missing,
                  received_response_calls=received, total_api_attempts_accounted=received + unknown,
                  accounting_analyzer_sha256=sha(__file__), model_calls_during_analysis=0)
    result["limitations"].append("Known received-response token sums exclude unobserved original transport attempts; exact whole-API totals stay unknown")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--recovery-manifest", type=Path, action="append")
    parser.add_argument("--recovery-manifest-sha256", action="append")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.recovery_manifest) != bool(args.recovery_manifest_sha256) or (
            args.recovery_manifest and len(args.recovery_manifest) != len(args.recovery_manifest_sha256)):
        parser.error("Each recovery manifest requires its SHA256")
    configs = ([{"manifest": path, "sha256": value} for path, value in
                zip(args.recovery_manifest, args.recovery_manifest_sha256)] if args.recovery_manifest else None)
    result = matrix_accounting(args.directory, ledger=args.ledger, recovery_configs=configs,
                               require_complete=args.require_complete)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

"""Explicit, one-time transport recovery for a frozen scale-study request.

This does not repair queries or erase the uncertain first send. Its complete
reservation stays held, the identical-payload recovery has a separate paid label,
and total API usage remains unknown even after a successful response is retained.
Preparation is offline; live execution requires a separate explicit command.
"""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from .credentials import load_api_key
from .model_provider import OpenAIResponsesClient, ProviderError, parse_response
from . import research_budget
from .run_pilot import digest
from .run_scale_study import DEFAULT_LEDGER, verify


PLAN_FILE = "transport-recovery-plan.json"
MANIFEST_FILE = "transport-recovery.json"
POLICY = ("One explicitly authorized infrastructure resubmission of the identical frozen payload; "
          "no query feedback or repair; retain the complete original unknown-charge reservation; "
          "count both paid sends and leave total API usage unknown.")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _new_json(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _event(path, event, **fields):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **fields}, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _study_lock(directory):
    with (directory / "run.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another process is already running this frozen study") from None
        yield


def _context(directory, request_id, ledger):
    directory, ledger = Path(directory).resolve(), Path(ledger).resolve()
    design = verify(directory)
    requests = [request for request in design["requests"] if request["id"] == request_id]
    if len(requests) != 1:
        raise ValueError("Select exactly one frozen request")
    request = requests[0]
    if digest(request["payload"]) != request["request_sha256"]:
        raise ValueError("Frozen payload hash changed")
    folder = directory / "runs" / request_id
    if not folder.resolve().is_relative_to(directory / "runs") or not folder.is_dir():
        raise ValueError("Missing or invalid request output directory")
    label = "scale-study/" + digest({"design": _sha(directory / "design.json")})[:20]
    state = _read(ledger / "budget.json")
    original = [call for call in state["calls"] if call.get("label") == label
                and call.get("request_sha256") == request["request_sha256"]]
    if len(original) != 1:
        raise ValueError("Recovery needs exactly one original paid ledger binding")
    original = original[0]
    if (original.get("status") != "transport_or_http_error_usage_unknown"
            or original.get("response_file") or original.get("estimated_micro_usd") is not None
            or original.get("model") != design["settings"]["model"]
            or type(original.get("upper_micro_usd")) is not int or original["upper_micro_usd"] <= 0):
        raise ValueError("Original call is not an unresolved, fully reserved transport failure")
    return directory, ledger, design, request, folder, original, state


def _check_plan(plan, directory, request, original, folder):
    expected = {"version": 1, "kind": "explicit_scale_transport_recovery_plan",
                "study_design_sha256": _sha(directory / "design.json"),
                "request_id": request["id"], "request_sha256": request["request_sha256"],
                "original_label": original["label"], "original_ledger_index": original["index"],
                "original_status": original["status"],
                "original_upper_micro_usd": original["upper_micro_usd"],
                "original_call_sha256": digest(original),
                "recovery_label": original["label"] + "/transport-recovery-1",
                "original_unknown_usage": True, "whole_study_api_token_usage": None,
                "policy": POLICY}
    if any(plan.get(key) != value for key, value in expected.items()):
        raise ValueError("Recovery plan disagrees with the frozen request or held original call")
    if not isinstance(plan.get("authorization"), str) or not plan["authorization"].strip():
        raise ValueError("Recovery requires explicit recorded authorization")
    journal = (folder / "journal.jsonl").read_bytes()
    prefix = journal[:plan["original_journal_bytes"]]
    if hashlib.sha256(prefix).hexdigest() != plan["original_journal_sha256"]:
        raise ValueError("Original request journal changed")


def prepare_recovery(directory, request_id, *, ledger=DEFAULT_LEDGER, authorization):
    """Create a reviewable offline plan; no provider is constructed or invoked."""
    directory = Path(directory).resolve()
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError("Supply explicit authorization for this manual recovery")
    with _study_lock(directory):
        directory, ledger, design, request, folder, original, state = _context(directory, request_id, ledger)
        plan_path = folder / PLAN_FILE
        if plan_path.exists():
            plan = _read(plan_path)
            _check_plan(plan, directory, request, original, folder)
            if plan["authorization"] != authorization:
                raise ValueError("Existing recovery authorization differs")
            return plan
        if (folder / "response.json").exists() or (folder / "report.json").exists():
            raise ValueError("A retained response/report cannot receive a transport retry")
        journal_path = folder / "journal.jsonl"
        journal = journal_path.read_bytes()
        events = [json.loads(line) for line in journal.decode("utf-8").splitlines() if line.strip()]
        if (not any(event.get("event") == "request_started"
                    and event.get("request_sha256") == request["request_sha256"] for event in events)
                or any(event.get("event") in {"response_retained", "completed"} for event in events)):
            raise ValueError("Original journal does not show an unresolved started request")
        stop = _read(directory / "latest-run.json")
        errors = [error for error in stop.get("run_errors", [])
                  if error.get("request_id") == request_id and error.get("error_type") == "ProviderError"]
        if len(errors) != 1 or not any(word in errors[0].get("message", "").lower()
                                      for word in ("transport", "http")):
            raise ValueError("Saved run does not report this request's transport infrastructure failure")
        plan = {"version": 1, "kind": "explicit_scale_transport_recovery_plan",
                "study_design_sha256": _sha(directory / "design.json"),
                "request_id": request_id, "request_sha256": request["request_sha256"],
                "original_label": original["label"], "original_ledger_index": original["index"],
                "original_status": original["status"],
                "original_upper_micro_usd": original["upper_micro_usd"],
                "original_call_sha256": digest(original),
                "recovery_label": original["label"] + "/transport-recovery-1",
                "authorization": authorization, "original_unknown_usage": True,
                "authorization_recorded_at": time.time(),
                "whole_study_api_token_usage": None, "policy": POLICY,
                "original_journal_sha256": hashlib.sha256(journal).hexdigest(),
                "original_journal_bytes": len(journal), "infrastructure_error": errors[0],
                "stopped_run_report_sha256": _sha(directory / "latest-run.json"),
                "budget_guard_sha256": _sha(research_budget.__file__),
                "recovery_helper_sha256": _sha(__file__)}
        _check_plan(plan, directory, request, original, folder)
        _new_json(plan_path, plan)
        return plan


def run_recovery(directory, request_id, *, ledger=DEFAULT_LEDGER,
                 api_key_file=None, live=False, client=None):
    """Execute at most one explicit resubmission, or reuse its retained response.

    ``client`` is injectable for offline tests. Every send still goes through the
    real durable spending guard; no automatic attempt follows an uncertain send.
    """
    if live is not True:
        raise ValueError("Execution requires the explicit live flag")
    directory = Path(directory).resolve()
    with _study_lock(directory):
        directory, ledger, design, request, folder, original, state = _context(directory, request_id, ledger)
        plan_path = folder / PLAN_FILE
        plan = _read(plan_path)
        _check_plan(plan, directory, request, original, folder)
        if plan["budget_guard_sha256"] != _sha(research_budget.__file__):
            raise ValueError("The reviewed spending guard changed")
        if plan["recovery_helper_sha256"] != _sha(__file__):
            raise ValueError("The reviewed recovery helper changed")
        entries = [call for call in state["calls"] if call.get("label") == plan["recovery_label"]
                   and call.get("request_sha256") == request["request_sha256"]]
        if len(entries) > 1:
            raise ValueError("More than one recovery ledger call exists; refuse further execution")
        response_path, manifest_path = folder / "response.json", folder / MANIFEST_FILE
        if response_path.exists():
            if not manifest_path.exists():
                raise ValueError("Existing local response has no explicit recovery manifest")
            saved, manifest = _read(response_path), _read(manifest_path)
            if len(entries) != 1 or not entries[0].get("response_file"):
                raise ValueError("Existing recovery has no unique retained ledger response")
            entry = entries[0]
            if (any(manifest.get(key) != value for key, value in plan.items() if key != "kind")
                    or manifest.get("kind") != "explicit_scale_transport_recovery"
                    or manifest.get("plan_sha256") != _sha(plan_path)
                    or manifest.get("recovery_ledger_index") != entry["index"]
                    or manifest.get("recovery_response_file") != entry["response_file"]
                    or manifest.get("ledger_response_file_sha256") != _sha(ledger / entry["response_file"])
                    or digest(_read(ledger / entry["response_file"])) != manifest.get("response_sha256")):
                raise ValueError("Existing recovery manifest disagrees with its paid ledger binding")
            if (saved.get("request_sha256") != request["request_sha256"]
                    or saved.get("transport_recovery", {}).get("manifest_sha256") != _sha(manifest_path)
                    or digest(saved.get("response")) != manifest.get("response_sha256")):
                raise ValueError("Existing recovery response bindings disagree")
            return manifest
        raw, elapsed = None, None
        if entries:
            entry = entries[0]
            if not entry.get("response_file"):
                raise ProviderError("Recovery send has unknown usage; refusing another resubmission")
            raw = _read(ledger / entry["response_file"])
        else:
            journal = folder / "journal.jsonl"
            if any(json.loads(line).get("event") == "transport_recovery_started"
                   for line in journal.read_text().splitlines() if line.strip()):
                raise ProviderError("A recovery start has no retained ledger response; inspect before further action")
            if client is None:
                if api_key_file is None:
                    raise ValueError("Live recovery requires an explicit credential file")
                client = OpenAIResponsesClient(load_api_key(api_key_file), design["limits"]["api_timeout_seconds"])
            provider = research_budget.BudgetedProvider(client, ledger, label=plan["recovery_label"])
            _event(journal, "transport_recovery_started", request_sha256=request["request_sha256"],
                   recovery_label=plan["recovery_label"], original_ledger_index=original["index"])
            started = time.monotonic()
            raw = provider(json.loads(json.dumps(request["payload"], allow_nan=False)))
            elapsed = time.monotonic() - started
            state = _read(ledger / "budget.json")
            entries = [call for call in state["calls"] if call.get("label") == plan["recovery_label"]
                       and call.get("request_sha256") == request["request_sha256"]]
            if len(entries) != 1 or not entries[0].get("response_file"):
                raise ValueError("Successful recovery lacks exactly one retained spending-ledger response")
            entry = entries[0]
        generated = parse_response(raw)
        if generated["model"] != design["settings"]["model"]:
            raise ProviderError("Recovery returned a different model; do not resubmit", raw_response=raw)
        manifest = {**plan, "kind": "explicit_scale_transport_recovery",
                    "plan_file": PLAN_FILE, "plan_sha256": _sha(plan_path),
                    "recovery_ledger_index": entry["index"],
                    "recovery_response_file": entry["response_file"],
                    "response_sha256": digest(raw),
                    "ledger_response_file_sha256": _sha(ledger / entry["response_file"]),
                    "known_retained_response_usage": generated["usage"],
                    "paid_call_count_for_frozen_request": 2,
                    "recovery_status": entry["status"]}
        # The spending guard has already saved raw output. Create the immutable
        # manifest before the local response, so a crash can always resume free.
        if manifest_path.exists():
            if _read(manifest_path) != manifest:
                raise ValueError("Retained recovery manifest disagrees with ledger output")
        else:
            _new_json(manifest_path, manifest)
        _new_json(response_path, {"request_sha256": request["request_sha256"],
            "generation_seconds": elapsed, "response": raw,
            "transport_recovery": {"manifest": MANIFEST_FILE, "manifest_sha256": _sha(manifest_path)}})
        _event(folder / "journal.jsonl", "transport_recovery_response_retained",
               recovery_manifest_sha256=_sha(manifest_path), recovery_ledger_index=entry["index"],
               original_unknown_reservation_held=True)
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("request_id")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--authorization")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare_recovery(args.directory, args.request_id, ledger=args.ledger,
                                  authorization=args.authorization)
    else:
        result = run_recovery(args.directory, args.request_id, ledger=args.ledger,
            api_key_file=args.api_key_file, live=args.live)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

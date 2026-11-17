"""Freeze exact model-only follow-ups and compare their saved scale-study results.

The original Luna run is reused. Neither cloning nor analysis invokes a provider;
model generation continues through the existing explicit, budgeted scale runner.
"""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .research_budget import RATES
from .run_pilot import digest
from .run_scale_study import DEFAULT_LEDGER, budget_summary, read, sha, verify, write_new


MODELS = ("gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra")
NAMES = {"gpt-6-luna": "luna", "gpt-6.1-sol": "sol", "gpt-6-astra": "astra"}


def verify_model_parity(source, destination):
    """Reject any changed question, request setting, schema, data or scorer."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    original, cloned = verify(source), verify(destination)
    parent = cloned.get("parent_study", {})
    if parent.get("design_sha256") != sha(source / "design.json"):
        raise ValueError("Follow-up is not bound to this parent design")
    if original["frozen_sha256"] != cloned["frozen_sha256"]:
        raise ValueError("Follow-up changed frozen inputs, compiler, guides or runtime")
    settings = deepcopy(original["settings"])
    settings["model"] = cloned["settings"]["model"]
    if settings != cloned["settings"]:
        raise ValueError("Follow-up changed settings beyond the model")
    for key in ("datasets", "batch_size", "seed", "supported_languages", "limits",
                "planned_unique_questions", "planned_calls", "planned_outputs", "attempt_policy"):
        if original[key] != cloned[key]:
            raise ValueError("Follow-up changed protocol: " + key)
    if len(original["requests"]) != len(cloned["requests"]):
        raise ValueError("Follow-up changed request coverage")
    for before, after in zip(original["requests"], cloned["requests"]):
        expected = deepcopy(before)
        expected["payload"]["model"] = cloned["settings"]["model"]
        expected["request_sha256"] = digest(expected["payload"])
        if expected != after:
            raise ValueError("Follow-up changed a request beyond its model: " + before["id"])
    return cloned


def clone_model_study(source, destination, model, *, ledger=DEFAULT_LEDGER):
    """Create a fresh runnable study with byte-identical original frozen files."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    original = verify(source)
    if model not in MODELS or model not in RATES or model == original["settings"]["model"]:
        raise ValueError("Choose a supported model different from the parent")
    if destination.exists():
        raise ValueError("Follow-up output must be a new directory")
    before_budget = budget_summary(ledger)
    destination.mkdir(mode=0o700, parents=True)
    try:
        for name in ("inputs", "guides", "runtime"):
            shutil.copytree(source / name, destination / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(source / "sqlpc", destination / "sqlpc")
        (destination / "runs").mkdir(mode=0o700)
        cloned = deepcopy(original)
        cloned["settings"]["model"] = model
        for request in cloned["requests"]:
            request["payload"]["model"] = model
            request["request_sha256"] = digest(request["payload"])
        cloned["budget_before"] = before_budget
        cloned["parent_study"] = {
            "design_sha256": sha(source / "design.json"),
            "model": original["settings"]["model"],
            "relative_directory": os.path.relpath(source, destination),
            "payload_changes": ["model"],
            "frozen_inputs_and_runtime_identical": True,
            "prior_outputs_reused_for_this_model": False,
        }
        write_new(destination / "design.json", cloned)
        verify_model_parity(source, destination)
        return cloned
    except Exception:
        # This function exclusively created this previously absent destination.
        shutil.rmtree(destination)
        raise


def prepare(source, output, *, models=("gpt-6.1-sol", "gpt-6-astra"), ledger=DEFAULT_LEDGER):
    source, output = Path(source).resolve(), Path(output).resolve()
    original = verify(source)
    if output.exists():
        raise ValueError("Matrix output must be a new directory")
    if not models or len(set(models)) != len(models) or any(
            model not in MODELS or model == original["settings"]["model"] for model in models):
        raise ValueError("Supply distinct supported follow-up models")
    output.mkdir(mode=0o700, parents=True)
    members = [{"model": original["settings"]["model"],
                "directory": os.path.relpath(source, output),
                "design_sha256": sha(source / "design.json"), "reused_run": True}]
    try:
        for model in models:
            folder = output / NAMES[model]
            clone_model_study(source, folder, model, ledger=ledger)
            members.append({"model": model, "directory": NAMES[model],
                            "design_sha256": sha(folder / "design.json"), "reused_run": False})
        matrix = {"version": 1, "kind": "scale_model_matrix", "members": members,
                  "source_directory": os.path.relpath(source, output),
                  "source_design_sha256": sha(source / "design.json"),
                  "unique_questions": original["planned_unique_questions"],
                  "planned_question_model_pairs": original["planned_unique_questions"] * len(members),
                  "planned_calls": original["planned_calls"] * len(members),
                  "planned_new_calls": original["planned_calls"] * len(models),
                  "planned_outputs": original["planned_outputs"] * len(members),
                  "model_calls_during_preparation": 0,
                  "attempt_policy": "Reuse the completed parent; one new first attempt per follow-up model",
                  "limitations": ["Same questions and related templates across models are correlated observations",
                                  "All models are from one provider; human semantic review remains pending",
                                  "Public SQL challenge stays separate from the matched SQL/SQLP comparison",
                                  "Primary scoring and full original schemas/guides remain unchanged"]}
        write_new(output / "matrix.json", matrix)
        verify_matrix(output)
        return matrix
    except Exception:
        shutil.rmtree(output)
        raise


def verify_matrix(directory):
    directory = Path(directory).resolve()
    matrix = read(directory / "matrix.json")
    if matrix.get("kind") != "scale_model_matrix":
        raise ValueError("Unexpected matrix kind")
    source = (directory / matrix["source_directory"]).resolve()
    if sha(source / "design.json") != matrix["source_design_sha256"]:
        raise ValueError("Parent study design changed")
    seen = set()
    for member in matrix["members"]:
        folder = (directory / member["directory"]).resolve()
        if member["model"] in seen:
            raise ValueError("Duplicate model in matrix")
        seen.add(member["model"])
        if sha(folder / "design.json") != member["design_sha256"]:
            raise ValueError("Matrix member design changed: " + member["model"])
        design = verify(folder) if member["reused_run"] else verify_model_parity(source, folder)
        if design["settings"]["model"] != member["model"]:
            raise ValueError("Matrix member model mismatch")
    return matrix


def _control_results(directory, design):
    groups = defaultdict(Counter)
    for request in design["requests"]:
        path = directory / "runs" / request["id"] / "report.json"
        if request["stratum"] != "supported" or not path.exists():
            continue
        report = read(path)
        for case in report["cases"]:
            metadata = case.get("metadata", {})
            label = ("deliberate_empty_control" if metadata.get("deliberate_empty_control") else
                     "nonempty" if metadata.get("expected_empty") is False else "other")
            groups[request["language"] + "/" + label]["generated"] += 1
            groups[request["language"] + "/" + label]["correct"] += case.get("correct") is True
    return dict(groups)


def aggregate(directory, *, require_complete=False):
    """Read saved results; preserve missing models/usage rather than inventing it."""
    directory = Path(directory).resolve()
    matrix = verify_matrix(directory)
    models, known_usage = {}, []
    complete = True
    completed_calls = generated = observed_question_model_pairs = 0
    for member in matrix["members"]:
        folder = (directory / member["directory"]).resolve()
        design = read(folder / "design.json")
        if not (folder / "summary.json").exists():
            models[member["model"]] = {"completed": False, "status": "not_started"}
            complete = False
            continue
        summary = read(folder / "summary.json")
        if summary["model"] != member["model"] or summary["planned_calls"] != design["planned_calls"]:
            raise ValueError("Summary disagrees with matrix member")
        if summary["completed"] and (summary["completed_calls"] != design["planned_calls"] or
                summary["generated_outputs"] != design["planned_outputs"] or
                summary["observed_unique_questions"] != matrix["unique_questions"]):
            raise ValueError("Completed summary has incomplete coverage")
        complete = complete and summary["completed"]
        completed_calls += summary["completed_calls"]
        generated += summary["generated_outputs"]
        observed_question_model_pairs += summary["observed_unique_questions"]
        if summary.get("token_usage") is not None:
            known_usage.append(summary["token_usage"])
        sql = summary["by_language"].get("supported/sql", {}).get("token_usage")
        sqlp = summary["by_language"].get("supported/sqlp", {}).get("token_usage")
        delta = ((sqlp["total_tokens"] / sql["total_tokens"] - 1) * 100
                 if sql and sqlp and sql["total_tokens"] else None)
        models[member["model"]] = {
            "completed": summary["completed"], "completed_calls": summary["completed_calls"],
            "observed_questions": summary["observed_unique_questions"],
            "generated_outputs": summary["generated_outputs"],
            "by_language": summary["by_language"],
            "paired_correctness": summary.get("supported_paired_correctness"),
            "sqlp_total_token_change_percent": delta,
            "token_usage": summary.get("token_usage"),
            "controls": _control_results(folder, design),
            "breakdowns": summary.get("breakdowns", {}),
            "reused_run": member["reused_run"],
        }
    if require_complete and not complete:
        raise ValueError("Model matrix is incomplete")
    usage = ({field: sum(value[field] for value in known_usage)
              for field in ("input_tokens", "output_tokens", "total_tokens")}
             if len(known_usage) == len(matrix["members"]) else None)
    return {"version": 1, "kind": "scale_model_matrix_summary", "completed": complete,
            "unique_questions": matrix["unique_questions"],
            "observed_question_model_pairs": observed_question_model_pairs,
            "completed_calls": completed_calls, "generated_outputs": generated,
            "models": models, "token_usage": usage, "limitations": matrix["limitations"],
            "model_calls_during_analysis": 0}


_EXECUTE_FROZEN = r'''
import importlib.util
import json
from pathlib import Path
import sys
from tools.credentials import load_api_key
from tools.model_provider import OpenAIResponsesClient
from tools.run_pilot import digest
from tools.run_scale_study import read, run, sha

directory, ledger, keyfile, budgetfile = map(Path, sys.argv[1:5])
workers, maximum = int(sys.argv[5]), sys.argv[6]
spec = importlib.util.spec_from_file_location("tools.reconciled_research_budget", budgetfile)
budget = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = budget
spec.loader.exec_module(budget)
design = read(directory / "design.json")
label = "scale-study/" + digest({"design": sha(directory / "design.json")})[:20]
client = OpenAIResponsesClient(load_api_key(keyfile), design["limits"]["api_timeout_seconds"])
provider = budget.BudgetedProvider(client, ledger, label=label)
result = run(directory, provider=provider, ledger=ledger, workers=workers,
             max_new_requests=None if maximum == "none" else int(maximum),
             progress=lambda item: print(json.dumps(item), flush=True))
print(json.dumps(result, indent=2), flush=True)
sys.exit(bool(result.get("run_errors")))
'''


def run_model(directory, model, *, api_key_file, ledger=DEFAULT_LEDGER, workers=4,
              max_new_requests=None, budget_module=None):
    """Explicit live action: frozen scorer, separately recorded current guard.

    A fresh interpreter starts in the copied runtime, so generation, parsing and
    scoring all use the original frozen modules. Only the external spending
    guard is injected; its source hash is bound separately before any send.
    """
    directory = Path(directory).resolve()
    matrix = verify_matrix(directory)
    candidates = [member for member in matrix["members"] if member["model"] == model]
    if len(candidates) != 1 or candidates[0]["reused_run"]:
        raise ValueError("Choose one new model; the completed parent must be reused")
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("Workers must be between 1 and 16")
    if max_new_requests is not None and (type(max_new_requests) is not int or max_new_requests < 0):
        raise ValueError("Request cap must be a nonnegative integer")
    folder = (directory / candidates[0]["directory"]).resolve()
    budget_module = (Path(budget_module) if budget_module else
                     Path(__file__).resolve().parent / "research_budget.py").resolve()
    binding = {"version": 1, "model": model, "design_sha256": sha(folder / "design.json"),
               "budget_module_sha256": sha(budget_module), "execution_wrapper_sha256": sha(__file__),
               "frozen_runtime_unchanged": True,
               "exception": "Inject only the separately audited spending guard; unchanged frozen generation/scoring",
               "ledger_directory": str(Path(ledger).resolve())}
    evidence = folder / "execution-budget.json"
    if evidence.exists():
        if read(evidence) != binding:
            raise ValueError("Live spending guard or wrapper changed after first execution")
    else:
        write_new(evidence, binding)
    result = subprocess.run([sys.executable, "-c", _EXECUTE_FROZEN, str(folder),
                             str(Path(ledger).resolve()), str(Path(api_key_file).resolve()),
                             str(budget_module), str(workers),
                             "none" if max_new_requests is None else str(max_new_requests)],
                            cwd=folder / "runtime", check=False)
    return result.returncode


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "analyze", "run"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--models", nargs="+", default=["gpt-6.1-sol", "gpt-6-astra"])
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-new-requests", type=int)
    parser.add_argument("--budget-module", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.source is None:
            parser.error("prepare requires --source")
        result = prepare(args.source, args.directory, models=tuple(args.models), ledger=args.ledger)
    elif args.action == "run":
        if args.model is None or args.api_key_file is None:
            parser.error("run requires --model and --api-key-file")
        raise SystemExit(run_model(args.directory, args.model, api_key_file=args.api_key_file,
                                   ledger=args.ledger, workers=args.workers,
                                   max_new_requests=args.max_new_requests, budget_module=args.budget_module))
    elif args.action == "verify":
        result = verify_matrix(args.directory)
    else:
        result = aggregate(args.directory, require_complete=args.require_complete)
        destination = args.directory / "summary.json"
        temporary = destination.with_suffix(".json.pending")
        temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(destination)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

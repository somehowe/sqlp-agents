"""Build a complete, explicitly synthetic comparison example without model access."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .analyze_runs import analyze_report
from .create_demo import create_demo
from .experiment_kit import plan_kit, prepare_kit
from .run_comparison import DEFAULT_COMPILER
from .validate_dataset import load_catalog


MODEL = "offline-example"
NOTE = ("Synthetic replay of prepared reference queries. No model was called; "
        "token usage is unknown. This checks integration, not model performance.")


def _write_json(path, value):
    # New private files only: never overwrite an earlier run or journal.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, allow_nan=False, ensure_ascii=True, indent=2)
        output.write("\n")


def create_offline_example(output_dir, compiler_path=DEFAULT_COMPILER):
    """Exercise prepared scoring, a frozen runtime, exact replay, and analysis.

    Uses only the repository's fictional academic fixture. Provider responses
    contain its prepared queries and unknown usage, visibly labeled synthetic.
    The frozen runner is called with --replay, never --live. No credentials are
    loaded, and an existing output directory is preserved.
    """
    compiler = Path(compiler_path).resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise ValueError("Compiler executable unavailable; run make first")
    output = Path(output_dir).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700)
    demo = output / "demo"
    create_demo(demo, compiler)
    catalog = load_catalog(demo / "catalog.json")
    workload = json.loads((demo / "prepared-workload.json").read_text(encoding="utf-8"))
    kit = output / "kit"
    verified = prepare_kit(demo / "academic.sqlite", catalog, {"development": workload}, kit, compiler)
    plan = plan_kit(kit, "development", model=MODEL, max_output_tokens=1024, seed=0)
    _write_json(output / "plan.json", plan)

    cases = {case["id"]: case for case in workload["cases"]}
    entries = []
    for index, request in enumerate(plan["requests"]):
        entries.append({"request_sha256": request["request_sha256"], "response": {
            "id": "offline-example-" + str(index), "model": MODEL, "status": "completed",
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text",
                                     "text": cases[request["case_id"]][request["language"]]}]}],
            "usage": None}})
    _write_json(output / "replay.json", {"version": 1, "note": NOTE, "entries": entries})

    # Execute the copied modules and compiler, not the host checkout. Passing
    # paths as argv elements also works when a parent contains spaces/Unicode.
    command = [sys.executable, "-m", "tools.run_pilot", str(kit / "data.sqlite"),
               str(kit / "public/catalog.json"), str(kit / "evaluation/development.json"),
               "--compiler", str(kit / "sqlpc"),
               "--sql-guide", str(kit / "public/guides/sql.md"),
               "--sqlp-guide", str(kit / "public/guides/sqlp.md"),
               "--model", MODEL, "--max-output-tokens", "1024", "--seed", "0",
               "--replay", str(output / "replay.json"),
               "--journal", str(output / "journal.jsonl"),
               "--max-requests", str(len(plan["requests"]))]
    process = subprocess.run(command, cwd=kit / "runtime", capture_output=True,
                             text=True, encoding="utf-8", timeout=90)
    if process.returncode != 0:
        raise ValueError("Frozen replay failed: " + process.stdout + process.stderr)
    report = json.loads(process.stdout)
    if (report.get("origin") != "replay" or not report.get("ok") or
            report.get("completed") is not True or report.get("requests") != plan["requests"]):
        raise ValueError("Frozen replay did not complete the exact planned requests")
    report["demo_note"] = NOTE
    report["experiment_kit_sha256"] = verified["kit_sha256"]
    analysis = analyze_report(report)
    pairs = len(workload["cases"])
    if (analysis["paired_correctness"]["both_correct"] != pairs or
            any(condition["token_usage"] is not None for condition in analysis["conditions"].values())):
        raise ValueError("Synthetic replay answers or unknown-usage accounting changed")
    _write_json(output / "replay-report.json", report)
    _write_json(output / "analysis.json", analysis)
    summary = {
        "version": 1, "ok": True, "origin": "offline_example", "model_calls": 0,
        "prepared_pairs": pairs, "replay_pairs": pairs, "note": NOTE,
        "paths": {name: str(path) for name, path in {
            "demo": demo, "kit": kit, "plan": output / "plan.json",
            "replay": output / "replay.json", "journal": output / "journal.jsonl",
            "report": output / "replay-report.json", "analysis": output / "analysis.json",
        }.items()},
    }
    # This is the completion marker. A failed run can leave an incomplete
    # directory; use a fresh output path after correcting the failure.
    _write_json(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="A new directory, normally under ignored build/")
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    args = parser.parse_args()
    try:
        summary = create_offline_example(args.output_dir, args.compiler)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"version": 1, "ok": False,
                          "error": {"stage": "offline_example", "message": str(error)}}, allow_nan=False))
        return 2
    print(json.dumps(summary, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Freeze a NatSQL subset follow-up and compare exact saved SQL/SQLP batches.

Selection uses complete original B4 groups and keeps full schemas and data.
Every language owns whole-response usage once. Existing SQL/SQLP attempts are
reused; these questions are not added to the parent study's distinct-task count.
Preparation and analysis make no model calls.
"""

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import shutil

from . import natsql_adapter
from .batch_pilot import parse_batch_queries
from .model_provider import parse_response
from .run_scale_study import DEFAULT_LEDGER, prepare as prepare_study, read, sha, verify, write_new
from .scale_model_matrix import verify_matrix
from .scale_accounting import study_accounting
from .validate_scale_study import _rescore_supported


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def prepare(matrix_directory, subset, output, *, guide, ledger=DEFAULT_LEDGER,
            network_profile="enlarged-indexed", literal_profile="preserved",
            schema_cache=None, reference_report=None):
    matrix_directory, subset, output = map(lambda p: Path(p).resolve(), (matrix_directory, subset, output))
    matrix = verify_matrix(matrix_directory)
    source = (matrix_directory / matrix["source_directory"]).resolve()
    selection = read(subset / "selection.json")
    _require(selection["original_design_sha256"] == sha(source / "design.json"), "Subset belongs to another parent")
    subset_manifest = read(subset / "manifest.json")
    for relative, expected in subset_manifest["files_sha256"].items():
        _require(sha(subset / relative) == expected, "Subset input changed: " + relative)
    _require(network_profile in ("base", "enlarged", "enlarged-indexed"), "Unknown network profile")
    _require(literal_profile in ("native", "preserved"), "Unknown literal profile")
    _require(not output.exists(), "NatSQL control output must be a new directory")
    reference_files = []
    coverage = None
    if reference_report is not None:
        reference_report = Path(reference_report).resolve()
        reference_files = [reference_report]
        for name in ("coverage-summary.json", "timeout-review.json"):
            candidate = reference_report.parent / name
            if candidate.exists():
                reference_files.append(candidate)
        coverage_path = reference_report.parent / "coverage-summary.json"
        _require(coverage_path.is_file(), "Reference report requires its frozen coverage summary")
        coverage = read(coverage_path)
        _require(coverage.get("model_outcomes_used_for_coverage") is False,
                 "Reference coverage must be selected before model outcomes")
    output.mkdir(parents=True, mode=0o700)
    try:
        members = []
        for member in matrix["members"]:
            main_folder = (matrix_directory / member["directory"]).resolve()
            parent = verify(main_folder)
            name = member["model"].removeprefix("gpt-6.1-").removeprefix("gpt-6-")
            folder = output / name
            prepare_study(folder, supported=subset, compiler_path=source / "sqlpc",
                          model=member["model"], batch_size=parent["batch_size"], languages=("natsql",),
                          max_output_tokens=parent["settings"]["max_output_tokens"],
                          reasoning_effort=parent["settings"]["reasoning"]["effort"], seed=parent["seed"],
                          ledger=ledger, guides={"natsql": guide}, natsql_network_profile=network_profile,
                          natsql_literal_profile=literal_profile, natsql_schema_cache=schema_cache,
                          reference_files=reference_files)
            design = verify(folder)
            target_ids = {key for request in design["requests"] for key in request["case_ids"]}
            common_ids = (set(coverage["common_reference_ids_after_serial_review"]) if coverage else target_ids)
            _require(common_ids <= target_ids, "Reference coverage contains unrelated questions")
            if coverage:
                _require(set(coverage["fixed_model_target_ids"]) == target_ids,
                         "Reference coverage changed the fixed model targets")
            parent_groups = {(r["dataset_id"], tuple(r["case_ids"]), r["language"]): r
                             for r in parent["requests"]}
            matched = []
            for request in design["requests"]:
                _require(len(request["case_ids"]) == 4, "NatSQL controls require complete original B4 groups")
                counterpart = {}
                for language in ("sql", "sqlp"):
                    key = (request["dataset_id"], tuple(request["case_ids"]), language)
                    _require(key in parent_groups, "NatSQL changed an original complete batch")
                    original = parent_groups[key]
                    _require(original["payload"]["input"] == request["payload"]["input"],
                             "NatSQL and original languages receive different public tasks or facts")
                    for setting in ("model", "max_output_tokens", "reasoning", "store", "tools"):
                        _require(original["payload"].get(setting) == request["payload"].get(setting),
                                 "NatSQL changed a matched model setting: " + setting)
                    counterpart[language] = {"id": original["id"], "request_sha256": original["request_sha256"]}
                matched.append({"dataset_id": request["dataset_id"], "case_ids": request["case_ids"],
                                "natsql_request": {"id": request["id"], "request_sha256": request["request_sha256"]},
                                "counterpart_requests": counterpart,
                                "reference_group_passed": all(key in common_ids for key in request["case_ids"])})
            members.append({"model": member["model"], "directory": name,
                            "design_sha256": sha(folder / "design.json"),
                            "main_directory": os.path.relpath(main_folder, output),
                            "main_design_sha256": sha(main_folder / "design.json"), "matched_batches": matched})
        shutil.copy2(subset / "selection.json", output / "selection.json")
        if reference_files:
            (output / "reference-preflight").mkdir()
            for path in reference_files:
                shutil.copy2(path, output / "reference-preflight" / path.name)
        manifest = {"version": 1, "kind": "natsql_scale_controls", "members": members,
                    "main_matrix_directory": os.path.relpath(matrix_directory, output),
                    "main_matrix_sha256": sha(matrix_directory / "matrix.json"),
                    "selection_sha256": sha(output / "selection.json"),
                    "unique_questions": subset_manifest["case_count"],
                    "questions_added_to_parent_distinct_count": 0,
                    "natsql_planned_calls": sum(len(m["matched_batches"]) for m in members),
                    "natsql_planned_outputs": subset_manifest["case_count"] * len(members),
                    "converter_profile": {"network_profile": network_profile, "literal_profile": literal_profile,
                                          "use_from_info": False,
                                          "upstream_commit": natsql_adapter.UPSTREAM_COMMIT},
                    "reference_coverage": {"preflight_supplied": coverage is not None,
                                           "common_reference_case_ids": sorted(common_ids),
                                           "common_reference_case_count": len(common_ids),
                                           "reference_preflight_failed_ids": sorted(target_ids - common_ids),
                                           "clean_whole_group_count": sum(b["reference_group_passed"] for b in members[0]["matched_batches"]),
                                           "clean_whole_group_question_count": sum(len(b["case_ids"]) for b in members[0]["matched_batches"] if b["reference_group_passed"]),
                                           "frozen_evidence": {path.name: sha(path) for path in reference_files}},
                    "adapter_sha256": sha(Path(natsql_adapter.__file__)),
                    "guide_sha256": sha(guide), "model_calls_during_preparation": 0,
                    "limitations": ["Post-hoc structural subset selected without consulting model outcomes",
                                    "Original complete B4 groups, public facts and settings are matched exactly",
                                    "Full 1000-question SQL/SQLP totals cannot be compared to this smaller subset",
                                    ("The explicit base diagnostic profile omits legacy join-network enlargement"
                                     if network_profile == "base" else
                                     "The indexed enlargement profile requires exact official-network equivalence validation"
                                     if network_profile == "enlarged-indexed" else
                                     "The pinned official enlarged network preparation is unchanged"),
                                    "Structural eligibility does not guarantee converter coverage; report prepared-backend failures separately",
                                    "This measures the model plus declared converter profile, not NatSQL syntax in isolation",
                                    "All original tables, relationships and rows remain supplied",
                                    "Same questions and models are correlated, not new independent questions"]}
        write_new(output / "controls.json", manifest)
        return manifest
    except Exception:
        shutil.rmtree(output)
        raise


def _bound_report(directory, request):
    path = directory / "runs" / request["id"]
    if not (path / "report.json").exists():
        return None
    report, saved = read(path / "report.json"), read(path / "response.json")
    _require(report["request_sha256"] == saved["request_sha256"] == request["request_sha256"],
             "Saved report/response differs from request")
    generated = parse_response(saved["response"])
    _require(generated == report["generation"], "Saved generation differs from raw response")
    _require(generated["model"] == request["payload"]["model"], "Returned model changed")
    _require(report["case_ids"] == request["case_ids"] == [c["case_id"] for c in report["cases"]],
             "Saved output coverage/order changed")
    parsed = (parse_batch_queries(generated["raw_text"], request["case_ids"])
              if generated["status"] == "completed" else {"queries": {}})
    for case in report["cases"]:
        _require(case["query"] == parsed["queries"].get(case["case_id"], ""), "Scored query changed")
        _require(case.get("token_usage") is None, "Whole-batch usage allocated to individual query")
    return report


def analyze(directory, *, require_complete=False, rescore=True, ledger=None, recovery_configs=None):
    directory = Path(directory).resolve()
    manifest = read(directory / "controls.json")
    _require(manifest["kind"] == "natsql_scale_controls", "Unexpected control kind")
    _require(sha(directory / "selection.json") == manifest["selection_sha256"], "Selection changed")
    main_matrix = (directory / manifest["main_matrix_directory"]).resolve()
    _require(sha(main_matrix / "matrix.json") == manifest["main_matrix_sha256"], "Main model matrix changed")
    verify_matrix(main_matrix)
    by_model = {}
    complete = True
    total_new_batches = total_new_outputs = reexecuted = 0
    for member in manifest["members"]:
        folder, main = directory / member["directory"], (directory / member["main_directory"]).resolve()
        _require(sha(folder / "design.json") == member["design_sha256"], "NatSQL design changed")
        _require(sha(main / "design.json") == member["main_design_sha256"], "Main model design changed")
        design, main_design = verify(folder), verify(main)
        _require(design.get("converter_profile") == {key: manifest["converter_profile"][key]
                 for key in ("network_profile", "literal_profile")},
                 "Frozen converter profile changed")
        requests = {r["id"]: r for r in design["requests"]}
        originals = {r["id"]: r for r in main_design["requests"]}
        original_ids = {binding["id"] for batch in member["matched_batches"]
                        for binding in batch["counterpart_requests"].values()}
        nat_accounting = study_accounting(folder, ledger=ledger, recovery_configs=recovery_configs)
        main_accounting = study_accounting(main, ledger=ledger, recovery_configs=recovery_configs,
                                           request_ids=original_ids)
        datasets = {d["id"]: d for d in design["datasets"]}
        groups, usages, counts, unknown = defaultdict(list), defaultdict(Counter), Counter(), Counter()
        common_records, clean_records = defaultdict(list), defaultdict(list)
        clean_usages, clean_counts, clean_unknown = defaultdict(Counter), Counter(), Counter()
        unobserved, clean_unobserved = Counter(), Counter()
        scoped_received, clean_received = Counter(), Counter()
        common_ids = set(manifest["reference_coverage"]["common_reference_case_ids"])
        contrasts = Counter()
        for batch in member["matched_batches"]:
            for language in ("sql", "sqlp", "natsql"):
                binding = batch["natsql_request"] if language == "natsql" else batch["counterpart_requests"][language]
                request = (requests if language == "natsql" else originals)[binding["id"]]
                _require(request["request_sha256"] == binding["request_sha256"], "Matched batch binding changed")
                _require(request["case_ids"] == batch["case_ids"], "Matched batch question order changed")
                accounted = (nat_accounting if language == "natsql" else main_accounting)["by_request"][request["id"]]
                unobserved[language] += accounted["unobserved_api_attempts"]
                scoped_received[language] += accounted["received_response_calls"]
                usages[language].update(accounted["known_response_token_usage"])
                unknown[language] += accounted["unknown_received_usage_calls"]
                if batch["reference_group_passed"]:
                    clean_unobserved[language] += accounted["unobserved_api_attempts"]
                    clean_received[language] += accounted["received_response_calls"]
                    clean_usages[language].update(accounted["known_response_token_usage"])
                    clean_unknown[language] += accounted["unknown_received_usage_calls"]
                report = _bound_report(folder if language == "natsql" else main, request)
                if report is None:
                    complete = False
                    continue
                groups[language].extend(report["cases"])
                common_records[language].extend(record for record in report["cases"] if record["case_id"] in common_ids)
                counts[language] += 1
                if batch["reference_group_passed"]:
                    clean_records[language].extend(report["cases"])
                    clean_counts[language] += 1
                if language != "natsql":
                    continue
                total_new_batches += 1
                total_new_outputs += len(report["cases"])
                dataset = datasets[request["dataset_id"]]
                primary = {c["id"]: c for c in read(folder / dataset["workload"])["cases"]}
                alternates = [{"snapshot": snapshot, "cases": {c["id"]: c for c in read(folder / snapshot["workload"])["cases"]}}
                              for snapshot in dataset.get("snapshots", []) if snapshot["database"] != dataset["database"]]
                for record in report["cases"]:
                    _require(record.get("conversion", {}).get("compiler_options", {}).get("network_profile",
                             manifest["converter_profile"]["network_profile"]) == manifest["converter_profile"]["network_profile"],
                             "Recorded conversion profile changed")
                    converted = record.get("conversion", {}).get("ok") is True
                    if converted:
                        _require(record["conversion"].get("compiler_options", {}).get("network_profile", "enlarged") ==
                                 manifest["converter_profile"]["network_profile"], "Successful conversion profile was not retained")
                        _require(record["conversion"].get("compiler_options", {}).get("literal_profile", "native") ==
                                 manifest["converter_profile"]["literal_profile"], "Successful literal profile was not retained")
                    if rescore and converted:
                        result = _rescore_supported(folder, dataset, primary[record["case_id"]], record["sql"], "sql", design)
                        _require(result["correct"] == record["correct"], "Saved converted SQL changed primary match")
                        reexecuted += 1
                    if rescore:
                        for alternate in alternates:
                            contrasts["attempts"] += 1
                            if converted:
                                result = _rescore_supported(folder, dataset, alternate["cases"][record["case_id"]],
                                                            record["sql"], "sql", design, alternate["snapshot"])
                                contrasts["correct"] += result["correct"]
                                contrasts["primary_match_contrast_mismatch"] += bool(record["correct"] and not result["correct"])
        language_results = {}
        for language in ("sql", "sqlp", "natsql"):
            records = groups[language]
            _require(len({c["case_id"] for c in records}) == len(records), "Duplicate question in language subset")
            if len(records) != manifest["unique_questions"]:
                complete = False
            language_results[language] = {"generated": len(records), "correct": sum(c["correct"] is True for c in records),
                "batches": counts[language],
                "known_response_token_usage": dict(usages[language]),
                "token_usage": dict(usages[language]) if not unknown[language] and not unobserved[language] and scoped_received[language] else None,
                "unobserved_api_attempts": unobserved[language],
                "received_response_calls": scoped_received[language],
                "api_token_usage_complete": not unknown[language] and not unobserved[language],
                "usage_unknown_batches": unknown[language],
                "errors": dict(Counter((c.get("error") or {}).get("code", "wrong_answer") for c in records if not c["correct"])),
                "common_reference_passed": {"generated": len(common_records[language]),
                    "correct": sum(c["correct"] is True for c in common_records[language]),
                    "usage_accounting": "No per-question usage allocation; use complete reference-passed batches below"},
                "reference_passed_whole_batches": {"generated": len(clean_records[language]),
                    "correct": sum(c["correct"] is True for c in clean_records[language]),
                    "batches": clean_counts[language],
                    "known_response_token_usage": dict(clean_usages[language]),
                    "unobserved_api_attempts": clean_unobserved[language],
                    "received_response_calls": clean_received[language],
                    "api_token_usage_complete": not clean_unknown[language] and not clean_unobserved[language],
                    "token_usage": dict(clean_usages[language]) if not clean_unknown[language] and not clean_unobserved[language] and clean_received[language] else None},
                "reference_preflight_failed_targets": {"generated": sum(c["case_id"] not in common_ids for c in records),
                    "correct": sum(c["correct"] is True and c["case_id"] not in common_ids for c in records)}}
        by_model[member["model"]] = {"languages": language_results, "natsql_contrast_rescoring": dict(contrasts),
            "accounting_binding_scope": nat_accounting["binding_scope"],
            "natsql_recovery_manifest_bindings": nat_accounting["recovery_manifest_bindings"]}
    _require(not require_complete or complete, "NatSQL control comparison is incomplete")
    known_nat = Counter({field: 0 for field in ("input_tokens", "output_tokens", "total_tokens")})
    for model in by_model.values():
        known_nat.update(model["languages"]["natsql"]["known_response_token_usage"])
    unknown_nat = sum(model["languages"]["natsql"]["unobserved_api_attempts"] for model in by_model.values())
    missing_nat = sum(model["languages"]["natsql"]["usage_unknown_batches"] for model in by_model.values())
    return {"version": 1, "kind": "natsql_scale_control_summary", "completed": complete,
            "unique_questions": manifest["unique_questions"], "questions_added_to_parent_distinct_count": 0,
            "completed_new_natsql_batches": total_new_batches, "generated_new_natsql_outputs": total_new_outputs,
            "reexecuted_converted_primary_outputs": reexecuted, "by_model": by_model,
            "converter_profile": manifest["converter_profile"], "model_calls_during_analysis": 0,
            "known_new_natsql_response_token_usage": dict(known_nat),
            "new_natsql_api_token_usage": dict(known_nat) if not unknown_nat and not missing_nat else None,
            "new_natsql_unobserved_api_attempts": unknown_nat,
            "reference_coverage": manifest["reference_coverage"],
            "limitations": manifest["limitations"]}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "analyze"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--main-matrix", type=Path)
    parser.add_argument("--subset", type=Path)
    parser.add_argument("--guide", type=Path)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--network-profile", choices=("base", "enlarged", "enlarged-indexed"), default="enlarged-indexed")
    parser.add_argument("--literal-profile", choices=("native", "preserved"), default="preserved")
    parser.add_argument("--schema-cache", type=Path)
    parser.add_argument("--reference-report", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--no-rescore", action="store_true")
    parser.add_argument("--recovery-manifest", type=Path, action="append")
    parser.add_argument("--recovery-manifest-sha256", action="append")
    args = parser.parse_args()
    if args.action == "prepare":
        if not all((args.main_matrix, args.subset, args.guide)):
            parser.error("prepare requires --main-matrix, --subset and --guide")
        result = prepare(args.main_matrix, args.subset, args.directory, guide=args.guide,
                         ledger=args.ledger, network_profile=args.network_profile,
                         literal_profile=args.literal_profile, schema_cache=args.schema_cache,
                         reference_report=args.reference_report)
    else:
        if bool(args.recovery_manifest) != bool(args.recovery_manifest_sha256) or (
                args.recovery_manifest and len(args.recovery_manifest) != len(args.recovery_manifest_sha256)):
            parser.error("Each recovery manifest requires its SHA256")
        configs = ([{"manifest": path, "sha256": value} for path, value in
                    zip(args.recovery_manifest, args.recovery_manifest_sha256)] if args.recovery_manifest else None)
        result = analyze(args.directory, require_complete=args.require_complete, rescore=not args.no_rescore,
                         ledger=args.ledger, recovery_configs=configs)
        target = args.directory / "summary.json"
        temporary = target.with_suffix(".json.pending")
        temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(target)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

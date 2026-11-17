"""Select a structural, outcome-independent NatSQL follow-up from frozen B4 groups.

Named SQLP roles can require several bindings of one physical table. This
conservative follow-up admits only complete original four-question batches
whose individual queries require at most one binding per physical table.
It does not claim this rule proves NatSQL representability or conversion success.
"""

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from .prepare_scale_study import DOMAINS
from .run_pilot import digest


RULE = ("Select a complete original four-question group only when every query's "
        "projection and predicate paths reach each physical table through at "
        "most one distinct role prefix, counting the root as the empty prefix. "
        "Shared prefixes reuse one binding; competing roles and table revisits "
        "through a different prefix exclude the entire original group.")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def binding_conflicts(plan, catalog, root_table):
    """Return structural duplicate-table bindings, without reading any outcomes."""
    references = {(ref["source_table"], ref["feature"]): ref["target_table"] for ref in catalog["references"]}
    columns = {table["name"]: {column["name"] for column in table["columns"]} for table in catalog["tables"]}
    if root_table not in columns:
        raise ValueError("Unknown root table")
    reached, conflicts = {root_table: ()}, {}
    for path in list(plan["projections"]) + [predicate[0] for predicate in plan["predicates"]]:
        if not path:
            raise ValueError("Empty private interpretation path")
        table, prefix = root_table, ()
        for role in path[:-1]:
            if (table, role) not in references:
                raise ValueError("Invalid private interpretation relationship")
            prefix, table = prefix + (role,), references[table, role]
            if table in reached and reached[table] != prefix:
                key = (table, reached[table], prefix)
                conflicts[key] = {"table": table, "first_prefix": list(reached[table]), "other_prefix": list(prefix)}
            else:
                reached[table] = prefix
        if path[-1] not in columns[table]:
            raise ValueError("Invalid private interpretation terminal field")
    return list(conflicts.values())


def select(source, original_study):
    """Read source plans/catalog and original request design, never model results."""
    source, original_study = Path(source).resolve(), Path(original_study).resolve()
    source_manifest, design = _read(source / "manifest.json"), _read(original_study / "design.json")
    if design["batch_size"] != 4:
        raise ValueError("This follow-up requires the original B4 protocol")
    for relative, expected in source_manifest["files_sha256"].items():
        if _sha(source / relative) != expected:
            raise ValueError("Source benchmark changed: " + relative)
    frozen_manifest = "inputs/supported/manifest.json"
    if (_sha(source / "manifest.json") != design["frozen_sha256"].get(frozen_manifest) or
            _sha(original_study / frozen_manifest) != design["frozen_sha256"].get(frozen_manifest)):
        raise ValueError("Original study is not bound to the supplied benchmark")
    datasets, totals = [], Counter()
    for dataset in source_manifest["datasets"]:
        domain = dataset["id"]
        root = DOMAINS[domain]["root"]
        plans = _read(source / dataset["directory"] / "oracle-plans.json")
        catalog = _read(source / dataset["directory"] / "catalog.json")
        plan_by_id = {plan["id"]: plan for plan in plans}
        if len(plan_by_id) != len(plans):
            raise ValueError("Duplicate source case IDs")
        conflicts = {plan["id"]: binding_conflicts(plan, catalog, root) for plan in plans}
        request_groups = {}
        for request in design["requests"]:
            if request["dataset_id"] != "supported/" + domain or request["language"] not in ("sql", "sqlp"):
                continue
            if digest(request["payload"]) != request["request_sha256"]:
                raise ValueError("Original request hash mismatch")
            group = tuple(request["case_ids"])
            language = request["language"]
            if language in request_groups.setdefault(group, {}):
                raise ValueError("Duplicate original language group")
            request_groups[group][language] = {key: request[key] for key in ("id", "request_sha256")}
        expected_groups = [tuple(plan["id"] for plan in plans[start:start + 4]) for start in range(0, len(plans), 4)]
        if set(request_groups) != set(expected_groups):
            raise ValueError("Original groups do not match source case ordering")
        groups, selected_ids = [], []
        for index, ids in enumerate(expected_groups):
            if set(request_groups[ids]) != {"sql", "sqlp"}:
                raise ValueError("Both original language requests must exist")
            rejected = {case_id: conflicts[case_id] for case_id in ids if conflicts[case_id]}
            eligible = len(ids) == 4 and not rejected
            group = {"original_group_index": index, "case_ids": list(ids), "eligible": eligible,
                     "exclusion": None if eligible else ("partial_original_group" if len(ids) != 4 else "repeated_physical_table_binding"),
                     "conflicting_cases": rejected, "original_requests": request_groups[ids]}
            groups.append(group)
            if eligible:
                selected_ids.extend(ids)
        selected_plans = [plan_by_id[case_id] for case_id in selected_ids]
        entry = {"id": domain, "directory": dataset["directory"], "root_table": root,
                 "source_case_count": len(plans), "individually_eligible_case_count": sum(not value for value in conflicts.values()),
                 "complete_original_group_count": sum(len(group["case_ids"]) == 4 for group in groups),
                 "partial_original_group_count": sum(len(group["case_ids"]) != 4 for group in groups),
                 "selected_group_count": sum(group["eligible"] for group in groups), "selected_case_count": len(selected_ids),
                 "selected_case_ids": selected_ids, "groups": groups,
                 "selected_depth_counts": dict(Counter(str(plan["depth"]) for plan in selected_plans)),
                 "selected_family_counts": dict(Counter(plan["family"] for plan in selected_plans))}
        datasets.append(entry)
        for field in ("source_case_count", "individually_eligible_case_count", "complete_original_group_count",
                      "partial_original_group_count", "selected_group_count", "selected_case_count"):
            totals[field] += entry[field]
    return {"version": 1, "rule": RULE, "selection_inputs": "catalog, private interpretation paths and original frozen request groups only",
            "model_outcomes_read": False, "batch_size": 4, "source_manifest_sha256": _sha(source / "manifest.json"),
            "original_design_sha256": _sha(original_study / "design.json"),
            "original_settings": design["settings"], "totals": dict(totals), "datasets": datasets}


def prepare(source, original_study, output):
    """Copy unchanged full data/catalogs while retaining complete eligible groups."""
    source, original_study, output = Path(source).resolve(), Path(original_study).resolve(), Path(output).resolve()
    selection = select(source, original_study)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        source_manifest = _read(source / "manifest.json")
        source_datasets = {dataset["id"]: dataset for dataset in source_manifest["datasets"]}
        datasets = []
        for entry in selection["datasets"]:
            if not entry["selected_case_ids"]:
                continue
            dataset = deepcopy(source_datasets[entry["id"]])
            directory = output / entry["directory"]
            shutil.copytree(source / entry["directory"], directory)
            selected = set(entry["selected_case_ids"])
            plans = _read(directory / "oracle-plans.json")
            _write(directory / "oracle-plans.json", [plan for plan in plans if plan["id"] in selected])
            templates, abstracts, primary = set(), set(), None
            changed_cases = set()
            for snapshot in dataset["snapshots"]:
                workload_path = output / snapshot["workload"]
                workload = _read(workload_path)
                workload["cases"] = [case for case in workload["cases"] if case["id"] in selected]
                if [case["id"] for case in workload["cases"]] != entry["selected_case_ids"]:
                    raise ValueError("Filtered snapshot did not preserve original group ordering")
                _write(workload_path, workload)
                bags = {case["id"]: Counter(map(tuple, case["expected_rows"])) for case in workload["cases"]}
                if primary is None:
                    primary = bags
                else:
                    changed_cases.update(case_id for case_id in bags if bags[case_id] != primary[case_id])
                if _sha(output / snapshot["database"]) != snapshot["database_sha256"]:
                    raise ValueError("Copy changed the full original database")
                snapshot.update(workload_sha256=_sha(workload_path), prepared_sql_matches=len(selected),
                                prepared_sqlp_matches=len(selected),
                                empty_answer_cases=sum(not case["expected_rows"] for case in workload["cases"]),
                                duplicate_bag_cases=sum(len(case["expected_rows"]) > len(set(map(tuple, case["expected_rows"]))) for case in workload["cases"]))
                templates.update(case["design"]["template_id"] for case in workload["cases"])
                abstracts.update(case["design"]["abstract_template_id"] for case in workload["cases"])
            if _sha(directory / "catalog.json") != _sha(source / entry["directory"] / "catalog.json"):
                raise ValueError("Copy changed the full original catalog")
            dataset.update(case_count=len(selected), structural_template_count=len(templates),
                           abstract_structural_template_count=len(abstracts), reused_template_cases=len(selected) - len(templates),
                           family_counts=entry["selected_family_counts"], depth_counts=entry["selected_depth_counts"],
                           cases_with_changed_answer_on_contrast=len(changed_cases))
            datasets.append(dataset)
        _write(output / "selection.json", selection)
        manifest = {"version": 1, "study": "structural NatSQL supported-subset follow-up", "case_count": selection["totals"]["selected_case_count"],
                    "datasets": datasets, "batch_size": 4, "selected_original_group_count": selection["totals"]["selected_group_count"],
                    "selection_rule": RULE, "selection_file": "selection.json", "selection_uses_model_outcomes": False,
                    "source_manifest_sha256": selection["source_manifest_sha256"], "original_design_sha256": selection["original_design_sha256"],
                    "builder_sha256": _sha(__file__), "model_calls": 0, "human_answer_review": "pending",
                    "files_sha256": {str(path.relative_to(output)): _sha(path) for path in sorted(output.rglob("*")) if path.is_file()},
                    "limitations": ["Post-hoc structural subset, selected without consulting model outcomes",
                                    "At most one physical-table binding is a conservative eligibility rule, not proof of NatSQL representability",
                                    "All original data rows, catalog tables, relationships and contrast snapshots remain unchanged",
                                    "SQL/SQLP comparison must use original saved requests for these exact complete B4 groups and count whole-batch usage once",
                                    "Subset excludes all depth-4, depth-6 and depth-8 tasks in this frozen workload",
                                    "Subset results cannot be compared against the full 1000-question SQL/SQLP totals"]}
        _write(output / "manifest.json", manifest)
        return manifest
    except Exception:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("original_study", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args()
    result = prepare(args.source, args.original_study, args.output) if args.output else select(args.source, args.original_study)
    print(json.dumps({"ok": True, "output": str(args.output) if args.output else None,
                      "case_count": result.get("case_count", result.get("totals", {}).get("selected_case_count")),
                      "selected_group_count": result.get("selected_original_group_count", result.get("totals", {}).get("selected_group_count"))}))


if __name__ == "__main__":
    main()

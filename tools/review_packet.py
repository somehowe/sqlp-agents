"""Export version-bound study questions and answers for a person's review."""

import argparse
from collections import Counter
import csv
import html
import json
import os
from pathlib import Path
import re
import shutil

from .analyze_runs import analyze_report
from .experiment_kit import _json, _sha, verify_kit
from .run_comparison import _rows
from .run_pilot import digest


CSV_FIELDS = ["case_number", "case_sha256", "reviewer", "decision", "notes"]
FIXED_FILES = {"data.sqlite", "catalog.json", "cases.json", "review.md"}
NOTE = ("Recorded annotations do not authenticate independent human review, "
        "establish answer correctness, or provide model results.")


def _write(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
        output.write(content)


def _write_json(path, value):
    _write(path, json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2) + "\n")


def _fence(value, language=""):
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", value)), default=0))
    return fence + language + "\n" + value + "\n" + fence + "\n"


def _plain(value):
    value = html.escape(str(value), quote=False).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def _identifier(value):
    return value if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) else '"' + value.replace('"', '""') + '"'


def _answer_cell(cell):
    kind, value = cell["type"], cell["value"]
    displayed = ("NULL" if kind == "null" else json.dumps(value, ensure_ascii=False)
                 if kind == "string" else str(value))
    return _plain(displayed + " [" + kind + "]")


def _markdown(catalog, cases, scope, kit_hash, preview_rows):
    lines = ["# Workload review", "", "Scope: " + scope, "", "Kit SHA-256: " + kit_hash, "",
             "Check each question's meaning, relationship roles, expected values, and duplicate counts.",
             "Use data.sqlite and catalog.json to inspect the frozen data. Reference queries and",
             "compiler traces explain the supplied answer; they are not independent proof of intent.",
             "The packet does not contain the separate Python oracle plans. No human review has",
             "been recorded automatically. Fill review.csv with your name, decision, and notes.",
             "Decisions: ok (all aspects checked), revise, or uncertain. Leave unfinished rows blank.",
             "The complete typed answer rows are in cases.json; previews below can be truncated.",
             "", "## Supplied catalog", ""]
    for table in catalog["tables"]:
        lines += ["### " + _plain(table["name"]), ""]
        lines += ["- " + _plain(column["name"]) + ": " + column["type"] +
                  (" (declared unique key)" if column.get("unique") else "") for column in table["columns"]]
        lines.append("")
    lines += ["### Named relationships", ""]
    for reference in catalog["references"]:
        equality = (reference["source_table"] + "." + reference["source_column"] + " = " +
                    reference["target_table"] + "." + reference["target_column"])
        lines += ["- " + _plain(reference["source_table"] + "." + reference["feature"]) +
                  ": " + _plain(equality)]
    for case in cases:
        lines += ["", "## Case " + str(case["number"]) + ": " + _plain(case["id"]), "",
                  _plain(case["question"]), "", "### Reference SQL", "", _fence(case["sql"], "sql"),
                  "### Reference SQLP", "", _fence(case["sqlp"], "sql"),
                  "### Compiled SQL and parameters", "", _fence(case["compiled_sql"], "sql"),
                  _fence(json.dumps(case["parameters"], ensure_ascii=True, indent=2), "json"),
                  "### Paths and relationship joins", ""]
        trace = case["trace"]
        for path in trace["paths"]:
            name = ".".join(_identifier(part) for part in [path["root"], *path["features"]])
            steps = []
            for index in path["join_ids"]:
                join = trace["joins"][index]
                source, target = join["source"], join["target"]
                steps.append(join["role"] + ": " + source["table"] + "." + source["column"] +
                             " = " + target["table"] + "." + target["column"])
            lines += ["- " + _plain(path["clause"] + " " + name) + ": " +
                      _plain("; ".join(steps) if steps else "direct column; no reference join")]
        counts = Counter(json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
                         for row in case["expected_rows"])
        preview = [{"count": count, "row": json.loads(row)} for row, count in list(counts.items())[:preview_rows]]
        lines += ["", "### Expected answer", "",
                  str(len(case["expected_rows"])) + " rows; " + str(len(counts)) + " distinct rows. " +
                  "Showing " + str(len(preview)) + " of " + str(len(counts)) + " distinct rows.",
                  "Values include their storage type; Count is the full row's multiplicity.", ""]
        if preview:
            lines += ["| Count | " + " | ".join(_plain(column) for column in case["columns"]) + " |",
                      "| --- | " + " | ".join("---" for _ in case["columns"]) + " |"]
            lines += ["| " + str(item["count"]) + " | " + " | ".join(_answer_cell(cell) for cell in item["row"]) + " |"
                      for item in preview]
            lines.append("")
        else:
            lines += ["Empty answer; projection width: " + str(case["column_count"]) + ".", ""]
        if len(preview) < len(counts):
            lines += ["**Preview truncated. Read cases.json for every row and its multiplicity.**", ""]
    return "\n".join(lines) + "\n"


def _cases_for_review(workload, report, scope):
    analysis = analyze_report(report)
    if (report["origin"] != "manual" or report["scope"] != scope or
            analysis["paired_correctness"]["both_correct"] != len(workload["cases"])):
        raise ValueError("Review needs complete, correct prepared pairs for the selected split")
    recorded = {case["id"]: case for case in report["cases"]}
    if set(recorded) != {case["id"] for case in workload["cases"]}:
        raise ValueError("Prepared report and workload have different cases")
    cases = []
    for number, original in enumerate(workload["cases"], 1):
        checked = recorded[original["id"]]
        runs = {run["language"]: run for run in checked["runs"]}
        if (checked["question"] != original["question"] or
                checked["column_count"] != original["column_count"] or
                checked["expected_rows"] != _rows(original["expected_rows"]) or
                any(runs[language]["query"] != original[language] for language in ("sql", "sqlp"))):
            raise ValueError("Prepared records differ from the frozen question, queries, or expected answer")
        cases.append({"number": number, "case_sha256": digest(original), "id": original["id"],
                      "question": original["question"], "column_count": original["column_count"],
                      "columns": runs["sqlp"]["columns"], "expected_rows": checked["expected_rows"],
                      "sql": original["sql"], "sqlp": original["sqlp"],
                      "compiled_sql": runs["sqlp"]["sql"], "parameters": runs["sqlp"]["parameters"],
                      "trace": runs["sqlp"]["trace"]})
    return cases


def create_review_packet(kit_dir, output_dir, *, scope="development", preview_rows=20):
    verified = verify_kit(kit_dir)
    if scope not in verified["splits"]:
        raise ValueError("Split not present in this kit")
    if type(preview_rows) is not int or not 1 <= preview_rows <= 100:
        raise ValueError("preview_rows must be an integer from 1 to 100")
    kit = Path(kit_dir).resolve()
    manifest = _json(kit / "manifest.json")
    workload = _json(kit / "evaluation" / (scope + ".json"))
    report = _json(kit / "validation" / (scope + ".json"))
    cases = _cases_for_review(workload, report, scope)
    catalog = _json(kit / "public/catalog.json")
    markdown = _markdown(catalog, cases, scope, verified["kit_sha256"], preview_rows)
    output = Path(output_dir).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700)
    with (kit / "data.sqlite").open("rb") as source:
        descriptor = os.open(output / "data.sqlite", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            shutil.copyfileobj(source, destination)
    if _sha(output / "data.sqlite") != manifest["database_sha256"]:
        raise ValueError("Database changed while exporting the review packet")
    _write_json(output / "catalog.json", catalog)
    _write_json(output / "cases.json", {"version": 1, "scope": scope, "cases": cases})
    _write(output / "review.md", markdown)
    descriptor = os.open(output / "review.csv", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as sheet:
        writer = csv.DictWriter(sheet, fieldnames=CSV_FIELDS)
        writer.writeheader()
        # No arbitrary question/identifier text in spreadsheet cells: only
        # generated integers/hashes and blank fields for a person's notes.
        writer.writerows({"case_number": case["number"], "case_sha256": case["case_sha256"],
                          "reviewer": "", "decision": "", "notes": ""} for case in cases)
    if verify_kit(kit)["kit_sha256"] != verified["kit_sha256"]:
        raise ValueError("Kit changed while exporting the review packet")
    packet = {"version": 1, "kind": "workload_review_packet", "scope": scope,
              "kit_sha256": verified["kit_sha256"], "preview_rows": preview_rows,
              "source_workload_sha256": _sha(kit / "evaluation" / (scope + ".json")),
              "source_validation_sha256": _sha(kit / "validation" / (scope + ".json")),
              "files": {name: _sha(output / name) for name in sorted(FIXED_FILES)},
              "cases": [{"number": case["number"], "id": case["id"], "sha256": case["case_sha256"]}
                        for case in cases], "note": NOTE}
    packet["packet_sha256"] = digest(packet)
    _write_json(output / "manifest.json", packet)  # Completion marker, written last.
    return check_review_packet(output, kit)


def check_review_packet(packet_dir, kit_dir=None):
    directory = Path(packet_dir).resolve()
    for name in FIXED_FILES | {"manifest.json", "review.csv"}:
        if (directory / name).is_symlink():
            raise ValueError("Review packet files must not be symlinks")
    packet = _json(directory / "manifest.json")
    if (not isinstance(packet, dict) or type(packet.get("version")) is not int or packet["version"] != 1 or
            packet.get("kind") != "workload_review_packet" or packet.get("scope") not in ("development", "held_out")):
        raise ValueError("Expected a completed version-1 review packet")
    if packet.get("packet_sha256") != digest({key: value for key, value in packet.items() if key != "packet_sha256"}):
        raise ValueError("Review packet manifest checksum differs")
    if not isinstance(packet.get("files"), dict) or set(packet["files"]) != FIXED_FILES:
        raise ValueError("Review packet is missing its fixed artifacts")
    for name, checksum in packet["files"].items():
        if not (directory / name).is_file() or _sha(directory / name) != checksum:
            raise ValueError("Review artifact changed or is missing: " + name)
    payload = _json(directory / "cases.json")
    if (not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("scope") != packet["scope"] or
            not isinstance(payload.get("cases"), list) or not isinstance(packet.get("cases"), list)):
        raise ValueError("Review case scope differs")
    if kit_dir is not None:
        verified = verify_kit(kit_dir)
        if verified["kit_sha256"] != packet["kit_sha256"]:
            raise ValueError("Review packet belongs to a different or stale kit")
        kit, scope = Path(kit_dir).resolve(), packet["scope"]
        if (_sha(kit / "evaluation" / (scope + ".json")) != packet["source_workload_sha256"] or
                _sha(kit / "validation" / (scope + ".json")) != packet["source_validation_sha256"]):
            raise ValueError("Review packet source versions differ")
        if (_sha(directory / "data.sqlite") != _sha(kit / "data.sqlite") or
                _json(directory / "catalog.json") != _json(kit / "public/catalog.json") or
                payload.get("cases") != _cases_for_review(_json(kit / "evaluation" / (scope + ".json")),
                                                          _json(kit / "validation" / (scope + ".json")), scope)):
            raise ValueError("Review data, catalog, or cases differ from the selected kit")
    expected = {}
    for index, (case, identity) in enumerate(zip(payload["cases"], packet["cases"]), 1):
        if (not isinstance(case, dict) or not isinstance(identity, dict) or
                type(case.get("number")) is not int or case["number"] != index or identity["number"] != index or
                case["id"] != identity["id"] or case["case_sha256"] != identity["sha256"]):
            raise ValueError("Review case identities differ")
        expected[str(index)] = case["case_sha256"]
    if not expected or len(payload["cases"]) != len(packet["cases"]):
        raise ValueError("Review packet case coverage differs")
    if (directory / "review.csv").stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Review notes exceed 2 MiB")
    counts = {"pending": 0, "ok": 0, "revise": 0, "uncertain": 0}
    seen, annotated = set(), 0
    with (directory / "review.csv").open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != CSV_FIELDS:
            raise ValueError("Review CSV header differs")
        for row in reader:
            if set(row) != set(CSV_FIELDS) or any(value is None for value in row.values()):
                raise ValueError("Malformed review CSV row")
            number, checksum = row["case_number"].strip(), row["case_sha256"].strip()
            if number not in expected or number in seen or checksum != expected[number]:
                raise ValueError("Review CSV has an unknown, duplicate, or mismatched case")
            seen.add(number)
            decision = row["decision"].strip().casefold()
            if decision not in ("", "ok", "revise", "uncertain"):
                raise ValueError("Use blank, ok, revise, or uncertain for review decisions")
            counts[decision or "pending"] += 1
            annotated += bool(decision and row["reviewer"].strip())
    if seen != set(expected):
        raise ValueError("Review CSV is missing cases")
    status = ("needs_changes" if counts["revise"] or counts["uncertain"] else
              "recorded" if annotated == len(expected) else "pending")
    return {"version": 1, "kind": packet["kind"], "ok": True, "directory": str(directory),
            "kit_sha256": packet["kit_sha256"], "scope": packet["scope"], "status": status,
            "case_count": len(expected), "annotated_cases": annotated, "decisions": counts, "note": NOTE}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("kit", type=Path); create.add_argument("output", type=Path)
    create.add_argument("--scope", choices=("development", "held_out"), default="development")
    create.add_argument("--preview-rows", type=int, default=20)
    status = commands.add_parser("status")
    status.add_argument("packet", type=Path); status.add_argument("--kit", type=Path)
    args = parser.parse_args()
    try:
        result = (create_review_packet(args.kit, args.output, scope=args.scope, preview_rows=args.preview_rows)
                  if args.command == "create" else check_review_packet(args.packet, args.kit))
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as error:
        print(json.dumps({"version": 1, "ok": False,
                          "error": {"stage": "review_packet", "message": str(error)}}, allow_nan=False))
        return 2
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

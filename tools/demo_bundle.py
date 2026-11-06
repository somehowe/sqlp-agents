"""Build the interactive lab from public fixtures, without keys or model calls."""

import argparse
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

from .demo_server import DemoLab, MAX_BUNDLE_BYTES, MAX_DATABASE_BYTES
from .prepare_path_study import prepare as prepare_path_study
from .run_comparison import DEFAULT_COMPILER, run_comparison
from .validate_dataset import _unique_object, load_catalog, validate_dataset

ROOT = Path(__file__).resolve().parents[1]
SELECTED_PATH_CASES = tuple("path-d" + str(depth) + "-05" for depth in (1, 2, 4, 6))


def _json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def _reject_constant(_):
    raise ValueError("Nonfinite JSON")


def _read_json(path):
    with Path(path).open("rb") as source:
        encoded = source.read(MAX_BUNDLE_BYTES + 1)
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ValueError("Fixture JSON exceeds four MiB")
    return json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)


def _write_json(path, value):
    with Path(path).open("xb") as destination:
        destination.write(_json_bytes(value))
    Path(path).chmod(0o400)


def _copy_database(source_path, destination, catalog):
    """Use SQLite backup so committed WAL pages are included without writes."""
    with closing(sqlite3.connect(Path(source_path).resolve().as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("BEGIN")
        logical_size = source.execute("PRAGMA page_count").fetchone()[0] * source.execute("PRAGMA page_size").fetchone()[0]
        if logical_size > MAX_DATABASE_BYTES or not validate_dataset(source, catalog, timeout_seconds=10)["ok"]:
            raise ValueError("Study snapshot is too large or fails catalog validation")
        deadline = time.monotonic() + 10
        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise TimeoutError("Copying the study snapshot timed out")
        with closing(sqlite3.connect(destination)) as copied:
            source.backup(copied, pages=128, progress=progress, sleep=0.0)
    if Path(destination).stat().st_size > MAX_DATABASE_BYTES:
        raise ValueError("Copied database exceeds the size limit")
    Path(destination).chmod(0o400)


def _prepared_label(text="Prepared reference queries; no model calls or model usage"):
    return {"label": text, "origin": "manual", "sql": {"token_usage": None},
            "sqlp": {"token_usage": None}}


def _scenario(case, catalog, snapshots, *, prefix="", saved=None):
    return {**{key: deepcopy(case[key]) for key in ("id", "question", "column_count", "sql", "sqlp")},
            "id": prefix + case["id"], "catalog": deepcopy(catalog), "snapshots": snapshots,
            "saved": saved or _prepared_label()}


def _import_study(study_root, output, compiler):
    """Copy only named public fixture members, then recheck every reference pair."""
    source = Path(study_root).resolve()
    output.mkdir(mode=0o700)
    catalog = load_catalog(source / "catalog.json")
    _write_json(output / "catalog.json", catalog)
    first = None
    for name in ("original", "contrast-1", "contrast-2"):
        owner = source if name == "original" else source / name
        destination = output if name == "original" else output / name
        if name != "original":
            destination.mkdir(mode=0o700)
        workload = _read_json(owner / "development.json")
        identity = {case["id"]: (case["question"], case["column_count"]) for case in workload["cases"]}
        if first is None:
            first = identity
        elif identity != first:
            raise ValueError("Study contrasts change question IDs, meanings, or projection widths")
        _copy_database(owner / "snapshot.sqlite", destination / "snapshot.sqlite", catalog)
        report = run_comparison(destination / "snapshot.sqlite", catalog, workload, compiler)
        if not report["ok"]:
            raise ValueError("Study reference pairs disagree with supplied expected row bags")
        _write_json(destination / "development.json", workload)
        _write_json(destination / "prepared-report.json", report)


def _duplicate_author_case(academic, catalog):
    """A keyed author can share a name; DISTINCT is then an observable mistake."""
    original = academic / "snapshot.sqlite"
    contrast = academic / "duplicate-names.sqlite"
    _copy_database(original, contrast, catalog)
    contrast.chmod(0o600)
    with closing(sqlite3.connect(contrast)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO AUTHOR (aid,name,uni_id,mentor_aid) "
                           "SELECT (SELECT MAX(aid)+1 FROM AUTHOR),name,uni_id,mentor_aid "
                           "FROM AUTHOR WHERE aid=1")
        connection.commit()
        if not validate_dataset(connection, catalog)["ok"]:
            raise ValueError("Duplicate-name contrast violates the catalog")
    contrast.chmod(0o400)
    snapshots = {}
    for name, database, label in (("original", original, "Different authors have different names"),
                                   ("contrast1", contrast, "Two different author records share a name")):
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            countries = dict(connection.execute("SELECT uid,country FROM UNIVERSITY"))
            rows = [[name] for _, name, university in connection.execute("SELECT aid,name,uni_id FROM AUTHOR ORDER BY aid")
                    if countries[university] == "CA"]
        snapshots[name] = {"database": str(database), "label": label, "expected_rows": rows}
    case = {"id": "duplicate-author-names",
            "question": "List author names at Canadian universities, one row per author record. Keep repeated names when different authors share a name.",
            "column_count": 1,
            "sql": "SELECT DISTINCT a.name FROM AUTHOR AS a INNER JOIN UNIVERSITY AS u ON a.uni_id=u.uid WHERE u.country='CA';",
            "sqlp": "SELECT DISTINCT a.name FROM AUTHOR AS a WHERE a.university.country='CA';"}
    return _scenario(case, catalog, snapshots, saved=_prepared_label(
        "Deliberately introduced DISTINCT: remove it to preserve author records. No model call."))


def build_bundle(output_dir, *, study_root=None, compiler_path=DEFAULT_COMPILER):
    """Publish a validated private bundle last; refuse any existing output path."""
    compiler = Path(compiler_path).resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise ValueError("Compiler executable unavailable; run make first")
    output = Path(output_dir).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700, exist_ok=False)
    frozen_compiler = output / "sqlpc"
    compiler_hash = hashlib.sha256(compiler.read_bytes()).hexdigest()
    shutil.copyfile(compiler, frozen_compiler)
    frozen_compiler.chmod(0o500)
    if (hashlib.sha256(compiler.read_bytes()).hexdigest() != compiler_hash or
            hashlib.sha256(frozen_compiler.read_bytes()).hexdigest() != compiler_hash):
        raise ValueError("Compiler changed while building the bundle")

    academic = output / "academic"
    academic.mkdir(mode=0o700)
    catalog = load_catalog(ROOT / "examples/academic/catalog.json")
    workload = _read_json(ROOT / "examples/academic/workload.json")
    _write_json(academic / "catalog.json", catalog)
    _write_json(academic / "development.json", workload)
    with closing(sqlite3.connect(academic / "snapshot.sqlite")) as connection:
        connection.executescript((ROOT / "examples/academic/data.sql").read_text(encoding="utf-8"))
    (academic / "snapshot.sqlite").chmod(0o400)
    prepared = run_comparison(academic / "snapshot.sqlite", catalog, workload, frozen_compiler)
    if not prepared["ok"]:
        raise ValueError("Academic reference pairs disagree with supplied expected rows")
    _write_json(academic / "prepared-report.json", prepared)
    scenarios = [_scenario(case, catalog, {"original": {
        "database": str(academic / "snapshot.sqlite"), "label": "Fictional academic records",
        "expected_rows": case["expected_rows"]}}, prefix="academic-") for case in workload["cases"]]

    study = output / "path-study"
    if study_root is None:
        prepare_path_study(study, compiler_path=frozen_compiler)
    else:
        _import_study(study_root, study, frozen_compiler)
    path_catalog = load_catalog(study / "catalog.json")
    versions = {"original": _read_json(study / "development.json"),
                **{name: _read_json(study / name / "development.json") for name in ("contrast-1", "contrast-2")}}
    indexed = {name: {case["id"]: case for case in value["cases"]} for name, value in versions.items()}
    for identifier in SELECTED_PATH_CASES:
        if any(identifier not in cases for cases in indexed.values()):
            raise ValueError("Study is missing a selected depth scenario")
        case = indexed["original"][identifier]
        snapshots = {name: {"database": str(study / ("snapshot.sqlite" if name == "original" else name + "/snapshot.sqlite")),
                            "label": "Synthetic primary records" if name == "original" else "Synthetic alternate records " + name[-1],
                            "expected_rows": cases[identifier]["expected_rows"]} for name, cases in indexed.items()}
        scenarios.append(_scenario(case, path_catalog, snapshots, prefix="controlled-"))
    scenarios.append(_duplicate_author_case(academic, catalog))
    bundle = {"version": 1, "title": "SQLP Query Lab — public offline examples", "scenarios": scenarios,
              "notes": ["Public project-authored synthetic fixtures; no recorded model experiment is included.",
                        "All interventions are local and manual; no model API call or invented token usage.",
                        "Supplied expected answers are not independently human-reviewed."]}
    # Validate the actual lab constructor and both starting queries on every
    # registered snapshot. The deliberate DISTINCT contrast must remain wrong.
    checks = []
    lab = DemoLab(bundle, frozen_compiler)
    try:
        for scenario in scenarios:
            for snapshot in scenario["snapshots"]:
                expected_correct = not (scenario["id"] == "duplicate-author-names" and snapshot == "contrast1")
                for language in ("sql", "sqlp"):
                    record = lab.run({"scenario": scenario["id"], "language": language,
                                      "query": scenario[language], "snapshot": snapshot})
                    if record["run"]["status"] != "ok" or record["run"]["correct"] is not expected_correct:
                        raise ValueError("Constructed scenario did not exhibit its declared behavior")
                    checks.append({"scenario": scenario["id"], "snapshot": snapshot,
                                   "language": language, "correct": record["run"]["correct"]})
    finally:
        lab.close()
    _write_json(output / "validation.json", {"kind": "manual_prepared_lab_checks", "ai_calls": 0,
                                             "checks": checks})
    encoded = _json_bytes(bundle)
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ValueError("Constructed bundle exceeds the lab size limit")
    manifest = {"version": 1, "kind": "offline_demo_bundle", "ai_calls": 0,
                "human_answer_review": "pending", "scenario_count": len(scenarios),
                "compiler_sha256": compiler_hash, "validation_executions": len(checks),
                "files_sha256": {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in sorted(output.rglob("*")) if path.is_file()}}
    manifest["files_sha256"]["bundle.json"] = hashlib.sha256(encoded).hexdigest()
    _write_json(output / "manifest.json", manifest)
    with (output / "bundle.json").open("xb") as destination:
        destination.write(encoded)
    (output / "bundle.json").chmod(0o400)
    return {"version": 1, "ok": True, "bundle": str(output / "bundle.json"),
            "compiler": str(frozen_compiler), "scenario_count": len(scenarios), "ai_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--study-root", type=Path)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    args = parser.parse_args()
    try:
        result = build_bundle(args.output_dir, study_root=args.study_root, compiler_path=args.compiler)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"version": 1, "ok": False, "error": str(error)}))
        return 2
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

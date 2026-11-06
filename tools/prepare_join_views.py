"""Catalog-only role-expanded SQL views for a declared engineering control.

No questions, reference queries, or expected answers are accepted. Views expand
all approved reference prefixes through a fixed depth, using the existing C++
compiler. This does not reproduce workload-driven view selection or discovery.
"""

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time

from .compiler_catalog import catalog_argument_file
from .run_comparison import DEFAULT_COMPILER, SetupError, _compile
from .run_pilot import _public_catalog, _snapshot_hash, digest
from .validate_dataset import PreflightTimeoutError, load_catalog, validate_dataset

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _alias(path, used):
    # Escaping every underscore makes the double-underscore separator
    # unambiguous. The suffix also handles SQLite's case-insensitive names.
    candidate = "__".join(part.replace("_", "_u") for part in path)
    if candidate.lower() in used:
        candidate += "__p" + digest(path)[:12]
    while candidate.lower() in used:
        candidate += "x"
    used.add(candidate.lower())
    return candidate


def _plans(catalog, depth, max_joins, max_columns):
    if type(depth) is not int or depth not in (1, 2, 3):
        raise ValueError("View depth must be one, two, or three")
    if type(max_joins) is not int or not 0 <= max_joins <= 63:
        raise ValueError("max_joins must be between zero and 63")
    if type(max_columns) is not int or not 1 <= max_columns <= 2000:
        raise ValueError("max_columns must be between one and 2000")
    public = _public_catalog(catalog)
    names = [table["name"] for table in public["tables"]]
    names += [column["name"] for table in public["tables"] for column in table["columns"]]
    names += [reference["feature"] for reference in public["references"]]
    if any(_IDENTIFIER.fullmatch(name) is None for name in names):
        raise ValueError("Join-view profile requires ASCII identifiers of at most 64 characters")
    if len(public["tables"]) > 256:
        raise ValueError("Join-view profile permits at most 256 root tables")
    tables = {table["name"]: table for table in public["tables"]}
    references = {name: [] for name in tables}
    for reference in public["references"]:
        references[reference["source_table"]].append(reference)
    for values in references.values():
        values.sort(key=lambda reference: reference["feature"])
    plans = []
    for root in sorted(tables):
        columns, prefixes, used = [], [((), root)], set()
        joins = 0
        for prefix, table_name in prefixes:
            for column in tables[table_name]["columns"]:
                path = [*prefix, column["name"]]
                columns.append({"name": _alias(path, used), "path": path,
                                "source_table": table_name, "source_column": column["name"],
                                "type": column["type"], "unique": not prefix and column["unique"]})
                if len(columns) > max_columns:
                    raise ValueError("Join-view column cap exceeded for root " + root)
            if len(prefix) < depth:
                for reference in references[table_name]:
                    joins += 1
                    if joins > max_joins:
                        raise ValueError("Join-view join cap exceeded for root " + root)
                    prefixes.append(((*prefix, reference["feature"]), reference["target_table"]))
        plans.append({"root_table": root, "columns": columns, "join_count": joins,
                      "prefixes": [{"roles": list(prefix), "table": table}
                                   for prefix, table in prefixes]})
    return public, plans


def _view_guide(views, depth):
    lines = ["# Optional catalog-generated SQL views", "",
             "These views expand every approved reference prefix through depth " + str(depth) + ".",
             "They were generated from the supplied catalog, without questions or answers.",
             "Each row corresponds to one row of its stated root table, including duplicates.",
             "Choose an original table or a generated view as the FROM root in the requested",
             "query language. Generated view columns are ordinary columns, accessible as",
             "root-alias.column in either SQL or SQLP. A flattened column denotes the",
             "root-relative role path shown below. Choosing the correct root and role",
             "remains necessary. SELECT DISTINCT is needed only when the question requests it.",
             "You may use these views or the original tables and approved relationship facts.",
             "The views contain no filtering, aggregation, or deduplication.", ""]
    for view in views:
        lines.append("View " + view["name"] + " (root " + view["root_table"] + "):")
        for column in view["columns"]:
            lines.append("- " + column["name"] + " = root." + ".".join(column["path"]))
        lines.append("")
    return "\n".join(lines)


def prepare_join_views(database_path, catalog, output_dir, *, depth=2, max_joins=63,
                       max_columns=2000, compiler_path=DEFAULT_COMPILER,
                       timeout_seconds=5.0, preflight_timeout_seconds=30.0,
                       max_snapshot_bytes=64 * 1024 * 1024):
    """Copy trusted data, add all bounded role views, validate, then publish.

    Existing output directories are refused. An incomplete reserved directory
    may remain after failure; metadata.json is the final completion marker.
    Only direct root columns retain declared unique flags. References are the
    unchanged base catalog references; no relationship discovery takes place.
    """
    for limit in (timeout_seconds, preflight_timeout_seconds):
        if type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0:
            raise ValueError("Timeouts must be finite and positive")
    if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0:
        raise ValueError("Snapshot byte cap must be positive")
    public, plans = _plans(catalog, depth, max_joins, max_columns)
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Join-view output directory must be new")
    compiler = Path(compiler_path).resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise SetupError("compiler", "Compiler executable unavailable; run make first")
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, cached_statements=0)) as source:
        source.execute("BEGIN")
        source_hash = _snapshot_hash(source, max_snapshot_bytes)
        checked_source = validate_dataset(source, public, timeout_seconds=preflight_timeout_seconds)
        if not checked_source["ok"]:
            raise SetupError("preflight", "Source failed the shared dataset preflight", checked_source)
        names = {row[0].lower() for row in source.execute("SELECT name FROM main.sqlite_schema")}
        for index, plan in enumerate(plans):
            name = "SQLP_VIEW_" + plan["root_table"] + "_D" + str(depth)
            while name.lower() in names:
                name += "_" + str(index)
            names.add(name.lower())
            plan["name"] = name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.mkdir(mode=0o700)
        with tempfile.TemporaryDirectory(prefix=".building-", dir=output) as temporary:
            directory = Path(temporary)
            frozen_compiler = directory/"sqlpc"
            compiler_hash = hashlib.sha256(compiler.read_bytes()).hexdigest()
            shutil.copyfile(compiler, frozen_compiler)
            if (hashlib.sha256(frozen_compiler.read_bytes()).hexdigest() != compiler_hash or
                    hashlib.sha256(compiler.read_bytes()).hexdigest() != compiler_hash):
                raise SetupError("compiler", "Compiler changed while freezing the view builder")
            frozen_compiler.chmod(0o500)
            with catalog_argument_file(public) as arguments:
                for plan in plans:
                    projections = ["r." + ".".join(_quote(part) for part in column["path"]) +
                                   " AS " + _quote(column["name"]) for column in plan["columns"]]
                    query = "SELECT " + ", ".join(projections) + " FROM " + _quote(plan["root_table"]) + " AS r;"
                    compiled, parameters = _compile(query, arguments, frozen_compiler, timeout_seconds)
                    if not compiled["ok"] or parameters:
                        raise SetupError("compiler", "Compiler rejected a catalog-derived view", compiled)
                    if len(compiled["trace"]["joins"]) != plan["join_count"]:
                        raise SetupError("compiler", "Compiled view join count differs from planned prefixes")
                    plan.update(sqlp=query, select_sql=compiled["sql"], trace=compiled["trace"])
            enhanced = {"version": 1, "tables": [*public["tables"], *[
                {"name": plan["name"], "columns": [{key: column[key] for key in ("name", "type", "unique")}
                                                   for column in plan["columns"]]}
                for plan in plans]], "references": public["references"]}
            snapshot = directory/"snapshot.sqlite"
            deadline = time.monotonic() + preflight_timeout_seconds
            def progress(_status, _remaining, _total):
                if time.monotonic() > deadline:
                    raise TimeoutError("Join-view snapshot copy timed out")
            with closing(sqlite3.connect(snapshot)) as target:
                snapshot.chmod(0o600)
                source.backup(target, pages=128, progress=progress, sleep=0.0)
                for plan in plans:
                    target.execute("CREATE VIEW " + _quote(plan["name"]) + " AS " + plan["select_sql"].rstrip().rstrip(";"))
                target.commit()
                checked_output = validate_dataset(target, enhanced, timeout_seconds=preflight_timeout_seconds)
                if not checked_output["ok"]:
                    raise SetupError("preflight", "Generated view catalog failed preflight", checked_output)
                # Cardinality is also checked concretely, rather than merely
                # relying on the compiler's total-reference argument.
                deadline = time.monotonic() + preflight_timeout_seconds
                target.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                try:
                    for plan in plans:
                        root_count = target.execute("SELECT COUNT(*) FROM " + _quote(plan["root_table"])).fetchone()[0]
                        view_count = target.execute("SELECT COUNT(*) FROM " + _quote(plan["name"])).fetchone()[0]
                        if root_count != view_count:
                            raise SetupError("views", "Generated view changed root-record multiplicity")
                        plan["row_count"] = view_count
                finally:
                    target.set_progress_handler(None, 0)
                snapshot_hash = _snapshot_hash(target, max_snapshot_bytes)
            metadata = {"version": 1, "kind": "catalog_join_views", "completed": True,
                        "construction": "all_catalog_reference_prefixes", "uses_workload": False,
                        "depth": depth, "max_joins": max_joins, "max_columns": max_columns,
                        "source_snapshot_sha256": source_hash, "snapshot_sha256": snapshot_hash,
                        "source_catalog_sha256": digest(public), "catalog_sha256": digest(enhanced),
                        "compiler_sha256": compiler_hash, "views": plans,
                        "base_relationship_facts": public["references"],
                        "source_preflight": checked_source, "output_preflight": checked_output,
                        "limitations": ["fixed depth", "approved total references only",
                            "no automatic view selection", "not a reproduction of workload-driven logical design"]}
            for name, value in (("catalog.json", enhanced), ("metadata.json", metadata)):
                (directory/name).write_text(json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2)+"\n")
            (directory/"view_guide.md").write_text(_view_guide(plans, depth), encoding="utf-8")
            for name in ("snapshot.sqlite", "catalog.json", "view_guide.md", "metadata.json"):
                path = directory/name
                path.chmod(0o400)
                path.replace(output/name)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--depth", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--max-joins", type=int, default=63)
    parser.add_argument("--max-columns", type=int, default=2000)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    args = parser.parse_args()
    try:
        metadata = prepare_join_views(args.database, load_catalog(args.catalog), args.output_dir,
            depth=args.depth, max_joins=args.max_joins, max_columns=args.max_columns, compiler_path=args.compiler)
    except (ValueError, OSError, sqlite3.Error, TimeoutError) as error:
        print(json.dumps({"version": 1, "ok": False, "error": {"stage": getattr(error, "stage", "join_views"),
                                                                   "message": str(error)}}))
        return 2
    print(json.dumps({"version": 1, "ok": True, "depth": metadata["depth"],
                      "views": len(metadata["views"]), "snapshot_sha256": metadata["snapshot_sha256"],
                      "output_dir": str(args.output_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

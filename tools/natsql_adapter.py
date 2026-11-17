"""Optional subprocess adapter for the unchanged official NatSQL converter.

No dependency installation, downloads, model calls, or query execution occur.
The external source is verified and copied before import. See docs/NATSQL.md.
"""

import argparse
from contextlib import closing, redirect_stdout, redirect_stderr
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.validate_dataset import _validate_catalog, load_catalog, validate_dataset

UPSTREAM_COMMIT = "dcd716fc9b920419cf838e490aad4a6f3b4fd231"
SOURCE_BLOBS = {
    "config.py": "4706f287267470e16f1c805aaab39d1fc9d3719e",
    "table_transform.py": "73009ccd70b9ff929e7edc88eaec10990beed2f3",
    "natsql2sql/natsql_parser.py": "083e847c6216952946ec0406fac5c9965fc62a9b",
    "natsql2sql/natsql2sql.py": "3ca57594b97acc29b7a006578542923c57d21d1b",
    "natsql2sql/process_sql.py": "967b841024695fa4a62baf43b31dde07d8942a13",
    "natsql2sql/utils.py": "033f510e4690a2f954e5fff71fe72c270e4a8bf3",
    "natsql2sql/preprocess/stemmer.py": "8217b4d002e61c0e2c2702d78b6a15663951fd0b",
    "natsql2sql/preprocess/match.py": "109dd6159b9fdda06742ebedca3d4783b065ad6f",
    "natsql2sql/preprocess/sql_back.py": "2607fd082cc98e160b1c6cafeea75ee3607f3e29",
    "natsql2sql/preprocess/TokenString.py": "be86aeb2da87006d9610f1770d6cdf0ebc28dc06",
    "natsql2sql/preprocess/Schema_Token.py": "c25659c454ae6d2bee2ba6364811ded53eacb729",
    "natsql2sql/preprocess/db_match.py": "b893e072c17599b818591da1a90db8d4dcfbb486",
    "natsql2sql/preprocess/utils.py": "6c6fc88206f3e5c3551b2a459c3499a67693154d",
}
MAX_QUERY_BYTES = 65536
MAX_OUTPUT_BYTES = 2_000_000
NETWORK_PROFILES = ("enlarged", "base", "enlarged-indexed")
SCHEMA_PROFILES = {"enlarged": "reviewed-structural-v1",
                   "base": "reviewed-structural-base-v2",
                   "enlarged-indexed": "reviewed-structural-enlarged-indexed-v3"}
LITERAL_PROFILES = ("native", "preserved")


def _structural_schema(schema, transform, network_profile):
    """Prepare full supplied facts, with an explicit network construction choice.

    ``enlarged`` preserves the original structural-v1 driver. ``base`` is a
    separately declared adapter profile, not the official preprocessing driver:
    it omits only the legacy network-enlargement function. ``enlarged-indexed``
    runs the verified function through an explicit adapter wrapper that indexes
    only its duplicate predicate and preserves its ordered network output.
    The verified upstream files, table/column/key facts and FK expansion are shared.
    """
    if network_profile not in NETWORK_PROFILES:
        raise ValueError("Unknown NatSQL network profile")
    original_hash = hashlib.sha256(json.dumps(schema, sort_keys=True,
        separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    original_counts = {key: len(schema[key]) for key in
                       ("table_names_original", "column_names_original", "foreign_keys")}
    flags = SimpleNamespace(add_alpha_to_table=False, remove_star_from_col=False,
        modify_column_names=False, force_modify_star_column_names=False,
        use_table_name_for_star_in_col_name=False, star_type="others")
    schema = transform.table_transform(schema, flags, None)
    schema = transform.build_index_range(schema)
    schema = transform.expand_foreign_key(schema)
    schema = transform.build_table_network(schema)
    network_count = len(schema["network"])
    if network_profile == "enlarged":
        schema["network"] = transform.enlarge_network(schema["network"], schema)
    elif network_profile == "enlarged-indexed":
        from tools import natsql_network
        schema["network"] = natsql_network.enlarge_network_indexed(schema["network"], schema, transform)
    trace = {"network_profile": network_profile,
             "enlarge_network_applied": network_profile != "base",
             "input_schema_sha256": original_hash, "input_counts": original_counts,
             "expanded_foreign_key_count": len(schema["foreign_keys"]),
             "base_network_count": network_count,
             "final_network_count": len(schema["network"])}
    if network_profile == "enlarged-indexed":
        trace.update(index_profile=natsql_network.INDEX_PROFILE_VERSION,
            index_implementation_sha256=hashlib.sha256(Path(natsql_network.__file__).read_bytes()).hexdigest())
    schema["tc_fast"] = [c[1].lower() for c in schema["table_column_names_original"]]
    schema["table_orig_low"] = [t.lower() for t in schema["table_names_original"]]
    transform.bridge_table_for_many2many_relationship([schema])
    schema["unique_fk"] = [transform.reversed_link_back_col(i, schema)
                           for i in schema["unique_fk"]]
    return schema, trace


def schema_input(catalog):
    """Export reviewed catalog facts, without name/key/discovery heuristics.

    This is a structural schema adapter, not the upstream Spider NLP pipeline.
    Original column indices are retained for upstream link_back processing.
    """
    _validate_catalog(catalog)
    if len(catalog["tables"]) > 12 or len(catalog["references"]) > 24:
        raise ValueError("NatSQL compatibility profile allows at most 12 tables and 24 references")
    names = [t["name"] for t in catalog["tables"]]
    columns, types, keys, unique_columns = [[-1, "*"]], ["text"], [], []
    identifiers = names + [c["name"] for t in catalog["tables"] for c in t["columns"]]
    if any(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", n) is None for n in identifiers):
        raise ValueError("NatSQL compatibility profile requires simple ASCII identifiers")
    for tid, table in enumerate(catalog["tables"]):
        candidates = []
        for column in table["columns"]:
            columns.append([tid, column["name"]])
            types.append("number" if column["type"] == "integer" else "text")
            if column["unique"]:
                candidates.append(len(columns) - 1)
        # Do not arbitrarily pick a primary key from several candidate keys.
        if len(candidates) > 1:
            raise ValueError("NatSQL schema export needs at most one declared unique key per table")
        keys.extend(candidates)
        unique_columns.extend(candidates)
    if len(columns) > 257:
        raise ValueError("NatSQL compatibility profile allows at most 256 columns")
    foreign_keys = []
    for ref in catalog["references"]:
        edge = [columns.index([names.index(ref["source_table"]), ref["source_column"]]),
                columns.index([names.index(ref["target_table"]), ref["target_column"]])]
        for direction in (edge, list(reversed(edge))):
            if direction not in foreign_keys:
                foreign_keys.append(direction)
    return {
        "db_id": "reviewed_snapshot", "table_names": names,
        "table_names_original": names.copy(), "column_names": copy.deepcopy(columns),
        "column_names_original": columns, "column_types": types,
        "primary_keys": keys, "original_primary_keys": keys.copy(),
        "foreign_keys": foreign_keys,
        # No cross-column synonym inference or data-dependent type/value repair.
        "same_col_idxs": [[i] for i in range(len(columns))],
        "unique_fk": [i for i in unique_columns
                      if any(i in edge for edge in foreign_keys)],
    }


def _copy_source(upstream, destination):
    """Import only the exact allowlisted files, excluding caller bytecode/init files."""
    for name, expected in SOURCE_BLOBS.items():
        source = Path(upstream) / name
        if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_OUTPUT_BYTES:
            raise ValueError("Missing or invalid pinned NatSQL source: " + name)
        data = source.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        if blob != expected:
            raise ValueError("NatSQL source differs from pinned commit: " + name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _failure(kind, message):
    return {"version": 1, "ok": False, "backend": "natsql", "upstream_commit": UPSTREAM_COMMIT,
            "error": {"kind": kind, "message": str(message)}}


def _schema_cache_key(original_schema, network_profile):
    from tools import natsql_cache, natsql_network
    return natsql_cache.make_key(original_schema, network_profile,
        adapter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        index_sha256=hashlib.sha256(Path(natsql_network.__file__).read_bytes()).hexdigest(),
        upstream_commit=UPSTREAM_COMMIT, source_blobs=SOURCE_BLOBS)


def prepare_schema_cache(catalogs, output, *, upstream, python=sys.executable,
                         nltk_data=None, timeout_seconds=60):
    """Freeze query-independent indexed schemas once, retaining all input facts.

    This requires no database, query, model response, or answer plan. The fresh
    output and exact manifest hash can be frozen with a later study. Each cache
    entry is bound to full catalog facts and all relevant implementation hashes.
    """
    from tools import natsql_cache
    if not isinstance(catalogs, dict) or not catalogs:
        raise ValueError("Supply named full catalogs")
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 60):
        raise ValueError("Schema preparation timeout must be positive and at most 60 seconds")
    inputs = []
    for name, catalog in catalogs.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Prepared schema needs a nonempty catalog label")
        schema = schema_input(catalog)
        inputs.append((name, schema, _schema_cache_key(schema, "enlarged-indexed")))
    output = Path(output).absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    entries = []
    with tempfile.TemporaryDirectory(prefix="sqlp-natsql-prepare-") as temporary:
        directory = Path(temporary)
        source = directory / "upstream"
        _copy_source(upstream, source)
        for name, original_schema, key in inputs:
            filename = natsql_cache.key_digest(key) + ".json"
            target = output / filename
            request = {"schema": original_schema, "upstream": str(source),
                "network_profile": "enlarged-indexed", "use_from_info": False,
                "nltk_data": str(Path(nltk_data).absolute()) if nltk_data else None,
                "prepare_schema_only": True, "cache_key": key, "cache_output": str(target)}
            request_path = directory / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            with (directory / "output.json").open("w+b") as destination:
                try:
                    process = subprocess.run([str(python), "-I", str(Path(__file__).resolve()),
                        "--worker", str(request_path)], cwd=directory, stdout=destination,
                        stderr=subprocess.STDOUT, timeout=timeout_seconds, check=False)
                except subprocess.TimeoutExpired as error:
                    raise ValueError("Indexed schema preparation exceeded its time limit") from error
                destination.seek(0)
                response = json.loads(destination.read(MAX_OUTPUT_BYTES + 1))
            if process.returncode != 0 or response.get("ok") is not True:
                raise ValueError("Indexed schema preparation failed: " + str(response.get("error", response)))
            expected = response["prepared_schema_sha256"]
            restored, trace, _ = natsql_cache.load_schema(target, expected, key)
            entries.append({"catalog_id": name, "cache_key_sha256": natsql_cache.key_digest(key),
                "input_schema_sha256": key["input_schema_sha256"], "file": filename,
                "sha256": expected, "bytes": target.stat().st_size,
                "schema_preparation": trace})
    manifest = {"version": 1, "kind": "natsql_prepared_schema_cache",
        "network_profile": "enlarged-indexed", "schema_profile": SCHEMA_PROFILES["enlarged-indexed"],
        "model_calls": 0, "query_independent": True, "upstream_commit": UPSTREAM_COMMIT,
        "entries": entries}
    with (output / "manifest.json").open("x", encoding="utf-8") as destination:
        json.dump(manifest, destination, indent=2, ensure_ascii=True, allow_nan=False)
        destination.write("\n")
    (output / "manifest.json").chmod(0o400)
    return manifest


def convert(query, database, catalog, *, upstream, python=sys.executable,
            nltk_data=None, timeout_seconds=10.0, use_from_info=False,
            network_profile="enlarged", schema_cache=None, literal_profile="native"):
    """Translate exactly the supplied NatSQL, retaining every conversion failure.

    A missing dependency/source is a setup error, not a scored NatSQL mistake.
    Callers must execute/score the emitted SQL through the common query scorer.
    """
    if not isinstance(query, str) or not query.strip() or "\0" in query:
        raise ValueError("NatSQL query must be nonempty text without NUL")
    if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        raise ValueError("NatSQL query exceeds byte limit")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60):
        raise ValueError("NatSQL timeout must be positive and at most 60 seconds")
    if type(use_from_info) is not bool:
        raise ValueError("use_from_info must be Boolean")
    if network_profile not in NETWORK_PROFILES:
        raise ValueError("NatSQL network profile must be enlarged, base, or enlarged-indexed")
    if literal_profile not in LITERAL_PROFILES:
        raise ValueError("NatSQL literal profile must be native or preserved")
    original_schema = schema_input(catalog)
    prepared_schema = None
    if schema_cache is not None:
        if network_profile != "enlarged-indexed":
            raise ValueError("Prepared schema caches require the enlarged-indexed profile")
        from tools import natsql_cache
        prepared_schema = natsql_cache.resolve_manifest(schema_cache,
            _schema_cache_key(original_schema, network_profile))
    with tempfile.TemporaryDirectory(prefix="sqlp-natsql-") as temporary:
        directory = Path(temporary)
        source = directory / "upstream"
        _copy_source(upstream, source)
        # Upstream get_schema uses a writable sqlite3.connect. Give it a private
        # consistent copy that cannot mutate the caller's database or WAL.
        uri = Path(database).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=timeout_seconds)) as connection:
            connection.execute("BEGIN")
            checks = validate_dataset(connection, catalog, timeout_seconds=timeout_seconds)
            if not checks["ok"]:
                raise ValueError("NatSQL database failed the shared dataset preflight")
            with closing(sqlite3.connect(directory / "snapshot.sqlite")) as target:
                connection.backup(target)
        request = {"query": query, "schema": original_schema, "upstream": str(source),
                   "database": str(directory / "snapshot.sqlite"),
                   "use_from_info": use_from_info,
                   "network_profile": network_profile,
                   "literal_profile": literal_profile,
                   "prepared_schema": prepared_schema,
                   "nltk_data": str(Path(nltk_data).resolve()) if nltk_data else None}
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        with (directory / "output.json").open("w+b") as output:
            try:
                result = subprocess.run([str(python), "-I", str(Path(__file__).resolve()),
                                         "--worker", str(request_path)],
                                        cwd=directory, stdout=output, stderr=subprocess.STDOUT,
                                        timeout=timeout_seconds, check=False)
            except subprocess.TimeoutExpired:
                return _failure("conversion_timeout", "NatSQL conversion exceeded its time limit")
            output.seek(0)
            data = output.read(MAX_OUTPUT_BYTES + 1)
        if len(data) > MAX_OUTPUT_BYTES:
            return _failure("converter_protocol", "NatSQL subprocess exceeded output limit")
        try:
            response = json.loads(data)
        except (UnicodeError, json.JSONDecodeError) as error:
            return _failure("converter_protocol", "NatSQL subprocess returned invalid JSON: " + str(error))
        if (not isinstance(response, dict) or response.get("version") != 1
                or type(response.get("ok")) is not bool or response.get("backend") != "natsql"):
            return _failure("converter_protocol", "NatSQL subprocess returned an invalid envelope")
        if response["ok"]:
            if not isinstance(response.get("sql"), str) or not response["sql"].strip():
                return _failure("converter_protocol", "Successful NatSQL response is missing SQL")
        elif (not isinstance(response.get("error"), dict)
              or not isinstance(response["error"].get("kind"), str)
              or not isinstance(response["error"].get("message"), str)):
            return _failure("converter_protocol", "Failed NatSQL response is missing its diagnostic")
        if result.returncode != 0:
            return _failure("converter_protocol", "NatSQL subprocess exited with status " + str(result.returncode))
        return response


def _worker(request_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    sys.path.insert(0, request["upstream"])
    response, schema_trace, literal_trace = None, None, None
    # Keep upstream diagnostics separately so they cannot corrupt JSON stdout.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as diagnostics:
        with redirect_stdout(diagnostics), redirect_stderr(diagnostics):
            try:
                import nltk
                if request["nltk_data"]:
                    nltk.data.path = [request["nltk_data"]]
                import table_transform as transform
                from natsql2sql.natsql_parser import create_sql_from_natSQL
                from natsql2sql.natsql2sql import Args, natsql_version
            except (ImportError, LookupError) as error:
                response = _failure("dependency_setup", type(error).__name__ + ": " + str(error))
            if response is None:
                try:
                    profile = request.get("network_profile", "enlarged")
                    if request.get("prepared_schema"):
                        from tools import natsql_cache
                        cached = request["prepared_schema"]
                        key = _schema_cache_key(request["schema"], profile)
                        if cached["key"] != key:
                            raise ValueError("Prepared schema key changed before worker import")
                        schema, schema_trace, content_hash = natsql_cache.load_schema(
                            cached["path"], cached["sha256"], key)
                        schema_trace.update(cache_hit=True, prepared_schema_sha256=cached["sha256"],
                            prepared_content_sha256=content_hash,
                            cache_manifest_sha256=cached["manifest_sha256"],
                            cache_key_sha256=natsql_cache.key_digest(key))
                    else:
                        schema, schema_trace = _structural_schema(request["schema"], transform, profile)
                        if profile == "enlarged-indexed":
                            schema_trace["cache_hit"] = False
                    if request.get("prepare_schema_only"):
                        from tools import natsql_cache
                        key = _schema_cache_key(request["schema"], profile)
                        if request["cache_key"] != key:
                            raise ValueError("Prepared schema source key changed before worker import")
                        prepared_hash = natsql_cache.write_schema(request["cache_output"], schema, schema_trace, key)
                        response = {"version": 1, "ok": True, "backend": "natsql",
                            "prepared_schema_sha256": prepared_hash}
                    else:
                        flags = Args()
                        flags.use_from_info = request["use_from_info"]
                        query = request["query"]
                        if request.get("literal_profile", "native") == "preserved":
                            from tools import natsql_literals
                            query, bindings = natsql_literals.protect_literals(query)
                            literal_trace = {**natsql_literals.transport_metadata(),
                                "source_query_sha256": bindings["source_sha256"],
                                "protected_query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                                "literal_count": len(bindings["literals"])}
                        sql, parsed, _ = create_sql_from_natSQL(query, schema["db_id"],
                            request["database"], schema, sq=None, remove_values=False, args=flags)
                        if not isinstance(sql, str) or not sql.strip():
                            response = _failure("conversion_rejected", "Official converter returned no SQL")
                        else:
                            if request.get("literal_profile", "native") == "preserved":
                                sql = natsql_literals.restore_literals(sql, bindings)
                            response = {"version": 1, "ok": True, "backend": "natsql",
                                "upstream_commit": UPSTREAM_COMMIT, "upstream_version": natsql_version(),
                                "environment": {"python": sys.version.split()[0], "nltk": nltk.__version__},
                                "schema_profile": SCHEMA_PROFILES[profile],
                                "schema_preparation": schema_trace, "sql": sql,
                                "parameters": [], "parsed_natsql": parsed}
                except Exception as error:
                    # NLTK reports absent tokenization tables with LookupError;
                    # query/schema KeyError subclasses must remain query errors.
                    kind = "dependency_setup" if type(error) is LookupError else "conversion_error"
                    response = _failure(kind, type(error).__name__ + ": " + str(error))
        diagnostics.seek(0)
        response["converter_diagnostics"] = diagnostics.read(16384)
    if schema_trace is not None:
        response["schema_preparation"] = schema_trace
        response["schema_profile"] = SCHEMA_PROFILES[schema_trace["network_profile"]]
    response["compiler_options"] = {"use_from_info": request["use_from_info"],
                                    "remove_values": False, "sq": None}
    if request.get("network_profile", "enlarged") != "enlarged":
        response["compiler_options"]["network_profile"] = request["network_profile"]
    if request.get("literal_profile", "native") != "native":
        response["compiler_options"]["literal_profile"] = request["literal_profile"]
    if literal_trace is not None:
        response["literal_transport"] = literal_trace
    print(json.dumps(response, ensure_ascii=True))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        _worker(sys.argv[2])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--nltk-data", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--use-from-info", action="store_true",
                        help="Explicit upstream option ablation; default remains upstream False")
    parser.add_argument("--network-profile", choices=NETWORK_PROFILES, default="enlarged",
                        help="Explicit adapter profile; default preserves the original upstream driver")
    parser.add_argument("--schema-cache-manifest", type=Path)
    parser.add_argument("--schema-cache-sha256")
    parser.add_argument("--literal-profile", choices=LITERAL_PROFILES, default="native")
    options = parser.parse_args()
    try:
        if bool(options.schema_cache_manifest) != bool(options.schema_cache_sha256):
            raise ValueError("Supply both prepared-schema manifest path and SHA256")
        cache = ({"manifest": str(options.schema_cache_manifest), "sha256": options.schema_cache_sha256}
                 if options.schema_cache_manifest else None)
        query = sys.stdin.buffer.read(MAX_QUERY_BYTES + 1).decode("utf-8")
        response = convert(query, options.database, load_catalog(options.catalog),
            upstream=options.upstream, python=options.python, nltk_data=options.nltk_data,
            timeout_seconds=options.timeout, use_from_info=options.use_from_info,
            network_profile=options.network_profile, schema_cache=cache,
            literal_profile=options.literal_profile)
    except (ValueError, OSError, sqlite3.Error) as error:
        response = _failure("adapter_setup", str(error))
    print(json.dumps(response, ensure_ascii=True))
    raise SystemExit(0 if response["ok"] else 1)


if __name__ == "__main__":
    main()

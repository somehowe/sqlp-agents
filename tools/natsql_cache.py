"""Typed JSON and exact hash bindings for query-independent NatSQL schemas."""

import hashlib
import json
from pathlib import Path


MAX_SCHEMA_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
_VALIDATED_SCHEMA_BINDINGS = set()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encode(value):
    # Upstream many2many maps use integer keys. JSON's ordinary object encoding
    # would turn these into strings and silently change inference semantics.
    if isinstance(value, dict):
        return {"$dict": [[encode(key), encode(item)] for key, item in value.items()]}
    if isinstance(value, tuple):
        return {"$tuple": [encode(item) for item in value]}
    if isinstance(value, list):
        return [encode(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError("Unsupported prepared-schema value type")


def decode(value):
    if isinstance(value, dict):
        if set(value) == {"$dict"}:
            entries = [(decode(key), decode(item)) for key, item in value["$dict"]]
            result = dict(entries)
            if len(result) != len(entries):
                raise ValueError("Duplicate prepared-schema mapping key")
            return result
        if set(value) == {"$tuple"}:
            return tuple(decode(item) for item in value["$tuple"])
        raise ValueError("Unknown prepared-schema JSON type tag")
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def make_key(original_schema, profile, *, adapter_sha256, index_sha256,
             upstream_commit, source_blobs):
    return {"version": 1, "input_schema_sha256": hashlib.sha256(canonical(original_schema)).hexdigest(),
            "network_profile": profile, "adapter_sha256": adapter_sha256,
            "index_implementation_sha256": index_sha256,
            "cache_implementation_sha256": sha(__file__),
            "upstream_commit": upstream_commit, "source_blobs": dict(source_blobs)}


def key_digest(key):
    return hashlib.sha256(canonical(key)).hexdigest()


def write_schema(path, schema, trace, key):
    encoded = encode(schema)
    # Check the actual serialization round trip before this becomes reusable.
    serialized = canonical(encoded)
    if decode(json.loads(serialized)) != schema:
        raise ValueError("Prepared-schema serialization changed its values or key types")
    bundle = {"version": 1, "key": key, "schema_encoded": encoded,
              "schema_sha256": hashlib.sha256(serialized).hexdigest(), "schema_preparation": trace}
    data = canonical(bundle)
    if len(data) > MAX_SCHEMA_BYTES:
        raise ValueError("Prepared schema exceeds its separate 64 MiB bound")
    with Path(path).open("xb") as destination:
        destination.write(data)
    Path(path).chmod(0o400)
    return hashlib.sha256(data).hexdigest()


def load_schema(path, expected_sha256, key):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SCHEMA_BYTES:
        raise ValueError("Missing, linked, or oversized prepared schema")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Prepared-schema SHA256 mismatch")
    bundle = json.loads(data)
    if bundle.get("version") != 1 or bundle.get("key") != key:
        raise ValueError("Prepared schema belongs to different facts, source, implementation, or profile")
    if hashlib.sha256(canonical(bundle["schema_encoded"])).hexdigest() != bundle["schema_sha256"]:
        raise ValueError("Prepared-schema content hash mismatch")
    schema = decode(bundle["schema_encoded"])
    trace = bundle["schema_preparation"]
    if trace.get("input_schema_sha256") != key["input_schema_sha256"]:
        raise ValueError("Prepared-schema trace is not bound to supplied facts")
    return schema, trace, bundle["schema_sha256"]


def resolve_manifest(config, key):
    if not isinstance(config, dict) or set(config) != {"manifest", "sha256"}:
        raise ValueError("Supply a prepared-schema manifest path and exact SHA256")
    path = Path(config["manifest"])
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Missing, linked, or oversized prepared-schema manifest")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != config["sha256"]:
        raise ValueError("Prepared-schema manifest SHA256 mismatch")
    manifest = json.loads(data)
    if manifest.get("version") != 1 or manifest.get("kind") != "natsql_prepared_schema_cache":
        raise ValueError("Invalid prepared-schema manifest")
    matches = [entry for entry in manifest["entries"] if entry["cache_key_sha256"] == key_digest(key)]
    if len(matches) != 1:
        raise ValueError("Prepared-schema manifest has no unique entry for supplied facts/source/profile")
    entry = matches[0]
    root = path.parent.resolve()
    target = root / entry["file"]
    if not target.resolve().is_relative_to(root):
        raise ValueError("Prepared schema path escapes its manifest directory")
    # Full binding validation once per exact content hash in the parent. Every
    # use still hashes the file, so a previous successful check never masks a
    # changed cache. A fresh worker parses/decodes its own private copy again.
    if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_SCHEMA_BYTES:
        raise ValueError("Missing, linked, or oversized prepared schema")
    if sha(target) != entry["sha256"]:
        raise ValueError("Prepared-schema SHA256 mismatch")
    binding = (entry["sha256"], key_digest(key))
    if binding not in _VALIDATED_SCHEMA_BINDINGS:
        load_schema(target, entry["sha256"], key)
        _VALIDATED_SCHEMA_BINDINGS.add(binding)
    return {"path": str(target.absolute()), "sha256": entry["sha256"],
            "key": key, "manifest_sha256": config["sha256"]}

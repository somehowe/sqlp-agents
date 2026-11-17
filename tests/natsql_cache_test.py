"""Adversarial cache-binding checks beyond the converter integration suite."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import natsql_cache as cache


class NatSQLCacheBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key = cache.make_key({"tables": ["t"], "foreign_keys": []},
            "enlarged-indexed", adapter_sha256="adapter", index_sha256="index",
            upstream_commit="commit", source_blobs={"x.py": "blob"})
        self.schema = {"many2many": {1: ("integer key",), "1": ["string key"]},
                       "network": [[[], [0]]]}
        self.trace = {"input_schema_sha256": self.key["input_schema_sha256"]}
        self.path = self.root / "schema.json"
        self.sha256 = cache.write_schema(self.path, self.schema, self.trace, self.key)
        self.original = self.path.read_bytes()
        self.entry = {"cache_key_sha256": cache.key_digest(self.key),
                      "file": "schema.json", "sha256": self.sha256}

    def manifest(self, entries):
        path = self.root / "manifest.json"
        path.write_bytes(cache.canonical({"version": 1,
            "kind": "natsql_prepared_schema_cache", "entries": entries}))
        return {"manifest": path, "sha256": cache.sha(path)}

    def test_duplicate_manifest_entries_are_ambiguous_and_rejected(self):
        config = self.manifest([self.entry, deepcopy(self.entry)])
        with self.assertRaisesRegex(ValueError, "unique"):
            cache.resolve_manifest(config, self.key)

    def test_manifest_paths_cannot_escape_and_symlinked_inputs_are_rejected(self):
        for escaped in ("../outside.json", str(self.root.parent / "outside.json")):
            with self.subTest(path=escaped), self.assertRaisesRegex(ValueError, "escapes"):
                cache.resolve_manifest(self.manifest([{**self.entry, "file": escaped}]), self.key)
        linked = self.root / "linked-schema.json"
        linked.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "linked"):
            cache.load_schema(linked, self.sha256, self.key)
        config = self.manifest([self.entry])
        linked_manifest = self.root / "linked-manifest.json"
        linked_manifest.symlink_to(config["manifest"])
        with self.assertRaisesRegex(ValueError, "linked"):
            cache.resolve_manifest({**config, "manifest": linked_manifest}, self.key)

    def test_rehashed_outer_bundle_still_requires_inner_content_and_trace_bindings(self):
        self.path.chmod(0o600)
        bundle = json.loads(self.original)
        bundle["schema_encoded"] = cache.encode({"foreign": "schema"})
        self.path.write_bytes(cache.canonical(bundle))
        with self.assertRaisesRegex(ValueError, "content hash"):
            cache.load_schema(self.path, cache.sha(self.path), self.key)
        bundle = json.loads(self.original)
        bundle["schema_preparation"]["input_schema_sha256"] = "foreign"
        self.path.write_bytes(cache.canonical(bundle))
        with self.assertRaisesRegex(ValueError, "trace"):
            cache.load_schema(self.path, cache.sha(self.path), self.key)

    def test_typed_decoder_rejects_duplicate_mapping_keys_and_unknown_tags(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            cache.decode({"$dict": [[1, "first"], [1, "second"]]})
        with self.assertRaisesRegex(ValueError, "Unknown"):
            cache.decode({"$executable": "not a supported data type"})

    def test_declared_bounds_are_checked_before_json_decoding(self):
        with patch.object(cache, "MAX_SCHEMA_BYTES", len(self.original) - 1):
            with self.assertRaisesRegex(ValueError, "oversized"):
                cache.load_schema(self.path, self.sha256, self.key)
        config = self.manifest([self.entry])
        size = Path(config["manifest"]).stat().st_size
        with patch.object(cache, "MAX_MANIFEST_BYTES", size - 1):
            with self.assertRaisesRegex(ValueError, "oversized"):
                cache.resolve_manifest(config, self.key)

    def test_existing_schema_is_never_overwritten_and_nonfinite_values_fail(self):
        with self.assertRaises(FileExistsError):
            cache.write_schema(self.path, self.schema, self.trace, self.key)
        self.assertEqual(self.path.read_bytes(), self.original)
        with self.assertRaises(ValueError):
            cache.write_schema(self.root / "invalid.json", {"value": float("nan")},
                               self.trace, self.key)


if __name__ == "__main__":
    unittest.main()

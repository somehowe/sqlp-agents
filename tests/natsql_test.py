"""Adapter checks plus explicitly opt-in tests of the external full converter."""

from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import natsql_adapter as adapter
from tools import natsql_cache
from tools.validate_dataset import load_catalog


class AdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / "academic.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript((ROOT / "examples/academic/data.sql").read_text())
        self.catalog = load_catalog(ROOT / "examples/academic/catalog.json")

    def convert(self, query="select AUTHOR.name from AUTHOR", **options):
        # Fake process outputs test the adapter protocol, never NatSQL behavior.
        with patch.object(adapter, "_copy_source"):
            return adapter.convert(query, self.database, self.catalog,
                                   upstream=self.directory, **options)

    def test_catalog_export_retains_duplicate_relationship_without_invented_key(self):
        schema = adapter.schema_input(self.catalog)
        wrote = schema["table_names"].index("WROTE")
        self.assertFalse(any(schema["column_names"][key][0] == wrote
                             for key in schema["primary_keys"]))
        mentor = [schema["column_names"].index([1, "mentor_aid"]),
                  schema["column_names"].index([1, "aid"])]
        self.assertIn(mentor, schema["foreign_keys"])
        self.assertIn(mentor[::-1], schema["foreign_keys"])
        self.assertEqual(len(schema["same_col_idxs"]), len(schema["column_names"]))

    def test_ambiguous_primary_key_is_rejected_before_external_import(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["tables"][0]["columns"][1]["unique"] = True
        with self.assertRaisesRegex(ValueError, "at most one declared unique"):
            adapter.schema_input(catalog)

    def test_unsupported_names_and_large_catalogs_are_setup_errors(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["tables"][0]["columns"][1]["name"] = "display name"
        with self.assertRaisesRegex(ValueError, "ASCII identifiers"):
            adapter.schema_input(catalog)
        catalog = copy.deepcopy(self.catalog)
        catalog["references"] = catalog["references"] * 6
        with self.assertRaises(ValueError):
            adapter.schema_input(catalog)

    def test_modified_external_source_is_not_executed(self):
        source = self.directory / "source"
        source.mkdir()
        (source / "module.py").write_text("raise Exception('do not import')\n")
        with patch.object(adapter, "SOURCE_BLOBS", {"module.py": "0" * 40}):
            with self.assertRaisesRegex(ValueError, "differs from pinned"):
                adapter._copy_source(source, self.directory / "copy")

    def test_only_verified_files_are_copied(self):
        source = self.directory / "source"
        source.mkdir()
        content = b"# verified source\n"
        (source / "module.py").write_bytes(content)
        (source / "__init__.py").write_text("raise Exception('must not copy')")
        expected = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        destination = self.directory / "copy"
        with patch.object(adapter, "SOURCE_BLOBS", {"module.py": expected}):
            adapter._copy_source(source, destination)
        self.assertEqual([p.name for p in destination.iterdir()], ["module.py"])

    def test_private_snapshot_isolated_from_converter_changes(self):
        before = self.database.read_bytes()
        with sqlite3.connect(self.database) as connection:
            count = connection.execute("SELECT COUNT(*) FROM WROTE").fetchone()[0]
        def fake(command, **options):
            request = json.loads(Path(command[-1]).read_text())
            snapshot = Path(request["database"])
            self.assertNotEqual(snapshot, self.database)
            with sqlite3.connect(snapshot) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM WROTE").fetchone()[0], count)
                connection.execute("DELETE FROM WROTE")
            options["stdout"].write(json.dumps({"version": 1, "ok": True,
                                               "backend": "natsql", "sql": "select 1"}).encode())
            return subprocess.CompletedProcess(command, 0)
        with patch.object(adapter.subprocess, "run", side_effect=fake):
            result = self.convert()
        self.assertTrue(result["ok"])
        self.assertEqual(self.database.read_bytes(), before)

    def test_preflight_failure_prevents_converter_call(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE AUTHOR SET uni_id=999 WHERE aid=1")
        with patch.object(adapter.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "shared dataset preflight"):
                self.convert()
        run.assert_not_called()

    def test_rejected_query_and_missing_dependency_are_preserved_separately(self):
        for kind in ("conversion_error", "conversion_rejected", "dependency_setup"):
            def fake(command, **options):
                options["stdout"].write(json.dumps(adapter._failure(kind, "original error")).encode())
                return subprocess.CompletedProcess(command, 0)
            with patch.object(adapter.subprocess, "run", side_effect=fake):
                result = self.convert()
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], {"kind": kind, "message": "original error"})

    def test_subprocess_timeout_is_a_retained_failure(self):
        with patch.object(adapter.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            result = self.convert()
        self.assertEqual(result["error"]["kind"], "conversion_timeout")

    def test_bad_output_and_process_failure_are_not_successes(self):
        for data, code in ((b"not JSON", 0), (b"[]", 0), (b'{"version":1,"ok":true}', 0),
                           (b'{"version":1,"ok":true,"backend":"natsql"}', 0),
                           (b'{"version":1,"ok":false,"backend":"natsql"}', 0),
                           (b'{"version":1,"ok":true,"backend":"natsql"}', 7),
                           (b"x" * (adapter.MAX_OUTPUT_BYTES + 1), 0)):
            def fake(command, **options):
                options["stdout"].write(data)
                return subprocess.CompletedProcess(command, code)
            with patch.object(adapter.subprocess, "run", side_effect=fake):
                result = self.convert()
            self.assertEqual(result["error"]["kind"], "converter_protocol")

    def test_input_limits_are_checked_before_subprocess(self):
        for query in ("", "\0", "x" * (adapter.MAX_QUERY_BYTES + 1), "\ud800"):
            with self.assertRaises((ValueError, UnicodeError)):
                self.convert(query)
        for timeout in (0, -1, True, float("nan"), float("inf"), 61):
            with self.assertRaises(ValueError):
                self.convert(timeout_seconds=timeout)
        with self.assertRaises(ValueError):
            self.convert(use_from_info="yes")
        for profile in (None, "", "fast", True, ["base"]):
            with self.assertRaises(ValueError):
                self.convert(network_profile=profile)
        for profile in (None, "", "repair", True):
            with self.assertRaises(ValueError):
                self.convert(literal_profile=profile)

    def test_network_profile_is_explicit_and_default_remains_enlarged(self):
        for supplied, expected in (({}, "enlarged"), ({"network_profile": "base"}, "base"),
                                   ({"network_profile": "enlarged-indexed"}, "enlarged-indexed")):
            def fake(command, **options):
                request = json.loads(Path(command[-1]).read_text())
                self.assertEqual(request["network_profile"], expected)
                self.assertEqual(request["literal_profile"], "native")
                self.assertEqual(request["schema"], adapter.schema_input(self.catalog))
                options["stdout"].write(json.dumps({"version": 1, "ok": True,
                    "backend": "natsql", "sql": "select 1"}).encode())
                return subprocess.CompletedProcess(command, 0)
            with patch.object(adapter.subprocess, "run", side_effect=fake):
                self.assertTrue(self.convert(**supplied)["ok"])

    def test_base_schema_keeps_all_facts_and_skips_only_enlargement(self):
        original = adapter.schema_input(self.catalog)
        traces, outputs = {}, {}
        for profile in ("enlarged", "base"):
            called = []
            def transform(schema, flags, tokens):
                called.append("transform")
                schema["table_column_names_original"] = copy.deepcopy(schema["column_names_original"])
                return schema
            def identity(name):
                def apply(schema):
                    called.append(name)
                    return schema
                return apply
            def network(schema):
                called.append("network")
                schema["network"] = [[[], [0]]]
                return schema
            def enlarge(networks, schema):
                called.append("enlarge")
                return networks + [[[], [1]]]
            def bridge(schemas):
                called.append("bridge")
            functions = SimpleNamespace(table_transform=transform,
                build_index_range=identity("index"), expand_foreign_key=identity("expand"),
                build_table_network=network, enlarge_network=enlarge,
                bridge_table_for_many2many_relationship=bridge,
                reversed_link_back_col=lambda key, schema: key)
            schema, trace = adapter._structural_schema(copy.deepcopy(original), functions, profile)
            expected = ["transform", "index", "expand", "network"]
            if profile == "enlarged":
                expected.append("enlarge")
            self.assertEqual(called, expected + ["bridge"])
            for key in ("table_names_original", "column_names_original", "foreign_keys", "primary_keys"):
                self.assertEqual(schema[key], original[key])
            self.assertEqual(trace["enlarge_network_applied"], profile == "enlarged")
            self.assertEqual(trace["base_network_count"], 1)
            self.assertEqual(trace["final_network_count"], 2 if profile == "enlarged" else 1)
            traces[profile], outputs[profile] = trace, schema
        self.assertEqual(traces["base"]["input_schema_sha256"], traces["enlarged"]["input_schema_sha256"])
        self.assertEqual(adapter.schema_input(self.catalog), original)

    def test_prepared_schema_roundtrip_preserves_integer_keys_and_nested_types(self):
        value = {"many2many": {0: [1, 2], 3: [4]}, "other": ("tuple", [True, None]),
                 1: "integer key", "1": "different string key"}
        restored = natsql_cache.decode(json.loads(json.dumps(natsql_cache.encode(value))))
        self.assertEqual(restored, value)
        self.assertIn(0, restored["many2many"])
        self.assertIs(type(restored["other"]), tuple)

    def test_prepared_schema_rejects_tamper_and_foreign_facts_before_process(self):
        original = adapter.schema_input(self.catalog)
        key = adapter._schema_cache_key(original, "enlarged-indexed")
        schema = {**original, "many2many": {0: [1, 2]}}
        target = self.directory / "prepared.json"
        trace = {"input_schema_sha256": key["input_schema_sha256"]}
        expected = natsql_cache.write_schema(target, schema, trace, key)
        manifest = self.directory / "manifest.json"
        manifest.write_text(json.dumps({"version": 1, "kind": "natsql_prepared_schema_cache", "entries": [
            {"cache_key_sha256": natsql_cache.key_digest(key), "file": target.name, "sha256": expected}]}))
        config = {"manifest": str(manifest), "sha256": natsql_cache.sha(manifest)}
        self.assertEqual(natsql_cache.load_schema(target, expected, key)[0], schema)
        self.assertEqual(natsql_cache.resolve_manifest(config, key)["sha256"], expected)
        changed = copy.deepcopy(original)
        changed["table_names_original"][0] = "Different"
        foreign = adapter._schema_cache_key(changed, "enlarged-indexed")
        with self.assertRaisesRegex(ValueError, "no unique entry"):
            natsql_cache.resolve_manifest(config, foreign)
        target.chmod(0o600)
        target.write_text("changed cache")
        with patch.object(adapter.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                self.convert(network_profile="enlarged-indexed", schema_cache=config)
        run.assert_not_called()
        with self.assertRaisesRegex(ValueError, "manifest SHA256 mismatch"):
            natsql_cache.resolve_manifest({**config, "sha256": "0" * 64}, key)

    def test_prepared_schema_rejects_implementation_and_profile_changes(self):
        schema = adapter.schema_input(self.catalog)
        key = adapter._schema_cache_key(schema, "enlarged-indexed")
        target = self.directory / "prepared.json"
        expected = natsql_cache.write_schema(target, schema,
            {"input_schema_sha256": key["input_schema_sha256"]}, key)
        for field in ("adapter_sha256", "index_implementation_sha256", "network_profile", "source_blobs"):
            changed = copy.deepcopy(key)
            changed[field] = "different"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "different facts"):
                natsql_cache.load_schema(target, expected, changed)
        with self.assertRaisesRegex(ValueError, "require the enlarged-indexed"):
            self.convert(schema_cache={"manifest": "missing", "sha256": "0" * 64})


@unittest.skipUnless(os.environ.get("SQLP_NATSQL_UPSTREAM") and os.environ.get("SQLP_NATSQL_PYTHON")
                     and os.environ.get("SQLP_NATSQL_DATA"),
                     "External pinned NatSQL source/environment not explicitly configured")
class FullConverterTests(unittest.TestCase):
    # These tests execute the complete unmodified upstream converter. The regular
    # adapter tests above do not constitute converter compatibility evidence.
    setUp = AdapterTests.setUp

    def full(self, query, **options):
        return adapter.convert(query, self.database, self.catalog,
            upstream=os.environ["SQLP_NATSQL_UPSTREAM"], python=os.environ["SQLP_NATSQL_PYTHON"],
            nltk_data=os.environ["SQLP_NATSQL_DATA"], **options)

    def test_full_base_profile_is_explicit_and_does_not_prune_schema(self):
        query = "select AUTHOR.name from AUTHOR where AUTHOR.aid = 1"
        base = self.full(query, network_profile="base")
        default = self.full(query)
        self.assertTrue(base["ok"], base)
        self.assertTrue(default["ok"], default)
        self.assertEqual(base["schema_profile"], "reviewed-structural-base-v2")
        self.assertEqual(default["schema_profile"], "reviewed-structural-v1")
        self.assertFalse(base["schema_preparation"]["enlarge_network_applied"])
        self.assertTrue(default["schema_preparation"]["enlarge_network_applied"])
        self.assertEqual(base["schema_preparation"]["input_schema_sha256"],
                         default["schema_preparation"]["input_schema_sha256"])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute(base["sql"]).fetchall(), [("Alice",)])

    def test_full_indexed_profile_preserves_enlarged_network_and_query_behavior(self):
        query = "select WROTE.author_aid, AUTHOR.name from WROTE where UNIVERSITY.country = 'CA'"
        indexed = self.full(query, network_profile="enlarged-indexed")
        default = self.full(query)
        self.assertTrue(indexed["ok"], indexed)
        self.assertTrue(default["ok"], default)
        self.assertEqual(indexed["schema_profile"], "reviewed-structural-enlarged-indexed-v3")
        self.assertEqual(indexed["compiler_options"]["network_profile"], "enlarged-indexed")
        self.assertEqual(indexed["schema_preparation"]["input_schema_sha256"],
                         default["schema_preparation"]["input_schema_sha256"])
        self.assertEqual(indexed["schema_preparation"]["final_network_count"],
                         default["schema_preparation"]["final_network_count"])
        self.assertTrue(indexed["schema_preparation"]["enlarge_network_applied"])
        self.assertIn("index_implementation_sha256", indexed["schema_preparation"])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(Counter(connection.execute(indexed["sql"]).fetchall()),
                             Counter(connection.execute(default["sql"]).fetchall()))

    def test_full_cached_indexed_schema_preserves_query_and_many2many_keys(self):
        destination = self.directory / "prepared-schemas"
        manifest = adapter.prepare_schema_cache({"academic": self.catalog}, destination,
            upstream=os.environ["SQLP_NATSQL_UPSTREAM"], python=os.environ["SQLP_NATSQL_PYTHON"],
            nltk_data=os.environ["SQLP_NATSQL_DATA"])
        cache = {"manifest": str(destination / "manifest.json"),
                 "sha256": natsql_cache.sha(destination / "manifest.json")}
        query = "select WROTE.author_aid, AUTHOR.name from WROTE where UNIVERSITY.country = 'CA'"
        cached = self.full(query, network_profile="enlarged-indexed", schema_cache=cache)
        uncached = self.full(query, network_profile="enlarged-indexed")
        self.assertTrue(cached["ok"], cached)
        self.assertTrue(uncached["ok"], uncached)
        self.assertTrue(cached["schema_preparation"]["cache_hit"])
        self.assertFalse(uncached["schema_preparation"]["cache_hit"])
        self.assertEqual(cached["schema_preparation"]["final_network_count"],
                         uncached["schema_preparation"]["final_network_count"])
        self.assertEqual(cached["schema_preparation"]["prepared_schema_sha256"], manifest["entries"][0]["sha256"])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(Counter(connection.execute(cached["sql"]).fetchall()),
                             Counter(connection.execute(uncached["sql"]).fetchall()))

    def test_full_preserved_literal_transport_keeps_apostrophe_values(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE AUTHOR SET name=? WHERE aid=1", ("O'Neil",))
        query = "select AUTHOR.name from AUTHOR where AUTHOR.name = 'O''Neil'"
        native = self.full(query)
        self.assertFalse(native["ok"], native)
        preserved = self.full(query, network_profile="enlarged-indexed", literal_profile="preserved")
        self.assertTrue(preserved["ok"], preserved)
        self.assertEqual(preserved["compiler_options"]["literal_profile"], "preserved")
        self.assertEqual(preserved["literal_transport"]["literal_count"], 1)
        self.assertIn("source_sha256", preserved["literal_transport"])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute(preserved["sql"]).fetchall(), [("O'Neil",)])
        malformed = self.full("select AUTHOR.name from AUTHOR where AUTHOR.name = 'unclosed",
                              literal_profile="preserved")
        self.assertFalse(malformed["ok"], malformed)

    def assert_answers(self, natsql, oracle):
        result = self.full(natsql)
        self.assertTrue(result["ok"], result)
        with sqlite3.connect(self.database) as connection:
            actual = connection.execute(result["sql"]).fetchall()
            expected = connection.execute(oracle).fetchall()
        self.assertEqual(Counter(actual), Counter(expected))
        return actual

    def test_full_direct_and_integer_literal(self):
        self.assert_answers("select AUTHOR.name from AUTHOR where AUTHOR.aid = 1",
                            "SELECT name FROM AUTHOR WHERE aid=1")

    def test_full_one_hop_string_literal(self):
        self.assert_answers("select AUTHOR.name from AUTHOR where UNIVERSITY.country = 'CA'",
            "SELECT a.name FROM AUTHOR a JOIN UNIVERSITY u ON a.uni_id=u.uid WHERE u.country='CA'")

    def test_full_two_hops_preserve_duplicate_rows(self):
        rows = self.assert_answers("select WROTE.author_aid , AUTHOR.name from WROTE "
            "where UNIVERSITY.country = 'CA'", "SELECT w.author_aid,a.name FROM WROTE w "
            "JOIN AUTHOR a ON w.author_aid=a.aid JOIN UNIVERSITY u ON a.uni_id=u.uid WHERE u.country='CA'")
        self.assertEqual(rows.count((1, "Alice")), 2)

    def test_full_and_predicates_and_empty_answer(self):
        for aid in (1, 999):
            self.assert_answers("select AUTHOR.name from AUTHOR where UNIVERSITY.country = 'CA' "
                "and AUTHOR.aid = " + str(aid), "SELECT a.name FROM AUTHOR a JOIN UNIVERSITY u "
                "ON a.uni_id=u.uid WHERE u.country='CA' AND a.aid=" + str(aid))

    def test_full_distinct(self):
        self.assert_answers("select distinct AUTHOR.name from WROTE where WROTE.author_aid = AUTHOR.aid "
            "and UNIVERSITY.country = 'CA'", "SELECT DISTINCT a.name FROM WROTE w JOIN AUTHOR a "
            "ON w.author_aid=a.aid JOIN UNIVERSITY u ON a.uni_id=u.uid WHERE u.country='CA'")

    def test_full_flat_in_reconstructs_a_six_table_existence_query(self):
        # Native NatSQL uses a flat IN relation to infer a subquery. A guide
        # restricted to literal equalities would exclude this supported form.
        # This public fixture needs no benchmark download or private answers.
        tables = {
            "Customer": [("cid", "integer", True), ("email", "string", False)],
            "Invoice": [("iid", "integer", True), ("cid", "integer", False)],
            "InvoiceLine": [("lid", "integer", True), ("iid", "integer", False), ("tid", "integer", False)],
            "Track": [("tid", "integer", True), ("aid", "integer", False)],
            "Album": [("aid", "integer", True), ("artist_id", "integer", False)],
            "Artist": [("artist_id", "integer", True), ("name", "string", False)],
        }
        references = [("Invoice", "customer", "cid", "Customer", "cid"),
                      ("InvoiceLine", "invoice", "iid", "Invoice", "iid"),
                      ("InvoiceLine", "track", "tid", "Track", "tid"),
                      ("Track", "album", "aid", "Album", "aid"),
                      ("Album", "artist", "artist_id", "Artist", "artist_id")]
        catalog = {"version": 1, "tables": [
            {"name": name, "columns": [
                {"name": c, "type": kind, "unique": unique}
                for c, kind, unique in columns]} for name, columns in tables.items()],
            "references": [dict(zip(("source_table", "feature", "source_column",
                                      "target_table", "target_column"), ref))
                           for ref in references]}
        database = self.directory / "six-tables.sqlite"
        with sqlite3.connect(database) as connection:
            for name, columns in tables.items():
                declaration = ", ".join(c + (" INTEGER" if kind == "integer" else " TEXT") +
                                        (" PRIMARY KEY" if unique else " NOT NULL")
                                        for c, kind, unique in columns)
                connection.execute("CREATE TABLE " + name + "(" + declaration + ")")
            connection.executescript("""
                INSERT INTO Customer VALUES(1,'ada@example'),(2,'bob@example'),(3,'cy@example');
                INSERT INTO Invoice VALUES(10,1),(11,1),(12,2),(13,3);
                INSERT INTO Artist VALUES(100,'Queen'),(101,'Other');
                INSERT INTO Album VALUES(200,100),(201,101);
                INSERT INTO Track VALUES(300,200),(301,201);
                INSERT INTO InvoiceLine VALUES(1,10,300),(2,10,300),(3,11,300),(4,12,301),(5,13,300);
            """)
        query = ("select distinct Customer.email from Customer where "
                 "Customer.cid in Invoice.cid and Artist.name = 'Queen'")
        result = adapter.convert(query, database, catalog,
            upstream=os.environ["SQLP_NATSQL_UPSTREAM"],
            python=os.environ["SQLP_NATSQL_PYTHON"], nltk_data=os.environ["SQLP_NATSQL_DATA"])
        self.assertTrue(result["ok"], result)
        with sqlite3.connect(database) as connection:
            actual = connection.execute(result["sql"]).fetchall()
            expected = connection.execute("""SELECT DISTINCT c.email FROM Customer c
                JOIN Invoice i ON i.cid=c.cid JOIN InvoiceLine l ON l.iid=i.iid
                JOIN Track t ON t.tid=l.tid JOIN Album a ON a.aid=t.aid
                JOIN Artist r ON r.artist_id=a.artist_id WHERE r.name='Queen'""").fetchall()
        self.assertEqual(Counter(actual), Counter(expected))
        self.assertEqual(Counter(actual), Counter([("ada@example",), ("cy@example",)]))
        self.assertIn(" in (select ", result["sql"].lower())

    def test_full_unknown_column_is_query_failure(self):
        result = self.full("select AUTHOR.missing from AUTHOR")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["kind"], "conversion_error")

    def test_full_missing_from_is_preserved_without_rewrite(self):
        result = self.full("select AUTHOR.name where AUTHOR.aid = 1")
        self.assertFalse(result["ok"])
        self.assertIn("from", result["error"]["message"])

    def test_full_repeated_role_characterization_keeps_wrong_answers_visible(self):
        # Characterize this pinned converter and tested encodings. This does
        # not establish that no other NatSQL encoding can answer these tasks.
        examples = [
            ("select a.name , m.name from AUTHOR as a join AUTHOR as m "
             "on a.mentor_aid = m.aid",
             "SELECT a.name,m.name FROM AUTHOR a JOIN AUTHOR m ON a.mentor_aid=m.aid"),
            ("select w.author_aid , a.name from WROTE as w join AUTHOR as a "
             "on w.author_aid = a.aid join UNIVERSITY as au on a.uni_id = au.uid "
             "join PAPER as p on w.paper_pid = p.pid join UNIVERSITY as pu "
             "on p.publisher_uid = pu.uid where au.country = 'CA' and pu.country = 'DK'",
             "SELECT w.author_aid,a.name FROM WROTE w JOIN AUTHOR a ON w.author_aid=a.aid "
             "JOIN UNIVERSITY au ON a.uni_id=au.uid JOIN PAPER p ON w.paper_pid=p.pid "
             "JOIN UNIVERSITY pu ON p.publisher_uid=pu.uid WHERE au.country='CA' AND pu.country='DK'")]
        for query, oracle in examples:
            for use_from_info in (False, True):
                with self.subTest(query=query, use_from_info=use_from_info):
                    result = adapter.convert(query, self.database, self.catalog,
                        upstream=os.environ["SQLP_NATSQL_UPSTREAM"],
                        python=os.environ["SQLP_NATSQL_PYTHON"],
                        nltk_data=os.environ["SQLP_NATSQL_DATA"], use_from_info=use_from_info)
                    self.assertTrue(result["ok"], result)
                    with sqlite3.connect(self.database) as connection:
                        actual = connection.execute(result["sql"]).fetchall()
                        expected = connection.execute(oracle).fetchall()
                    self.assertNotEqual(Counter(actual), Counter(expected))
                    self.assertEqual(result["compiler_options"]["use_from_info"], use_from_info)


if __name__ == "__main__":
    unittest.main()

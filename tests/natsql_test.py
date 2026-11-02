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
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import natsql_adapter as adapter
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


@unittest.skipUnless(os.environ.get("SQLP_NATSQL_UPSTREAM") and os.environ.get("SQLP_NATSQL_PYTHON")
                     and os.environ.get("SQLP_NATSQL_DATA"),
                     "External pinned NatSQL source/environment not explicitly configured")
class FullConverterTests(unittest.TestCase):
    # These tests execute the complete unmodified upstream converter. The regular
    # adapter tests above do not constitute converter compatibility evidence.
    setUp = AdapterTests.setUp

    def full(self, query):
        return adapter.convert(query, self.database, self.catalog,
            upstream=os.environ["SQLP_NATSQL_UPSTREAM"], python=os.environ["SQLP_NATSQL_PYTHON"],
            nltk_data=os.environ["SQLP_NATSQL_DATA"])

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

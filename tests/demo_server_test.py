"""Real compiler/SQLite interventions and loopback HTTP boundary checks."""

import http.client
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.demo_server import DemoLab, MAX_BODY_BYTES, create_server


class DemoServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.original = self.directory / "original.sqlite"
        self.contrast = self.directory / "contrast.sqlite"
        with sqlite3.connect(self.original) as connection:
            connection.executescript((ROOT / "examples/academic/data.sql").read_text())
            with sqlite3.connect(self.contrast) as copied:
                connection.backup(copied)
        with sqlite3.connect(self.contrast) as connection:
            connection.execute("INSERT INTO WROTE VALUES (1, 10)")
        cases = json.loads((ROOT / "examples/academic/workload.json").read_text())["cases"]
        role = next(c for c in cases if c["id"] == "canadian_authors_danish_publishers")
        empty = next(c for c in cases if c["id"] == "empty_us_authorships")
        catalog = json.loads((ROOT / "examples/academic/catalog.json").read_text())
        self.bundle = {"version": 1, "scenarios": []}
        for case in (role, empty):
            self.bundle["scenarios"].append({**case, "catalog": catalog, "snapshots": {
                "original": {"database": str(self.original), "expected_rows": case["expected_rows"]},
                "contrast1": {"database": str(self.contrast), "expected_rows":
                              case["expected_rows"] + ([["Alice"]] if case is role else [])}}})
        self.lab = DemoLab(self.bundle, timeout=0.1)
        self.addCleanup(self.lab.close)
        self.scenario = role

    def run_query(self, **changes):
        return self.lab.run({"scenario": self.scenario["id"], "language": "sqlp",
                             "query": self.scenario["sqlp"], "snapshot": "original", **changes})

    def start(self):
        server = create_server(self.lab, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def request(self, port, method, path, body=None, headers=None):
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=3)) as connection:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def test_real_compiler_role_edit_changes_answer_and_owns_distinct_aliases(self):
        original = self.run_query()
        self.assertTrue(original["run"]["correct"])
        self.assertEqual(original["ai_calls"], 0)
        self.assertEqual(len(original["run"]["rows"]), 2)
        university_joins = [j for j in original["run"]["trace"]["joins"] if j["target"]["table"] == "UNIVERSITY"]
        self.assertEqual(len({j["target"]["alias"] for j in university_joins}), 2)
        edited = self.run_query(query=self.scenario["sqlp"].replace("w.paper.publisher.country", "w.author.university.country"))
        self.assertEqual(edited["run"]["status"], "ok")
        self.assertFalse(edited["run"]["correct"])
        self.assertTrue(edited["changed_from_baseline"])
        self.assertEqual(edited["run"]["rows"], [])
        self.assertIsNone(edited["run"]["token_usage"])

    def test_contrast_snapshot_preserves_duplicate_rows_and_exposes_distinct(self):
        original = self.run_query(snapshot="contrast1")
        self.assertTrue(original["run"]["correct"])
        self.assertEqual(len(original["run"]["rows"]), 3)
        distinct = self.run_query(snapshot="contrast1", query=self.scenario["sqlp"].replace("SELECT ", "SELECT DISTINCT "))
        self.assertFalse(distinct["run"]["correct"])
        self.assertEqual(len(distinct["run"]["rows"]), 1)

    def test_startup_freezes_database_and_metadata_does_not_publish_paths(self):
        with sqlite3.connect(self.original) as connection:
            connection.execute("DELETE FROM WROTE")
        self.assertEqual(len(self.run_query()["run"]["rows"]), 2)
        metadata = json.dumps(self.lab.describe())
        self.assertNotIn(str(self.directory), metadata)
        self.assertNotIn(self.lab.directory.name, metadata)
        self.assertNotIn('"database"', metadata)

    def test_writes_and_attachments_are_rejected_by_sqlite_authorizer(self):
        for query in ("DELETE FROM WROTE;", "ATTACH DATABASE ':memory:' AS other;", "SELECT load_extension('missing');"):
            with self.subTest(query=query):
                result = self.run_query(language="sql", query=query)
                self.assertEqual(result["run"]["status"], "error")
                self.assertEqual(result["run"]["error"]["stage"], "execute")
        self.assertEqual(len(self.run_query()["run"]["rows"]), 2)

    def test_empty_answer_and_compiler_error_remain_distinct(self):
        case = self.bundle["scenarios"][1]
        empty = self.run_query(scenario=case["id"], query=case["sqlp"])
        self.assertTrue(empty["run"]["correct"])
        self.assertEqual(empty["run"]["rows"], [])
        invalid = self.run_query(query="SELECT broken; COPY secrets;")
        self.assertFalse(invalid["run"]["correct"])
        self.assertEqual(invalid["run"]["error"]["stage"], "compile")
        self.assertIsNone(invalid["run"]["rows"])

    def test_unbounded_recursive_query_times_out(self):
        result = self.run_query(language="sql", query="WITH RECURSIVE t(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM t) SELECT sum(x) FROM t;")
        self.assertEqual(result["run"]["error"]["code"], "timeout")

    def test_oversized_answers_are_failures_without_scored_previews(self):
        queries = [("WITH RECURSIVE t(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM t WHERE x<1000) SELECT x FROM t;", "row_limit"),
                   ("SELECT hex(zeroblob(20000));", "size_limit")]
        for query, expected_code in queries:
            with self.subTest(expected_code=expected_code):
                result = self.run_query(language="sql", query=query)
                self.assertEqual(result["run"]["error"]["code"], expected_code)
                self.assertFalse(result["run"]["correct"])
                self.assertIsNone(result["run"]["rows"])

    def test_browser_cannot_select_files_or_arbitrary_snapshots(self):
        with self.assertRaises(ValueError):
            self.run_query(snapshot=str(self.original))
        for changes in ({"database": str(self.original)}, {"query": 2}, {"language": "natsql"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_query(**changes)

    def test_http_static_allowlist_security_headers_and_no_filesystem_routes(self):
        port = self.start()
        status, headers, page = self.request(port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Choose a path", page)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        for path in ("/../resources/private/openai_api_key", "/%2e%2e/resources/private/openai_api_key", "/tools/demo_server.py", "/favicon.ico"):
            with self.subTest(path=path):
                status, _, body = self.request(port, "GET", path)
                self.assertEqual(status, 404)
                self.assertNotIn(str(ROOT).encode(), body)

    def test_http_rejects_external_hosts_origins_and_cross_site_fetches(self):
        port = self.start()
        for headers in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(port, "GET", "/api/scenarios", headers=headers)[0], 403)
        self.assertEqual(self.request(port, "GET", "/api/scenarios", headers={"Origin": "http://127.0.0.1:" + str(port)})[0], 200)

    def test_http_json_validation_and_actual_intervention(self):
        port = self.start()
        request = {"scenario": self.scenario["id"], "language": "sqlp", "query": self.scenario["sqlp"], "snapshot": "original"}
        headers = {"Content-Type": "application/json"}
        status, _, body = self.request(port, "POST", "/api/run", json.dumps(request), headers)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["run"]["correct"])
        for body in (b"\xff", b'{"scenario":"first","scenario":"second"}', b'{"query":NaN}', b"[]"):
            with self.subTest(body=body):
                self.assertEqual(self.request(port, "POST", "/api/run", body, headers)[0], 400)
        self.assertEqual(self.request(port, "POST", "/api/run", "x", {"Content-Type":"text/plain"})[0], 415)
        self.assertEqual(self.request(port, "POST", "/api/run", "x" * (MAX_BODY_BYTES + 1), headers)[0], 413)
        self.assertEqual(self.request(port, "OPTIONS", "/api/run")[0], 405)

    def test_http_setup_failure_does_not_disclose_registered_or_temporary_paths(self):
        port = self.start()
        self.lab.compiler.unlink()
        request = {"scenario": self.scenario["id"], "language": "sqlp", "query": self.scenario["sqlp"], "snapshot": "original"}
        status, _, body = self.request(port, "POST", "/api/run", json.dumps(request), {"Content-Type":"application/json"})
        self.assertEqual(status, 503)
        self.assertNotIn(self.lab.directory.name.encode(), body)
        self.assertNotIn(str(ROOT).encode(), body)

    def test_bundle_rejects_unregistered_relative_paths_and_invalid_expected_rows(self):
        for change in ("relative", "duplicates", "bad_rows"):
            bundle = json.loads(json.dumps(self.bundle))
            if change == "relative": bundle["scenarios"][0]["snapshots"]["original"]["database"] = "relative.sqlite"
            elif change == "duplicates": bundle["scenarios"].append(bundle["scenarios"][0])
            else: bundle["scenarios"][0]["snapshots"]["original"]["expected_rows"] = [["too", "wide"]]
            with self.subTest(change=change), self.assertRaises(ValueError): DemoLab(bundle)

    def test_table_preview_reads_real_registered_records_with_typed_cells(self):
        preview = self.lab.preview({"scenario": self.scenario["id"], "snapshot": "original",
                                    "table": "AUTHOR"})
        with sqlite3.connect(self.original) as connection:
            rows = connection.execute("SELECT aid, name, uni_id, mentor_aid FROM AUTHOR").fetchall()
        self.assertEqual(preview["columns"], ["aid", "name", "uni_id", "mentor_aid"])
        self.assertEqual(preview["row_count"], len(rows))
        self.assertEqual(preview["rows"][0][1], {"type": "string", "value": rows[0][1]})
        self.assertEqual(preview["rows"][0][0], {"type": "int64", "value": str(rows[0][0])})
        self.assertEqual(preview["ai_calls"], 0)
        self.assertIn("not query results or row provenance", preview["note"])
        self.assertNotIn(self.lab.directory.name, json.dumps(preview))

    def test_preview_uses_frozen_selected_snapshot_and_preserves_duplicates(self):
        request = {"scenario": self.scenario["id"], "snapshot": "original", "table": "WROTE"}
        original = self.lab.preview(request)
        with sqlite3.connect(self.original) as connection:
            connection.execute("DELETE FROM WROTE")
        self.assertEqual(self.lab.preview(request), original)
        contrast = self.lab.preview({**request, "snapshot": "contrast1"})
        self.assertEqual(contrast["row_count"], original["row_count"] + 1)
        self.assertNotEqual(contrast["snapshot_sha256"], original["snapshot_sha256"])
        self.assertEqual(len(contrast["rows"]), len(original["rows"]) + 1)

    def test_preview_rejects_unregistered_names_and_browser_sql_or_file_paths(self):
        request = {"scenario": self.scenario["id"], "snapshot": "original", "table": "AUTHOR"}
        changes = ({"table": "sqlite_master"}, {"table": "AUTHOR; DELETE FROM WROTE;"},
                   {"snapshot": str(self.original)}, {"database": str(self.original)},
                   {"query": "SELECT * FROM AUTHOR;"}, {"table": 3}, {"scenario": "missing"})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.lab.preview({**request, **change})

    def test_preview_bounds_rows_columns_and_quotes_catalog_identifiers(self):
        path = self.directory / "quoted.sqlite"
        table = 'T"able'
        columns = ['k"ey'] + ["value" + str(i) for i in range(19)]
        quote = lambda name: '"' + name.replace('"', '""') + '"'
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE " + quote(table) + "(" + ",".join(
                quote(name) + " INTEGER" for name in columns) + ")")
            connection.executemany("INSERT INTO " + quote(table) + " VALUES (" +
                                   ",".join("?" for _ in columns) + ")",
                                   [tuple([i] * len(columns)) for i in range(20)])
        query = 'SELECT r."k""ey" FROM "T""able" AS r;'
        bundle = {"version": 1, "scenarios": [{"id": "quoted", "question": "List keys.",
                  "column_count": 1, "sql": query, "sqlp": query,
                  "catalog": {"version": 1, "tables": [{"name": table, "columns": [
                      {"name": name, "type": "integer", "unique": i == 0}
                      for i, name in enumerate(columns)]}], "references": []},
                  "snapshots": {"original": {"database": str(path),
                                              "expected_rows": [[i] for i in range(20)]}}}]}
        lab = DemoLab(bundle)
        self.addCleanup(lab.close)
        preview = lab.preview({"scenario": "quoted", "snapshot": "original", "table": table})
        self.assertEqual((len(preview["rows"]), len(preview["columns"])), (8, 16))
        self.assertEqual(preview["row_count"], 20)
        self.assertEqual(preview["column_count"], 20)
        self.assertTrue(preview["has_more"])
        self.assertTrue(preview["columns_truncated"])
        self.assertEqual(preview["columns"][0], 'k"ey')

    def test_http_preview_executes_only_registered_table_sampling(self):
        port = self.start()
        request = {"scenario": self.scenario["id"], "snapshot": "original", "table": "AUTHOR"}
        status, _, body = self.request(port, "POST", "/api/preview", json.dumps(request),
                                       {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        preview = json.loads(body)
        self.assertEqual(preview["origin"], "registered_snapshot_table_sample")
        self.assertEqual(preview["table"], "AUTHOR")
        self.assertGreater(preview["row_count"], 0)
        self.assertNotIn(str(ROOT).encode(), body)
        for change in ({"table": "sqlite_master"}, {"database": str(self.original)}):
            with self.subTest(change=change):
                self.assertEqual(self.request(port, "POST", "/api/preview", json.dumps({**request, **change}),
                                              {"Content-Type": "application/json"})[0], 400)


class OptionalBrowserLogicTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is optional for browser checks")
    def test_lab_interaction_regressions_run_with_the_offline_ci_suite(self):
        process = subprocess.run(["node", str(ROOT/"tests/lab_test.js")], cwd=ROOT,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()

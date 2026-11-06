"""Loopback-only SQLP intervention lab. Executes local queries; never calls AI."""

import argparse
from collections import Counter
from contextlib import closing
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import math
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading

from .compiler_catalog import catalog_argument_file
from .run_comparison import DEFAULT_COMPILER, SetupError, _check_workload, _compile, _condition, _rows
from .validate_dataset import _unique_object, compiler_arguments, validate_dataset

ROOT = Path(__file__).resolve().parents[1]
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DATABASE_BYTES = 64 * 1024 * 1024
STATIC = {"/": ("lab.html", "text/html"), "/lab.html": ("lab.html", "text/html"),
          "/lab.js": ("lab.js", "text/javascript"), "/lab.css": ("lab.css", "text/css")}
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
          "img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _reject_constant(value):
    raise ValueError("Nonfinite JSON is not supported")


def _json(encoded):
    return json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)


def _bag(rows):
    return Counter(json.dumps(row, sort_keys=True, allow_nan=False) for row in rows)


class DemoLab:
    """Freeze a trusted owner's bundle before serving browser interventions."""

    def __init__(self, bundle, compiler=DEFAULT_COMPILER, *, max_rows=500, timeout=2.0):
        if len(json.dumps(bundle, allow_nan=False).encode("utf-8")) > MAX_BUNDLE_BYTES:
            raise ValueError("Bundle exceeds four MiB")
        if type(max_rows) is not int or not 1 <= max_rows <= 1000:
            raise ValueError("max_rows must be between 1 and 1000")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("timeout must be finite and between zero and thirty seconds")
        self.max_rows, self.timeout = max_rows, timeout
        self.compiler = Path(compiler).resolve()
        if not self.compiler.is_file():
            raise ValueError("Build the compiler before starting the lab")
        if (not isinstance(bundle, dict) or type(bundle.get("version")) is not int or
                bundle["version"] != 1 or not isinstance(bundle.get("scenarios"), list) or
                not 1 <= len(bundle["scenarios"]) <= 100):
            raise ValueError("Bundle requires version 1 and one to one hundred scenarios")
        self.directory = tempfile.TemporaryDirectory(prefix="sqlp-lab-")
        frozen_compiler = Path(self.directory.name) / "sqlpc"
        shutil.copyfile(self.compiler, frozen_compiler)
        frozen_compiler.chmod(0o500)
        self.compiler = frozen_compiler
        self.compiler_sha256 = hashlib.sha256(frozen_compiler.read_bytes()).hexdigest()
        self.scenarios, frozen = {}, {}
        self.title = bundle.get("title", "SQLP Query Lab")
        if not isinstance(self.title, str):
            self.close()
            raise ValueError("Bundle title must be text")
        self.experiments = deepcopy(bundle.get("experiments"))
        try:
            for supplied in bundle["scenarios"]:
                scenario = deepcopy(supplied)
                if not isinstance(scenario, dict) or not isinstance(scenario.get("id"), str):
                    raise ValueError("Scenario requires an identifier")
                if not scenario["id"] or len(scenario["id"]) > 200 or scenario["id"] in self.scenarios:
                    raise ValueError("Scenario identifiers must be nonempty and unique")
                catalog = scenario.get("catalog")
                compiler_arguments(catalog)
                snapshots = scenario.get("snapshots")
                if (not isinstance(snapshots, dict) or "original" not in snapshots or
                        not 1 <= len(snapshots) <= 4):
                    raise ValueError("Scenario requires an original snapshot and at most four snapshots")
                for name, snapshot in snapshots.items():
                    if not isinstance(name, str) or not name or not isinstance(snapshot, dict):
                        raise ValueError("Snapshot names and definitions are required")
                    raw_path = snapshot.get("database")
                    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
                        raise ValueError("Registered database paths must be absolute")
                    path = Path(raw_path).resolve()
                    case = {key: scenario.get(key) for key in
                            ("id", "question", "column_count", "sql", "sqlp")}
                    case["expected_rows"] = snapshot.get("expected_rows")
                    _check_workload({"version": 1, "scope": "development", "cases": [case]}, max_rows)
                    identity = (str(path), json.dumps(catalog, sort_keys=True, allow_nan=False))
                    if identity not in frozen:
                        if len(frozen) >= 64:
                            raise ValueError("Bundle exceeds sixty-four distinct registered snapshots")
                        if not path.is_file() or path.stat().st_size > MAX_DATABASE_BYTES:
                            raise ValueError("Registered snapshot unavailable or too large")
                        destination = Path(self.directory.name) / (str(len(frozen)) + ".sqlite")
                        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                            source.execute("BEGIN")
                            if not validate_dataset(source, catalog, timeout_seconds=10)["ok"]:
                                raise ValueError("Registered snapshot failed dataset validation")
                            with closing(sqlite3.connect(destination)) as target:
                                source.backup(target)
                        if destination.stat().st_size > MAX_DATABASE_BYTES:
                            raise ValueError("Registered snapshot exceeds the size limit")
                        destination.chmod(0o400)
                        frozen[identity] = destination
                    snapshot["database"] = frozen[identity]
                    snapshot["sha256"] = hashlib.sha256(frozen[identity].read_bytes()).hexdigest()
                    snapshot["case"] = case
                    if not isinstance(snapshot.get("label", name), str):
                        raise ValueError("Snapshot label must be text")
                # Establish a usable compiler/catalog before receiving visitors.
                table = catalog["tables"][0]
                quote = lambda value: '"' + value.replace('"', '""') + '"'
                smoke = "SELECT r." + quote(table["columns"][0]["name"]) + " FROM " + quote(table["name"]) + " AS r;"
                with catalog_argument_file(catalog) as arguments:
                    compiled, _ = _compile(smoke, arguments, self.compiler, timeout)
                    if not compiled["ok"]:
                        raise ValueError("Compiler/catalog smoke check failed")
                self.scenarios[scenario["id"]] = scenario
        except Exception:
            self.close()
            raise

    def close(self):
        self.directory.cleanup()

    def describe(self):
        scenarios = []
        for scenario in self.scenarios.values():
            item = {key: scenario[key] for key in ("id", "question", "column_count", "sql", "sqlp", "catalog")}
            item["snapshots"] = [{"id": name, "label": snapshot.get("label", name),
                                  "expected_count": len(snapshot["expected_rows"]), "sha256": snapshot["sha256"]}
                                 for name, snapshot in scenario["snapshots"].items()]
            # These are deliberately selected owner-supplied display records,
            # not arbitrary filesystem/report metadata.
            saved = scenario.get("saved")
            if isinstance(saved, dict):
                item["saved"] = {key: saved[key] for key in
                                 ("label", "model", "origin", "sql", "sqlp", "token_usage") if key in saved}
            scenarios.append(item)
        return {"version": 1, "title": self.title, "scenarios": scenarios,
                "experiments": self.experiments, "mode": "manual_intervention_no_ai_calls",
                "compiler_sha256": self.compiler_sha256,
                "limits": {"max_rows": self.max_rows, "timeout_seconds": self.timeout}}

    def run(self, request):
        if not isinstance(request, dict) or set(request) != {"scenario", "language", "query", "snapshot"}:
            raise ValueError("Request requires scenario, language, query, and snapshot")
        if any(not isinstance(request[key], str) for key in request):
            raise ValueError("Request fields must be text")
        if request["language"] not in ("sql", "sqlp"):
            raise ValueError("Language must be sql or sqlp")
        if not request["query"].strip() or len(request["query"].encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError("Query must be nonempty and fit the query limit")
        scenario = self.scenarios.get(request["scenario"])
        if scenario is None or request["snapshot"] not in scenario["snapshots"]:
            raise ValueError("Unknown registered scenario or snapshot")
        snapshot = scenario["snapshots"][request["snapshot"]]
        language = request["language"]
        case = {**snapshot["case"], language: request["query"]}
        with catalog_argument_file(scenario["catalog"]) as arguments, closing(sqlite3.connect(
                snapshot["database"].as_uri() + "?mode=ro", uri=True, cached_statements=0)) as connection:
            connection.execute("BEGIN")
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16384)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_BODY_BYTES * 2)
            run = _condition(connection, case, language, arguments, self.compiler, self.max_rows, self.timeout)
            baseline = _condition(connection, snapshot["case"], language, arguments,
                                  self.compiler, self.max_rows, self.timeout)
        changed = (run["status"] != baseline["status"] or
                   len(run.get("columns") or []) != len(baseline.get("columns") or []) or
                   _bag(run.get("rows") or []) != _bag(baseline.get("rows") or []))
        return {"version": 1, "origin": "manual_intervention", "ai_calls": 0,
                "scenario": scenario["id"], "question": scenario["question"],
                "snapshot": request["snapshot"], "snapshot_label": snapshot.get("label", request["snapshot"]),
                "snapshot_sha256": snapshot["sha256"], "compiler_sha256": self.compiler_sha256,
                "run": run, "baseline": baseline, "changed_from_baseline": changed,
                "expected_rows": _rows(snapshot["expected_rows"]),
                "note": "Matches supplied snapshot answer; not a proof of general query equivalence."}


def create_server(lab, port=8765):
    semaphore = threading.BoundedSemaphore(4)

    class Handler(BaseHTTPRequestHandler):
        server_version = "SQLPLab/1"

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, format, *args):
            pass  # Do not log visitor queries or local request contents.

        def respond(self, status, value, kind="application/json"):
            encoded = (json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8")
                       if kind == "application/json" else value)
            if len(encoded) > MAX_RESPONSE_BYTES:
                status, kind = 413, "application/json"
                encoded = b'{"error":"Answer exceeds the display byte limit"}'
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", POLICY)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(encoded)

        def allowed(self):
            port = self.server.server_address[1]
            hosts = {"127.0.0.1:" + str(port), "localhost:" + str(port)}
            if port == 80:
                hosts.update(("127.0.0.1", "localhost"))
            values = self.headers.get_all("Host", [])
            if len(values) != 1 or values[0].lower() not in hosts:
                return False
            origins = self.headers.get_all("Origin", [])
            if len(origins) > 1 or origins and origins[0] not in {"http://" + host for host in hosts}:
                return False
            return self.headers.get("Sec-Fetch-Site") != "cross-site"

        def do_GET(self):
            if not self.allowed():
                self.respond(403, {"error": "Local origin required"}); return
            path = self.path.split("?", 1)[0]
            if path == "/api/scenarios":
                self.respond(200, lab.describe())
            elif path in STATIC:
                name, kind = STATIC[path]
                self.respond(200, (ROOT / "web" / name).read_bytes(), kind)
            else:
                self.respond(404, {"error": "Not found"})

        def do_POST(self):
            if not self.allowed():
                self.respond(403, {"error": "Local origin required"}); return
            if self.path != "/api/run":
                self.respond(404, {"error": "Not found"}); return
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
                self.respond(400, {"error": "One content length is required"}); return
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY_BYTES:
                self.respond(413, {"error": "Request exceeds the body limit"}); return
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                self.respond(415, {"error": "Use application/json"}); return
            if not semaphore.acquire(blocking=False):
                self.respond(429, {"error": "Local execution is busy"}); return
            try:
                encoded = self.rfile.read(length)
                if len(encoded) != length:
                    raise ValueError("Incomplete JSON request")
                request = _json(encoded)
                try:
                    result = lab.run(request)
                except SetupError:
                    self.respond(503, {"error": "Compiler or registered dataset unavailable"})
                else:
                    self.respond(200, result)
            except (ValueError, UnicodeError, RecursionError):
                self.respond(400, {"error": "Invalid request or unsupported field values"})
            except (OSError, sqlite3.Error):
                self.respond(503, {"error": "Local execution unavailable"})
            finally:
                semaphore.release()

        def do_OPTIONS(self):
            self.respond(405, {"error": "Cross-origin access is not supported"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    lab = None
    try:
        with args.bundle.open("rb") as source:
            encoded = source.read(MAX_BUNDLE_BYTES + 1)
        if len(encoded) > MAX_BUNDLE_BYTES:
            raise ValueError("Bundle exceeds four MiB")
        lab = DemoLab(_json(encoded), args.compiler)
        with create_server(lab, args.port) as server:
            print("SQLP lab: http://127.0.0.1:" + str(server.server_address[1]) + "/ (no AI calls)", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print("Lab startup failed: " + str(error))
        return 2
    finally:
        if lab is not None:
            lab.close()


if __name__ == "__main__":
    raise SystemExit(main())

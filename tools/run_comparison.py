"""Validate prepared SQL/SQLP pairs against one SQLite snapshot; no model calls."""

import argparse
from collections import Counter
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from .compiler_catalog import catalog_argument_file
from .validate_dataset import (PreflightTimeoutError, _unique_object, compiler_arguments,
                               load_catalog, validate_dataset)


DEFAULT_COMPILER = Path(__file__).resolve().parents[1] / "build" / "sqlpc"
SQLITE_BYTES = 1_000_000


class SetupError(ValueError):
    def __init__(self, stage, message, details=None):
        super().__init__(message)
        self.stage, self.details = stage, details


class QueryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _text(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Expected a nonempty string")
    value.encode("utf-8")


def _check_workload(workload, max_rows):
    if (not isinstance(workload, dict) or type(workload.get("version")) is not int or
            workload["version"] != 1 or workload.get("scope") not in ("development", "held_out")):
        raise ValueError("Expected a version-1 development or held_out workload")
    cases = workload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Workload needs a nonempty cases list")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each case must be an object")
        for field in ("id", "question"):
            _text(case.get(field))
        for field in ("sql", "sqlp"):
            if not isinstance(case.get(field), str):
                raise ValueError("Queries must be strings")
            case[field].encode("utf-8")
        if case["id"] in seen:
            raise ValueError("Duplicate case ID: " + case["id"])
        seen.add(case["id"])
        width, rows = case.get("column_count"), case.get("expected_rows")
        if type(width) is not int or width < 1 or not isinstance(rows, list) or len(rows) > max_rows:
            raise ValueError("Expected rows need a positive column_count and must fit max_rows")
        for row in rows:
            if not isinstance(row, list) or len(row) != width:
                raise ValueError("Expected row width does not match column_count")
            for value in row:
                if value is None:
                    continue
                if type(value) is int and -(2**63) <= value < 2**63:
                    continue
                if type(value) is float and math.isfinite(value):
                    continue
                if isinstance(value, str):
                    value.encode("utf-8")
                    continue
                raise ValueError("Expected cells must be null, strings, int64, or finite real numbers")


def _cell(value):
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bytes):
        return {"type": "blob", "value": value.hex()}
    if type(value) is int:
        return {"type": "int64", "value": str(value)}
    if type(value) is float:
        return {"type": "real", "value": repr(value)}
    return {"type": "string", "value": value}


def _rows(rows):
    return [[_cell(value) for value in row] for row in rows]


def _compile(query, arguments, compiler, timeout):
    try:
        process = subprocess.run([str(compiler), *arguments], input=query,
                                 text=True, encoding="utf-8", capture_output=True, timeout=timeout)
        response = json.loads(process.stdout, object_pairs_hook=_unique_object)
        if not isinstance(response, dict) or type(response.get("version")) is not int or response["version"] != 1:
            raise ValueError("Unsupported compiler response")
        if process.returncode == 1 and response.get("ok") is False:
            error = response.get("error")
            if (isinstance(error, dict) and error.get("stage") in ("lexer", "parser", "compiler")
                    and isinstance(error.get("message"), str)):
                return response, []
        if process.returncode != 0 or response.get("ok") is not True:
            raise ValueError("Compiler reported a setup/internal failure: " + str(response.get("error")))
        _text(response.get("sql"))
        trace = response.get("trace")
        if not isinstance(trace, dict) or not all(isinstance(trace.get(k), list) for k in ("joins", "paths")):
            raise ValueError("Missing compiler trace")
        if not isinstance(response.get("parameters"), list):
            raise ValueError("Missing compiler parameters")
        parameters = []
        for parameter in response["parameters"]:
            if not isinstance(parameter, dict) or not isinstance(parameter.get("value"), str):
                raise ValueError("Malformed compiler parameter")
            kind, value = parameter.get("type"), parameter["value"]
            value.encode("utf-8")
            if kind == "int64":
                value = int(value)
                if not -(2**63) <= value < 2**63:
                    raise ValueError("Compiler parameter outside int64 range")
            elif kind != "string":
                raise ValueError("Unknown compiler parameter type")
            parameters.append(value)
        return response, parameters
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise SetupError("compiler", str(error)) from error


def _read_only(action, first, second, database, source):
    if action == sqlite3.SQLITE_READ:
        # SQLite may report an empty-column table read with no database name
        # for an integer-primary-key optimization. This connection has no
        # attached databases or TEMP data, and queries cannot create either.
        allowed = database == "main" or (database is None and second == "")
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if (second or "").lower() == "load_extension" else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE) else sqlite3.SQLITE_DENY


def _execute(connection, sql, parameters, max_rows, timeout):
    deadline = time.monotonic() + timeout
    expired = False

    def progress():
        nonlocal expired
        expired = time.monotonic() >= deadline
        return int(expired)

    cursor = None
    connection.set_authorizer(_read_only)
    connection.set_progress_handler(progress, 1000)
    try:
        # Python may raise DataError without sqlite_errorcode for an oversized
        # SQL string. Detect the byte limit explicitly so compiler-produced SQL
        # receives the same resource-limit result as directly generated SQL.
        if len(sql.encode("utf-8")) > connection.getlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH):
            raise QueryError("size_limit", "Query exceeded the SQLite SQL byte limit")
        cursor = connection.execute(sql, parameters)
        if cursor.description is None:
            raise QueryError("sqlite", "Query did not return columns")
        rows = cursor.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            raise QueryError("row_limit", "Answer exceeded max_rows; no truncated answer was scored")
        return [item[0] for item in cursor.description], rows
    except (sqlite3.Error, UnicodeError) as error:
        if expired:
            code = "timeout"
        elif getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_TOOBIG:
            code = "size_limit"
        elif isinstance(error, UnicodeError) or str(error).startswith("Could not decode to UTF-8"):
            code = "text_decoding"
        else:
            code = "sqlite"
        raise QueryError(code, str(error)) from error
    finally:
        if cursor is not None:
            cursor.close()
        connection.set_progress_handler(None, 0)
        connection.set_authorizer(None)


def _condition(connection, case, language, arguments, compiler, max_rows, timeout):
    query = case[language]
    record = {"language": language, "query": query, "sql": query if language == "sql" else None,
              "parameters": [], "trace": None, "status": "error", "correct": False,
              "columns": None, "rows": None, "error": None, "token_usage": None}
    parameters = []
    if language == "sqlp":
        compiled, parameters = _compile(query, arguments, compiler, timeout)
        if not compiled["ok"]:
            record["error"] = {"stage": "compile", "diagnostic": compiled["error"]}
            return record
        record.update(sql=compiled["sql"], parameters=compiled["parameters"], trace=compiled["trace"])
    try:
        columns, rows = _execute(connection, record["sql"], parameters, max_rows, timeout)
    except QueryError as error:
        if language == "sqlp" and error.code == "text_decoding":
            # This compiler profile only projects supplied fields and binds
            # validated literals. A residual decode failure is data trouble,
            # whereas arbitrary SQL can itself construct malformed TEXT.
            raise SetupError("database", "Could not decode SQLP query results: " + str(error)) from error
        if language == "sqlp" and error.code == "sqlite":
            raise SetupError("compiled_sql", "Compiler-produced SQL failed to execute: " + str(error)) from error
        record["error"] = {"stage": "execute", "code": error.code, "message": str(error)}
    else:
        correct = (len(columns) == case["column_count"] and
                   Counter(tuple(row) for row in rows) == Counter(tuple(row) for row in case["expected_rows"]))
        record.update(status="ok", correct=correct, columns=columns, rows=_rows(rows))
    return record


def run_comparison(database_path, catalog, workload, compiler_path=DEFAULT_COMPILER, *,
                   max_rows=1000, timeout_seconds=5.0, preflight_timeout_seconds=30.0):
    try:
        if type(max_rows) is not int or not 1 <= max_rows < sys.maxsize:
            raise ValueError("max_rows must be a positive integer below sys.maxsize")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if (type(preflight_timeout_seconds) not in (int, float) or
                not math.isfinite(preflight_timeout_seconds) or preflight_timeout_seconds <= 0):
            raise ValueError("preflight_timeout_seconds must be finite and positive")
        _check_workload(workload, max_rows)
    except (ValueError, OverflowError) as error:
        raise SetupError("workload", str(error)) from error
    try:
        arguments = compiler_arguments(catalog)
    except ValueError as error:
        raise SetupError("catalog", str(error)) from error
    compiler = Path(compiler_path).resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise SetupError("compiler", "Compiler executable unavailable; run make first")
    if not hasattr(sqlite3.Connection, "setlimit"):
        raise SetupError("database", "Comparison runner requires Python 3.11 or newer")
    result = {"version": 1, "ok": True, "origin": "manual", "scope": workload["scope"],
              "limits": {"max_rows": max_rows, "timeout_seconds": timeout_seconds,
                         "preflight_timeout_seconds": preflight_timeout_seconds, "sqlite_bytes": SQLITE_BYTES},
              "cases": []}
    try:
        uri = Path(database_path).resolve().as_uri() + "?mode=ro"
        with catalog_argument_file(catalog) as arguments, closing(
                sqlite3.connect(uri, uri=True, cached_statements=0)) as connection:
            connection.execute("BEGIN")
            preflight = validate_dataset(connection, catalog, timeout_seconds=preflight_timeout_seconds)
            if not preflight["ok"]:
                raise SetupError("preflight", "Dataset failed preflight", preflight)
            result["preflight"] = preflight
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQLITE_BYTES)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQLITE_BYTES)
            for case in workload["cases"]:
                runs = []
                for language in ("sql", "sqlp"):
                    runs.append(_condition(connection, case, language, arguments, compiler, max_rows, timeout_seconds))
                    if not connection.in_transaction:
                        raise SetupError("database", "Read snapshot ended before the comparison completed")
                result["cases"].append({"id": case["id"], "question": case["question"],
                                        "column_count": case["column_count"],
                                        "expected_rows": _rows(case["expected_rows"]), "runs": runs})
                result["ok"] = result["ok"] and all(run["correct"] for run in runs)
    except ValueError as error:
        if isinstance(error, SetupError):
            raise
        raise SetupError("catalog", str(error)) from error
    except PreflightTimeoutError as error:
        raise SetupError("preflight_timeout", str(error)) from error
    except OSError as error:
        raise SetupError("catalog", str(error)) from error
    except sqlite3.Error as error:
        raise SetupError("database", str(error)) from error
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("workload", type=Path)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--max-rows", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--preflight-timeout", type=float, default=30.0,
                        help="separate dataset preflight budget in seconds (default: 30)")
    args = parser.parse_args()
    stage = "catalog"
    try:
        catalog = load_catalog(args.catalog)
        stage = "workload"
        workload = json.loads(args.workload.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        report = run_comparison(args.database, catalog, workload, args.compiler,
                                max_rows=args.max_rows, timeout_seconds=args.timeout,
                                preflight_timeout_seconds=args.preflight_timeout)
    except (OSError, ValueError) as error:
        diagnostic = {"stage": getattr(error, "stage", stage), "message": str(error)}
        if isinstance(error, SetupError) and error.details is not None:
            diagnostic["details"] = error.details
        report = {"version": 1, "ok": False, "origin": "manual", "scope": "development",
                  "error": diagnostic, "cases": []}
        print(json.dumps(report, allow_nan=False))
        return 2
    print(json.dumps(report, allow_nan=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

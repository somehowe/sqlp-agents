"""Exercise the real compiler process and independently check SQLite answers."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT / "build" / "sqlpc"
CATALOG = [
    "--column", "AUTHOR", "aid", "integer", "unique",
    "--column", "AUTHOR", "name", "string", "nonunique",
    "--column", "PAPER", "pid", "integer", "unique",
    "--column", "PAPER", "title", "string", "nonunique",
    "--column", "WROTE", "author_aid", "integer", "nonunique",
    "--column", "WROTE", "paper_pid", "integer", "nonunique",
    "--reference", "WROTE", "author", "author_aid", "AUTHOR", "aid",
    "--reference", "WROTE", "paper", "paper_pid", "PAPER", "pid",
]

MULTIHOP_CATALOG = CATALOG + [
    "--column", "AUTHOR", "uni_id", "integer", "nonunique",
    "--column", "AUTHOR", "mentor_aid", "integer", "nonunique",
    "--column", "PAPER", "publisher_uid", "integer", "nonunique",
    "--column", "UNIVERSITY", "uid", "integer", "unique",
    "--column", "UNIVERSITY", "name", "string", "nonunique",
    "--column", "UNIVERSITY", "country", "string", "nonunique",
    "--reference", "AUTHOR", "university", "uni_id", "UNIVERSITY", "uid",
    "--reference", "AUTHOR", "mentor", "mentor_aid", "AUTHOR", "aid",
    "--reference", "PAPER", "publisher", "publisher_uid", "UNIVERSITY", "uid",
]


def multihop_fixture():
    database = sqlite3.connect(":memory:")
    database.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE UNIVERSITY(uid INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL);
        CREATE TABLE AUTHOR(
            aid INTEGER PRIMARY KEY, name TEXT NOT NULL,
            uni_id INTEGER NOT NULL REFERENCES UNIVERSITY(uid),
            mentor_aid INTEGER NOT NULL REFERENCES AUTHOR(aid) DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TABLE PAPER(
            pid INTEGER PRIMARY KEY, title TEXT NOT NULL,
            publisher_uid INTEGER NOT NULL REFERENCES UNIVERSITY(uid)
        );
        CREATE TABLE WROTE(
            author_aid INTEGER NOT NULL REFERENCES AUTHOR(aid),
            paper_pid INTEGER NOT NULL REFERENCES PAPER(pid)
        );
        BEGIN;
        INSERT INTO UNIVERSITY VALUES (1, 'Waterloo', 'CA'), (2, 'Aarhus', 'DK');
        INSERT INTO AUTHOR VALUES (1, 'Alice', 1, 2), (2, 'Bob', 2, 1), (3, 'Caro', 1, 2);
        INSERT INTO PAPER VALUES (10, 'Databases', 2), (11, 'Graphs', 1);
        INSERT INTO WROTE VALUES (1, 10), (1, 10), (2, 10), (3, 11), (2, 11);
        COMMIT;
    """)
    return database


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


class CompilerCliTests(unittest.TestCase):
    def invoke(self, query, args=None, code=0, stage=None):
        result = subprocess.run(
            [str(COMPILER), *(CATALOG if args is None else args)],
            input=query.encode("utf-8") if isinstance(query, str) else query,
            capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, code, result.stdout)
        self.assertEqual(result.stderr, b"")
        text = result.stdout.decode("utf-8")
        self.assertEqual(text.count("\n"), 1)
        record = json.loads(text)
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["ok"], code == 0)
        if stage:
            self.assertEqual(record["error"]["stage"], stage)
            self.assertTrue(record["error"]["message"])
        return record

    @staticmethod
    def parameters(record):
        return [int(p["value"]) if p["type"] == "int64" else p["value"]
                for p in record["parameters"]]

    def test_sqlite_answers_preserve_duplicates_and_literal_values(self):
        with sqlite3.connect(":memory:") as database:
            database.executescript("""
                CREATE TABLE AUTHOR(aid INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE PAPER(pid INTEGER PRIMARY KEY, title TEXT NOT NULL);
                CREATE TABLE WROTE(author_aid INTEGER NOT NULL, paper_pid INTEGER NOT NULL);
                INSERT INTO AUTHOR VALUES (1, 'Alice'), (2, 'Bob');
                INSERT INTO PAPER VALUES (10, 'Databases');
                INSERT INTO WROTE VALUES (1, 10), (1, 10), (2, 10);
            """)
            for distinct, expected in [("", [("Alice",), ("Alice",), ("Bob",)]),
                                       ("DISTINCT ", [("Alice",), ("Bob",)])]:
                query = (f"SELECT {distinct}w.author.name AS name FROM WROTE w "
                         "WHERE w.paper.title = 'Databases';")
                record = self.invoke(query)
                self.assertEqual(record["parameters"], [{"type": "string", "value": "Databases"}])
                rows = database.execute(record["sql"], self.parameters(record)).fetchall()
                self.assertCountEqual(rows, expected)

            record = self.invoke("SELECT a.name FROM AUTHOR a WHERE a.name = 'x'' OR 1=1 --';")
            self.assertNotIn("OR 1=1", record["sql"])
            self.assertEqual(database.execute(record["sql"], self.parameters(record)).fetchall(), [])

    def test_integer_boundaries_are_strings_and_execute_exactly(self):
        with sqlite3.connect(":memory:") as database:
            database.execute("CREATE TABLE AUTHOR(aid INTEGER PRIMARY KEY, name TEXT)")
            for number in [-9223372036854775808, 9223372036854775807]:
                database.execute("INSERT INTO AUTHOR VALUES (?, ?)", (number, str(number)))
                record = self.invoke(f"SELECT a.name FROM AUTHOR a WHERE a.aid = {number};")
                self.assertEqual(record["parameters"], [{"type": "int64", "value": str(number)}])
                self.assertEqual(database.execute(record["sql"], self.parameters(record)).fetchall(), [(str(number),)])

    def test_multihop_filter_preserves_duplicates_and_distinct(self):
        with multihop_fixture() as database:
            for distinct, expected in [("", [("Alice",), ("Alice",), ("Caro",)]),
                                       ("DISTINCT ", [("Alice",), ("Caro",)])]:
                with self.subTest(distinct=distinct):
                    record = self.invoke(
                        f"SELECT {distinct}w.author.name FROM WROTE w "
                        "WHERE w.author.university.country = 'CA';", MULTIHOP_CATALOG)
                    rows = database.execute(record["sql"], self.parameters(record)).fetchall()
                    self.assertCountEqual(rows, expected)
                    self.assertEqual(record["sql"].count("JOIN "), 2)

    def test_multihop_roles_and_shared_prefixes(self):
        record = self.invoke(
            "SELECT w.author.name, w.author.university.name, w.paper.publisher.name "
            "FROM WROTE w WHERE w.paper.publisher.country = 'DK';", MULTIHOP_CATALOG)
        with multihop_fixture() as database:
            rows = database.execute(record["sql"], self.parameters(record)).fetchall()
        self.assertCountEqual(rows, [("Alice", "Waterloo", "Aarhus"),
                                     ("Alice", "Waterloo", "Aarhus"),
                                     ("Bob", "Aarhus", "Aarhus")])
        # Author and publisher reach the same table through different prefixes.
        # The WHERE path should reuse the publisher joins already selected.
        self.assertEqual(record["sql"].count("JOIN "), 4)

    def test_finite_self_reference_cycle_uses_distinct_bindings(self):
        record = self.invoke(
            "SELECT a.name, a.mentor.name, a.mentor.mentor.name, a.mentor.university.country "
            "FROM AUTHOR a WHERE a.mentor.mentor.aid = 1;", MULTIHOP_CATALOG)
        with multihop_fixture() as database:
            rows = database.execute(record["sql"], self.parameters(record)).fetchall()
        self.assertCountEqual(rows, [("Alice", "Bob", "Alice", "DK"),
                                     ("Caro", "Bob", "Alice", "DK")])
        self.assertEqual(record["sql"].count("JOIN "), 3)

    def test_deep_path_semantic_errors(self):
        for query in [
            "SELECT w.author.name FROM WROTE w WHERE w.author.university.uid = 'one';",
            "SELECT w.author.university.missing FROM WROTE w;",
            "SELECT w.author.missing.country FROM WROTE w;",
        ]:
            with self.subTest(query=query):
                self.invoke(query, MULTIHOP_CATALOG, code=1, stage="compiler")

    def test_quoted_names_unicode_and_control_characters_round_trip(self):
        table, column, alias = 'table" café', 'field\t\n"', 'from'
        value = 'O\'Brien "\\\t\n\x01 café 🧠'
        args = ["--column", table, column, "string", "nonunique"]
        query = (f"SELECT {identifier(alias)}.{identifier(column)} "
                 f"FROM {identifier(table)} {identifier(alias)} "
                 f"WHERE {identifier(alias)}.{identifier(column)} = '{value.replace(chr(39), chr(39) * 2)}';")
        record = self.invoke(query, args)
        self.assertEqual(record["parameters"], [{"type": "string", "value": value}])
        with sqlite3.connect(":memory:") as database:
            database.execute(f"CREATE TABLE {identifier(table)}({identifier(column)} TEXT)")
            database.execute(f"INSERT INTO {identifier(table)} VALUES (?)", (value,))
            self.assertEqual(database.execute(record["sql"], self.parameters(record)).fetchall(), [(value,)])

    def test_structured_query_errors(self):
        query = "SELECT a.name\nFROM AUTHOR a\nWHERE a.aid = ;"
        record = self.invoke(query, code=1, stage="parser")
        self.assertEqual(record["error"]["location"], {"offset": query.index(';'), "line": 3, "column": 15})
        self.invoke("SELECT a.name FROM AUTHOR a WHERE a.aid = 9223372036854775808;", code=1, stage="lexer")
        self.invoke("SELECT a.missing FROM AUTHOR a;", code=1, stage="compiler")
        self.invoke("SELECT a.name FROM AUTHOR a WHERE a.aid = 'one';", code=1, stage="compiler")
        self.invoke("SELECT a.name FROM AUTHOR a; SELECT a.aid FROM AUTHOR a;", code=1, stage="parser")
        self.invoke("", code=1, stage="parser")
        self.invoke("SELECT a.name FROM AUTHOR a;\0", code=1, stage="lexer")

    def test_argument_errors(self):
        for args in [["--unknown"], ["--column", "AUTHOR"], ["--reference", "AUTHOR"],
                     ["--column", "T", "id", "float", "unique"],
                     ["--column", "T", "id", "integer", "yes"], CATALOG + ["--help"]]:
            with self.subTest(args=args):
                self.invoke("", args, code=2, stage="arguments")

    def test_catalog_errors_including_unused_references(self):
        for args in [[], ["--column", "", "id", "integer", "unique"],
                     ["--column", "T", "", "integer", "unique"],
                     CATALOG + ["--column", "author", "other", "integer", "nonunique"],
                     CATALOG + ["--column", "AUTHOR", "AID", "integer", "unique"],
                     CATALOG + ["--reference", "WROTE", "author", "author_aid", "AUTHOR", "aid"],
                     CATALOG + ["--reference", "WROTE", "AUTHOR", "author_aid", "AUTHOR", "aid"],
                     CATALOG + ["--reference", "WROTE", "missing", "unknown", "AUTHOR", "aid"],
                     CATALOG + ["--reference", "WROTE", "bad_type", "author_aid", "AUTHOR", "name"],
                     CATALOG + ["--reference", "AUTHOR", "bad_unique", "name", "PAPER", "title"]]:
            with self.subTest(args=args):
                self.invoke("SELECT a.name FROM AUTHOR a;", args, code=1, stage="catalog")

    def test_invalid_utf8_produces_valid_json(self):
        for source in [b"\x80", b"\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xe2(\xa1", b"\xf0\x90"]:
            with self.subTest(source=source):
                self.invoke(source, code=1, stage="input")

    def test_help(self):
        result = subprocess.run([str(COMPILER), "--help"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertIn(b"Usage: sqlpc", result.stdout)

    def test_stdin_read_failure_is_an_input_error(self):
        with open(os.devnull, "wb") as write_only:
            result = subprocess.run([str(COMPILER), *CATALOG], stdin=write_only,
                                    capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"]["stage"], "input")

    @unittest.skipUnless(Path("/dev/full").exists(), "Output-failure check requires /dev/full")
    def test_output_failure_does_not_report_success(self):
        with open("/dev/full", "wb") as full:
            result = subprocess.run([str(COMPILER), "--help"], stdout=full,
                                    stderr=subprocess.PIPE, timeout=5)
        self.assertEqual(result.returncode, 3)


if __name__ == "__main__":
    unittest.main()

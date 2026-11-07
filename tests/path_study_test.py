"""Synthetic path-study semantics, contrasts, and no-model preparation checks."""

from collections import Counter
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_path_study import (DEPTHS, build_workload, create_snapshot,
                                     load_records, make_catalog, oracle_rows,
                                     prepare, reference_queries, task_plans)
from tools.run_pilot import build_request
from tools.validate_dataset import validate_dataset


class PathStudyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_questions_cover_declared_depths_roles_and_semantics_without_duplicate_text(self):
        plans = task_plans()
        self.assertEqual(len(plans), 32)
        self.assertEqual(Counter(plan["depth"] for plan in plans), {depth: 8 for depth in DEPTHS})
        self.assertEqual(len({p["id"] for p in plans}), 32)
        self.assertEqual(len({p["question"] for p in plans}), 32)
        self.assertEqual(sum(p["kind"] == "empty_control" for p in plans), 0)
        families = [[p for p in plans if p["depth"] == depth] for depth in DEPTHS]
        for family in families[1:]:
            self.assertEqual([p["kind"] for p in family], [p["kind"] for p in families[0]])
            self.assertEqual([p["distinct"] for p in family], [p["distinct"] for p in families[0]])
            self.assertEqual([len(p["projections"]) for p in family],
                             [len(p["projections"]) for p in families[0]])
            self.assertEqual([[value for _, value in p["predicates"]] for p in family],
                             [[value for _, value in p["predicates"]] for p in families[0]])
        for plan in plans:
            paths = [*plan["projections"], *(path for path, _ in plan["predicates"])]
            self.assertEqual(max(len(path) - 1 for path in paths), plan["depth"])
            sql, sqlp = reference_queries(plan)
            self.assertTrue(sql.endswith(";"))
            self.assertTrue(sqlp.endswith(";"))
            self.assertNotIn("JOIN", sqlp)

    def test_record_walk_distinguishes_roles_and_preserves_duplicates(self):
        records = {"Assignment": [
            {"AssignmentId": 1, "Code": "A", "RequesterId": 1, "ApproverId": 2, "Country": "Canada"},
            {"AssignmentId": 2, "Code": "B", "RequesterId": 1, "ApproverId": 3, "Country": "Denmark"}],
            "Person": [{"PersonId": 1, "Name": "Alice", "Country": "Canada"},
                       {"PersonId": 2, "Name": "Bob", "Country": "Denmark"},
                       {"PersonId": 3, "Name": "Alice", "Country": "Japan"}]}
        indexes = {"Person": {p["PersonId"]: p for p in records["Person"]}}
        plans = task_plans()
        self.assertEqual(oracle_rows(plans[4], records, indexes), [["A", "Alice", "Bob"], ["B", "Alice", "Alice"]])
        self.assertEqual(oracle_rows(plans[6], records, indexes), [["Alice"], ["Alice"]])
        distinct = {**plans[6], "distinct": True}
        self.assertEqual(oracle_rows(distinct, records, indexes), [["Alice"]])
        self.assertEqual(oracle_rows(plans[2], records, indexes), [["A", "Alice"], ["B", "Alice"]])

    def test_seeded_records_repeat_and_different_seeds_change_valid_graphs(self):
        first, same, other = (self.directory / name for name in ("a.sqlite", "b.sqlite", "c.sqlite"))
        for path, seed in ((first, 7), (same, 7), (other, 8)):
            create_snapshot(path, seed)
        with closing(sqlite3.connect(first)) as a, closing(sqlite3.connect(same)) as b, closing(sqlite3.connect(other)) as c:
            self.assertEqual(load_records(a), load_records(b))
            self.assertNotEqual(load_records(a), load_records(c))
            self.assertTrue(validate_dataset(a, make_catalog())["ok"])
            self.assertFalse(a.execute("PRAGMA foreign_key_check").fetchall())
            records, indexes = load_records(a)
            seen, person = set(), 1
            for _ in range(16):
                self.assertNotIn(person, seen)
                seen.add(person)
                person = indexes["Person"][person]["ManagerId"]
            self.assertEqual(person, 1)
            self.assertEqual(len(seen), 16)

    def test_complete_preparation_checks_both_languages_on_all_contrast_snapshots(self):
        output = self.directory / "study"
        manifest = prepare(output)
        self.assertEqual(manifest["unique_question_templates"], 32)
        self.assertEqual(manifest["model_calls"], 0)
        self.assertEqual(manifest["human_answer_review"], "pending")
        self.assertEqual(len(manifest["snapshots"]), 3)
        self.assertEqual(manifest["role_swap_sensitivity"]["cases_detected_on_at_least_one_snapshot"], 32)
        self.assertEqual(manifest["role_swap_sensitivity"]["undetected_cases"], [])
        questions = None
        for item in manifest["snapshots"]:
            self.assertTrue(item["prepared_pairs_correct"])
            workload = json.loads((output / item["workload"]).read_text())
            public = [(c["id"], c["question"], c["sql"], c["sqlp"]) for c in workload["cases"]]
            if questions is None:
                questions = public
            self.assertEqual(public, questions)
            self.assertEqual(workload["scope"], "development")
            folder = (output / item["database"]).parent
            report = json.loads((folder / "prepared-report.json").read_text())
            self.assertTrue(report["ok"])
            self.assertEqual(report["origin"], "manual")
            self.assertEqual(sum(len(c["runs"]) for c in report["cases"]), 64)
            repeated = next(c for c in workload["cases"] if c["id"] == "path-d1-07")["expected_rows"]
            self.assertEqual(len(repeated), 32)
            self.assertLess(len(set(map(tuple, repeated))), len(repeated))

    def test_distractor_schema_keeps_core_data_questions_and_answers_identical(self):
        path_a, path_b = self.directory / "small.sqlite", self.directory / "wide.sqlite"
        create_snapshot(path_a, 123)
        create_snapshot(path_b, 123, distractor_tables=16)
        with closing(sqlite3.connect(path_a)) as a, closing(sqlite3.connect(path_b)) as b:
            self.assertEqual(build_workload(a), build_workload(b, distractor_tables=16))
            self.assertEqual(load_records(a)[0], {name: rows for name, rows in load_records(b, 16)[0].items()
                                                if name in ("Person", "Assignment")})
            self.assertTrue(validate_dataset(b, make_catalog(16))["ok"])
        self.assertEqual(len(make_catalog(16)["tables"]), 18)
        self.assertEqual(len(make_catalog(16)["references"]), 23)

    def test_public_requests_exclude_private_oracle_plans_and_expected_rows(self):
        path = self.directory / "data.sqlite"
        create_snapshot(path, 2)
        with closing(sqlite3.connect(path)) as connection:
            workload = build_workload(connection)
        case = workload["cases"][0]
        payload = build_request(case["question"], make_catalog(), "sqlp", "Fixture guide",
                                {"model": "offline-fixture", "max_output_tokens": 128})
        encoded = json.dumps(payload)
        self.assertNotIn(case["sql"], encoded)
        self.assertNotIn(case["sqlp"], encoded)
        for field in ("expected_rows", "oracle-plans", "projections", "predicates", "dataset_kind"):
            self.assertNotIn(field, encoded)
        self.assertEqual(json.loads(payload["input"][0]["content"])["question"], case["question"])

    def test_invalid_parameters_and_existing_destinations_do_not_overwrite_files(self):
        output = self.directory / "keep"
        output.mkdir()
        marker = output / "mine"
        marker.write_text("preserve")
        with self.assertRaises(FileExistsError):
            prepare(output)
        self.assertEqual(marker.read_text(), "preserve")
        for options in ({"seed": -1}, {"seed": True}, {"alternate_seeds": (3, 3)},
                        {"distractor_tables": -1}, {"distractor_tables": 65}):
            new = self.directory / "new"
            with self.subTest(options=options), self.assertRaises(ValueError):
                prepare(new, **options)
            self.assertFalse(new.exists())


if __name__ == "__main__":
    unittest.main()

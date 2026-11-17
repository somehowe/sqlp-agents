"""Checks for the larger synthetic text-to-SQL workload and its answer oracle."""

from collections import Counter
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import prepare_scale_study as scale


def bag(rows):
    return Counter(map(tuple, rows))


def records_from(path, domain):
    records, indexes = {}, {}
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        for table in scale.make_catalog(domain)["tables"]:
            name = table["name"]
            records[name] = [dict(row) for row in connection.execute('SELECT * FROM "' + name + '"')]
            indexes[name] = {row["Id"]: row for row in records[name]}
    return records, indexes


class ScaleStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / "build/sqlpc").exists():
            raise unittest.SkipTest("Build sqlpc before running workload integration checks")
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name) / "study"
        cls.manifest = scale.prepare(cls.directory, cases_per_domain=70, root_rows=2400)

    def workload(self, domain, contrast=False):
        directory = self.directory / domain
        if contrast:
            directory /= "contrast-1"
        return json.loads((directory / "workload.json").read_text())

    def test_all_reference_pairs_match_python_bags_on_both_snapshots(self):
        self.assertEqual(self.manifest["case_count"], 280)
        self.assertEqual(self.manifest["prepared_query_executions"], 1120)
        for dataset in self.manifest["datasets"]:
            self.assertEqual(len(dataset["snapshots"]), 2)
            for snapshot in dataset["snapshots"]:
                self.assertEqual(snapshot["prepared_sql_matches"], 70)
                self.assertEqual(snapshot["prepared_sqlp_matches"], 70)
                self.assertTrue(all(len(case["expected_rows"]) <= 1000
                                    for case in self.workload(dataset["id"], snapshot["id"] != "primary")["cases"]))

    def test_default_count_has_1000_unique_queries_and_harder_structures(self):
        all_questions, templates = set(), set()
        for domain in scale.DOMAINS:
            records, indexes = records_from(self.directory / domain / "database.sqlite", domain)
            plans = scale.task_plans(domain, records, indexes)
            self.assertEqual(len(plans), 250)
            queries = [scale.reference_queries(domain, plan)[1] for plan in plans]
            self.assertEqual(len(set(queries)), 250)
            self.assertEqual({plan["depth"] for plan in plans}, {1, 2, 3, 4, 6, 8})
            self.assertEqual({plan["family"] for plan in plans}, set(scale.FAMILIES))
            self.assertTrue(any(len(plan["predicates"]) == 3 for plan in plans))
            self.assertTrue(any(scale.reference_queries(domain, plan)[2] >= 12 for plan in plans))
            all_questions.update(plan["question"] for plan in plans)
            templates.update(plan["template_id"] for plan in plans)
        self.assertEqual(len(all_questions), 1000)
        self.assertGreater(len(templates), 700)

    def test_wrong_role_missing_predicate_and_distinct_mutants_are_detected(self):
        domain = "commerce"
        records, indexes = records_from(self.directory / domain / "database.sqlite", domain)
        plans = json.loads((self.directory / domain / "oracle-plans.json").read_text())
        competing = deepcopy(plans[1])
        original = bag(scale.oracle_rows(domain, competing, records, indexes))
        competing["projections"][1:] = list(reversed(competing["projections"][1:]))
        self.assertNotEqual(bag(scale.oracle_rows(domain, competing, records, indexes)), original)
        filtered = deepcopy(plans[9])
        original = bag(scale.oracle_rows(domain, filtered, records, indexes))
        filtered["predicates"] = filtered["predicates"][:1]
        self.assertNotEqual(bag(scale.oracle_rows(domain, filtered, records, indexes)), original)
        duplicated = deepcopy(plans[4])
        original = bag(scale.oracle_rows(domain, duplicated, records, indexes))
        duplicated["distinct"] = True
        self.assertNotEqual(bag(scale.oracle_rows(domain, duplicated, records, indexes)), original)

    def test_contrasts_change_answers_but_never_question_or_reference_queries(self):
        for domain in scale.DOMAINS:
            original = self.workload(domain)["cases"]
            contrast = self.workload(domain, True)["cases"]
            changed = 0
            for left, right in zip(original, contrast):
                for field in ("id", "question", "sql", "sqlp", "column_count"):
                    self.assertEqual(left[field], right[field])
                changed += bag(left["expected_rows"]) != bag(right["expected_rows"])
                if left["design"]["deliberate_empty_control"]:
                    self.assertEqual(left["expected_rows"], [])
                    self.assertEqual(right["expected_rows"], [])
            self.assertGreater(changed, 50)

    def test_field_questions_disambiguate_attributes_from_relationships(self):
        for domain in scale.DOMAINS:
            cases = self.workload(domain)["cases"]
            self.assertTrue(any("stored Country field" in case["question"] for case in cases))
            self.assertTrue(any("stored Score field" in case["question"] for case in cases))
            self.assertTrue(any("each distinct output tuple once" in case["question"] for case in cases))
            self.assertTrue(any("preserve repeated output tuples" in case["question"] for case in cases))
            for case in cases:
                self.assertIn("stored Batch field equals ", case["question"])
                self.assertEqual(case["design"]["expected_empty"], not case["expected_rows"])

    def test_manifest_hashes_and_honest_scope_metadata(self):
        for name, expected in self.manifest["files_sha256"].items():
            self.assertEqual(hashlib.sha256((self.directory / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(self.manifest["human_answer_review"], "pending")
        self.assertEqual(self.manifest["model_calls"], 0)
        self.assertEqual(self.manifest["deliberate_empty_control_cases"], 28)
        self.assertLess(self.manifest["abstract_structural_template_count"], self.manifest["structural_template_count"])
        self.assertTrue(any("correlated" in limitation for limitation in self.manifest["limitations"]))

    def test_existing_output_is_preserved_and_invalid_settings_rejected(self):
        manifest = (self.directory / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            scale.prepare(self.directory, cases_per_domain=1, root_rows=48)
        self.assertEqual((self.directory / "manifest.json").read_bytes(), manifest)
        for options in ({"cases_per_domain": 0}, {"cases_per_domain": True}, {"root_rows": 24001},
                        {"alternate_seeds": (20261116,)}, {"seed": -1}):
            destination = Path(self.temp.name) / "invalid"
            with self.subTest(options=options), self.assertRaises(ValueError):
                scale.prepare(destination, **options)
            self.assertFalse(destination.exists())

    def test_seed_recreates_identical_frozen_data(self):
        path = Path(self.temp.name) / "reproduced.sqlite"
        _, _, counts = scale.create_snapshot(path, "commerce", 20261116, root_rows=2400)
        source = self.directory / "commerce" / "database.sqlite"
        self.assertEqual(path.read_bytes(), source.read_bytes())
        self.assertEqual(counts, self.manifest["datasets"][0]["row_counts"])


if __name__ == "__main__":
    unittest.main()

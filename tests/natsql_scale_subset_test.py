"""Structural subset selection must preserve groups and ignore model outcomes."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import prepare_natsql_scale_subset as subset
from tools import prepare_scale_study as scale
from tools.run_pilot import digest


class NatSQLScaleSubsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / "build/sqlpc").exists():
            raise unittest.SkipTest("Build sqlpc before running workload integration checks")
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.source = cls.directory / "source"
        cls.manifest = scale.prepare(cls.source, cases_per_domain=30, root_rows=96)
        cls.study = cls.directory / "original-study"
        shutil.copytree(cls.source, cls.study / "inputs/supported")
        requests = []
        for dataset in cls.manifest["datasets"]:
            cases = json.loads((cls.source / dataset["directory"] / "workload.json").read_text())["cases"]
            for start in range(0, len(cases), 4):
                ids = [case["id"] for case in cases[start:start + 4]]
                for language in ("sql", "sqlp"):
                    payload = {"language": language, "tasks": ids}
                    requests.append({"id": "request-" + str(len(requests)), "dataset_id": "supported/" + dataset["id"],
                                     "language": language, "case_ids": ids, "payload": payload,
                                     "request_sha256": digest(payload)})
        cls.design = {"batch_size": 4, "settings": {"model": "test-model", "max_output_tokens": 100}, "requests": requests,
                      "frozen_sha256": {"inputs/supported/manifest.json": hashlib.sha256((cls.source / "manifest.json").read_bytes()).hexdigest()}}
        (cls.study / "design.json").write_text(json.dumps(cls.design))
        # Deliberately unreadable outcomes demonstrate selection has no dependency
        # on saved scores or model-generated text.
        (cls.study / "runs").mkdir()
        (cls.study / "runs/report.json").write_text("THIS IS NOT JSON AND MUST NEVER BE READ")
        cls.output = cls.directory / "subset"
        cls.result = subset.prepare(cls.source, cls.study, cls.output)

    def test_repeated_role_bindings_cycles_and_shared_prefixes(self):
        catalog = {"tables": [{"name": name, "columns": [{"name": "Name"}]} for name in ("Root", "Person", "Office")],
                   "references": [{"source_table": a, "feature": b, "target_table": c}
                                  for a, b, c in (("Root", "owner", "Person"), ("Root", "reviewer", "Person"),
                                                  ("Person", "manager", "Person"), ("Person", "office", "Office"))]}
        shared = {"projections": [["owner", "Name"], ["owner", "office", "Name"]], "predicates": [[["owner", "Name"], "X"]]}
        self.assertEqual(subset.binding_conflicts(shared, catalog, "Root"), [])
        competing = {"projections": [["owner", "Name"], ["reviewer", "Name"]], "predicates": []}
        self.assertTrue(subset.binding_conflicts(competing, catalog, "Root"))
        cycle = {"projections": [["owner", "manager", "Name"]], "predicates": []}
        self.assertTrue(subset.binding_conflicts(cycle, catalog, "Root"))
        invalid = {"projections": [["missing", "Name"]], "predicates": []}
        with self.assertRaises(ValueError):
            subset.binding_conflicts(invalid, catalog, "Root")

    def test_complete_original_groups_and_original_hashes_are_preserved(self):
        selection = json.loads((self.output / "selection.json").read_text())
        self.assertFalse(selection["model_outcomes_read"])
        self.assertGreater(selection["totals"]["selected_group_count"], 0)
        original = {request["id"]: request for request in self.design["requests"]}
        for dataset in selection["datasets"]:
            selected = []
            for group in dataset["groups"]:
                for language, source in group["original_requests"].items():
                    self.assertEqual(source["request_sha256"], original[source["id"]]["request_sha256"])
                    self.assertEqual(group["case_ids"], original[source["id"]]["case_ids"])
                    self.assertEqual(language, original[source["id"]]["language"])
                if group["eligible"]:
                    self.assertEqual(len(group["case_ids"]), 4)
                    self.assertEqual(group["conflicting_cases"], {})
                    selected.extend(group["case_ids"])
                elif len(group["case_ids"]) != 4:
                    self.assertEqual(group["exclusion"], "partial_original_group")
            self.assertEqual(selected, dataset["selected_case_ids"])
        self.assertEqual(self.result["case_count"], self.result["selected_original_group_count"] * 4)

    def test_full_catalog_and_all_database_snapshots_remain_byte_identical(self):
        for dataset in self.result["datasets"]:
            directory = dataset["directory"]
            self.assertEqual((self.source / directory / "catalog.json").read_bytes(), (self.output / directory / "catalog.json").read_bytes())
            for snapshot in dataset["snapshots"]:
                self.assertEqual((self.source / snapshot["database"]).read_bytes(), (self.output / snapshot["database"]).read_bytes())
                before = json.loads((self.source / snapshot["workload"]).read_text())["cases"]
                after = json.loads((self.output / snapshot["workload"]).read_text())["cases"]
                expected = {case["id"]: case for case in before}
                for case in after:
                    self.assertEqual(case, expected[case["id"]])

    def test_subset_hashes_and_existing_outputs(self):
        for relative, expected in self.result["files_sha256"].items():
            self.assertEqual(hashlib.sha256((self.output / relative).read_bytes()).hexdigest(), expected)
        before = (self.output / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            subset.prepare(self.source, self.study, self.output)
        self.assertEqual((self.output / "manifest.json").read_bytes(), before)

    def test_changed_source_or_original_request_is_rejected(self):
        altered = self.directory / "altered-source"
        shutil.copytree(self.source, altered)
        with (altered / "commerce/catalog.json").open("a") as stream:
            stream.write(" ")
        with self.assertRaisesRegex(ValueError, "Source benchmark changed"):
            subset.select(altered, self.study)
        changed_study = self.directory / "altered-study"
        shutil.copytree(self.study, changed_study)
        changed = deepcopy(self.design)
        changed["requests"][0]["payload"]["changed"] = True
        (changed_study / "design.json").write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "Original request hash mismatch"):
            subset.select(self.source, changed_study)


if __name__ == "__main__":
    unittest.main()

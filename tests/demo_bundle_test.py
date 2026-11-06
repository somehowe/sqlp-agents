"""Public-fixture demo creation and real manual interventions, without APIs."""

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import demo_bundle as bundle_module
from tools.demo_bundle import build_bundle
from tools.demo_server import DemoLab


class DemoBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def build(self, name="bundle", **options):
        report = build_bundle(self.directory / name, **options)
        bundle = json.loads(Path(report["bundle"]).read_text())
        return report, bundle

    def lab(self, report, bundle):
        lab = DemoLab(bundle, Path(report["compiler"]))
        self.addCleanup(lab.close)
        return lab

    def test_default_bundle_builds_from_public_sources_and_publishes_verified_artifacts(self):
        sources = [ROOT / "examples/academic" / name for name in ("data.sql", "catalog.json", "workload.json")]
        before = [source.read_bytes() for source in sources]
        report, bundle = self.build()
        self.assertTrue(report["ok"])
        self.assertEqual((report["scenario_count"], report["ai_calls"]), (11, 0))
        self.assertEqual([source.read_bytes() for source in sources], before)
        manifest = json.loads((Path(report["bundle"]).parent / "manifest.json").read_text())
        self.assertEqual(manifest["validation_executions"], 40)
        self.assertEqual(manifest["human_answer_review"], "pending")
        for name, expected in manifest["files_sha256"].items():
            actual = hashlib.sha256((Path(report["bundle"]).parent / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, name)
        for scenario in bundle["scenarios"]:
            self.assertEqual(scenario["saved"]["origin"], "manual")
            self.assertNotIn("model", scenario["saved"])
            for language in ("sql", "sqlp"):
                self.assertIsNone(scenario["saved"][language]["token_usage"])
            for snapshot in scenario["snapshots"].values():
                self.assertTrue(Path(snapshot["database"]).is_absolute())
                self.assertTrue(Path(snapshot["database"]).is_file())
        self.assertNotIn("experiments", bundle)
        self.assertEqual(self.lab(report, bundle).describe()["mode"], "manual_intervention_no_ai_calls")

    def test_actual_compiler_role_intervention_and_six_hop_trace(self):
        report, bundle = self.build()
        lab = self.lab(report, bundle)
        role = next(case for case in bundle["scenarios"] if case["id"] == "academic-canadian_authors_danish_publishers")
        request = {"scenario": role["id"], "language": "sqlp", "query": role["sqlp"], "snapshot": "original"}
        original = lab.run(request)
        self.assertTrue(original["run"]["correct"])
        self.assertEqual(original["ai_calls"], 0)
        edited = lab.run({**request, "query": role["sqlp"].replace(
            "w.paper.publisher.country", "w.author.university.country")})
        self.assertFalse(edited["run"]["correct"])
        self.assertTrue(edited["changed_from_baseline"])
        deep = next(case for case in bundle["scenarios"] if case["id"] == "controlled-path-d6-05")
        result = lab.run({"scenario": deep["id"], "language": "sqlp", "query": deep["sqlp"],
                          "snapshot": "contrast-1"})
        self.assertTrue(result["run"]["correct"])
        self.assertEqual(max(len(path["join_ids"]) for path in result["run"]["trace"]["paths"]), 6)
        self.assertEqual(len(result["run"]["trace"]["joins"]), 12)

    def test_duplicate_name_contrast_exposes_and_repairs_distinct_with_valid_keys(self):
        report, bundle = self.build()
        lab = self.lab(report, bundle)
        case = next(case for case in bundle["scenarios"] if case["id"] == "duplicate-author-names")
        self.assertIn("Deliberately introduced DISTINCT", case["saved"]["label"])
        for language in ("sql", "sqlp"):
            request = {"scenario": case["id"], "language": language, "query": case[language], "snapshot": "original"}
            self.assertTrue(lab.run(request)["run"]["correct"])
            collision = lab.run({**request, "snapshot": "contrast1"})
            self.assertFalse(collision["run"]["correct"])
            self.assertEqual(len(collision["run"]["rows"]), 2)
            self.assertEqual(len(collision["expected_rows"]), 3)
            repaired = lab.run({**request, "snapshot": "contrast1", "query": case[language].replace("SELECT DISTINCT ", "SELECT ", 1)})
            self.assertTrue(repaired["run"]["correct"])
            self.assertEqual(len(repaired["run"]["rows"]), 3)
            self.assertIsNone(repaired["run"]["token_usage"])

    def test_supplied_study_is_copied_without_changing_source_files(self):
        first, _ = self.build("source")
        source = Path(first["bundle"]).parent / "path-study"
        before = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
        with patch.object(bundle_module, "prepare_path_study", side_effect=AssertionError("must use supplied study")):
            second, bundle = self.build("imported", study_root=source)
        after = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
        self.assertEqual(after, before)
        imported = Path(second["bundle"]).parent
        for case in bundle["scenarios"]:
            for snapshot in case["snapshots"].values():
                self.assertTrue(Path(snapshot["database"]).is_relative_to(imported))
        self.assertTrue(self.lab(second, bundle).scenarios)

    def test_existing_output_is_never_overwritten_and_failed_validation_is_not_published(self):
        existing = self.directory / "existing"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_text("keep existing work")
        with self.assertRaises(FileExistsError):
            build_bundle(existing)
        self.assertEqual(marker.read_text(), "keep existing work")
        self.assertFalse((existing / "bundle.json").exists())
        with patch.object(bundle_module, "DemoLab", side_effect=ValueError("fixture validation failed")):
            with self.assertRaisesRegex(ValueError, "fixture validation failed"):
                build_bundle(self.directory / "incomplete")
        self.assertFalse((self.directory / "incomplete/bundle.json").exists())

    def test_cli_returns_usable_bundle_and_fails_existing_directory_as_json(self):
        output = self.directory / "cli"
        stdout = io.StringIO()
        with patch.object(sys, "argv", ["tools.demo_bundle", str(output)]), redirect_stdout(stdout):
            status = bundle_module.main()
        report = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertTrue(Path(report["bundle"]).is_file())
        stdout = io.StringIO()
        with patch.object(sys, "argv", ["tools.demo_bundle", str(output)]), redirect_stdout(stdout):
            status = bundle_module.main()
        self.assertEqual(status, 2)
        self.assertFalse(json.loads(stdout.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()

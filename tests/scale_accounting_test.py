"""Accounting overlays preserve unknown original sends; fixture providers only."""

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scale_validation_test as fixtures
from tools import scale_accounting as accounting
from tools import scale_model_matrix as matrix
from tools import run_scale_study as runner


class ScaleAccountingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ScaleValidationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.study = self.fixture.study

    def test_known_response_usage_is_exact_when_no_response_is_lost(self):
        self.fixture.run_fixture()
        result = accounting.study_accounting(self.study)
        self.assertEqual(result["known_response_token_usage"]["total_tokens"], 30)
        self.assertEqual(result["token_usage"]["total_tokens"], 30)
        self.assertTrue(result["api_token_usage_complete"])
        self.assertEqual((result["received_response_calls"], result["unobserved_api_attempts"]), (2, 0))

    def test_lost_original_plus_recovery_does_not_become_an_exact_total(self):
        config, request, _ = self.fixture.recovery_fixture()
        result = accounting.study_accounting(self.study, ledger=self.fixture.ledger, recovery_configs=[config])
        self.assertIsNone(result["token_usage"])
        self.assertEqual(result["known_response_token_usage"]["total_tokens"], 30)
        self.assertEqual(result["unobserved_api_attempts"], 1)
        affected = "supported/" + request["language"]
        self.assertIsNone(result["by_language"][affected]["token_usage"])
        other = next(r["id"] for r in runner.read(self.study / "design.json")["requests"] if r["id"] != request["id"])
        scoped = accounting.study_accounting(self.study, ledger=self.fixture.ledger,
                                             recovery_configs=[config], request_ids={other})
        self.assertEqual(scoped["unobserved_api_attempts"], 0)
        self.assertEqual(scoped["token_usage"]["total_tokens"], 15)

    def test_local_recovery_hash_chain_is_conservative_and_discloses_its_scope(self):
        config, _, _ = self.fixture.recovery_fixture()
        result = accounting.study_accounting(self.study)
        self.assertIsNone(result["token_usage"])
        self.assertEqual(result["unobserved_api_attempts"], 1)
        self.assertIn("ledger not supplied", result["binding_scope"])
        path = Path(config["manifest"]).with_name("transport-recovery-plan.json")
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "plan hash"):
            accounting.study_accounting(self.study)

    def test_multiple_unknown_originals_are_counted_separately(self):
        configs, _ = self.fixture.two_recoveries_fixture()
        result = accounting.study_accounting(self.study, ledger=self.fixture.ledger, recovery_configs=configs)
        self.assertEqual(result["unobserved_api_attempts"], 2)
        self.assertEqual(len(result["recovery_manifest_bindings"]), 2)
        self.assertEqual(result["known_response_token_usage"]["total_tokens"], 30)
        self.assertIsNone(result["token_usage"])

    def test_ledger_validation_does_not_release_unknown_reservations(self):
        config, _, calls = self.fixture.recovery_fixture()
        calls[0]["upper_micro_usd"] -= 1
        fixtures.write(self.fixture.ledger / "budget.json", {"calls": calls})
        with self.assertRaisesRegex(ValueError, "reservation was changed"):
            accounting.study_accounting(self.study, ledger=self.fixture.ledger, recovery_configs=[config])

    def test_partial_original_transport_failure_is_unknown_before_recovery(self):
        config, request, calls = self.fixture.recovery_fixture()
        folder = self.study / "runs" / request["id"]
        for name in ("response.json", "report.json", "transport-recovery.json", "transport-recovery-plan.json"):
            (folder / name).unlink()
        fixtures.write(self.fixture.ledger / "budget.json", {"calls": calls[:-1]})
        result = accounting.study_accounting(self.study, ledger=self.fixture.ledger)
        self.assertEqual(result["unobserved_api_attempts"], 1)
        self.assertEqual(result["known_response_token_usage"]["total_tokens"], 15)
        self.assertIsNone(result["token_usage"])
        local = accounting.study_accounting(self.study)
        self.assertEqual(local["unobserved_api_attempts"], 1)
        self.assertIsNone(local["token_usage"])

    def test_matrix_overlay_keeps_original_wrapper_unchanged_and_scopes_unknown_usage(self):
        self.fixture.run_fixture()
        directory = self.fixture.root / "matrix"
        matrix.prepare(self.study, directory, ledger=self.fixture.ledger)
        for name, model in (("sol", "gpt-6.1-sol"), ("astra", "gpt-6-astra")):
            def provider(payload):
                result = self.fixture.provider(payload)
                result["model"] = payload["model"]
                return result
            runner.run(directory / name, provider=provider, ledger=self.fixture.ledger, workers=1)
        original_study = self.fixture.study
        original_provider = self.fixture.provider
        self.fixture.study = directory / "sol"
        def sol(payload):
            result = original_provider(payload)
            result["model"] = "gpt-6.1-sol"
            return result
        self.fixture.provider = sol
        self.fixture.recovery_fixture()
        self.fixture.study = original_study
        wrapper_sha = runner.sha(ROOT / "tools/scale_model_matrix.py")
        summary = accounting.matrix_accounting(directory, require_complete=True)
        self.assertEqual(summary["known_response_token_usage"]["total_tokens"], 90)
        self.assertIsNone(summary["token_usage"])
        self.assertEqual(summary["unobserved_api_attempts"], 1)
        self.assertEqual(summary["total_api_attempts_accounted"], 7)
        self.assertEqual(summary["models"]["gpt-6-luna"]["token_usage"]["total_tokens"], 30)
        self.assertIsNone(summary["models"]["gpt-6.1-sol"]["token_usage"])
        self.assertIsNone(summary["models"]["gpt-6.1-sol"]["sqlp_total_token_change_percent"])
        self.assertEqual(runner.sha(ROOT / "tools/scale_model_matrix.py"), wrapper_sha)


if __name__ == "__main__":
    unittest.main()

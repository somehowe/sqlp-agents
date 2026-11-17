"""Offline recovery tests: no API calls or query repair."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import recover_scale_transport as recovery
from tools.model_provider import ProviderError
from tools.run_pilot import digest


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.study, self.ledger = self.root / "study", self.root / "ledger"
        self.study.mkdir()
        self.ledger.mkdir()
        self.folder = self.study / "runs/request-00000"
        self.folder.mkdir(parents=True)
        self.payload = {"model": "gpt-6.1-sol", "max_output_tokens": 128,
                        "store": False, "tools": [], "input": []}
        self.request = {"id": "request-00000", "payload": self.payload,
                        "request_sha256": digest(self.payload), "case_ids": ["a"]}
        self.design = {"settings": {"model": "gpt-6.1-sol"},
                       "limits": {"api_timeout_seconds": 10},
                       "requests": [self.request], "frozen_sha256": {}}
        recovery._new_json(self.study / "design.json", self.design)
        self.label = "scale-study/" + digest({"design": recovery._sha(self.study / "design.json")})[:20]
        self.original = {"index": 0, "label": self.label, "model": "gpt-6.1-sol",
                         "request_sha256": digest(self.payload), "upper_micro_usd": 50000,
                         "status": "transport_or_http_error_usage_unknown"}
        self.state = {"version": 1, "authorized_micro_usd": 100000000,
                      "prior_upper_micro_usd": 1000000, "calls": [self.original]}
        recovery._new_json(self.ledger / "budget.json", self.state)
        recovery._event(self.folder / "journal.jsonl", "request_started", request_sha256=digest(self.payload))
        recovery._new_json(self.study / "latest-run.json", {"run_errors": [
            {"request_id": "request-00000", "error_type": "ProviderError", "message": "Provider transport failed"}]})

    def prepare(self):
        return recovery.prepare_recovery(self.study, "request-00000", ledger=self.ledger,
                                          authorization="Explicit one-time infrastructure recovery authorized for this test")

    def raw(self):
        return {"id": "offline", "model": "gpt-6.1-sol", "status": "completed", "service_tier": "default",
                "output": [{"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": "[]"}]}],
                "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13,
                          "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 10}}}

    def run_offline(self, client):
        return recovery.run_recovery(self.study, "request-00000", ledger=self.ledger,
                                      live=True, client=client)

    def test_plan_is_offline_idempotent_and_keeps_original_unknown_charge(self):
        before = (self.ledger / "budget.json").read_bytes()
        plan = self.prepare()
        self.assertEqual(plan, self.prepare())
        self.assertEqual((self.ledger / "budget.json").read_bytes(), before)
        self.assertEqual(plan["original_upper_micro_usd"], 50000)
        self.assertEqual(plan["recovery_label"], self.label + "/transport-recovery-1")
        self.assertIsNone(plan["whole_study_api_token_usage"])
        self.assertFalse((self.folder / "response.json").exists())

    def test_one_identical_send_is_durable_and_second_run_never_calls_again(self):
        self.prepare()
        received = []
        result = self.run_offline(lambda payload: (received.append(payload), self.raw())[1])
        self.assertEqual(received, [self.payload])
        self.assertEqual(result["paid_call_count_for_frozen_request"], 2)
        self.assertIsNone(result["whole_study_api_token_usage"])
        self.assertEqual(result["known_retained_response_usage"]["total_tokens"], 13)
        state = recovery._read(self.ledger / "budget.json")
        self.assertEqual(state["calls"][0], self.original)
        self.assertEqual(len(state["calls"]), 2)
        saved = recovery._read(self.folder / "response.json")
        self.assertEqual(saved["response"], self.raw())
        self.assertEqual(saved["request_sha256"], digest(self.payload))
        self.assertEqual(saved["transport_recovery"]["manifest_sha256"],
                         recovery._sha(self.folder / recovery.MANIFEST_FILE))
        self.assertEqual(result["plan_sha256"], recovery._sha(self.folder / recovery.PLAN_FILE))
        self.assertEqual(self.run_offline(lambda _: self.fail("Must reuse retained response")), result)

    def test_crash_after_ledger_retention_is_recovered_without_a_paid_resend(self):
        self.prepare()
        result = self.run_offline(lambda _: self.raw())
        (self.folder / "response.json").unlink()
        recovered = self.run_offline(lambda _: self.fail("Ledger raw must be reused"))
        self.assertEqual(recovered, result)
        self.assertEqual(len(recovery._read(self.ledger / "budget.json")["calls"]), 2)

    def test_second_uncertain_transport_is_never_retried_and_both_reserves_stay(self):
        self.prepare()
        count = 0
        def failed(_):
            nonlocal count
            count += 1
            raise ProviderError("fixture transport failed")
        with self.assertRaises(ProviderError):
            self.run_offline(failed)
        state = recovery._read(self.ledger / "budget.json")
        self.assertEqual(len(state["calls"]), 2)
        self.assertEqual(state["calls"][0], self.original)
        self.assertGreater(state["calls"][1]["upper_micro_usd"], 0)
        with self.assertRaisesRegex(ProviderError, "refusing another"):
            self.run_offline(failed)
        self.assertEqual(count, 1)

    def test_missing_authorization_or_explicit_live_flag_rejects_before_client(self):
        with self.assertRaises(ValueError):
            recovery.prepare_recovery(self.study, "request-00000", ledger=self.ledger, authorization="")
        self.prepare()
        with self.assertRaisesRegex(ValueError, "explicit live flag"):
            recovery.run_recovery(self.study, "request-00000", ledger=self.ledger,
                                  client=lambda _: self.fail("Must not call"))

    def test_completed_original_call_and_existing_response_cannot_be_retried(self):
        self.state["calls"][0]["status"] = "completed"
        (self.ledger / "budget.json").write_text(json.dumps(self.state))
        with self.assertRaisesRegex(ValueError, "not an unresolved"):
            self.prepare()
        self.state["calls"][0]["status"] = "transport_or_http_error_usage_unknown"
        (self.ledger / "budget.json").write_text(json.dumps(self.state))
        recovery._new_json(self.folder / "response.json", {})
        with self.assertRaisesRegex(ValueError, "retained response"):
            self.prepare()

    def test_changed_payload_original_reservation_or_journal_aborts(self):
        self.prepare()
        self.state["calls"][0]["upper_micro_usd"] = 1
        (self.ledger / "budget.json").write_text(json.dumps(self.state))
        with self.assertRaisesRegex(ValueError, "plan disagrees"):
            self.run_offline(lambda _: self.fail("Must not call"))
        self.state["calls"][0]["upper_micro_usd"] = 50000
        (self.ledger / "budget.json").write_text(json.dumps(self.state))
        (self.folder / "journal.jsonl").write_text("changed")
        with self.assertRaisesRegex(ValueError, "journal changed"):
            self.run_offline(lambda _: self.fail("Must not call"))

    def test_saved_response_or_recovery_manifest_tampering_is_rejected(self):
        self.prepare()
        self.run_offline(lambda _: self.raw())
        manifest_path = self.folder / recovery.MANIFEST_FILE
        result = recovery._read(manifest_path)
        result["recovery_ledger_index"] = 100
        manifest_path.write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError, "paid ledger binding"):
            self.run_offline(lambda _: self.fail("Must not call"))


if __name__ == "__main__":
    unittest.main()

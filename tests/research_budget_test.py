"""No-network accounting regression checks with temporary dummy responses."""
import json
import hashlib
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.model_provider import ProviderError
from tools.research_budget import (BudgetedProvider, estimate_charge, settled_charge_upper,
                                   reconcile_completed_reservations)
from tools.run_pilot import digest


def payload():
    return {"model":"gpt-6.1-sol", "max_output_tokens":128, "store":False,"tools":[],"input":[]}


def response():
    return {"model":"gpt-6.1-sol","status":"completed","service_tier":"default","usage":{"input_tokens":1000,
        "output_tokens":100,"input_tokens_details":{"cached_tokens":100,"cache_write_tokens":500}}}


class BudgetTests(unittest.TestCase):
    def test_writes_are_a_replacement_rate_and_cache_reads_are_not_double_counted(self):
        self.assertEqual(estimate_charge(response(),"gpt-6.1-sol"),3060)

    def test_shared_ledger_refuses_reset_and_accounts_for_multiple_provider_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            a=BudgetedProvider(lambda p:response(),directory,authorized_usd=2,prior_upper_usd=0)
            b=BudgetedProvider(lambda p:response(),directory,authorized_usd=2,prior_upper_usd=0)
            a(payload()); b(payload())
            state=json.loads((Path(directory)/'budget.json').read_text())
            self.assertEqual(len(state['calls']),2)
            self.assertEqual(sum(c['estimated_micro_usd'] for c in state['calls']),6120)
            self.assertTrue((Path(directory)/'response-00001.json').is_file())
            self.assertEqual(state['calls'][0]['request_sha256'],digest(payload()))
            with self.assertRaises(ValueError):BudgetedProvider(lambda p:response(),directory,authorized_usd=100)

    def test_budget_rejects_before_call_and_transport_errors_keep_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            def client(p):calls.append(p);raise ProviderError('safe failure')
            provider=BudgetedProvider(client,directory,authorized_usd=1,prior_upper_usd=0)
            with self.assertRaises(ProviderError):provider(payload())
            state=json.loads((Path(directory)/'budget.json').read_text())
            self.assertGreater(state['calls'][0]['upper_micro_usd'],0)
            p=payload();p['max_output_tokens']=32768
            with self.assertRaises(ProviderError):provider(p)
            self.assertEqual(len(calls),1)

    def test_missing_usage_does_not_turn_into_zero(self):
        raw=response();raw['usage']=None
        self.assertIsNone(estimate_charge(raw,'gpt-6.1-sol'))
        with tempfile.TemporaryDirectory() as directory:
            provider=BudgetedProvider(lambda p:raw,directory,authorized_usd=2,prior_upper_usd=0)
            provider(payload())
            call=json.loads((Path(directory)/'budget.json').read_text())['calls'][0]
            self.assertIsNone(call['estimated_micro_usd'])
            self.assertGreater(call['upper_micro_usd'],0)

    def test_model_mismatch_keeps_reservation_and_raw_response(self):
        raw=response();raw['model']='different-model'
        with tempfile.TemporaryDirectory() as directory:
            provider=BudgetedProvider(lambda p:raw,directory,authorized_usd=2,prior_upper_usd=0)
            with self.assertRaises(ProviderError) as caught:provider(payload())
            self.assertEqual(caught.exception.raw_response,raw)
            call=json.loads((Path(directory)/'budget.json').read_text())['calls'][0]
            self.assertIsNone(call['estimated_micro_usd'])
            self.assertEqual(call['status'],'returned_model_mismatch')


    def complete_response(self):
        raw = response()
        raw["usage"]["total_tokens"] = 1100
        raw["prompt_cache_retention"] = "24h"
        return raw

    def ledger(self, directory, raw=None):
        raw = self.complete_response() if raw is None else raw
        provider = BudgetedProvider(lambda p: raw, directory, authorized_usd=2, prior_upper_usd=0)
        provider(payload())
        path = Path(directory) / "budget.json"
        state = json.loads(path.read_text())
        # Emulate a historical settlement that charged cache reads at write rate.
        state["calls"][0]["upper_micro_usd"] = 6600
        path.write_text(json.dumps(state))
        return path, state

    def test_settled_upper_only_discounts_known_reads_and_keeps_long_write_margin(self):
        raw = self.complete_response()
        self.assertEqual(settled_charge_upper(raw, "gpt-6.1-sol"), 6061)
        raw["usage"]["input_tokens_details"]["cache_write_tokens"] = 0
        self.assertEqual(settled_charge_upper(raw, "gpt-6.1-sol"), 6061)
        raw["usage"]["input_tokens_details"]["cached_tokens"] = 1000
        self.assertEqual(settled_charge_upper(raw, "gpt-6.1-sol"), 1210)
        raw["service_tier"] = "priority"
        self.assertEqual(settled_charge_upper(raw, "gpt-6.1-sol"), 2420)

    def test_unknown_or_inconsistent_accounting_cannot_release_reservations(self):
        base = self.complete_response()
        variants = [deepcopy(base) for _ in range(7)]
        variants[0]["model"] = "other"
        variants[1]["status"] = "incomplete"
        variants[2]["usage"]["total_tokens"] = 0
        del variants[3]["usage"]["input_tokens_details"]["cached_tokens"]
        variants[4]["usage"]["input_tokens_details"]["cached_tokens"] = True
        variants[5]["service_tier"] = "unknown"
        variants[6]["usage"]["input_tokens_details"]["cache_write_tokens"] = 2000
        for raw in variants:
            with self.subTest(raw=raw):
                self.assertIsNone(settled_charge_upper(raw, "gpt-6.1-sol"))

    def test_live_settlement_uses_exact_default_policy_and_records_its_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = BudgetedProvider(lambda p: self.complete_response(), directory,
                                        authorized_usd=2, prior_upper_usd=0)
            provider(payload())
            state = json.loads((Path(directory) / "budget.json").read_text())
            self.assertEqual(state["calls"][0]["upper_micro_usd"], 3366)
            self.assertEqual(state["calls"][0]["estimated_micro_usd"], 3060)
            self.assertEqual(state["calls"][0]["settlement_policy"], "verified_text_request_default_30m_v2")

    def test_reconciliation_dry_run_preserves_bytes_and_apply_requires_exact_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path, state = self.ledger(directory)
            original = path.read_bytes()
            audit = reconcile_completed_reservations(directory)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(audit["released_micro_usd"], 539)
            self.assertEqual(audit["remaining_micro_usd"], 2_000_000 - 6061)
            with self.assertRaisesRegex(ValueError, "exact reviewed ledger hash"):
                reconcile_completed_reservations(directory, apply=True)
            with self.assertRaisesRegex(ValueError, "exact reviewed ledger hash"):
                reconcile_completed_reservations(directory, apply=True, expected_ledger_sha256="0" * 64)
            self.assertEqual(path.read_bytes(), original)

    def test_apply_keeps_immutable_backup_audit_and_budget_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path, old = self.ledger(directory)
            original = path.read_bytes()
            reviewed = reconcile_completed_reservations(directory)
            applied = reconcile_completed_reservations(directory, apply=True,
                expected_ledger_sha256=reviewed["ledger_before_sha256"])
            new = json.loads(path.read_text())
            self.assertEqual(new["authorized_micro_usd"], old["authorized_micro_usd"])
            self.assertEqual(new["prior_upper_micro_usd"], old["prior_upper_micro_usd"])
            self.assertEqual(new["calls"][0]["upper_micro_usd"], 6061)
            record = new["reservation_reconciliations"][0]
            backup = Path(directory) / record["backup_file"]
            evidence = json.loads((Path(directory) / record["audit_file"]).read_text())
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(evidence["changes"][0]["old_upper_micro_usd"], 6600)
            self.assertEqual(evidence["ledger_after_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertTrue(applied["applied"])
            self.assertEqual(reconcile_completed_reservations(directory)["released_micro_usd"], 0)

    def test_unknown_calls_remain_unchanged_and_bad_recorded_estimate_aborts(self):
        with tempfile.TemporaryDirectory() as directory:
            path, state = self.ledger(directory)
            state["calls"].append({"index": 1, "upper_micro_usd": 100000,
                                    "status": "transport_or_http_error_usage_unknown"})
            path.write_text(json.dumps(state))
            audit = reconcile_completed_reservations(directory)
            self.assertEqual(audit["skipped"], [{"index": 1, "reason": "not_completed_with_retained_response"}])
            self.assertEqual(audit["after_upper_micro_usd"], 106061)
            state["calls"][0]["estimated_micro_usd"] = 0
            path.write_text(json.dumps(state))
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "estimate disagree"):
                reconcile_completed_reservations(directory)
            self.assertEqual(path.read_bytes(), original)

    def test_response_path_escape_and_ledger_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, state = self.ledger(directory)
            reviewed = reconcile_completed_reservations(directory)
            state["calls"][0]["response_file"] = "../outside.json"
            path.write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError, "exact reviewed ledger hash"):
                reconcile_completed_reservations(directory, apply=True,
                    expected_ledger_sha256=reviewed["ledger_before_sha256"])
            with self.assertRaisesRegex(ValueError, "escapes ledger directory"):
                reconcile_completed_reservations(directory)


    def test_matching_text_request_proves_default_30m_write_rate_without_changing_ttl(self):
        raw = self.complete_response()
        self.assertEqual(settled_charge_upper(raw, "gpt-6.1-sol", payload=payload()), 3366)
        # Returned legacy retention does not select GPT-6 cache-write TTL.
        raw["prompt_cache_retention"] = "24h"
        self.assertEqual(settled_charge_upper(raw, "gpt-6.1-sol", payload=payload()), 3366)
        raw["usage"]["input_tokens_details"]["cache_write_tokens"] = 0
        self.assertEqual(settled_charge_upper(raw, "gpt-6.1-sol", payload=payload()), 3091)

    def test_unknown_cache_options_breakpoints_or_message_shapes_keep_long_write_bound(self):
        base = payload()
        cases = [deepcopy(base) for _ in range(7)]
        cases[0]["prompt_cache_options"] = {"ttl": "12h"}
        cases[1]["prompt_cache_options"] = {"ttl": "30m"}
        cases[2]["prompt_cache_breakpoint"] = {"ttl": "12h"}
        cases[3]["input"] = [{"role": "user", "content": [{"type": "input_text", "text": "x"}]}]
        cases[4]["input"] = [{"role": "user", "content": "x", "prompt_cache_breakpoint": {"ttl": "12h"}}]
        cases[5]["model"] = "gpt-6-astra"
        cases[6]["previous_response_id"] = "prior"
        for request in cases:
            with self.subTest(request=request):
                self.assertEqual(settled_charge_upper(self.complete_response(), "gpt-6.1-sol", payload=request), 6061)
        raw = self.complete_response()
        del raw["usage"]["input_tokens_details"]["cache_write_tokens"]
        self.assertEqual(settled_charge_upper(raw, "gpt-6.1-sol", payload=base), 6061)


if __name__=='__main__':unittest.main()

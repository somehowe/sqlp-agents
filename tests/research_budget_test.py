"""No-network accounting regression checks with temporary dummy responses."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.model_provider import ProviderError
from tools.research_budget import BudgetedProvider, estimate_charge
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


if __name__=='__main__':unittest.main()

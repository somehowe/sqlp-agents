"""Paired first-attempt protocol tests; all model responses are offline fixtures."""

from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_comparison as comparison_module
from tools import run_pilot as pilot_module
from tools.model_provider import ProviderError
from tools.run_comparison import SetupError
from tools.run_pilot import ReplayProvider, build_request, digest, run_pilot
from tools.validate_dataset import load_catalog


SQL = "SELECT name FROM AUTHOR WHERE aid = 1;"
SQLP = "SELECT a.name FROM AUTHOR a WHERE a.aid = 1;"
SETTINGS = {"model": "offline-fixture-model", "max_output_tokens": 128}


def workload():
    return {"version": 1, "scope": "development", "cases": [{
        "id": "author", "question": "Which author has identifier 1?",
        "sql": SQL, "sqlp": SQLP, "column_count": 1, "expected_rows": [["Alice"]]}]}


def response(text):
    return {"model": "offline-fixture-model", "id": "resp_offline_fixture", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27,
                      "input_tokens_details": {"cached_tokens": 12},
                      "output_tokens_details": {"reasoning_tokens": 4}}}


class PilotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / "academic.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript((ROOT / "examples/academic/data.sql").read_text())
        self.catalog = load_catalog(ROOT / "examples/academic/catalog.json")
        self.guides = {language: (ROOT / "prompts" / (language + ".md")).read_text()
                       for language in ("sql", "sqlp")}
        self.journal_number = 0

    def plan(self, **options):
        return run_pilot(self.database, self.catalog, workload(), guides=self.guides,
                         settings=SETTINGS, **options)

    def entries(self, plan, transform=None):
        entries = []
        for request in plan["requests"]:
            raw = response(SQL if request["language"] == "sql" else SQLP)
            if transform is not None:
                transform(raw, request["language"])
            entries.append({"request_sha256": request["request_sha256"], "response": raw})
        return entries

    def execute_with(self, provider, *, origin="live", **options):
        # "live" here identifies the callable-provider protocol only. Every
        # caller supplies a local fake; no HTTP client is ever constructed.
        self.journal_number += 1
        self.last_journal = self.directory / ("journal-" + str(self.journal_number) + ".jsonl")
        return run_pilot(self.database, self.catalog, workload(), guides=self.guides,
                         settings=SETTINGS, origin=origin, provider=provider,
                         journal_path=self.last_journal, max_requests=2, **options)

    def events(self):
        return [json.loads(line) for line in self.last_journal.read_text().splitlines()]

    def test_requests_share_only_public_facts_and_never_include_gold_or_extra_metadata(self):
        catalog = deepcopy(self.catalog)
        for value in (catalog, catalog["tables"][0], catalog["tables"][0]["columns"][0],
                      catalog["references"][0]):
            value["private_note"] = "SECRET_GOLD_SENTINEL"
        tasks = []
        for language in ("sql", "sqlp"):
            request = build_request(workload()["cases"][0]["question"], catalog, language,
                                    self.guides[language], SETTINGS)
            serialized = json.dumps(request)
            self.assertNotIn("SECRET_GOLD_SENTINEL", serialized)
            self.assertNotIn("expected_rows", serialized)
            self.assertNotIn(SQL, serialized)
            self.assertNotIn(SQLP, serialized)
            self.assertIs(request["store"], False)
            self.assertEqual(request["instructions"], self.guides[language])
            tasks.append(json.loads(request["input"][0]["content"]))
        self.assertEqual(tasks[0], tasks[1])
        self.assertEqual(tasks[0]["schema_and_relationships"], self.catalog)

    def test_dry_run_never_constructs_client_or_executes_queries(self):
        with patch.object(pilot_module, "OpenAIResponsesClient") as client, \
                patch.object(pilot_module, "_condition") as condition:
            report = self.plan()
        client.assert_not_called()
        condition.assert_not_called()
        self.assertEqual((report["origin"], report["scope"]), ("dry_run", "development"))
        self.assertTrue(report["completed"])
        self.assertEqual(report["cases"], [])
        self.assertEqual(len(report["requests"]), 2)
        self.assertEqual(report["manifest"]["settings"], SETTINGS)
        for request in report["requests"]:
            self.assertEqual(request["request_sha256"], digest(request["payload"]))
        self.assertFalse(list(self.directory.glob("*.jsonl")))

    def test_explicit_reasoning_is_bound_to_both_requests_without_changing_default_settings(self):
        baseline = self.plan()
        self.assertNotIn("reasoning", baseline["manifest"]["settings"])
        settings = {**SETTINGS, "reasoning": {"effort": "max"}}
        explicit = run_pilot(self.database, self.catalog, workload(), guides=self.guides,
                             settings=settings)
        self.assertEqual(explicit["manifest"]["settings"], settings)
        for request in explicit["requests"]:
            self.assertEqual(request["payload"]["reasoning"], {"effort": "max"})
            self.assertEqual(request["request_sha256"], digest(request["payload"]))
        self.assertNotEqual({item["request_sha256"] for item in baseline["requests"]},
                            {item["request_sha256"] for item in explicit["requests"]})
        for invalid in ("unknown", None, 3, ["high"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                pilot_module._settings({**SETTINGS, "reasoning": {"effort": invalid}})

    def cli(self, *options):
        tasks = self.directory / "tasks.json"
        tasks.write_text(json.dumps(workload()))
        arguments = ["tools.run_pilot", str(self.database),
                     str(ROOT / "examples/academic/catalog.json"), str(tasks),
                     "--model", SETTINGS["model"], *options]
        output = io.StringIO()
        with patch.object(sys, "argv", arguments), redirect_stdout(output):
            status = pilot_module.main()
        return status, json.loads(output.getvalue())

    def test_cli_key_file_only_reads_in_live_mode_and_never_enters_requests(self):
        path = self.directory / "private-key"
        with patch.object(pilot_module, "load_api_key", return_value="dummy-file-key") as loader, \
                patch.object(pilot_module, "OpenAIResponsesClient") as client, \
                patch.object(pilot_module, "run_pilot", return_value={"ok": True}) as runner:
            status, _ = self.cli("--live", "--api-key-file", str(path),
                                 "--reasoning-effort", "high", "--max-output-tokens", "8192")
        self.assertEqual(status, 0)
        loader.assert_called_once_with(path)
        client.assert_called_once_with("dummy-file-key", 60.0)
        self.assertEqual(runner.call_args.kwargs["settings"], {
            "model": SETTINGS["model"], "max_output_tokens": 8192,
            "reasoning": {"effort": "high"}})
        self.assertNotIn("dummy-file-key", str(runner.call_args))
        self.assertNotIn(str(path), str(runner.call_args))

    def test_cli_rejects_key_file_for_planning_or_replay_without_reading_it(self):
        for mode in ((), ("--replay", "unread-replay.json")):
            with self.subTest(mode=mode), \
                    patch.object(pilot_module, "load_api_key") as loader, \
                    patch.object(pilot_module, "OpenAIResponsesClient") as client, \
                    patch.object(pilot_module, "run_pilot") as runner:
                status, report = self.cli(*mode, "--api-key-file", "unread-credential")
            self.assertEqual(status, 2)
            self.assertIn("requires --live", report["error"]["message"])
            loader.assert_not_called()
            client.assert_not_called()
            runner.assert_not_called()

    def test_cli_environment_fallback_and_omitted_reasoning_preserve_existing_behavior(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "dummy-environment-key"}), \
                patch.object(pilot_module, "load_api_key") as loader, \
                patch.object(pilot_module, "OpenAIResponsesClient") as client, \
                patch.object(pilot_module, "run_pilot", return_value={"ok": True}) as runner:
            status, _ = self.cli("--live")
        self.assertEqual(status, 0)
        loader.assert_not_called()
        client.assert_called_once_with("dummy-environment-key", 60.0)
        self.assertEqual(runner.call_args.kwargs["settings"], {
            "model": SETTINGS["model"], "max_output_tokens": 1024})

    def test_exact_replay_scores_both_conditions_and_keeps_usage_raw_and_journal(self):
        plan = self.plan()
        entries = self.entries(plan)
        report = self.execute_with(ReplayProvider(entries), origin="replay")
        self.assertEqual(report["origin"], "replay")
        self.assertTrue(report["completed"] and report["ok"])
        for run in report["cases"][0]["runs"]:
            self.assertTrue(run["correct"])
            self.assertEqual(run["rows"], [[{"type": "string", "value": "Alice"}]])
            self.assertEqual(run["token_usage"], {"input_tokens": 20, "output_tokens": 7,
                                                   "total_tokens": 27})
            self.assertEqual(run["generation"]["raw_response"], next(
                entry["response"] for entry in entries
                if entry["request_sha256"] == run["request_sha256"]))
        kinds = [event["event"] for event in self.events()]
        self.assertEqual(kinds.count("request_started"), 2)
        self.assertEqual(kinds.count("response_received"), 2)
        self.assertEqual(kinds.count("run_scored"), 2)
        self.assertEqual(kinds[-1], "completed")

    def test_changed_guide_invalidates_replay_before_database_or_journal_side_effects(self):
        provider = ReplayProvider(self.entries(self.plan()))
        self.guides["sqlp"] += "\nA changed instruction.\n"
        with patch.object(pilot_module.sqlite3, "connect") as connect:
            with self.assertRaises(SetupError) as failure:
                self.execute_with(provider, origin="replay")
        self.assertEqual(failure.exception.stage, "plan")
        connect.assert_not_called()
        self.assertFalse(self.last_journal.exists())

    def test_replay_rejects_duplicate_missing_extra_and_reused_responses(self):
        plan = self.plan()
        entries = self.entries(plan)
        variants = [entries + [deepcopy(entries[0])], entries[:1], entries + [
            {"request_sha256": "0" * 64, "response": response(SQL)}]]
        for variant in variants:
            with self.subTest(length=len(variant)), self.assertRaises(ValueError):
                ReplayProvider(variant).validate_requests(plan["requests"])
        provider = ReplayProvider(entries)
        provider.validate_requests(plan["requests"])
        provider(plan["requests"][0]["payload"])
        with self.assertRaises(ProviderError):
            provider(plan["requests"][0]["payload"])

    def test_request_cap_covers_entire_plan_before_any_provider_or_database_work(self):
        provider = Mock()
        journal = self.directory / "budget.jsonl"
        with patch.object(pilot_module.sqlite3, "connect") as connect:
            with self.assertRaises(SetupError) as failure:
                run_pilot(self.database, self.catalog, workload(), guides=self.guides,
                          settings=SETTINGS, origin="live", provider=provider,
                          journal_path=journal, max_requests=1)
        self.assertEqual(failure.exception.stage, "plan")
        connect.assert_not_called()
        provider.assert_not_called()
        self.assertFalse(journal.exists())

    def test_model_query_is_not_repaired_or_extracted_from_markdown(self):
        def fence(raw, language):
            part = raw["output"][0]["content"][0]
            part["text"] = "```" + language + "\n" + part["text"] + "\n```"
        entries = self.entries(self.plan(), fence)
        report = self.execute_with(ReplayProvider(entries), origin="replay")
        self.assertFalse(report["ok"])
        for run in report["cases"][0]["runs"]:
            self.assertFalse(run["correct"])
            self.assertEqual(run["query"], run["generation"]["raw_text"])
            self.assertTrue(run["query"].startswith("```"))
            self.assertEqual(run["token_usage"]["total_tokens"], 27)

    def test_incomplete_refused_and_empty_generations_are_saved_without_execution(self):
        for status in ("incomplete", "refused", "empty"):
            def transform(raw, language):
                if status == "incomplete":
                    raw["status"] = "incomplete"
                elif status == "refused":
                    raw["output"][0]["content"].append({"type": "refusal", "refusal": "Refused"})
                else:
                    raw["output"][0]["content"][0]["text"] = " \n"
            provider = ReplayProvider(self.entries(self.plan(), transform))
            with self.subTest(status=status), patch.object(pilot_module, "_condition") as condition:
                report = self.execute_with(provider, origin="replay")
            condition.assert_not_called()
            for run in report["cases"][0]["runs"]:
                self.assertEqual(run["generation"]["status"], status)
                self.assertEqual(run["error"]["stage"], "generation")
                self.assertEqual(run["token_usage"]["total_tokens"], 27)
                self.assertFalse(run["correct"])
                self.assertIsNone(run["rows"])

    def test_unknown_usage_stays_null_in_records_and_totals(self):
        def omit_usage(raw, language):
            raw.pop("usage")
        report = self.execute_with(ReplayProvider(self.entries(self.plan(), omit_usage)), origin="replay")
        self.assertTrue(report["ok"])
        for run in report["cases"][0]["runs"]:
            self.assertIsNone(run["token_usage"])
        for summary in report["summary"].values():
            self.assertIsNone(summary["token_usage"])
            self.assertEqual(summary["usage_known_attempts"], 0)

    def test_agreement_on_the_wrong_answer_does_not_count_as_correctness(self):
        def wrong(raw, language):
            raw["output"][0]["content"][0]["text"] = (
                "SELECT name FROM AUTHOR WHERE aid = 2;" if language == "sql" else
                "SELECT a.name FROM AUTHOR a WHERE a.aid = 2;")
        report = self.execute_with(ReplayProvider(self.entries(self.plan(), wrong)), origin="replay")
        runs = report["cases"][0]["runs"]
        self.assertEqual(runs[0]["rows"], runs[1]["rows"])
        self.assertTrue(all(run["status"] == "ok" and not run["correct"] for run in runs))
        self.assertFalse(report["ok"])

    def test_malformed_response_is_journaled_before_protocol_failure(self):
        raw = response(SQL)
        raw["usage"]["input_tokens"] = -1
        with self.assertRaises(SetupError) as failure:
            self.execute_with(lambda payload: deepcopy(raw))
        self.assertEqual(failure.exception.stage, "provider")
        events = self.events()
        self.assertEqual(next(event["response"] for event in events
                              if event["event"] == "response_received"), raw)
        self.assertEqual(events[-1]["event"], "infrastructure_error")
        self.assertEqual(events[-1]["raw_response"], raw)

    def test_compiler_infrastructure_failure_preserves_received_model_response(self):
        entries = self.entries(self.plan())
        with patch.object(comparison_module, "_compile", side_effect=SetupError("compiler", "offline failure")):
            with self.assertRaises(SetupError) as failure:
                self.execute_with(ReplayProvider(entries), origin="replay")
        self.assertEqual(failure.exception.stage, "compiler")
        events = self.events()
        received = [event for event in events if event["event"] == "response_received"]
        self.assertTrue(received)
        self.assertEqual(received[-1]["language"], "sqlp")
        self.assertEqual(received[-1]["response"], next(entry["response"] for entry in entries
                         if entry["request_sha256"] == received[-1]["request_sha256"]))
        self.assertNotIn("completed", [event["event"] for event in events])

    def test_existing_journal_is_never_overwritten_or_used_for_new_calls(self):
        journal = self.directory / "existing.jsonl"
        journal.write_text("earlier results\n")
        provider = Mock()
        with self.assertRaises(SetupError):
            run_pilot(self.database, self.catalog, workload(), guides=self.guides,
                      settings=SETTINGS, origin="live", provider=provider,
                      journal_path=journal, max_requests=2)
        provider.assert_not_called()
        self.assertEqual(journal.read_text(), "earlier results\n")

    def test_snapshot_hash_includes_uncheckpointed_wal_commits(self):
        writer = sqlite3.connect(self.database)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = self.plan()["manifest"]["data_snapshot_sha256"]
        main_bytes = self.database.read_bytes()
        writer.execute("UPDATE AUTHOR SET name='Updated in WAL' WHERE aid=1")
        writer.commit()
        self.assertEqual(self.database.read_bytes(), main_bytes)
        after = self.plan()["manifest"]["data_snapshot_sha256"]
        self.assertNotEqual(before, after)

    def test_concurrent_commit_does_not_change_scored_rows_or_snapshot_identity(self):
        writer = sqlite3.connect(self.database)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        plan = self.plan()
        responses = {entry["request_sha256"]: entry["response"] for entry in self.entries(plan)}
        committed = False

        def provider(payload):
            nonlocal committed
            if not committed:
                writer.execute("UPDATE AUTHOR SET name='Changed during generation' WHERE aid=1")
                writer.commit()
                committed = True
            return responses[digest(payload)]

        report = self.execute_with(provider)
        self.assertTrue(report["ok"])
        self.assertEqual(report["manifest"]["data_snapshot_sha256"],
                         plan["manifest"]["data_snapshot_sha256"])
        self.assertEqual(writer.execute("SELECT name FROM AUTHOR WHERE aid=1").fetchone()[0],
                         "Changed during generation")

    def test_provider_cannot_mutate_retained_prompts_or_settings(self):
        plan = self.plan()
        responses = {entry["request_sha256"]: entry["response"] for entry in self.entries(plan)}

        def provider(payload):
            raw = responses[digest(payload)]
            payload["model"] = "mutated-by-adapter"
            payload["input"][0]["content"] = "mutated-by-adapter"
            return raw

        report = self.execute_with(provider)
        self.assertTrue(report["ok"])
        self.assertEqual(report["manifest"]["settings"], SETTINGS)
        for request in report["requests"]:
            self.assertEqual(request["payload"]["model"], SETTINGS["model"])
            self.assertEqual(request["request_sha256"], digest(request["payload"]))

    def test_compiler_is_frozen_before_generation_changes_the_configured_binary(self):
        configured_compiler = self.directory / "configured-sqlpc"
        shutil.copy2(ROOT / "build/sqlpc", configured_compiler)
        expected_hash = hashlib.sha256(configured_compiler.read_bytes()).hexdigest()
        plan = self.plan(compiler_path=configured_compiler)
        responses = {entry["request_sha256"]: entry["response"] for entry in self.entries(plan)}

        def provider(payload):
            # Overwrite only this test's copy, simulating a concurrent rebuild.
            configured_compiler.write_bytes(b"no longer an executable compiler\n")
            return responses[digest(payload)]

        report = self.execute_with(provider, compiler_path=configured_compiler)
        self.assertTrue(report["ok"])
        self.assertEqual(report["manifest"]["compiler_sha256"], expected_hash)
        self.assertNotEqual(hashlib.sha256(configured_compiler.read_bytes()).hexdigest(), expected_hash)
        self.assertTrue(all(run["correct"] for run in report["cases"][0]["runs"]))

    def test_returned_model_drift_aborts_pair_and_preserves_both_responses(self):
        entries = self.entries(self.plan())
        entries[1]["response"]["model"] = "different-returned-model"
        with self.assertRaises(SetupError) as failure:
            self.execute_with(ReplayProvider(entries), origin="replay")
        self.assertEqual(failure.exception.stage, "provider")
        events = self.events()
        received = [event["response"] for event in events if event["event"] == "response_received"]
        self.assertEqual(received, [entry["response"] for entry in entries])
        self.assertEqual(sum(event["event"] == "run_scored" for event in events), 1)
        self.assertEqual(events[-1]["event"], "infrastructure_error")
        self.assertEqual(events[-1]["raw_response"], entries[1]["response"])
        self.assertNotIn("completed", [event["event"] for event in events])

    def test_failed_and_cancelled_provider_responses_abort_without_query_scoring(self):
        for status in ("failed", "cancelled"):
            raw = response(SQL)
            raw["status"] = status
            with self.subTest(status=status), patch.object(pilot_module, "_condition") as condition:
                with self.assertRaises(SetupError) as failure:
                    self.execute_with(lambda payload: deepcopy(raw))
            self.assertEqual(failure.exception.stage, "provider")
            condition.assert_not_called()
            events = self.events()
            self.assertEqual(next(event["response"] for event in events
                                  if event["event"] == "response_received"), raw)
            self.assertEqual(events[-1]["event"], "infrastructure_error")
            self.assertEqual(events[-1]["raw_response"], raw)
            self.assertNotIn("run_scored", [event["event"] for event in events])
            self.assertNotIn("completed", [event["event"] for event in events])

    def test_transport_failure_after_first_answer_retains_incomplete_journal(self):
        entries = self.entries(self.plan())
        responses = {entry["request_sha256"]: entry["response"] for entry in entries}
        calls = []

        def provider(payload):
            calls.append(digest(payload))
            if len(calls) == 2:
                raise ProviderError("Offline fixture transport interrupted")
            return responses[calls[-1]]

        with self.assertRaises(SetupError) as failure:
            self.execute_with(provider)
        self.assertEqual(failure.exception.stage, "provider")
        self.assertEqual(len(calls), 2)
        events = self.events()
        received = [event["response"] for event in events if event["event"] == "response_received"]
        self.assertEqual(received, [responses[calls[0]]])
        self.assertEqual(sum(event["event"] == "request_started" for event in events), 2)
        self.assertEqual(sum(event["event"] == "run_scored" for event in events), 1)
        self.assertEqual(events[-1]["event"], "infrastructure_error")
        self.assertIsNone(events[-1]["raw_response"])
        self.assertNotIn("completed", [event["event"] for event in events])


if __name__ == "__main__":
    unittest.main()

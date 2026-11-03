"""Exercise the public offline example across the compiler and copied runtime."""

import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import offline_example as example_module
from tools import run_pilot as pilot_module
from tools.experiment_kit import verify_kit
from tools.run_pilot import digest


class OfflineExampleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.output = self.directory / 'offline # 学習'

    def read(self, path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def test_actual_example_keeps_requests_answers_usage_and_artifacts_consistent(self):
        with patch.object(pilot_module, 'OpenAIResponsesClient',
                          side_effect=AssertionError('Offline examples must not create a model client')) as client:
            summary = example_module.create_offline_example(self.output)
        client.assert_not_called()
        self.assertEqual(summary['version'], 1)
        self.assertTrue(summary['ok'])
        self.assertEqual(summary['origin'], 'offline_example')
        self.assertEqual(summary['model_calls'], 0)
        self.assertEqual((summary['prepared_pairs'], summary['replay_pairs']), (6, 6))
        self.assertEqual(self.read(self.output / 'summary.json'), summary)
        paths = {name: Path(value) for name, value in summary['paths'].items()}
        self.assertEqual(set(paths), {'demo', 'kit', 'plan', 'replay', 'journal', 'report', 'analysis'})
        for path in paths.values():
            self.assertTrue(path.is_absolute())
            self.assertTrue(path.exists())
            self.assertTrue(path.is_relative_to(self.output.resolve()))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((paths['kit'] / 'data.sqlite').stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(paths['journal'].stat().st_mode), 0o600)
        self.assertTrue(verify_kit(paths['kit'])['ok'])

        plan, replay = self.read(paths['plan']), self.read(paths['replay'])
        report, analysis = self.read(paths['report']), self.read(paths['analysis'])
        self.assertEqual(plan['origin'], 'dry_run')
        self.assertEqual(plan['cases'], [])
        self.assertEqual(len(plan['requests']), 12)
        self.assertEqual(plan['manifest']['settings']['model'], 'offline-example')
        self.assertEqual(report['requests'], plan['requests'])
        self.assertEqual({item['request_sha256'] for item in replay['entries']},
                         {item['request_sha256'] for item in plan['requests']})
        for request in plan['requests']:
            self.assertEqual(request['request_sha256'], digest(request['payload']))
            task = json.loads(request['payload']['input'][0]['content'])
            self.assertEqual(set(task), {'question', 'schema_and_relationships'})
            self.assertNotIn('expected_rows', json.dumps(request['payload']))
        self.assertEqual(report['origin'], 'replay')
        self.assertTrue(report['completed'] and report['ok'])
        self.assertIn('synthetic', report['demo_note'].lower())
        self.assertIn('no model', report['demo_note'].lower())
        self.assertIsNone(report['manifest']['git_head'])
        self.assertIsNone(report['manifest']['git_dirty'])
        self.assertEqual(analysis['paired_correctness'], {
            'both_correct': 6, 'sql_only_correct': 0, 'sqlp_only_correct': 0, 'neither_correct': 0})
        for condition in analysis['conditions'].values():
            self.assertIsNone(condition['token_usage'])
            self.assertEqual(condition['missing_usage_attempts'], 6)
        for case in report['cases']:
            self.assertEqual({run['language'] for run in case['runs']}, {'sql', 'sqlp'})
            for run in case['runs']:
                self.assertTrue(run['correct'])
                self.assertIsNone(run['token_usage'])
                self.assertIsNone(run['generation']['raw_response']['usage'])
        events = [json.loads(line) for line in paths['journal'].read_text().splitlines()]
        self.assertEqual(sum(event['event'] == 'response_received' for event in events), 12)
        deliberate = self.read(paths['demo'] / 'deliberate-analysis.json')
        self.assertEqual(deliberate['paired_correctness'], {
            'both_correct': 1, 'sql_only_correct': 2, 'sqlp_only_correct': 2, 'neither_correct': 1})

    def test_frozen_runner_failure_never_publishes_a_success_summary(self):
        actual_run = subprocess.run

        def fail_replay(command, *args, **kwargs):
            if isinstance(command, list) and '--replay' in command:
                return subprocess.CompletedProcess(command, 2,
                    stdout=json.dumps({'ok': False, 'error': {'message': 'example replay failure'}}), stderr='')
            return actual_run(command, *args, **kwargs)

        with patch.object(example_module.subprocess, 'run', side_effect=fail_replay):
            with self.assertRaises(ValueError):
                example_module.create_offline_example(self.output)
        self.assertFalse((self.output / 'summary.json').exists())
        self.assertFalse((self.output / 'replay-report.json').exists())

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / 'previous-results.txt'
        marker.write_text('keep these results exactly')
        with self.assertRaises(FileExistsError):
            example_module.create_offline_example(self.output)
        self.assertEqual(marker.read_text(), 'keep these results exactly')
        self.assertEqual(list(self.output.iterdir()), [marker])

    def test_invalid_compiler_fails_before_reserving_output(self):
        with self.assertRaises(ValueError):
            example_module.create_offline_example(self.output, self.directory / 'missing-compiler')
        self.assertFalse(self.output.exists())

    def test_public_cli_returns_a_complete_bundle_or_structured_error(self):
        command = [sys.executable, '-m', 'tools.offline_example', str(self.output)]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary, self.read(self.output / 'summary.json'))
        self.assertTrue(Path(summary['paths']['report']).is_file())
        repeated = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(repeated.returncode, 2)
        error = json.loads(repeated.stdout)
        self.assertFalse(error['ok'])
        self.assertTrue(error['error']['message'])
        self.assertEqual(self.read(self.output / 'summary.json'), summary)


if __name__ == '__main__':
    unittest.main()

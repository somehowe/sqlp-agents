"""Exercise the actual local demo bundle, including balanced intentional faults."""

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.create_demo import create_demo
from tools.analyze_runs import analyze_report


class DemoTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name)/'demo'

    def test_actual_demo_executes_both_workloads_and_labels_manual_failures(self):
        result = create_demo(self.output)
        correct = json.loads(Path(result['prepared_report']).read_text())
        wrong = json.loads(Path(result['deliberate_report']).read_text())
        self.assertTrue(correct['ok'])
        self.assertFalse(wrong['ok'])
        self.assertEqual(correct['origin'], 'manual')
        self.assertEqual(wrong['origin'], 'manual')
        self.assertIn('deliberately', wrong['demo_note'])
        self.assertEqual(analyze_report(wrong)['paired_correctness'], {
            'both_correct': 1, 'sql_only_correct': 2, 'sqlp_only_correct': 2, 'neither_correct': 1})
        for report in (correct, wrong):
            for case in report['cases']:
                for run in case['runs']:
                    self.assertIsNone(run['token_usage'])
        cycle = next(case for case in wrong['cases'] if case['id']=='mentor_cycle')['runs']
        self.assertEqual(cycle[0]['rows'], cycle[1]['rows'])
        self.assertTrue(all(run['status']=='ok' and not run['correct'] for run in cycle))
        faulty = next(case for case in wrong['cases'] if case['id']=='paper_author_pair')['runs'][1]
        self.assertEqual(faulty['error']['stage'], 'compile')
        with sqlite3.connect(self.output/'academic.sqlite') as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM WROTE').fetchone()[0], 5)

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        marker=self.output/'previous-results.json'
        marker.write_text('previous results')
        with self.assertRaises(FileExistsError):
            create_demo(self.output)
        self.assertEqual(marker.read_text(), 'previous results')
        self.assertEqual(list(self.output.iterdir()), [marker])

    def test_missing_compiler_fails_before_creating_output(self):
        with self.assertRaises(ValueError):
            create_demo(self.output, self.output.parent/'absent')
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()

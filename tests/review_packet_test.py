"""Review real frozen answers without claiming that notes authenticate a review."""

from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import review_packet as review_module
from tools import run_pilot as pilot_module
from tools.experiment_kit import prepare_kit
from tools.run_pilot import digest
from tools.validate_dataset import load_catalog


class ReviewPacketTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / 'source # 学習.sqlite'
        with sqlite3.connect(self.database) as connection:
            connection.executescript((ROOT / 'examples/academic/data.sql').read_text())
            connection.execute('CREATE TABLE MEASURE (value INTEGER NOT NULL, label TEXT)')
            connection.executemany('INSERT INTO MEASURE VALUES (?,?)', [
                (2**63-1, None), (2**63-1, None),
                (-(2**63), '```\n<img src=x onerror=alert(1)>\n```'), (0, 'last')])
        self.catalog = load_catalog(ROOT / 'examples/academic/catalog.json')
        self.catalog['tables'].append({'name': 'MEASURE', 'columns': [
            {'name': 'value', 'type': 'integer', 'unique': False},
            {'name': 'label', 'type': 'string', 'unique': False}]})
        role = json.loads((ROOT / 'examples/academic/workload.json').read_text())['cases'][0]
        self.workloads = {
            'development': {'version': 1, 'scope': 'development', 'cases': [
                {'id': '=HYPERLINK("https://example.invalid")',
                 'question': 'List every measurement.\n```\n<img src=x onerror=alert(1)>\n```',
                 'column_count': 2, 'sql': 'SELECT value, label FROM MEASURE;',
                 'sqlp': 'SELECT m.value, m.label FROM MEASURE m;',
                 'expected_rows': [[2**63-1, None], [2**63-1, None],
                                   [-(2**63), '```\n<img src=x onerror=alert(1)>\n```'], [0, 'last']]},
                {'id': 'empty', 'question': 'Which measurements have value 42?',
                 'column_count': 2, 'sql': 'SELECT value, label FROM MEASURE WHERE value=42;',
                 'sqlp': 'SELECT m.value, m.label FROM MEASURE m WHERE m.value=42;',
                 'expected_rows': []},
                role]},
            'held_out': {'version': 1, 'scope': 'held_out', 'cases': [
                {'id': 'reserved-nulls', 'question': 'List labels on the maximum measurements, retaining repeated records.',
                 'column_count': 1, 'sql': 'SELECT label FROM MEASURE WHERE value=9223372036854775807;',
                 'sqlp': 'SELECT m.label FROM MEASURE m WHERE m.value=9223372036854775807;',
                 'expected_rows': [[None], [None]]}]}}
        self.kit = self.directory / 'kit'
        self.prepared = prepare_kit(self.database, self.catalog, self.workloads, self.kit)
        self.output = self.directory / 'review # 学習'

    def read(self, path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def create(self, **options):
        return review_module.create_review_packet(self.kit, self.output, **options)

    def sheet(self):
        with (self.output / 'review.csv').open(newline='', encoding='utf-8') as source:
            reader = csv.DictReader(source)
            return reader.fieldnames, list(reader)

    def write_sheet(self, rows, fields=None):
        fields = fields or ['case_number', 'case_sha256', 'reviewer', 'decision', 'notes']
        with (self.output / 'review.csv').open('w', newline='', encoding='utf-8') as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)

    def test_private_packet_preserves_exact_values_multiplicity_paths_and_pending_status(self):
        before = (self.kit / 'data.sqlite').read_bytes()
        with patch.object(pilot_module, 'OpenAIResponsesClient',
                          side_effect=AssertionError('Review export must not construct a provider')) as client:
            result = self.create(preview_rows=1)
        client.assert_not_called()
        self.assertTrue(result['ok'])
        self.assertEqual(result['kind'], 'workload_review_packet')
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['case_count'], 3)
        self.assertEqual(result['annotated_cases'], 0)
        self.assertEqual(result['scope'], 'development')
        self.assertEqual(result['kit_sha256'], self.prepared['kit_sha256'])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual((self.output / 'data.sqlite').read_bytes(), before)
        self.assertEqual((self.kit / 'data.sqlite').read_bytes(), before)
        self.assertEqual(self.read(self.output / 'catalog.json'), self.read(self.kit / 'public/catalog.json'))
        cases = self.read(self.output / 'cases.json')['cases']
        self.assertEqual(cases[0]['case_sha256'], digest(self.workloads['development']['cases'][0]))
        rows = Counter(json.dumps(row, sort_keys=True) for row in cases[0]['expected_rows'])
        exact = [{'type': 'int64', 'value': str(2**63-1)}, {'type': 'null', 'value': None}]
        self.assertEqual(rows[json.dumps(exact, sort_keys=True)], 2)
        self.assertEqual(len(cases[0]['expected_rows']), 4)
        self.assertEqual(cases[1]['expected_rows'], [])
        self.assertEqual(cases[1]['column_count'], 2)
        self.assertEqual([join['role'] for join in cases[2]['trace']['joins']], ['author', 'university'])
        self.assertIn('JOIN', cases[2]['compiled_sql'])
        markdown = (self.output / 'review.md').read_text()
        self.assertIn(str(2**63-1), markdown)
        self.assertRegex(markdown.lower(), 'preview|truncat|omitt')
        self.assertIn('university', markdown)
        self.assertIn('record', markdown.lower())
        fields, sheet = self.sheet()
        self.assertEqual(fields, ['case_number', 'case_sha256', 'reviewer', 'decision', 'notes'])
        self.assertEqual(len(sheet), 3)
        self.assertNotIn('HYPERLINK', (self.output / 'review.csv').read_text())
        self.assertTrue(all(not row['reviewer'] and not row['decision'] and not row['notes'] for row in sheet))
        self.assertEqual(review_module.check_review_packet(self.output, self.kit)['status'], 'pending')

    def test_human_notes_can_be_reordered_and_recorded_without_changing_fixed_artifacts(self):
        self.create()
        manifest_before = (self.output / 'manifest.json').read_bytes()
        _, rows = self.sheet()
        for row in rows:
            row.update(reviewer='Reviewer 学習', decision='ok', notes='Checked intent, roles, and repeated records.')
        self.write_sheet(list(reversed(rows)))
        result = review_module.check_review_packet(self.output, self.kit)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['annotated_cases'], 3)
        self.assertEqual(result['decisions']['ok'], 3)
        self.assertEqual((self.output / 'manifest.json').read_bytes(), manifest_before)
        rows[0]['reviewer'] = ''
        self.write_sheet(rows)
        self.assertEqual(review_module.check_review_packet(self.output)['status'], 'pending')
        rows[0]['reviewer'] = 'Reviewer 学習'
        rows[0]['decision'] = 'revise'
        rows[0]['notes'] = 'Question wording needs a change.'
        rows[1]['decision'] = 'uncertain'
        self.write_sheet(rows)
        result = review_module.check_review_packet(self.output)
        self.assertEqual(result['status'], 'needs_changes')
        self.assertEqual(result['decisions']['revise'], 1)
        self.assertEqual(result['decisions']['uncertain'], 1)

    def test_sheet_changes_cannot_silently_drop_duplicate_or_rebind_questions(self):
        self.create()
        fields, baseline = self.sheet()
        mutations = []
        mutations.append([*deepcopy(baseline), deepcopy(baseline[0])])
        mutations.append(deepcopy(baseline[:-1]))
        unknown = deepcopy(baseline); unknown[0]['case_number'] = '999'; mutations.append(unknown)
        rebound = deepcopy(baseline); rebound[0]['case_sha256'] = '0' * 64; mutations.append(rebound)
        invalid = deepcopy(baseline); invalid[0]['decision'] = 'approved'; mutations.append(invalid)
        for rows in mutations:
            with self.subTest(rows=rows):
                self.write_sheet(rows, fields)
                with self.assertRaises(ValueError):
                    review_module.check_review_packet(self.output)

    def test_changed_packet_files_and_symlinks_are_rejected(self):
        self.create()
        for name in ('data.sqlite', 'catalog.json', 'cases.json', 'review.md', 'manifest.json'):
            copy = self.directory / ('changed-' + name.replace('.', '-'))
            shutil.copytree(self.output, copy)
            path = copy / name
            with path.open('ab') as output:
                output.write(b'changed')
            with self.subTest(name=name), self.assertRaises(ValueError):
                review_module.check_review_packet(copy)
        for name in ('review.csv', 'manifest.json', 'data.sqlite'):
            copy = self.directory / ('symlink-' + name.replace('.', '-'))
            shutil.copytree(self.output, copy)
            path = copy / name
            path.unlink(); path.symlink_to(self.output / name)
            with self.subTest(symlink=name), self.assertRaises(ValueError):
                review_module.check_review_packet(copy)

    def test_stale_or_changed_source_kit_is_detected_before_export_and_when_requested(self):
        self.create()
        other = self.directory / 'other-kit'
        changed = deepcopy(self.workloads)
        changed['development']['cases'][0]['question'] += ' Include all four recorded rows.'
        prepare_kit(self.database, self.catalog, changed, other)
        with self.assertRaises(ValueError):
            review_module.check_review_packet(self.output, other)
        source = self.kit / 'public/catalog.json'
        source.chmod(0o600)
        with source.open('a') as output:
            output.write('\n')
        rejected = self.directory / 'rejected'
        with self.assertRaises(ValueError):
            review_module.create_review_packet(self.kit, rejected)
        self.assertFalse(rejected.exists())
        with self.assertRaises(ValueError):
            review_module.check_review_packet(self.output, self.kit)
        # A portable packet still checks its own immutable copied artifacts.
        self.assertEqual(review_module.check_review_packet(self.output)['status'], 'pending')

    def test_original_kit_check_rejects_rechecksummed_data_catalog_or_question_changes(self):
        self.create()
        for name in ('data.sqlite', 'catalog.json', 'cases.json'):
            copy = self.directory / ('rechecksummed-' + name.replace('.', '-'))
            shutil.copytree(self.output, copy)
            path = copy / name
            if name == 'data.sqlite':
                with sqlite3.connect(path) as connection:
                    connection.execute("UPDATE MEASURE SET label='changed'")
            else:
                value = self.read(path)
                if name == 'catalog.json':
                    value['tables'][0]['columns'][0]['name'] = 'different_column'
                else:
                    value['cases'][0]['question'] = 'A different question from the frozen source'
                path.write_text(json.dumps(value))
            # A manifest is an editable checksum record, not a signature. When
            # the original kit is available, compare content to that source too.
            manifest = self.read(copy / 'manifest.json')
            manifest['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest.pop('packet_sha256')
            manifest['packet_sha256'] = digest(manifest)
            (copy / 'manifest.json').write_text(json.dumps(manifest))
            with self.subTest(name=name), self.assertRaises(ValueError):
                review_module.check_review_packet(copy, self.kit)

    def test_existing_output_and_failed_copy_never_replace_successful_records(self):
        self.output.mkdir()
        marker = self.output / 'previous-notes.txt'
        marker.write_text('keep exactly')
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(marker.read_text(), 'keep exactly')
        self.assertEqual(list(self.output.iterdir()), [marker])
        self.output = self.directory / 'copy-failure'
        with patch.object(review_module.shutil, 'copyfileobj', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):
                self.create()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_markdown_hostile_text_does_not_escape_its_literal_display(self):
        self.create(preview_rows=5)
        markdown = (self.output / 'review.md').read_text()
        outside, fence = [], None
        for line in markdown.splitlines():
            stripped = line.lstrip()
            ticks = len(stripped) - len(stripped.lstrip('`'))
            if ticks >= 3:
                if fence is None:
                    fence = ticks
                elif ticks >= fence and not stripped[ticks:].strip():
                    fence = None
                continue
            if fence is None:
                outside.append(line)
        self.assertIsNone(fence, 'Generated Markdown fences must close')
        self.assertNotIn('<img', '\n'.join(outside))
        self.assertEqual(len(self.read(self.output / 'cases.json')['cases'][0]['expected_rows']), 4)

    def test_cli_exports_reserved_scope_and_returns_machine_readable_status_or_errors(self):
        command = [sys.executable, '-m', 'tools.review_packet', 'create', str(self.kit), str(self.output),
                   '--scope', 'held_out', '--preview-rows', '1']
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual((summary['scope'], summary['case_count'], summary['status']), ('held_out', 1, 'pending'))
        status = subprocess.run([sys.executable, '-m', 'tools.review_packet', 'status', str(self.output),
                                 '--kit', str(self.kit)], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertEqual(json.loads(status.stdout)['kit_sha256'], summary['kit_sha256'])
        repeated = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(repeated.returncode, 2)
        self.assertFalse(json.loads(repeated.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()

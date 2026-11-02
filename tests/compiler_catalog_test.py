"""Private catalog response files and compiler/runner transport regressions."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import compiler_catalog
from tools.compiler_catalog import HEADER, catalog_argument_file, encode_catalog
from tools.run_comparison import run_comparison
from tools.run_pilot import run_pilot
from tools.validate_dataset import compiler_arguments, load_catalog


class CatalogTransportTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(ROOT / 'examples/academic/catalog.json')

    def test_response_file_contains_exact_utf8_arguments_and_is_private(self):
        catalog = {'version': 1, 'tables': [{'name': 'T\n雪', 'columns': [
            {'name': 'a"b\n', 'type': 'string', 'unique': False}]}], 'references': []}
        expected = HEADER + b''.join(arg.encode('utf-8') + b'\0' for arg in compiler_arguments(catalog))
        with catalog_argument_file(catalog) as arguments:
            self.assertEqual(arguments[0], '--catalog-args-file')
            path = Path(arguments[1])
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            process = subprocess.run([str(ROOT/'build/sqlpc'), *arguments],
                                     input='SELECT t."a""b\n" FROM "T\n雪" t;',
                                     text=True, capture_output=True, check=True)
            self.assertTrue(json.loads(process.stdout)['ok'])
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    def test_cleanup_also_happens_when_query_processing_raises(self):
        with self.assertRaisesRegex(RuntimeError, 'failed condition'):
            with catalog_argument_file(self.catalog) as arguments:
                path = Path(arguments[1])
                raise RuntimeError('failed condition')
        self.assertFalse(path.parent.exists())

    def test_transport_limits_are_checked_before_creating_a_file(self):
        for setting, value in [('MAX_TOKENS', 2), ('MAX_FILE_BYTES', len(HEADER)+1)]:
            with self.subTest(setting=setting), patch.object(compiler_catalog, setting, value):
                with patch.object(compiler_catalog.tempfile, 'TemporaryDirectory') as directory:
                    with self.assertRaises(ValueError):
                        with catalog_argument_file(self.catalog):
                            self.fail('Oversized catalog entered context')
                    directory.assert_not_called()

    def test_exact_file_size_and_token_boundaries_are_allowed(self):
        encoded = encode_catalog(self.catalog)
        with patch.object(compiler_catalog, 'MAX_FILE_BYTES', len(encoded)), \
                patch.object(compiler_catalog, 'MAX_TOKENS', len(compiler_arguments(self.catalog))):
            self.assertEqual(encode_catalog(self.catalog), encoded)

    def test_invalid_metadata_is_rejected_before_transport(self):
        catalog = deepcopy(self.catalog)
        catalog['tables'][0]['name'] += '\0bad'
        with self.assertRaises(ValueError):
            encode_catalog(catalog)

    def test_large_identifier_runs_without_putting_it_in_process_argv(self):
        # Linux limits each argv string to roughly 128 KiB. SQLite and the
        # language can represent a larger quoted identifier independently.
        column = 'column_' + 'x'*140_000
        catalog = {'version': 1, 'tables': [{'name': 'T', 'columns': [
            {'name': column, 'type': 'integer', 'unique': True}]}], 'references': []}
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)/'large.sqlite'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE T ("'+column+'" INTEGER PRIMARY KEY)')
                connection.execute('INSERT INTO T VALUES (7)')
            workload = {'version': 1, 'scope': 'development', 'cases': [{
                'id': 'large', 'question': 'Return the integer key.', 'column_count': 1,
                'sql': 'SELECT "'+column+'" FROM T;',
                'sqlp': 'SELECT t."'+column+'" FROM T t;', 'expected_rows': [[7]]}]}
            report = run_comparison(database, catalog, workload)
            self.assertTrue(report['ok'])
            plan = run_pilot(database, catalog, workload, guides={'sql':'SQL guide', 'sqlp':'SQLP guide'},
                             settings={'model':'offline-fixture','max_output_tokens':128})
            self.assertTrue(plan['ok'])
            self.assertEqual(len(plan['requests']), 2)

    def test_incompatible_executable_fails_before_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)/'academic.sqlite'
            with sqlite3.connect(database) as connection:
                connection.executescript((ROOT/'examples/academic/data.sql').read_text())
            compiler = Path(directory)/'incompatible-compiler'
            compiler.write_text('#!/bin/sh\nprintf \'{"version":1,"ok":false,"error":{"stage":"arguments","message":"old protocol"}}\\n\'\nexit 2\n')
            compiler.chmod(0o700)
            provider = Mock()
            from tools.run_comparison import SetupError
            with self.assertRaises(SetupError) as error:
                run_pilot(database, self.catalog, json.loads((ROOT/'examples/academic/workload.json').read_text()),
                          compiler, guides={'sql':'SQL guide','sqlp':'SQLP guide'},
                          settings={'model':'offline-fixture','max_output_tokens':128},
                          origin='live', provider=provider, max_requests=12,
                          journal_path=Path(directory)/'unused.jsonl')
            self.assertEqual(error.exception.stage,'compiler')
            provider.assert_not_called()
            self.assertFalse((Path(directory)/'unused.jsonl').exists())


if __name__ == '__main__':
    unittest.main()

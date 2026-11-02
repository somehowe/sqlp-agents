"""Check metadata inspection independently of the planned ARM mapping builder."""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.inspect_schema import inspect_file, inspect_schema


class SchemaInspectionTests(unittest.TestCase):
    def schema(self, sql):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript(sql)
        return {table['name']: table for table in inspect_schema(connection)['tables']}

    def test_primary_keys_and_implicit_foreign_target_are_resolved(self):
        tables = self.schema('CREATE TABLE Parent (id INTEGER PRIMARY KEY, name TEXT);'
                             'CREATE TABLE Child (id INTEGER PRIMARY KEY, parent_id INT NOT NULL REFERENCES Parent);')
        self.assertTrue(tables['Parent']['arm_candidate'])
        self.assertFalse(tables['Parent']['columns'][0]['not_null'])
        self.assertTrue(tables['Parent']['columns'][0]['effective_not_null'])
        reference = tables['Child']['foreign_keys'][0]
        self.assertTrue(reference['arm_candidate'])
        self.assertTrue(reference['implicit_target'])
        self.assertEqual(reference['target_columns'], ['id'])

    def test_inline_integer_primary_key_desc_is_not_a_nonnull_rowid_alias(self):
        tables = self.schema('CREATE TABLE T (id INTEGER PRIMARY KEY DESC, value TEXT);')
        self.assertFalse(tables['T']['arm_candidate'])
        self.assertIn('nullable_primary_key', tables['T']['issues'])
        self.assertFalse(tables['T']['columns'][0]['effective_not_null'])

    def test_table_level_desc_primary_key_is_a_rowid_alias(self):
        tables = self.schema('CREATE TABLE T (id INTEGER, PRIMARY KEY(id DESC));')
        self.assertTrue(tables['T']['arm_candidate'])
        self.assertTrue(tables['T']['columns'][0]['effective_not_null'])

    def test_without_rowid_and_explicit_nonnull_text_keys_are_candidates(self):
        tables = self.schema('CREATE TABLE A (id TEXT PRIMARY KEY) WITHOUT ROWID;'
                             'CREATE TABLE B (id TEXT NOT NULL PRIMARY KEY);'
                             'CREATE TABLE C (id TEXT PRIMARY KEY);')
        self.assertTrue(tables['A']['without_rowid'])
        self.assertTrue(tables['A']['arm_candidate'])
        self.assertTrue(tables['B']['arm_candidate'])
        self.assertFalse(tables['C']['arm_candidate'])

    def test_composite_key_order_and_foreign_key_order_are_preserved(self):
        tables = self.schema('CREATE TABLE P (a INT, b TEXT, PRIMARY KEY(b,a)) WITHOUT ROWID;'
                             'CREATE TABLE C (id INTEGER PRIMARY KEY, x TEXT NOT NULL, y INT NOT NULL, '
                             'FOREIGN KEY(x,y) REFERENCES P);')
        self.assertEqual(tables['P']['primary_key'], ['b','a'])
        self.assertIn('composite_primary_key', tables['P']['issues'])
        reference = tables['C']['foreign_keys'][0]
        self.assertEqual(reference['source_columns'], ['x','y'])
        self.assertEqual(reference['target_columns'], ['b','a'])
        self.assertIn('composite_foreign_key', reference['issues'])
        self.assertFalse(reference['arm_candidate'])

    def test_missing_keys_nullable_sources_and_nonprimary_targets_need_review(self):
        tables = self.schema('CREATE TABLE P (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE);'
                             'CREATE TABLE C (id INTEGER PRIMARY KEY, ref TEXT REFERENCES P(code));'
                             'CREATE TABLE NoKey (arbitrary INTEGER NOT NULL UNIQUE);')
        self.assertIn('missing_primary_key', tables['NoKey']['issues'])
        reference = tables['C']['foreign_keys'][0]
        self.assertIn('nullable_source', reference['issues'])
        self.assertIn('target_is_not_primary_key', reference['issues'])
        self.assertFalse(reference['arm_candidate'])

    def test_identifier_matching_follows_sqlite_ascii_case_rules(self):
        tables = self.schema('CREATE TABLE "Parent" ("ID" INTEGER PRIMARY KEY);'
                             'CREATE TABLE C (id INTEGER PRIMARY KEY, "Owner" INT NOT NULL, '
                             'FOREIGN KEY(owner) REFERENCES parent(id));'
                             'CREATE TABLE "Ä" (id INTEGER PRIMARY KEY);'
                             'CREATE TABLE "ä" (id INTEGER PRIMARY KEY);')
        reference = tables['C']['foreign_keys'][0]
        self.assertTrue(reference['arm_candidate'])
        self.assertEqual(reference['source_columns'], ['Owner'])
        self.assertEqual(reference['target_table'], 'Parent')
        self.assertEqual(reference['target_columns'], ['ID'])
        self.assertEqual(reference['declared_target_table'], 'parent')
        self.assertEqual(reference['declared_target_columns'], ['id'])
        self.assertIn('Ä', tables)
        self.assertIn('ä', tables)

    def test_quoted_unicode_names_are_metadata_not_executable_sql(self):
        tables = self.schema('CREATE TABLE "雪\"\"; DROP TABLE safe;--" ("k\"\"ey" INTEGER PRIMARY KEY);'
                             'CREATE TABLE safe (id INTEGER PRIMARY KEY);')
        self.assertEqual(len(tables), 2)
        self.assertTrue(tables['雪"; DROP TABLE safe;--']['arm_candidate'])
        self.assertIn('safe', tables)

    def test_generated_sources_and_missing_targets_are_flagged(self):
        tables = self.schema('CREATE TABLE P (id INTEGER PRIMARY KEY);'
                             'CREATE TABLE C (id INTEGER PRIMARY KEY, ref INT NOT NULL '
                             'GENERATED ALWAYS AS (id) STORED REFERENCES P(id));'
                             'CREATE TABLE D (id INTEGER PRIMARY KEY, ref INT NOT NULL REFERENCES Absent(id));')
        self.assertIn('generated_source', tables['C']['foreign_keys'][0]['issues'])
        self.assertIn('missing_target_table', tables['D']['foreign_keys'][0]['issues'])

    def test_views_and_virtual_shadow_tables_are_excluded(self):
        connection=sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript('CREATE TABLE T(id INTEGER PRIMARY KEY); CREATE VIEW V AS SELECT * FROM T;')
        # FTS5 is optional; ordinary view exclusion is checked on every build.
        try:
            connection.execute('CREATE VIRTUAL TABLE Search USING fts5(content)')
        except sqlite3.OperationalError:
            pass
        result=inspect_schema(connection)
        self.assertEqual([table['name'] for table in result['tables']], ['T'])
        self.assertIn({'name':'V','kind':'view'},result['excluded'])
        self.assertTrue(all(item['kind'] in ('view','virtual','shadow') for item in result['excluded']))

    def test_inspection_preserves_caller_transactions_and_does_not_validate_rows(self):
        connection=sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript('CREATE TABLE P(id INTEGER PRIMARY KEY);'
                                 'CREATE TABLE C(id INTEGER PRIMARY KEY, ref INT NOT NULL REFERENCES P(id));')
        connection.execute('INSERT INTO C VALUES (1,999)')
        self.assertTrue(connection.in_transaction)
        result=inspect_schema(connection)
        self.assertTrue(connection.in_transaction)
        self.assertFalse(result['data_checked'])
        self.assertTrue(result['review_required'])
        self.assertEqual(connection.execute('SELECT * FROM C').fetchall(), [(1,999)])
        connection.rollback()
        self.assertEqual(connection.execute('SELECT * FROM C').fetchall(), [])

    def test_strict_text_primary_key_is_implicitly_nonnull(self):
        tables = self.schema('CREATE TABLE T (id TEXT PRIMARY KEY) STRICT;')
        self.assertTrue(tables['T']['arm_candidate'])
        self.assertTrue(tables['T']['columns'][0]['effective_not_null'])

    def test_mismatched_reference_type_is_not_an_arm_candidate(self):
        tables = self.schema('CREATE TABLE P (id TEXT NOT NULL PRIMARY KEY);'
                             'CREATE TABLE C (id INTEGER PRIMARY KEY, ref INTEGER NOT NULL REFERENCES P(id));')
        reference = tables['C']['foreign_keys'][0]
        self.assertFalse(reference['arm_candidate'])
        self.assertIn('key_type_mismatch', reference['issues'])

    def test_temp_shadows_and_attached_schemas_do_not_change_main_inspection(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript('CREATE TABLE Parent (id INTEGER PRIMARY KEY);'
                                 'CREATE TEMP TABLE Parent (unrelated TEXT);'
                                 "ATTACH ':memory:' AS other;"
                                 'CREATE TABLE other.Other (id INTEGER PRIMARY KEY);')
        result = inspect_schema(connection)
        self.assertEqual([table['name'] for table in result['tables']], ['Parent'])
        self.assertEqual(result['tables'][0]['primary_key'], ['id'])
        self.assertTrue(result['tables'][0]['arm_candidate'])
        self.assertFalse(connection.in_transaction)

    def test_file_and_cli_are_readonly_and_missing_paths_are_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db ?#.sqlite'
            with sqlite3.connect(path) as connection:
                connection.execute('CREATE TABLE T(id INTEGER PRIMARY KEY)')
            before=path.read_bytes()
            report=inspect_file(path)
            self.assertTrue(report['ok'])
            self.assertEqual(path.read_bytes(),before)
            process=subprocess.run([sys.executable,'-m','tools.inspect_schema',str(path)],
                                   cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual(json.loads(process.stdout),report)
            absent=Path(directory)/'missing.sqlite'
            process=subprocess.run([sys.executable,'-m','tools.inspect_schema',str(absent)],
                                   cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(process.returncode,2)
            self.assertEqual(json.loads(process.stdout)['error']['stage'],'schema')
            self.assertFalse(absent.exists())
            self.assertEqual(path.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()

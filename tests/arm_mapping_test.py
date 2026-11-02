"""Exercise restricted mapping semantics, snapshot safety and SQLP execution."""

from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.arm_mapping import (MappingError, MappingTimeoutError, build_mapping,
                               load_mapping, materialize_snapshot)
from tools.run_comparison import run_comparison
from tools.validate_dataset import validate_dataset


def simple_mapping():
    return {'version': 1, 'entities': [
        {'name': 'Parent', 'source_table': 'parent_rows', 'key': 'id', 'columns': [
            {'name': 'id', 'source_column': 'pk'}, {'name': 'label', 'source_column': 'label'}],
         'references': []},
        {'name': 'Child', 'source_table': 'child_rows', 'key': 'id', 'columns': [
            {'name': 'id', 'source_column': 'pk'}, {'name': 'parent_id', 'source_column': 'parent_fk'}],
         'references': [{'feature': 'parent', 'source_column': 'parent_id', 'target_entity': 'Parent'}]}
    ]}


SIMPLE_SQL = '''
CREATE TABLE parent_rows(pk INTEGER PRIMARY KEY, label TEXT);
CREATE TABLE child_rows(pk INTEGER PRIMARY KEY, parent_fk INTEGER NOT NULL REFERENCES parent_rows(pk));
INSERT INTO parent_rows VALUES (1,'one');
INSERT INTO child_rows VALUES (10,1), (11,1);
'''


class ArmMappingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'source ?#.sqlite'
        self.destination = self.root / 'mapped.sqlite'
        self.config = load_mapping(ROOT / 'examples/arm/mapping.json')
        with sqlite3.connect(self.source) as connection:
            connection.executescript((ROOT / 'examples/arm/data.sql').read_text())

    def memory(self, sql=SIMPLE_SQL):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript(sql)
        return connection

    def build_simple(self, connection=None, config=None, **options):
        return build_mapping(self.memory() if connection is None else connection,
                             simple_mapping() if config is None else config, **options)

    def test_build_is_pure_and_exposes_explicit_identification_and_role_mappings(self):
        connection = self.memory()
        before = list(connection.iterdump())
        first = self.build_simple(connection)
        second = self.build_simple(connection, copy.deepcopy(simple_mapping()))
        self.assertEqual(first, second)
        self.assertEqual(list(connection.iterdump()), before)
        self.assertFalse(connection.in_transaction)
        self.assertFalse(first['entity_results_supported'])
        self.assertFalse(first['entity_equality_supported'])
        self.assertTrue(first['data_checked'])
        parent = next(entity for entity in first['entities'] if entity['name'] == 'Parent')
        self.assertEqual(parent['identification'], {'kind': 'single_primary_key', 'column': 'id',
                                                  'source_column': 'pk', 'type': 'integer'})
        reference = first['catalog']['references'][0]
        self.assertEqual(reference, {'source_table': 'Child', 'feature': 'parent', 'source_column': 'parent_id',
                                     'target_table': 'Parent', 'target_column': 'id'})
        self.assertEqual(len(first['hashes']['mapping_sha256']), 64)

    def test_materialization_creates_private_valid_views_and_keeps_source_unchanged(self):
        before = self.source.read_bytes()
        artifact = materialize_snapshot(self.source, self.destination, self.config)
        self.assertEqual(before, self.source.read_bytes())
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        with sqlite3.connect(self.destination) as connection:
            self.assertTrue(validate_dataset(connection, artifact['catalog'])['ok'])
            digest = hashlib.sha256(connection.serialize()).hexdigest()
        self.assertEqual(artifact['snapshot_sha256'], digest)
        self.assertEqual({view['name'] for view in artifact['views']}, {'AUTHOR','PAPER','WROTE','UNIVERSITY'})
        self.assertNotEqual(artifact['source_snapshot_sha256'], artifact['snapshot_sha256'])

    def test_multihop_sqlp_matches_logical_sql_independent_physical_sql_and_row_bag(self):
        artifact = materialize_snapshot(self.source, self.destination, self.config)
        workload = json.loads((ROOT / 'examples/arm/workload.json').read_text())
        report = run_comparison(self.destination, artifact['catalog'], workload)
        self.assertTrue(report['ok'])
        self.assertTrue(all(run['correct'] for case in report['cases'] for run in case['runs']))
        with sqlite3.connect(self.source) as connection:
            rows = connection.execute('''SELECT a.display_name, p.heading FROM authorship_events w
                JOIN researcher_rows a ON w.person_key=a.person_key
                JOIN uni_directory u ON a.institution_key=u.institution_key
                JOIN publication_rows p ON w.publication_key=p.publication_key WHERE u.nation_code='CA' ''').fetchall()
        self.assertEqual(Counter(rows), Counter(map(tuple, workload['cases'][0]['expected_rows'])))

    def test_duplicate_authorship_pairs_are_preserved_as_separately_identified_records(self):
        materialize_snapshot(self.source, self.destination, self.config)
        with sqlite3.connect(self.destination) as connection:
            rows = connection.execute('SELECT wid, author_aid, paper_pid FROM WROTE').fetchall()
        self.assertEqual(len(rows), 5)
        self.assertEqual(len({row[0] for row in rows}), 5)
        self.assertEqual(Counter(row[1:] for row in rows)[(1,10)], 2)

    def test_keyless_and_composite_source_keys_are_rejected(self):
        for declaration in ('pk INTEGER NOT NULL UNIQUE', 'pk INTEGER, label TEXT, PRIMARY KEY(pk,label)'):
            with self.subTest(declaration=declaration):
                connection = self.memory('CREATE TABLE parent_rows(' + declaration + ');')
                config = simple_mapping()
                config['entities'] = config['entities'][:1]
                with self.assertRaisesRegex(MappingError, 'Unsupported source key'):
                    self.build_simple(connection, config)

    def test_nullable_text_and_desc_integer_primary_keys_are_rejected(self):
        for declaration in ('TEXT PRIMARY KEY', 'INTEGER PRIMARY KEY DESC'):
            with self.subTest(declaration=declaration):
                connection = self.memory('CREATE TABLE parent_rows(pk ' + declaration + ', label TEXT);')
                config = simple_mapping()
                config['entities'] = config['entities'][:1]
                with self.assertRaisesRegex(MappingError, 'nullable_primary_key'):
                    self.build_simple(connection, config)

    def test_logical_key_must_be_the_actual_declared_anchor(self):
        config = simple_mapping()
        config['entities'][0]['key'] = 'label'
        with self.assertRaisesRegex(MappingError, 'declared single-column primary key'):
            self.build_simple(config=config)

    def test_actual_storage_types_are_checked_in_addition_to_declared_affinity(self):
        connection = self.memory()
        connection.execute("UPDATE parent_rows SET label = X'80'")
        with self.assertRaisesRegex(MappingError, 'invalid_type'):
            self.build_simple(connection)

    def test_actual_dangling_foreign_keys_are_rejected_even_when_enforcement_was_disabled(self):
        connection = self.memory()
        connection.execute('UPDATE child_rows SET parent_fk=999 WHERE pk=10')
        with self.assertRaisesRegex(MappingError, 'missing_reference_target'):
            self.build_simple(connection)

    def test_nullable_foreign_key_declaration_is_unsupported_even_if_current_rows_are_total(self):
        connection = self.memory(SIMPLE_SQL.replace('parent_fk INTEGER NOT NULL', 'parent_fk INTEGER'))
        with self.assertRaisesRegex(MappingError, 'supported declared unary foreign key'):
            self.build_simple(connection)

    def test_undeclared_relationships_are_not_inferred_from_matching_values(self):
        connection = self.memory(SIMPLE_SQL.replace(' REFERENCES parent_rows(pk)', ''))
        with self.assertRaisesRegex(MappingError, 'supported declared unary foreign key'):
            self.build_simple(connection)

    def test_duplicate_foreign_key_declarations_do_not_silently_choose_an_ambiguous_mapping(self):
        connection = self.memory(SIMPLE_SQL.replace('REFERENCES parent_rows(pk)',
                                                     'REFERENCES parent_rows(pk) REFERENCES parent_rows(pk)'))
        with self.assertRaisesRegex(MappingError, 'supported declared unary foreign key'):
            self.build_simple(connection)

    def test_source_left_collation_with_multiple_matches_is_rejected(self):
        connection = self.memory('''CREATE TABLE parent_rows(pk TEXT NOT NULL PRIMARY KEY, label TEXT);
            CREATE TABLE child_rows(pk INTEGER PRIMARY KEY, parent_fk TEXT COLLATE NOCASE NOT NULL REFERENCES parent_rows(pk));
            INSERT INTO parent_rows VALUES ('A','upper'),('a','lower');
            INSERT INTO child_rows VALUES (1,'A');''')
        with self.assertRaisesRegex(MappingError, 'ambiguous_reference_target'):
            self.build_simple(connection)

    def test_generated_mapped_columns_and_view_sources_are_rejected(self):
        connection = self.memory('''CREATE TABLE parent_rows(pk INTEGER PRIMARY KEY,
            label TEXT GENERATED ALWAYS AS (CAST(pk AS TEXT)) STORED);''')
        config = simple_mapping()
        config['entities'] = config['entities'][:1]
        with self.assertRaisesRegex(MappingError, 'ordinary integer/text columns'):
            self.build_simple(connection, config)
        connection = self.memory('CREATE VIEW parent_rows AS SELECT 1 AS pk, \'one\' AS label;')
        with self.assertRaisesRegex(MappingError, 'ordinary main-schema table'):
            self.build_simple(connection, config)

    def test_mapping_rejects_existing_names_and_temp_shadows(self):
        for ddl in ('CREATE TABLE Parent(id INTEGER);', 'CREATE VIEW parent AS SELECT 1;',
                    'CREATE TEMP TABLE Parent(id INTEGER);'):
            with self.subTest(ddl=ddl):
                connection = self.memory()
                connection.execute(ddl)
                with self.assertRaisesRegex(MappingError, 'Logical view name already exists'):
                    self.build_simple(connection)
        connection = self.memory()
        connection.execute('CREATE TEMP TABLE child_rows(pk INTEGER, parent_fk INTEGER)')
        with self.assertRaisesRegex(MappingError, 'shadowed_table'):
            self.build_simple(connection)

    def test_quoted_unicode_names_are_safe_in_views_catalog_and_compiler(self):
        quoted = lambda value: '"' + value.replace('"', '""') + '"'
        physical, logical, column = 'physical."雪', 'logical."雪', 'pk."键'
        source = self.root / 'unicode.sqlite'
        with sqlite3.connect(source) as connection:
            connection.execute(f'CREATE TABLE {quoted(physical)}({quoted(column)} INTEGER PRIMARY KEY)')
            connection.execute(f'INSERT INTO {quoted(physical)} VALUES (123)')
        config = {'version': 1, 'entities': [{'name': logical, 'source_table': physical, 'key': 'id',
                    'columns': [{'name': 'id', 'source_column': column}], 'references': []}]}
        artifact = materialize_snapshot(source, self.destination, config)
        workload = {'version':1,'scope':'development','cases':[{'id':'quoted','question':'Return the record key.',
            'sql': f'SELECT id FROM {quoted(logical)};',
            'sqlp': f'SELECT a.id FROM {quoted(logical)} a;', 'column_count':1,'expected_rows':[[123]]}]}
        self.assertTrue(run_comparison(self.destination, artifact['catalog'], workload)['ok'])

    def test_declared_foreign_names_and_physical_config_names_follow_ascii_case_rules(self):
        connection = self.memory(SIMPLE_SQL.replace('REFERENCES parent_rows(pk)', 'REFERENCES PARENT_ROWS(PK)'))
        config = simple_mapping()
        config['entities'][0]['source_table'] = 'PARENT_ROWS'
        config['entities'][0]['columns'][0]['source_column'] = 'PK'
        artifact = self.build_simple(connection, config)
        parent = next(entity for entity in artifact['entities'] if entity['name'] == 'Parent')
        self.assertEqual(parent['source_table'], 'parent_rows')
        self.assertEqual(parent['identification']['source_column'], 'pk')

    def test_ambiguous_configuration_names_and_unknown_fields_are_rejected(self):
        changes = [lambda c: c.update(version=True), lambda c: c.update(unknown='ignored?'),
                   lambda c: c['entities'].append(copy.deepcopy(c['entities'][0])),
                   lambda c: c['entities'][0]['columns'].append({'name':'ID','source_column':'label'}),
                   lambda c: c['entities'][0]['columns'].append({'name':'another','source_column':'PK'}),
                   lambda c: c['entities'][1]['references'].append(copy.deepcopy(c['entities'][1]['references'][0])),
                   lambda c: c['entities'][1]['references'][0].update(feature='parent_id'),
                   lambda c: c['entities'][0].update(name='sqlite_bad'),
                   lambda c: c['entities'][0].update(name='bad\0name'),
                   lambda c: c['entities'][0].update(name='bad\ud800name')]
        for change in changes:
            with self.subTest(change=change):
                config = simple_mapping()
                change(config)
                with self.assertRaises(MappingError):
                    self.build_simple(config=config)

    def test_duplicate_json_members_and_oversized_configuration_are_rejected(self):
        path = self.root / 'mapping.json'
        path.write_text('{"version":1,"version":1,"entities":[]}')
        with self.assertRaisesRegex(MappingError, 'Duplicate JSON key'):
            load_mapping(path)
        path.write_bytes(b' ' * (1024 * 1024 + 1))
        with self.assertRaisesRegex(MappingError, '1 MiB'):
            load_mapping(path)

    def test_snapshot_size_limit_and_timeout_leave_no_destination(self):
        with self.assertRaisesRegex(MappingError, 'max_snapshot_bytes'):
            materialize_snapshot(self.source, self.destination, self.config, max_snapshot_bytes=1024)
        self.assertFalse(self.destination.exists())
        with self.assertRaises(MappingTimeoutError):
            materialize_snapshot(self.source, self.destination, self.config, timeout_seconds=1e-12)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob('.sqlp-mapping-*')))

    def test_destination_overwrite_source_and_dangling_symlink_are_refused(self):
        before = self.source.read_bytes()
        for target in (self.source, self.destination):
            if target == self.destination:
                target.write_bytes(b'existing')
            with self.assertRaisesRegex(MappingError, 'already exists or names the source'):
                materialize_snapshot(self.source, target, self.config)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.destination.read_bytes(), b'existing')
        self.destination.unlink()
        self.destination.symlink_to(self.root / 'absent.sqlite')
        with self.assertRaises(MappingError):
            materialize_snapshot(self.source, self.destination, self.config)
        self.assertTrue(self.destination.is_symlink())
        self.assertFalse((self.root / 'absent.sqlite').exists())

    def test_failed_view_creation_does_not_publish_partial_database(self):
        original = build_mapping
        def broken(*args, **kwargs):
            artifact = original(*args, **kwargs)
            artifact['views'].append({'name':'broken','sql':'CREATE VIEW INVALID SYNTAX'})
            return artifact
        with patch('tools.arm_mapping.build_mapping', side_effect=broken):
            with self.assertRaises(sqlite3.Error):
                materialize_snapshot(self.source, self.destination, self.config)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob('.sqlp-mapping-*')))

    def test_wal_snapshot_includes_committed_rows_absent_from_main_file(self):
        connection = sqlite3.connect(self.source)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute("INSERT INTO uni_directory VALUES (4,'WAL University','DE')")
        connection.commit()
        self.assertTrue(Path(str(self.source)+'-wal').exists())
        before_main, before_wal = self.source.read_bytes(), Path(str(self.source)+'-wal').read_bytes()
        artifact = materialize_snapshot(self.source, self.destination, self.config)
        with sqlite3.connect(self.destination) as target:
            self.assertEqual(target.execute('SELECT name FROM UNIVERSITY WHERE uid=4').fetchall(), [('WAL University',)])
            self.assertEqual(artifact['snapshot_sha256'], hashlib.sha256(target.serialize()).hexdigest())
        self.assertEqual(self.source.read_bytes(), before_main)
        self.assertEqual(Path(str(self.source)+'-wal').read_bytes(), before_wal)
        self.assertEqual(artifact['source_snapshot_sha256'], hashlib.sha256(connection.serialize()).hexdigest())
        # A different connection commits after inspection. The mapper's held
        # read transaction must keep backup on the earlier validated snapshot.
        original = build_mapping
        def concurrent_commit(*args, **kwargs):
            result = original(*args, **kwargs)
            connection.execute("INSERT INTO uni_directory VALUES (5,'Later Commit','FR')")
            connection.commit()
            return result
        concurrent_destination = self.root / 'concurrent.sqlite'
        with patch('tools.arm_mapping.build_mapping', side_effect=concurrent_commit):
            earlier = materialize_snapshot(self.source, concurrent_destination, self.config)
        with sqlite3.connect(concurrent_destination) as target:
            self.assertEqual(target.execute('SELECT uid FROM UNIVERSITY WHERE uid >= 4').fetchall(), [(4,)])
        self.assertEqual(earlier['source_snapshot_sha256'], artifact['source_snapshot_sha256'])
        self.assertEqual(connection.execute('SELECT institution_key FROM uni_directory WHERE institution_key=5').fetchall(), [(5,)])

    def test_build_preserves_a_caller_transaction_on_success_and_failure(self):
        connection = self.memory()
        connection.execute('INSERT INTO child_rows VALUES (12,1)')
        self.build_simple(connection)
        self.assertTrue(connection.in_transaction)
        connection.execute('UPDATE child_rows SET parent_fk=999 WHERE pk=12')
        with self.assertRaises(MappingError):
            self.build_simple(connection)
        self.assertTrue(connection.in_transaction)
        connection.rollback()
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM child_rows').fetchone()[0], 2)

    def test_cli_inspection_and_materialization_and_missing_source_errors(self):
        def invoke(*arguments):
            process = subprocess.run([sys.executable,'-m','tools.arm_mapping',*map(str,arguments)],
                                     cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(process.stderr, '')
            return process.returncode, json.loads(process.stdout)
        code, artifact = invoke(self.source, ROOT / 'examples/arm/mapping.json')
        self.assertEqual(code,0)
        self.assertNotIn('destination',artifact)
        code, artifact = invoke(self.source, ROOT / 'examples/arm/mapping.json','--destination',self.destination)
        self.assertEqual(code,0)
        self.assertTrue(self.destination.exists())
        code, error = invoke(self.root/'absent.sqlite', ROOT / 'examples/arm/mapping.json')
        self.assertEqual(code,2)
        self.assertFalse(error['ok'])
        self.assertFalse((self.root/'absent.sqlite').exists())


if __name__ == '__main__':
    unittest.main()

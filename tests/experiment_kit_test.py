"""Freeze and verify real offline inputs without constructing a model client."""

from copy import deepcopy
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
from tools import experiment_kit as kit_module
from tools import run_pilot as pilot_module
from tools.arm_mapping import load_mapping, materialize_snapshot
from tools.experiment_kit import plan_kit, prepare_kit, verify_kit
from tools.run_comparison import SetupError
from tools.run_pilot import digest
from tools.validate_dataset import load_catalog


def workloads():
    return {
        'development': {'version': 1, 'scope': 'development', 'cases': [{
            'id': 'dev-author', 'question': 'Which author has identifier 1?',
            'sql': 'SELECT name FROM AUTHOR WHERE aid = 1;',
            'sqlp': 'SELECT a.name FROM AUTHOR a WHERE a.aid = 1;',
            'column_count': 1, 'expected_rows': [['Alice']]}]},
        'held_out': {'version': 1, 'scope': 'held_out', 'review': 'not yet independently reviewed', 'cases': [{
            'id': 'reserved-paper', 'question': 'Which paper has identifier 10?',
            'sql': 'SELECT title FROM PAPER WHERE pid = 10;',
            'sqlp': 'SELECT p.title FROM PAPER p WHERE p.pid = 10;',
            'column_count': 1, 'expected_rows': [['Databases']]}]}
    }


class ExperimentKitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.database = self.directory / 'source ?#.sqlite'
        with sqlite3.connect(self.database) as connection:
            connection.executescript((ROOT/'examples/academic/data.sql').read_text())
        self.catalog = load_catalog(ROOT/'examples/academic/catalog.json')
        self.guides = {language: (ROOT/'prompts'/(language+'.md')).read_text()
                       for language in ('sql', 'sqlp')}
        self.number = 0

    def prepare(self, **options):
        self.number += 1
        output = options.pop('output_dir', self.directory/('kit-'+str(self.number)))
        prepared = prepare_kit(options.pop('database_path', self.database), options.pop('catalog', self.catalog),
                               options.pop('workloads', workloads()), output,
                               options.pop('compiler_path', ROOT/'build/sqlpc'),
                               guides=options.pop('guides', self.guides), **options)
        return output, prepared

    def read_manifest(self, directory):
        return json.loads((directory/'manifest.json').read_text())

    def rewrite_manifest(self, directory, manifest):
        manifest['kit_sha256'] = digest({key: value for key, value in manifest.items() if key != 'kit_sha256'})
        path = directory/'manifest.json'
        path.chmod(0o600)
        path.write_text(json.dumps(manifest))
        path.chmod(0o400)

    def rewrite_member(self, directory, name, value):
        path = directory/name
        path.chmod(0o600)
        path.write_text(json.dumps(value))
        path.chmod(0o400)
        manifest = self.read_manifest(directory)
        manifest['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.rewrite_manifest(directory, manifest)

    def test_preparation_freezes_complete_private_inputs_without_claiming_model_results(self):
        before = self.database.read_bytes()
        directory, verified = self.prepare()
        manifest = self.read_manifest(directory)
        self.assertEqual(verified, verify_kit(directory))
        self.assertEqual(manifest['status'], 'offline_validated')
        self.assertEqual(manifest['model_run_status'], 'not_started')
        self.assertIsNone(manifest['provider_token_usage'])
        self.assertEqual(set(manifest['splits']), {'development','held_out'})
        self.assertEqual(manifest['splits']['held_out']['requests_per_model'], 2)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((directory/'sqlpc').stat().st_mode), 0o500)
        self.assertEqual(stat.S_IMODE((directory/'data.sqlite').stat().st_mode), 0o400)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(list(directory.glob('.preparing-*')))

    def test_public_model_inputs_exclude_gold_queries_and_arbitrary_private_metadata(self):
        catalog = deepcopy(self.catalog)
        private = 'SECRET_EVALUATION_SENTINEL'
        for item in (catalog, catalog['tables'][0], catalog['tables'][0]['columns'][0], catalog['references'][0]):
            item['private_gold_note'] = private
        tasks = workloads()
        tasks['development']['private_note'] = private
        tasks['development']['cases'][0]['private_note'] = private
        directory, _ = self.prepare(catalog=catalog, workloads=tasks)
        public_text = '\n'.join(path.read_text() for path in (directory/'public').rglob('*') if path.is_file())
        self.assertNotIn(private, public_text)
        self.assertNotIn('expected_rows', public_text)
        self.assertNotIn(tasks['development']['cases'][0]['sql'], public_text)
        self.assertNotIn(tasks['development']['cases'][0]['sqlp'], public_text)
        self.assertEqual(json.loads((directory/'evaluation/development.json').read_text()), tasks['development'])
        self.assertEqual(json.loads((directory/'evaluation/catalog.json').read_text()), catalog)
        self.assertNotEqual(self.read_manifest(directory)['input_catalog_sha256'],
                            self.read_manifest(directory)['catalog_sha256'])
        self.assertEqual(json.loads((directory/'public/tasks/held_out.json').read_text()), {
            'version':1, 'scope':'held_out', 'cases':[{'id':'reserved-paper','question':'Which paper has identifier 10?'}]})

    def test_split_identifiers_questions_and_scope_must_be_disjoint_and_consistent(self):
        for change in ('identifier', 'question', 'scope'):
            tasks = workloads()
            if change == 'identifier':
                tasks['held_out']['cases'][0]['id'] = tasks['development']['cases'][0]['id']
            elif change == 'question':
                tasks['held_out']['cases'][0]['question'] = ' WHICH  AUTHOR\nHAS IDENTIFIER 1? '
            else:
                tasks['held_out']['scope'] = 'development'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(workloads=tasks)
        self.assertFalse(list(self.directory.glob('kit-*')))

    def test_wrong_reference_answer_aborts_without_a_completed_manifest(self):
        tasks = workloads()
        tasks['development']['cases'][0]['expected_rows'] = [['Wrong independent answer']]
        output = self.directory/'rejected'
        with self.assertRaises(SetupError) as failure:
            self.prepare(workloads=tasks, output_dir=output)
        self.assertEqual(failure.exception.stage, 'reference_answers')
        self.assertFalse((output/'manifest.json').exists())
        self.assertFalse(list(output.glob('.preparing-*')))

    def test_existing_output_directory_is_untouched(self):
        output = self.directory/'existing'
        output.mkdir()
        marker = output/'earlier-results.txt'
        marker.write_text('retain this exactly')
        with self.assertRaises(FileExistsError):
            self.prepare(output_dir=output)
        self.assertEqual(marker.read_text(), 'retain this exactly')
        self.assertEqual(list(output.iterdir()), [marker])

    def test_changed_missing_or_unchecksummed_manifest_files_are_rejected(self):
        for mutation in ('changed', 'missing', 'manifest'):
            directory, _ = self.prepare()
            if mutation == 'manifest':
                manifest = self.read_manifest(directory)
                manifest['model_run_status'] = 'changed'
                path = directory/'manifest.json'
                path.chmod(0o600)
                path.write_text(json.dumps(manifest))
            else:
                path = directory/'public/guides/sqlp.md'
                path.chmod(0o600)
                if mutation == 'changed':
                    path.write_text('changed guide')
                else:
                    path.unlink()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_kit(directory)

    def test_manifest_file_itself_must_not_be_a_symlink(self):
        directory, _ = self.prepare()
        path = directory/'manifest.json'
        outside = self.directory/'outside-manifest.json'
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, '[Ss]ymlink'):
            verify_kit(directory)

    def test_listed_file_and_parent_directory_symlinks_are_rejected(self):
        for member in ('public/catalog.json', 'public/guides'):
            directory, _ = self.prepare()
            path = directory/member
            outside = self.directory/('outside-'+str(self.number))
            path.rename(outside)
            path.symlink_to(outside, target_is_directory=outside.is_dir())
            with self.subTest(member=member), self.assertRaisesRegex(ValueError, '[Ss]ymlink'):
                verify_kit(directory)

    def test_resigned_manifest_cannot_escape_the_kit_through_unsafe_paths(self):
        directory, _ = self.prepare()
        original = self.read_manifest(directory)
        for name in ('../outside', '/tmp/outside', 'public/../outside', 'public\\catalog.json', './data.sqlite'):
            manifest = deepcopy(original)
            manifest['files'][name] = '0'*64
            self.rewrite_manifest(directory, manifest)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe kit file path'):
                verify_kit(directory)

    def test_public_task_extra_gold_is_rejected_even_if_file_hash_was_updated(self):
        directory, _ = self.prepare()
        task = json.loads((directory/'public/tasks/development.json').read_text())
        task['cases'][0]['expected_rows'] = [['Alice']]
        self.rewrite_member(directory, 'public/tasks/development.json', task)
        with self.assertRaisesRegex(ValueError, 'Public tasks differ'):
            verify_kit(directory)

    def test_materialized_mapping_is_bound_to_its_actual_catalog_and_source_snapshot(self):
        source = self.directory/'arm-source.sqlite'
        mapped = self.directory/'arm-mapped.sqlite'
        with sqlite3.connect(source) as connection:
            connection.executescript((ROOT/'examples/arm/data.sql').read_text())
        artifact = materialize_snapshot(source, mapped, load_mapping(ROOT/'examples/arm/mapping.json'))
        arm_workload = json.loads((ROOT/'examples/arm/workload.json').read_text())
        directory, _ = self.prepare(database_path=mapped, catalog=artifact['catalog'],
                                    workloads={'development':arm_workload}, mapping_artifact=artifact)
        self.assertEqual(json.loads((directory/'mapping.json').read_text()), artifact)
        self.assertEqual(self.read_manifest(directory)['source_read_snapshot_sha256'], artifact['snapshot_sha256'])
        for field in ('catalog', 'snapshot_sha256'):
            wrong = deepcopy(artifact)
            if field == 'catalog':
                wrong['catalog']['tables'][0]['columns'][0]['name'] = 'wrong_column'
            else:
                wrong['snapshot_sha256'] = '0'*64
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare(database_path=mapped, catalog=artifact['catalog'],
                             workloads={'development':arm_workload}, mapping_artifact=wrong)

    def test_wal_and_concurrent_commit_keep_the_same_validated_source_snapshot(self):
        writer = sqlite3.connect(self.database)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("INSERT INTO AUTHOR VALUES (4,'Only in WAL',1,1)")
        writer.commit()
        before_hash = hashlib.sha256(writer.serialize()).hexdigest()
        original = kit_module.validate_dataset
        def commit_after_preflight(connection, catalog, **options):
            result = original(connection, catalog, **options)
            writer.execute("UPDATE AUTHOR SET name='Changed after inspection' WHERE aid=1")
            writer.commit()
            return result
        with patch.object(kit_module, 'validate_dataset', side_effect=commit_after_preflight):
            directory, _ = self.prepare()
        with sqlite3.connect(directory/'data.sqlite') as frozen:
            self.assertEqual(frozen.execute('SELECT name FROM AUTHOR WHERE aid=1').fetchall(), [('Alice',)])
            self.assertEqual(frozen.execute('SELECT name FROM AUTHOR WHERE aid=4').fetchall(), [('Only in WAL',)])
        self.assertEqual(self.read_manifest(directory)['source_read_snapshot_sha256'], before_hash)
        self.assertEqual(writer.execute('SELECT name FROM AUTHOR WHERE aid=1').fetchone()[0], 'Changed after inspection')

    def test_runtime_compiler_and_data_are_independent_of_later_host_changes_and_provider_construction(self):
        # Locate this unrelated source copy beneath a real checkout. Git would
        # otherwise discover the ancestor repository and mislabel this runtime.
        nested = tempfile.TemporaryDirectory(prefix='kit-runtime-test-', dir=ROOT/'build')
        self.addCleanup(nested.cleanup)
        workspace = Path(nested.name)/'host-workspace'
        (workspace/'tools').mkdir(parents=True)
        shutil.copytree(ROOT/'src', workspace/'src')
        for path in (ROOT/'tools').glob('*.py'):
            shutil.copy2(path, workspace/'tools'/path.name)
        # A client-construction guard is frozen into the test runtime. Planning
        # must still work, proving it did not instantiate the HTTP provider.
        provider = workspace/'tools/model_provider.py'
        provider.write_text(provider.read_text().replace('class OpenAIResponsesClient:',
            "class OpenAIResponsesClient:\n    def __new__(cls, *args, **kwargs):\n        raise AssertionError('NETWORK_CLIENT_FORBIDDEN_IN_THIS_OFFLINE_TEST')\n"))
        compiler = self.directory/'host-sqlpc'
        shutil.copy2(ROOT/'build/sqlpc', compiler)
        with patch.object(kit_module, 'ROOT', workspace), patch.object(pilot_module, 'ROOT', workspace):
            directory, _ = self.prepare(compiler_path=compiler)
        manifest = self.read_manifest(directory)
        self.assertIsNone(manifest['runtime']['git_head'])
        (workspace/'tools/run_pilot.py').write_text("raise SystemExit('host source no longer works')\n")
        compiler.write_bytes(b'host compiler no longer works\n')
        with sqlite3.connect(self.database) as source:
            source.execute("UPDATE AUTHOR SET name='Host data changed' WHERE aid=1")
        plan = plan_kit(directory, 'development', model='offline-test-model')
        self.assertEqual(plan['origin'], 'dry_run')
        self.assertEqual(plan['experiment_kit_sha256'], manifest['kit_sha256'])
        self.assertEqual(len(plan['requests']), 2)
        with sqlite3.connect(directory/'data.sqlite') as frozen:
            self.assertEqual(plan['manifest']['data_snapshot_sha256'],
                             hashlib.sha256(frozen.serialize()).hexdigest())
        self.assertFalse(list(directory.rglob('*.jsonl')))

    def test_reserved_questions_keep_held_out_scope_in_offline_request_plan(self):
        directory, _ = self.prepare()
        plan = plan_kit(directory, 'held_out', model='offline-test-model', max_output_tokens=200, seed=17)
        self.assertEqual(plan['scope'], 'held_out')
        self.assertEqual(plan['origin'], 'dry_run')
        self.assertTrue(plan['completed'])
        self.assertEqual(plan['cases'], [])
        self.assertEqual(len(plan['requests']), 2)
        self.assertEqual(plan['manifest']['settings']['model'], 'offline-test-model')
        for request in plan['requests']:
            self.assertEqual(request['request_sha256'], digest(request['payload']))
            self.assertNotIn('expected_rows', json.dumps(request['payload']))
            self.assertNotIn('Databases', json.dumps(request['payload']))
        self.assertEqual(verify_kit(directory)['model_run_status'], 'not_started')

    def test_frozen_plan_records_explicit_reasoning_on_both_arms(self):
        directory, _ = self.prepare()
        baseline = plan_kit(directory, 'development', model='offline-test-model')
        self.assertNotIn('reasoning', baseline['manifest']['settings'])
        explicit = plan_kit(directory, 'development', model='offline-test-model',
                            reasoning_effort='high', max_output_tokens=8192)
        self.assertEqual(explicit['manifest']['settings'], {
            'model':'offline-test-model', 'max_output_tokens':8192, 'reasoning':{'effort':'high'}})
        self.assertEqual(explicit['experiment_kit_sha256'], baseline['experiment_kit_sha256'])
        for request in explicit['requests']:
            self.assertEqual(request['payload']['reasoning'], {'effort':'high'})
            self.assertEqual(request['request_sha256'], digest(request['payload']))
        self.assertNotEqual({item['request_sha256'] for item in explicit['requests']},
                            {item['request_sha256'] for item in baseline['requests']})
        self.assertFalse(list(directory.rglob('*.jsonl')))
        with self.assertRaisesRegex(ValueError, 'Unsupported reasoning effort'):
            plan_kit(directory, 'development', model='offline-test-model', reasoning_effort='unknown')

    def test_invalid_limits_guides_and_unknown_splits_do_not_reserve_output(self):
        for options in ({'max_rows':0}, {'timeout_seconds':float('nan')}, {'preflight_timeout_seconds':-1},
                        {'max_snapshot_bytes':False}, {'guides':{'sql':'','sqlp':'valid'}},
                        {'workloads':{'unknown':workloads()['development']}}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.prepare(**options)
        self.assertFalse(list(self.directory.glob('kit-*')))

    def test_verification_requires_every_named_split_artifact(self):
        directory, _ = self.prepare()
        manifest = self.read_manifest(directory)
        del manifest['files']['validation/held_out.json']
        self.rewrite_manifest(directory, manifest)
        with self.assertRaisesRegex(ValueError, 'split artifact missing'):
            verify_kit(directory)

    def test_verification_keeps_split_disjointness_after_consistent_artifact_edits(self):
        directory, _ = self.prepare()
        reserved = json.loads((directory/'evaluation/held_out.json').read_text())
        reserved['cases'][0]['id'] = workloads()['development']['cases'][0]['id']
        reserved['cases'][0]['question'] = workloads()['development']['cases'][0]['question']
        self.rewrite_member(directory, 'evaluation/held_out.json', reserved)
        public = {'version':1, 'scope':'held_out', 'cases':[
            {'id':case['id'], 'question':case['question']} for case in reserved['cases']]}
        self.rewrite_member(directory, 'public/tasks/held_out.json', public)
        with self.assertRaisesRegex(ValueError, 'Duplicate case identifier or question'):
            verify_kit(directory)

    def test_cli_prepare_verify_plan_and_missing_split_errors_are_json(self):
        catalog_path, development_path = self.directory/'catalog.json', self.directory/'development.json'
        catalog_path.write_text(json.dumps(self.catalog))
        development_path.write_text(json.dumps(workloads()['development']))
        output = self.directory/'cli-kit'
        def cli(*arguments):
            process = subprocess.run([sys.executable,'-m','tools.experiment_kit',*map(str,arguments)],
                                     cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.stderr,'')
            return process.returncode, json.loads(process.stdout)
        for arguments in (('prepare', self.database, catalog_path, development_path, output),
                          ('verify', output), ('plan', output,'--model','offline-test-model')):
            code, report = cli(*arguments)
            self.assertEqual(code,0,report)
            self.assertTrue(report['ok'])
        code, report = cli('plan',output,'--scope','held_out','--model','offline-test-model')
        self.assertEqual(code,2)
        self.assertEqual(report['error']['stage'],'experiment_kit')


if __name__ == '__main__':
    unittest.main()

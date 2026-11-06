"""Prepare and verify a private, self-contained experiment kit without model calls."""

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from .run_comparison import DEFAULT_COMPILER, SetupError, _check_workload, run_comparison
from .run_pilot import ROOT, REASONING_EFFORTS, _public_catalog, _snapshot_hash, _source_manifest, digest
from .validate_dataset import _unique_object, compiler_arguments, load_catalog, validate_dataset

MAX_FILE_BYTES = 64 * 1024 * 1024


def _sha(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _reject_constant(value):
    raise ValueError('Nonfinite JSON number: ' + value)


def _json(path):
    with Path(path).open('rb') as source:
        encoded = source.read(MAX_FILE_BYTES + 1)
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError('JSON input exceeds 64 MiB')
    return json.loads(encoded, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=True, allow_nan=False, indent=2)
        output.write('\n')
    path.chmod(0o400)


def _validate_inputs(catalog, workloads, guides, max_rows):
    compiler_arguments(catalog)
    if not isinstance(workloads, dict) or not workloads or set(workloads) - {'development', 'held_out'}:
        raise ValueError('Workloads must map development and/or held_out to prepared workloads')
    seen_ids, seen_questions = set(), set()
    for scope, workload in workloads.items():
        _check_workload(workload, max_rows)
        if workload['scope'] != scope:
            raise ValueError('Workload scope differs from its split name')
        for case in workload['cases']:
            normalized = ' '.join(case['question'].split()).casefold()
            if case['id'] in seen_ids or normalized in seen_questions:
                raise ValueError('Duplicate case identifier or question across splits')
            seen_ids.add(case['id']); seen_questions.add(normalized)
    if not isinstance(guides, dict) or set(guides) != {'sql', 'sqlp'}:
        raise ValueError('Supply the two corresponding language guides')
    for guide in guides.values():
        if not isinstance(guide, str) or not guide.strip():
            raise ValueError('Language guides must be nonempty text')
        guide.encode('utf-8')


def prepare_kit(database_path, catalog, workloads, output_dir, compiler_path=DEFAULT_COMPILER, *,
                guides=None, mapping_artifact=None, max_rows=1000, timeout_seconds=5.0,
                preflight_timeout_seconds=30.0, max_snapshot_bytes=64 * 1024 * 1024):
    """Freeze data, source/runtime, guides, compiler, and scored prepared pairs.

    No model is selected or called. The resulting runtime can later plan or run
    fresh requests using the exact inputs. Prepared responses remain labeled
    manual; reserved questions have not been evaluated by a model.
    """
    guides = ({language: (ROOT/'prompts'/(language+'.md')).read_text(encoding='utf-8')
               for language in ('sql', 'sqlp')} if guides is None else guides)
    if type(max_rows) is not int or not 1 <= max_rows < sys.maxsize:
        raise ValueError('max_rows must be a positive integer below sys.maxsize')
    for value in (timeout_seconds, preflight_timeout_seconds):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Timeouts must be finite and positive')
    if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0:
        raise ValueError('max_snapshot_bytes must be positive')
    _validate_inputs(catalog, workloads, guides, max_rows)
    # Copy mutable inputs once: preparation never grants a provider access to them.
    catalog, workloads, guides = json.loads(json.dumps([catalog, workloads, guides], allow_nan=False))
    if mapping_artifact is not None:
        if (not isinstance(mapping_artifact, dict) or
                mapping_artifact.get('origin') != 'restricted_arm_mapping' or
                digest(mapping_artifact.get('catalog')) != digest(catalog)):
            raise ValueError('ARM artifact must match the selected compiler catalog')
    compiler = Path(compiler_path).resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise ValueError('Compiler unavailable; run make first')
    output_dir = Path(output_dir).resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(mode=0o700)  # Reserve exclusively; never overwrite earlier experiments.
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='.preparing-', dir=output_dir) as temporary:
        staging = Path(temporary)
        database = staging/'data.sqlite'
        compiler_hash = _sha(compiler)
        shutil.copyfile(compiler, staging/'sqlpc')
        (staging/'sqlpc').chmod(0o500)
        if _sha(compiler) != compiler_hash or _sha(staging/'sqlpc') != compiler_hash:
            raise ValueError('Compiler changed while freezing the kit')
        with closing(sqlite3.connect(Path(database_path).resolve().as_uri()+'?mode=ro', uri=True,
                                     cached_statements=0)) as source:
            source.execute('BEGIN')
            source_hash = _snapshot_hash(source, max_snapshot_bytes)
            preflight = validate_dataset(source, catalog, timeout_seconds=preflight_timeout_seconds)
            if not preflight['ok']:
                raise SetupError('preflight', 'Source dataset failed preflight', preflight)
            deadline = time.monotonic() + preflight_timeout_seconds
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise TimeoutError('Snapshot copy exceeded its time budget')
            with closing(sqlite3.connect(database)) as destination:
                source.backup(destination, pages=128, progress=progress, sleep=0.0)
        if database.stat().st_size > max_snapshot_bytes:
            raise ValueError('Copied snapshot exceeds its size limit')
        database.chmod(0o400)
        if mapping_artifact is not None and mapping_artifact.get('snapshot_sha256') != source_hash:
            raise ValueError('ARM artifact is not bound to the selected materialized snapshot')
        public_catalog = _public_catalog(catalog)
        _write_json(staging/'public/catalog.json', public_catalog)
        _write_json(staging/'evaluation/catalog.json', catalog)
        _write_json(staging/'preflight.json', preflight)
        splits = {}
        for language, guide in guides.items():
            path = staging/'public/guides'/(language+'.md')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(guide, encoding='utf-8'); path.chmod(0o400)
        if mapping_artifact is not None:
            _write_json(staging/'mapping.json', mapping_artifact)
        for scope, workload in sorted(workloads.items()):
            report = run_comparison(database, catalog, workload, staging/'sqlpc', max_rows=max_rows,
                                    timeout_seconds=timeout_seconds,
                                    preflight_timeout_seconds=preflight_timeout_seconds)
            if not report['ok']:
                raise SetupError('reference_answers', 'Prepared answers failed in '+scope, report)
            _write_json(staging/'evaluation'/(scope+'.json'), workload)
            _write_json(staging/'validation'/(scope+'.json'), report)
            _write_json(staging/'public/tasks'/(scope+'.json'), {
                'version': 1, 'scope': scope,
                'cases': [{'id': case['id'], 'question': case['question']} for case in workload['cases']]})
            splits[scope] = {'cases': len(workload['cases']), 'requests_per_model': 2*len(workload['cases']),
                             'prepared_pairs_correct': True,
                             'review': workload.get('review', workload.get('provenance', 'not recorded'))}
        # Freeze the runtime used later, including all its standard-library modules
        # and source provenance. No private mentor material or paper is copied.
        runtime_files = [*sorted((ROOT/'tools').glob('*.py')),
                         *sorted(path for path in (ROOT/'src').glob('*') if path.is_file())]
        for source in runtime_files:
            destination = staging/'runtime'/source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination); destination.chmod(0o400)
        source_version = _source_manifest(staging/'sqlpc')
        expected_sources = source_version['source_files']
        for name, checksum in expected_sources.items():
            if _sha(staging/'runtime'/name) != checksum:
                raise ValueError('Source changed while freezing the runtime')
        files = {path.relative_to(staging).as_posix(): _sha(path)
                 for path in sorted(staging.rglob('*')) if path.is_file()}
        manifest = {'version': 1, 'kind': 'offline_experiment_kit', 'status': 'offline_validated',
                    'model_run_status': 'not_started', 'provider_token_usage': None,
                    'source_read_snapshot_sha256': source_hash, 'database_sha256': _sha(database),
                    'catalog_sha256': digest(public_catalog), 'input_catalog_sha256': digest(catalog),
                    'splits': splits, 'files': files,
                    'runtime': {'python_version': sys.version, 'sqlite_version': sqlite3.sqlite_version,
                                **source_version},
                    'limits': {'max_rows': max_rows, 'timeout_seconds': timeout_seconds,
                               'preflight_timeout_seconds': preflight_timeout_seconds,
                               'max_snapshot_bytes': max_snapshot_bytes},
                    'setup_seconds': time.monotonic()-started,
                    'notes': ['Prepared queries/gold are separate from public model inputs.',
                              'Reserved split labels are not evidence of human interpretation review.',
                              'Choose and freeze model/settings and spending before real calls.',
                              'Hashes detect changes; this kit is not a signed authenticity claim.']}
        manifest['kit_sha256'] = digest(manifest)
        # Copy into the reserved directory; publish manifest last as completion marker.
        for path in sorted(staging.iterdir()):
            path.rename(output_dir/path.name)
        _write_json(output_dir/'manifest.json', manifest)
    return verify_kit(output_dir)


def verify_kit(directory):
    directory = Path(directory).resolve()
    if (directory/'manifest.json').is_symlink():
        raise ValueError('Kit manifest must not be a symlink')
    manifest = _json(directory/'manifest.json')
    if (not isinstance(manifest, dict) or type(manifest.get('version')) is not int or
            manifest['version'] != 1 or manifest.get('kind') != 'offline_experiment_kit' or
            manifest.get('status') != 'offline_validated'):
        raise ValueError('Not a completed version-1 offline experiment kit')
    recorded = manifest.get('kit_sha256')
    if recorded != digest({key: value for key, value in manifest.items() if key != 'kit_sha256'}):
        raise ValueError('Kit manifest checksum mismatch')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('Kit has no frozen files')
    for name, checksum in files.items():
        relative = PurePosixPath(name)
        if (not isinstance(name, str) or not name or relative.is_absolute() or
                '..' in relative.parts or '\\' in name or relative.as_posix() != name):
            raise ValueError('Unsafe kit file path')
        path = directory.joinpath(*relative.parts)
        current = directory
        for component in relative.parts:
            current = current/component
            if current.is_symlink():
                raise ValueError('Symlinks are not frozen kit files')
        if not path.is_file() or _sha(path) != checksum:
            raise ValueError('Kit file changed or is missing: '+name)
    required = {'data.sqlite', 'sqlpc', 'public/catalog.json', 'evaluation/catalog.json', 'public/guides/sql.md',
                'public/guides/sqlp.md', 'runtime/tools/run_pilot.py'}
    if not required <= set(files):
        raise ValueError('Kit is missing required artifacts')
    catalog = _json(directory/'public/catalog.json')
    compiler_arguments(catalog)
    original_catalog = _json(directory/'evaluation/catalog.json')
    if (_public_catalog(original_catalog) != catalog or
            digest(original_catalog) != manifest['input_catalog_sha256']):
        raise ValueError('Public catalog differs from allowlisted input facts')
    if digest(catalog) != manifest['catalog_sha256'] or _sha(directory/'data.sqlite') != manifest['database_sha256']:
        raise ValueError('Kit data/catalog metadata disagree')
    splits = manifest.get('splits')
    if not isinstance(splits, dict) or not splits or set(splits)-{'development', 'held_out'}:
        raise ValueError('Invalid kit splits')
    verified_workloads = {}
    for scope, split in splits.items():
        for prefix in ('evaluation/', 'validation/', 'public/tasks/'):
            if prefix+scope+'.json' not in files:
                raise ValueError('Kit split artifact missing')
        workload = _json(directory/'evaluation'/(scope+'.json'))
        _check_workload(workload, manifest['limits']['max_rows'])
        if workload['scope'] != scope or len(workload['cases']) != split['cases'] or split['requests_per_model'] != 2*split['cases']:
            raise ValueError('Kit split metadata disagree')
        verified_workloads[scope] = workload
        expected_public = {'version': 1, 'scope': scope, 'cases':
                           [{'id': case['id'], 'question': case['question']} for case in workload['cases']]}
        if _json(directory/'public/tasks'/(scope+'.json')) != expected_public:
            raise ValueError('Public tasks differ from evaluation questions')
    frozen_guides = {language: (directory/'public/guides'/(language+'.md')).read_text(encoding='utf-8')
                     for language in ('sql', 'sqlp')}
    _validate_inputs(catalog, verified_workloads, frozen_guides, manifest['limits']['max_rows'])
    return {'version': 1, 'ok': True, 'kind': manifest['kind'], 'kit_sha256': recorded,
            'directory': str(directory), 'splits': splits, 'model_run_status': 'not_started'}


def plan_kit(directory, scope, *, model, max_output_tokens=1024, seed=0, reasoning_effort=None):
    if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
        raise ValueError('Unsupported reasoning effort')
    verified = verify_kit(directory)
    if scope not in verified['splits']:
        raise ValueError('Split not present in this kit')
    directory = Path(directory).resolve()
    manifest = _json(directory/'manifest.json')
    limits = manifest['limits']
    command = [sys.executable, '-m', 'tools.run_pilot', str(directory/'data.sqlite'),
               str(directory/'public/catalog.json'), str(directory/'evaluation'/(scope+'.json')),
               '--compiler', str(directory/'sqlpc'), '--sql-guide', str(directory/'public/guides/sql.md'),
               '--sqlp-guide', str(directory/'public/guides/sqlp.md'), '--model', model,
               '--max-output-tokens', str(max_output_tokens), '--seed', str(seed),
               '--max-rows', str(limits['max_rows']), '--timeout', str(limits['timeout_seconds']),
               '--preflight-timeout', str(limits['preflight_timeout_seconds'])]
    if reasoning_effort is not None:
        command.extend(('--reasoning-effort', reasoning_effort))
    process = subprocess.run(command, cwd=directory/'runtime', capture_output=True, text=True,
                             encoding='utf-8', timeout=limits['preflight_timeout_seconds']+limits['timeout_seconds']+30)
    if process.returncode != 0:
        raise ValueError('Frozen runtime planning failed: '+process.stdout+process.stderr)
    plan = json.loads(process.stdout, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if plan.get('origin') != 'dry_run' or plan.get('ok') is not True or len(plan.get('requests', [])) != verified['splits'][scope]['requests_per_model']:
        raise ValueError('Unexpected frozen runtime plan')
    plan['experiment_kit_sha256'] = verified['kit_sha256']
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('database', type=Path); prepare.add_argument('catalog', type=Path)
    prepare.add_argument('development', type=Path); prepare.add_argument('output_dir', type=Path)
    prepare.add_argument('--held-out', type=Path); prepare.add_argument('--mapping', type=Path)
    prepare.add_argument('--compiler', type=Path, default=DEFAULT_COMPILER)
    verify = commands.add_parser('verify'); verify.add_argument('directory', type=Path)
    plan = commands.add_parser('plan'); plan.add_argument('directory', type=Path)
    plan.add_argument('--scope', choices=('development', 'held_out'), default='development')
    plan.add_argument('--model', required=True); plan.add_argument('--max-output-tokens', type=int, default=1024)
    plan.add_argument('--reasoning-effort', choices=REASONING_EFFORTS)
    plan.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            workloads = {'development': _json(args.development)}
            if args.held_out:
                workloads['held_out'] = _json(args.held_out)
            report = prepare_kit(args.database, load_catalog(args.catalog), workloads, args.output_dir,
                                 args.compiler, mapping_artifact=_json(args.mapping) if args.mapping else None)
        elif args.command == 'verify':
            report = verify_kit(args.directory)
        else:
            report = plan_kit(args.directory, args.scope, model=args.model,
                              max_output_tokens=args.max_output_tokens, seed=args.seed,
                              reasoning_effort=args.reasoning_effort)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'version': 1, 'ok': False, 'error': {'stage': 'experiment_kit', 'message': str(error)}}, allow_nan=False))
        return 2
    print(json.dumps(report, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

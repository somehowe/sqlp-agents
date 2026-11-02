"""Build a restricted, reviewed physical-to-logical SQLite mapping snapshot.

This is a single-primary-key mapping/view layer, not full ARM BuildARM or
entity-resolution semantics. Source connections are inspected without writes.
"""

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from tools.inspect_schema import inspect_schema
from tools.validate_dataset import validate_dataset


_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_MAX_ENTITIES = 256
_MAX_COLUMNS = 4096
_MAX_REFERENCES = 4096
_MAX_CONFIG_BYTES = 1024 * 1024


class MappingError(ValueError):
    """The reviewed mapping does not fit the supported, validated profile."""


class MappingTimeoutError(TimeoutError):
    """Mapping inspection or snapshot construction exceeded its time budget."""


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _fold(name):
    return name.translate(_ASCII_FOLD)


def _name(value, label):
    if not isinstance(value, str) or not value or '\0' in value:
        raise MappingError(label + ' must be a nonempty name without NUL')
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeEncodeError as error:
        raise MappingError(label + ' must be valid UTF-8') from error
    return value


def _fields(value, required, label):
    if not isinstance(value, dict) or set(value) != set(required):
        raise MappingError(label + ' must contain exactly: ' + ', '.join(required))


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _limits(timeout_seconds, max_snapshot_bytes):
    try:
        valid_timeout = (type(timeout_seconds) in (int, float) and
                         math.isfinite(timeout_seconds) and timeout_seconds > 0)
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        raise MappingError('timeout_seconds must be finite and positive')
    if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0:
        raise MappingError('max_snapshot_bytes must be a positive integer')


def _snapshot_hash(connection, maximum):
    size = (connection.execute('PRAGMA main.page_count').fetchone()[0] *
            connection.execute('PRAGMA main.page_size').fetchone()[0])
    if size > maximum:
        raise MappingError('Database exceeds max_snapshot_bytes')
    if not hasattr(connection, 'serialize'):
        raise MappingError('SQLite serialization support is required for snapshot hashes')
    snapshot = connection.serialize(name='main')
    if len(snapshot) > maximum:
        raise MappingError('Serialized database exceeds max_snapshot_bytes')
    return hashlib.sha256(snapshot).hexdigest()


def _config(config):
    _fields(config, ('version', 'entities'), 'Mapping')
    if type(config['version']) is not int or config['version'] != 1:
        raise MappingError('Expected mapping version 1')
    entities = config['entities']
    if not isinstance(entities, list) or not 1 <= len(entities) <= _MAX_ENTITIES:
        raise MappingError('Mapping needs 1..256 entities')
    seen, total_columns, total_references = set(), 0, 0
    for entity in entities:
        _fields(entity, ('name', 'source_table', 'key', 'columns', 'references'), 'Entity')
        name = _name(entity['name'], 'Entity name')
        if _fold(name).startswith('sqlite_') or _fold(name) in seen:
            raise MappingError('Reserved or duplicate entity name: ' + name)
        seen.add(_fold(name))
        _name(entity['source_table'], 'Source table')
        _name(entity['key'], 'Logical key')
        if not isinstance(entity['columns'], list) or not entity['columns']:
            raise MappingError('Entity columns must be a nonempty list')
        if not isinstance(entity['references'], list):
            raise MappingError('Entity references must be a list')
        total_columns += len(entity['columns'])
        total_references += len(entity['references'])
        column_names, physical_names, roles = set(), set(), set()
        for column in entity['columns']:
            _fields(column, ('name', 'source_column'), 'Column')
            logical = _name(column['name'], 'Logical column')
            physical = _name(column['source_column'], 'Physical column')
            if _fold(logical) in column_names or _fold(physical) in physical_names:
                raise MappingError('Duplicate logical or physical column: ' + logical)
            column_names.add(_fold(logical))
            physical_names.add(_fold(physical))
        # Explicit names in the mapping use their stated spelling. Physical
        # metadata lookup below follows SQLite's ASCII identifier matching.
        logical_names = {column['name'] for column in entity['columns']}
        if entity['key'] not in logical_names:
            raise MappingError('Logical key must name a mapped column: ' + entity['key'])
        for reference in entity['references']:
            _fields(reference, ('feature', 'source_column', 'target_entity'), 'Reference')
            role = _name(reference['feature'], 'Reference feature')
            source = _name(reference['source_column'], 'Reference source column')
            _name(reference['target_entity'], 'Reference target entity')
            if _fold(role) in roles or _fold(role) in column_names:
                raise MappingError('Reference role duplicates a role or scalar column: ' + role)
            roles.add(_fold(role))
            if source not in logical_names:
                raise MappingError('Reference source must name a mapped column: ' + source)
    if total_columns > _MAX_COLUMNS or total_references > _MAX_REFERENCES:
        raise MappingError('Mapping exceeds column or reference limits')
    if len(_json_bytes(config)) > _MAX_CONFIG_BYTES:
        raise MappingError('Mapping exceeds the 1 MiB configuration limit')


def _build(connection, config, maximum):
    _config(config)
    # Reject oversized sources before scanning data. This also establishes the
    # main-schema read snapshot used by all subsequent checks and backup.
    source_snapshot_sha256 = _snapshot_hash(connection, maximum)
    schema = inspect_schema(connection)
    schema_definition = [list(row) for row in connection.execute(
        'SELECT type, name, tbl_name, sql FROM main.sqlite_schema ORDER BY type, name')]
    physical_tables = {_fold(table['name']): table for table in schema['tables']}
    existing_names = {_fold(row[0]) for row in connection.execute('SELECT name FROM main.sqlite_schema')}
    temporary_names = {_fold(row[0]) for row in connection.execute('SELECT name FROM temp.sqlite_schema')}
    entities, physical_seen = {}, set()
    for specification in sorted(config['entities'], key=lambda entity: entity['name']):
        name = specification['name']
        if _fold(name) in existing_names or _fold(name) in temporary_names:
            raise MappingError('Logical view name already exists: ' + name)
        physical = physical_tables.get(_fold(specification['source_table']))
        if physical is None:
            raise MappingError('Source must be an ordinary main-schema table: ' + specification['source_table'])
        if not physical['arm_candidate']:
            raise MappingError('Unsupported source key for ' + physical['name'] + ': ' + ', '.join(physical['issues']))
        if _fold(physical['name']) in physical_seen:
            raise MappingError('A physical table may map to only one entity: ' + physical['name'])
        physical_seen.add(_fold(physical['name']))
        available = {_fold(column['name']): column for column in physical['columns']}
        columns = []
        for specified_column in specification['columns']:
            source = available.get(_fold(specified_column['source_column']))
            if source is None:
                raise MappingError('Missing source column: ' + specified_column['source_column'])
            if source['generated'] or source['type_hint'] is None:
                raise MappingError('Mapped columns must be ordinary integer/text columns: ' + source['name'])
            columns.append({'name': specified_column['name'], 'source_column': source['name'],
                            'type': source['type_hint'], 'key': specified_column['name'] == specification['key']})
        key = next(column for column in columns if column['key'])
        if physical['primary_key'] != [key['source_column']]:
            raise MappingError('Logical key must map to the declared single-column primary key: ' + name)
        entities[name] = {'name': name, 'source_table': physical['name'], 'columns': columns,
                          'identification': {'kind': 'single_primary_key', 'column': key['name'],
                                             'source_column': key['source_column'], 'type': key['type']},
                          'references': [], '_specification': specification, '_physical': physical}
    logical_references, physical_references = [], []
    for entity in entities.values():
        columns = {column['name']: column for column in entity['columns']}
        for specified_reference in sorted(entity['_specification']['references'], key=lambda reference: reference['feature']):
            target = entities.get(specified_reference['target_entity'])
            if target is None:
                raise MappingError('Unknown target entity: ' + specified_reference['target_entity'])
            source_column = columns[specified_reference['source_column']]
            target_key = target['identification']
            candidates = [reference for reference in entity['_physical']['foreign_keys']
                          if reference['source_columns'] == [source_column['source_column']]
                          and reference['target_table'] == target['source_table']
                          and reference['target_columns'] == [target_key['source_column']]]
            if len(candidates) != 1 or not candidates[0]['arm_candidate']:
                raise MappingError('Reference needs one supported declared unary foreign key: ' +
                                   entity['name'] + '.' + specified_reference['feature'])
            logical = {'source_table': entity['name'], 'feature': specified_reference['feature'],
                       'source_column': source_column['name'], 'target_table': target['name'],
                       'target_column': target_key['column']}
            physical = dict(logical, source_table=entity['source_table'],
                            source_column=source_column['source_column'], target_table=target['source_table'],
                            target_column=target_key['source_column'])
            logical_references.append(logical)
            physical_references.append(physical)
            entity['references'].append({'feature': specified_reference['feature'], 'logical': logical,
                                         'physical': physical, 'declared_foreign_key_id': candidates[0]['id']})
    physical_catalog = {'version': 1, 'tables': [
        {'name': entity['source_table'], 'columns': [
            {'name': column['source_column'], 'type': column['type'], 'unique': column['key']}
            for column in entity['columns']]} for entity in entities.values()], 'references': physical_references}
    check = validate_dataset(connection, physical_catalog)
    if not check['ok']:
        raise MappingError('Source data failed mapping preflight: ' + json.dumps(check['issues'], sort_keys=True))
    catalog = {'version': 1, 'tables': [
        {'name': entity['name'], 'columns': [
            {'name': column['name'], 'type': column['type'], 'unique': column['key']}
            for column in entity['columns']]} for entity in entities.values()], 'references': logical_references}
    views = [{'name': entity['name'], 'sql': 'CREATE VIEW main.' + _quoted(entity['name']) + ' AS SELECT ' +
              ', '.join(_quoted(column['source_column']) + ' AS ' + _quoted(column['name'])
                        for column in entity['columns']) + ' FROM main.' + _quoted(entity['source_table']) + ';'}
             for entity in entities.values()]
    for entity in entities.values():
        del entity['_specification'], entity['_physical']
    return {'version': 1, 'ok': True, 'origin': 'restricted_arm_mapping', 'data_checked': True,
            'identity_semantics': 'single_primary_key_within_source_table',
            'entity_results_supported': False, 'entity_equality_supported': False,
            'entities': list(entities.values()), 'views': views, 'catalog': catalog,
            'source_snapshot_sha256': source_snapshot_sha256,
            'hashes': {'mapping_sha256': _hash(config), 'source_schema_sha256': _hash(schema_definition),
                       'catalog_sha256': _hash(catalog), 'views_sha256': _hash(views)}}


def build_mapping(connection, config, *, timeout_seconds=30.0, max_snapshot_bytes=64 * 1024 * 1024):
    """Inspect one source snapshot and return a mapping without creating views.

    Preserves an existing caller transaction. This timed API owns and clears the
    connection's progress-handler slot; supply a connection without a handler.
    Configuration is reviewed input, not a claim that real entities were merged.
    """
    _limits(timeout_seconds, max_snapshot_bytes)
    deadline = time.monotonic() + timeout_seconds
    expired = False

    def progress():
        nonlocal expired
        expired = time.monotonic() >= deadline
        return int(expired)

    owns_transaction = not connection.in_transaction
    connection.set_progress_handler(progress, 1000)
    try:
        if owns_transaction:
            connection.execute('BEGIN')
        result = _build(connection, config, max_snapshot_bytes)
        if time.monotonic() >= deadline:
            raise MappingTimeoutError('Mapping inspection exceeded timeout_seconds')
        return result
    except sqlite3.Error as error:
        if expired:
            raise MappingTimeoutError('Mapping inspection exceeded timeout_seconds') from error
        raise
    finally:
        connection.set_progress_handler(None, 0)
        if owns_transaction and connection.in_transaction:
            connection.rollback()


def materialize_snapshot(source_path, destination_path, config, *, timeout_seconds=30.0,
                         max_snapshot_bytes=64 * 1024 * 1024):
    """Publish a new private-copy database with logical views; never overwrite.

    A read transaction covers source validation and SQLite's backup operation,
    including committed WAL pages. A completed database is linked atomically
    into the destination path only after its generated catalog passes preflight.
    """
    _limits(timeout_seconds, max_snapshot_bytes)
    source_path = Path(source_path).resolve()
    # Resolve the parent, not the destination: an existing symlink must also be
    # refused, including a dangling one. The exclusive link below closes races.
    supplied_destination = Path(destination_path).absolute()
    destination = supplied_destination.parent.resolve() / supplied_destination.name
    if source_path == destination or os.path.lexists(destination):
        raise MappingError('Destination already exists or names the source')
    deadline = time.monotonic() + timeout_seconds

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise MappingTimeoutError('Snapshot construction exceeded timeout_seconds')
        return value

    with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True,
                                timeout=min(timeout_seconds, 5.0))) as source:
        source.execute('BEGIN')
        artifact = build_mapping(source, config, timeout_seconds=remaining(), max_snapshot_bytes=max_snapshot_bytes)
        with tempfile.TemporaryDirectory(prefix='.sqlp-mapping-', dir=destination.parent) as directory:
            temporary = Path(directory) / 'snapshot.sqlite'
            temporary.touch(mode=0o600)
            with closing(sqlite3.connect(temporary, timeout=min(remaining(), 5.0))) as target:
                def backup_progress(status, remaining_pages, total_pages):
                    remaining()
                source.backup(target, pages=64, progress=backup_progress, sleep=0.01)
                # SQLite backup reads the transaction's page snapshot, including
                # WAL. Its destination header counters may differ, so raw hashes
                # are recorded separately rather than falsely required to match.
                artifact['copied_snapshot_sha256'] = _snapshot_hash(target, max_snapshot_bytes)
                target.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                try:
                    target.execute('BEGIN')
                    for view in artifact['views']:
                        remaining()
                        target.execute(view['sql'])
                    check = validate_dataset(target, artifact['catalog'])
                    if not check['ok']:
                        raise MappingError('Generated views failed preflight: ' + json.dumps(check['issues'], sort_keys=True))
                    target.commit()
                    artifact['snapshot_sha256'] = _snapshot_hash(target, max_snapshot_bytes)
                    remaining()
                except sqlite3.Error as error:
                    if time.monotonic() >= deadline:
                        raise MappingTimeoutError('Snapshot construction exceeded timeout_seconds') from error
                    raise
                finally:
                    target.set_progress_handler(None, 0)
            with temporary.open('rb') as stream:
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError as error:
                raise MappingError('Destination already exists') from error
    artifact['destination'] = str(destination)
    return artifact


def load_mapping(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MappingError('Duplicate JSON key: ' + key)
            result[key] = value
        return result

    with Path(path).open('rb') as stream:
        raw = stream.read(_MAX_CONFIG_BYTES + 1)
    if len(raw) > _MAX_CONFIG_BYTES:
        raise MappingError('Mapping exceeds the 1 MiB configuration limit')
    config = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    _config(config)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('mapping', type=Path)
    parser.add_argument('--destination', type=Path, help='create a new snapshot with generated views')
    parser.add_argument('--timeout', type=float, default=30.0)
    parser.add_argument('--max-snapshot-bytes', type=int, default=64 * 1024 * 1024)
    args = parser.parse_args()
    try:
        config = load_mapping(args.mapping)
        options = {'timeout_seconds': args.timeout, 'max_snapshot_bytes': args.max_snapshot_bytes}
        if args.destination is not None:
            artifact = materialize_snapshot(args.source, args.destination, config, **options)
        else:
            with closing(sqlite3.connect(args.source.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                artifact = build_mapping(connection, config, **options)
    except (OSError, ValueError, sqlite3.Error, MappingTimeoutError) as error:
        print(json.dumps({'version': 1, 'ok': False, 'origin': 'restricted_arm_mapping',
                          'error': {'stage': 'mapping', 'message': str(error)}}))
        return 2
    print(json.dumps(artifact, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

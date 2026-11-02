"""Inspect declared SQLite keys and references without changing the database."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _fold(name):
    return name.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def _type_hint(declaration):
    declared = declaration.upper()
    if 'INT' in declared:
        return 'integer'
    if any(part in declared for part in ('CHAR', 'CLOB', 'TEXT')):
        return 'string'
    return None


def inspect_schema(connection):
    """Return metadata candidates, not approved mappings or data validation.

    Uses only main-schema metadata in one read snapshot. An existing caller
    transaction is preserved. SQLite 3.37+ is needed for table_list so views,
    virtual tables, and their shadow tables are distinguished reliably.
    """
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute('BEGIN')
    try:
        cursor = connection.execute('PRAGMA main.table_list')
        if cursor.description is None:
            raise ValueError('Schema inspection requires SQLite 3.37 or newer')
        listed = {row[1]: row for row in cursor.fetchall()
                  if row[0] == 'main' and not row[1].lower().startswith('sqlite_')}
        tables, excluded = {}, []
        for name, entry in sorted(listed.items()):
            if entry[2] != 'table':
                excluded.append({'name': name, 'kind': entry[2]})
                continue
            columns = [{'name': row[1], 'declared_type': row[2],
                        'type_hint': _type_hint(row[2]), 'not_null': bool(row[3]),
                        'primary_key_position': row[5], 'generated': row[6] != 0}
                       for row in connection.execute('PRAGMA main.table_xinfo(' + _quoted(name) + ')')]
            primary = sorted((column for column in columns if column['primary_key_position']),
                             key=lambda column: column['primary_key_position'])
            has_primary_index = any(row[3] == 'pk' for row in connection.execute(
                'PRAGMA main.index_list(' + _quoted(name) + ')'))
            rowid_alias = (not entry[4] and len(primary) == 1 and not has_primary_index and
                           primary[0]['declared_type'].strip().upper() == 'INTEGER')
            for column in columns:
                column['effective_not_null'] = column['not_null'] or (
                    rowid_alias and column['primary_key_position'] == 1)
            issues = []
            if not primary:
                issues.append('missing_primary_key')
            elif len(primary) != 1:
                issues.append('composite_primary_key')
            else:
                if not primary[0]['effective_not_null']:
                    issues.append('nullable_primary_key')
                if primary[0]['type_hint'] is None:
                    issues.append('unsupported_key_type')
                if primary[0]['generated']:
                    issues.append('generated_key')
            tables[name] = {'name': name, 'without_rowid': bool(entry[4]),
                            'columns': columns, 'primary_key': [column['name'] for column in primary],
                            'arm_candidate': not issues, 'issues': issues, 'foreign_keys': []}
        by_name = {_fold(name): table for name, table in tables.items()}
        listed_names = {_fold(name) for name in listed}
        for name, table in tables.items():
            columns = {_fold(column['name']): column for column in table['columns']}
            grouped = {}
            for row in connection.execute('PRAGMA main.foreign_key_list(' + _quoted(name) + ')'):
                grouped.setdefault(row[0], []).append(row)
            for identifier, rows in sorted(grouped.items()):
                rows.sort(key=lambda row: row[1])
                declared_target = rows[0][2]
                target = by_name.get(_fold(declared_target))
                target_name = target['name'] if target else declared_target
                source_columns = [columns.get(_fold(row[3]), {}).get('name', row[3]) for row in rows]
                declared_targets = [row[4] for row in rows]
                implicit = all(column is None for column in declared_targets)
                target_fields = {} if target is None else {_fold(column['name']): column for column in target['columns']}
                target_columns = (list(target['primary_key']) if target and implicit else
                                  [target_fields.get(_fold(column), {}).get('name', column)
                                   if column is not None else None for column in declared_targets])
                issues = []
                if len(rows) != 1:
                    issues.append('composite_foreign_key')
                if not table['arm_candidate']:
                    issues.append('unsupported_source_entity')
                if target is None:
                    issues.append('unsupported_target' if _fold(target_name) in listed_names else 'missing_target_table')
                elif not target['arm_candidate']:
                    issues.append('unsupported_target_entity')
                if target and target_columns != target['primary_key']:
                    issues.append('target_is_not_primary_key')
                if target and len(target_columns) != len(source_columns):
                    issues.append('key_width_mismatch')
                for source_column, target_column in zip(source_columns, target_columns):
                    left = columns.get(_fold(source_column))
                    right = target_fields.get(_fold(target_column)) if target_column is not None else None
                    if left is None:
                        issues.append('missing_source_column')
                        continue
                    if not left['effective_not_null']:
                        issues.append('nullable_source')
                    if left['generated']:
                        issues.append('generated_source')
                    if left['type_hint'] is None:
                        issues.append('unsupported_source_type')
                    if target is not None and right is None:
                        issues.append('missing_target_column')
                    elif right is not None and left['type_hint'] != right['type_hint']:
                        issues.append('key_type_mismatch')
                table['foreign_keys'].append({
                    'id': identifier, 'source_columns': source_columns, 'target_table': target_name,
                    'declared_target_table': declared_target, 'declared_target_columns': declared_targets,
                    'target_columns': target_columns, 'implicit_target': implicit,
                    'on_update': rows[0][5], 'on_delete': rows[0][6], 'match': rows[0][7],
                    'arm_candidate': not issues, 'issues': list(dict.fromkeys(issues))})
        return {'version': 1, 'ok': True, 'origin': 'schema_inspection',
                'data_checked': False, 'review_required': True,
                'tables': list(tables.values()), 'excluded': excluded}
    finally:
        if owns_transaction and connection.in_transaction:
            connection.rollback()


def inspect_file(database_path):
    uri = Path(database_path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return inspect_schema(connection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    try:
        report = inspect_file(args.database)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({'version': 1, 'ok': False, 'origin': 'schema_inspection',
                          'error': {'stage': 'schema', 'message': str(error)}}))
        return 2
    print(json.dumps(report, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

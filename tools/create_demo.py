"""Create fresh academic demo records, including explicitly deliberate mistakes."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3

from .analyze_runs import analyze_report, render_markdown
from .run_comparison import DEFAULT_COMPILER, run_comparison
from .validate_dataset import load_catalog

ROOT = Path(__file__).resolve().parents[1]


def _deliberate_mistakes(workload):
    changed = deepcopy(workload)
    cases = {case['id']: case for case in changed['cases']}
    # Exercise both failure directions. These hand-written changes illustrate
    # diagnostics and scoring, not observed behavior or advantages of a model.
    cases['canadian_authorships']['sqlp'] = (
        "SELECT w.author.name FROM WROTE w WHERE w.paper.publisher.country = 'CA';")
    cases['canadian_authors_distinct']['sql'] = cases['canadian_authors_distinct']['sql'].replace('SELECT DISTINCT', 'SELECT', 1)
    for language, predicate in [('sql', 'mm.aid'), ('sqlp', 'a.mentor.mentor.aid')]:
        cases['mentor_cycle'][language] = cases['mentor_cycle'][language].replace(predicate + ' = 1', 'a.aid = 1')
    cases['empty_us_authorships']['sql'] = (
        "SELECT a.name FROM WROTE w JOIN AUTHOR a ON w.author_aid=a.aid "
        "JOIN PAPER p ON w.paper_pid=p.pid JOIN UNIVERSITY pub "
        "ON p.publisher_uid=pub.uid WHERE pub.country='US';")
    cases['paper_author_pair']['sqlp'] = (
        "SELECT w.paper.title, w.unknown.name FROM WROTE w WHERE w.author.aid = 2;")
    return changed


def _write_json(path, value):
    with path.open('x', encoding='utf-8') as output:
        json.dump(value, output, allow_nan=False, ensure_ascii=True, indent=2)
        output.write('\n')


def create_demo(output_dir, compiler_path=DEFAULT_COMPILER):
    """Create a new directory; refuse to overwrite prior records or databases."""
    compiler = Path(compiler_path).resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise ValueError('Compiler executable unavailable; run make first')
    fixture = ROOT / 'examples/academic'
    catalog = load_catalog(fixture/'catalog.json')
    workload = json.loads((fixture/'workload.json').read_text(encoding='utf-8'))
    deliberate = _deliberate_mistakes(workload)
    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir()  # Atomic reservation; existing directories are preserved.
    database = output_dir/'academic.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript((fixture/'data.sql').read_text(encoding='utf-8'))
    _write_json(output_dir/'catalog.json', catalog)
    _write_json(output_dir/'prepared-workload.json', workload)
    _write_json(output_dir/'deliberate-workload.json', deliberate)
    prepared_report = run_comparison(database, catalog, workload, compiler)
    if not prepared_report["ok"]:
        raise ValueError("Prepared fixture answers failed; fix them before creating the demo")
    deliberate_report = run_comparison(database, catalog, deliberate, compiler)
    note = ('Hand-written development queries with deliberately introduced mistakes. '
            'No model was called; these records do not measure model performance.')
    deliberate_report['demo_note'] = note
    _write_json(output_dir/'prepared-report.json', prepared_report)
    _write_json(output_dir/'deliberate-report.json', deliberate_report)
    analysis = analyze_report(deliberate_report)
    _write_json(output_dir/'deliberate-analysis.json', analysis)
    with (output_dir/'deliberate-analysis.md').open('x', encoding='utf-8') as output:
        output.write(render_markdown(analysis))
    return {'version': 1, 'origin': 'manual', 'directory': str(output_dir.resolve()),
            'prepared_report': str((output_dir/'prepared-report.json').resolve()),
            'deliberate_report': str((output_dir/'deliberate-report.json').resolve()),
            'note': note}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_dir', type=Path, help='A new directory, usually under ignored build/')
    parser.add_argument('--compiler', type=Path, default=DEFAULT_COMPILER)
    args = parser.parse_args()
    try:
        result = create_demo(args.output_dir, args.compiler)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({'version': 1, 'ok': False, 'error': {'stage': 'demo', 'message': str(error)}}, allow_nan=False))
        return 2
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

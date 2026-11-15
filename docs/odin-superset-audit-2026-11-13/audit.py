#!/usr/bin/env python3
"""Compare identical, native-valid source through Odin and both Thor drivers.

This records differences for review; it does not declare known bugs acceptable
or change the compilers. All builds and generated files stay in a scratch tree.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def invoke(command, cwd, timeout=60):
    started = time.monotonic()
    try:
        result = subprocess.run(list(map(str, command)), cwd=cwd, capture_output=True,
                                text=True, errors='replace', timeout=timeout)
        return {'status':result.returncode, 'stdout':result.stdout[:6000],
                'stderr':result.stderr[:6000], 'seconds':round(time.monotonic()-started, 4)}
    except subprocess.TimeoutExpired as error:
        return {'status':'timeout', 'stdout':str(error.stdout or '')[:6000],
                'stderr':str(error.stderr or '')[:6000], 'seconds':round(time.monotonic()-started, 4)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', action='append', choices=['rust', 'python'])
    parser.add_argument('--case', action='append')
    parser.add_argument('--unchecked', action='store_true')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rust-executable',type=Path,default=ROOT/'thor')
    options = parser.parse_args()
    work = (options.work_dir or Path(tempfile.mkdtemp(prefix='thor-superset-audit-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    engines = options.engine or ['rust', 'python']
    cases = json.loads((HERE/'cases.json').read_text())
    rows = []
    for case in cases:
        name = case['name']
        if options.case and name not in options.case:
            continue
        if options.unchecked and case.get('checked_only'):
            continue  # Native out-of-bounds accesses would be undefined here.
        directory = work/name
        directory.mkdir(exist_ok=True)
        for filename, text in case.get('files', {}).items():
            path = directory/filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for extension in ('odin', 'thor'):
            source = case['source']
            if case.get('absolute_payload'):
                source = source.replace('ABSOLUTE_PAYLOAD', str(directory/'payload.txt'))
            (directory/('main.'+extension)).write_text(source)
        run = case.get('run', False)
        action = 'build' if run else 'check'
        flags = ['-thread-count:1', *[flag.replace('-collection:thor:PLACEHOLDER', '-collection:thor='+str(directory/'custom'))
                                     for flag in case.get('flags', [])]]
        if run:
            flags += (['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']
                      if options.unchecked else ['-o:none'])
        native_program = directory/'native-program'
        directory_entry = case.get('directory_entry', False)
        baseline = invoke(['odin', action, directory if directory_entry else directory/'main.odin',
                           *([] if directory_entry else ['-file']), *flags,
                           *(['-out:'+str(native_program)] if run else [])], directory)
        row = {'name':name, 'group':case['group'], 'odin':{'compile':baseline}, 'engines':{}}
        if baseline['status'] != 0:
            row['qualification'] = 'not-native-valid'
        else:
            row['qualification'] = 'native-valid'
            if run:
                row['odin']['run'] = invoke([native_program], directory, 10)
            if directory_entry:
                # The native scan ignores .thor; the Thor scan accepts both.
                # Remove the native twin so the Thor package has one main.
                (directory/'main.odin').unlink()
            for engine in engines:
                program = directory/(engine+'-program')
                compiler = options.rust_executable if engine=='rust' else ROOT/'thor-python'
                result = {'compile':invoke([compiler, action, directory if directory_entry else directory/'main.thor',
                    '--output-dir', directory/(engine+'-generated'), *flags,
                    *(['-out:'+str(program)] if run else [])], directory)}
                if result['compile']['status'] != 0:
                    result['comparison'] = 'rejected'
                elif run:
                    result['run'] = invoke([program], directory, 10)
                    original = row['odin']['run']
                    # Renaming naturally changes only the source extension.
                    def normalize(text):
                        return text.replace(str(directory/'main.odin'), '<source>')\
                                   .replace(str(directory/'main.thor'), '<source>')
                    result['comparison'] = ('same' if
                        (result['run']['status'], normalize(result['run']['stdout'])) ==
                        (original['status'], normalize(original['stdout'])) else 'runtime-difference')
                else:
                    result['comparison'] = 'same'
                row['engines'][engine] = result
        rows.append(row)
        print(name, row['qualification'],
              ' '.join(engine+':'+value['comparison'] for engine,value in row['engines'].items()), flush=True)
    report = {
        'odin':invoke(['odin', 'version'], work),
        'thor':invoke([ROOT/'thor', 'version'], work),
        'unchecked':options.unchecked, 'work_dir':str(work), 'cases':rows,
    }
    options.output.write_text(json.dumps(report, indent=2)+'\n')
    qualified = sum(row['qualification']=='native-valid' for row in rows)
    counts = {engine:{category:sum(row['engines'].get(engine, {}).get('comparison')==category
                                 for row in rows)
                     for category in ('same', 'rejected', 'runtime-difference')}
              for engine in engines}
    print(json.dumps({'native_valid':qualified, 'total':len(rows), 'results':counts}), flush=True)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Measure warm transpile commands on a shared, supported synthetic program.

This is not a whole-language parity or real-project performance result. Native
compilation and execution validate the fixture before the timed lowering runs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--procedures', type=int, default=200)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--engine', action='append', default=[], metavar='NAME=EXECUTABLE',
                        help='additional compiler, such as a historical Rust binary')
    parser.add_argument('--report', type=Path)
    options = parser.parse_args(argv)
    if options.procedures < 1 or options.runs < 1:
        parser.error('--procedures and --runs must be positive')
    engines = {'python': ROOT/'thor', 'rust': ROOT/'rust/target/release/thor-rust'}
    for entry in options.engine:
        if '=' not in entry:
            parser.error('--engine requires NAME=EXECUTABLE')
        name, executable = entry.split('=', 1)
        if not name or name in engines or '/' in name or '\\' in name or name in {'.', '..'}:
            parser.error('engine names must be unique single directory names')
        engines[name] = Path(executable).resolve()
    for executable in engines.values():
        if not executable.is_file():
            parser.error('compiler does not exist: '+str(executable))
    code = 'package main\n'
    for index in range(options.procedures):
        code += (f'f_{index} :: proc(x:int)->int {{text:=f"{{x}}-{{x**2}}"; '
                 'result:=len(text);delete(text);return result}\n')
    code += (f'main::proc(){{if f_0(2)!=3||f_{options.procedures-1}(3)!=3'
             '{panic("values")}}\n')
    environment = {**os.environ, 'THOR_ROOT': str(ROOT)}
    samples = {name: [] for name in engines}
    with tempfile.TemporaryDirectory(prefix='thor-lowering-benchmark-') as directory:
        scratch = Path(directory)
        source = scratch/'main.thor'
        source.write_text(code)

        def invoke(name, command):
            arguments = [str(engines[name]), command, str(source), '--cache-dir',
                         str(scratch/'cache'), '--output-dir', str(scratch/name)]
            if command == 'run':
                arguments.append('-thread-count:1')
            started = time.perf_counter()
            result = subprocess.run(arguments, capture_output=True, text=True,
                                    env=environment, timeout=60)
            elapsed = time.perf_counter()-started
            if result.returncode:
                raise RuntimeError(name+' '+command+' failed:\n'+result.stdout+result.stderr)
            return elapsed

        for name in engines:
            invoke(name, 'run')
            invoke(name, 'transpile')
        for iteration in range(options.runs):
            order = list(engines) if iteration % 2 == 0 else list(reversed(engines))
            for name in order:
                samples[name].append(invoke(name, 'transpile'))
        if source.read_text() != code:
            raise RuntimeError('a compiler modified the input source')
    report = {
        'scope': 'warm-cache transpile only; synthetic interpolation/power procedures; no Odin backend in timed runs',
        'procedures': options.procedures, 'source_bytes': len(code.encode()),
        'source_sha256': hashlib.sha256(code.encode()).hexdigest(),
        'engines': {name:str(path) for name,path in engines.items()},
        'runs_seconds': samples,
        'median_seconds': {name:statistics.median(values) for name,values in samples.items()},
    }
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report['median_seconds']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

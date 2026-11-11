#!/usr/bin/env python3
"""Measure transpile commands on synthetic code or copies of real projects.

The synthetic fixture is executed before timing. Real projects are type checked
after timing and never executed. Timed commands exclude the final Odin backend,
but include any native helpers required for a compile-time cache miss.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def project_benchmark(options, engines):
    environment = {**os.environ, 'THOR_ROOT': str(ROOT)}
    environment.pop('THOR_PROFILE', None)
    report = {
        'scope': 'transpile only; empty project cache then warm runs; shared SDK/parser caches retained; final Odin backend excluded; compile-time helper work included',
        'platform': platform.platform(),
        'engines': {name: str(path) for name, path in engines.items()},
        'profiling': bool(options.profile_dir),
        'projects': [],
    }
    for case_index, original in enumerate(options.project):
        original = original.resolve()
        if not original.is_dir():
            raise ValueError('--project must name a project directory: '+str(original))
        with tempfile.TemporaryDirectory(prefix='thor-project-benchmark-') as directory:
            work = Path(directory)
            entry = work/'source'/original.name
            artifacts = shutil.ignore_patterns('.git', '.vscode', '.thor-*',
                                              'node_modules', '*.app', '*.dSYM')

            def ignored(directory, names):
                excluded = set(artifacts(directory, names))
                if Path(directory) == original:
                    excluded.update(name for name in (original.name, original.name+'.exe')
                                    if (original/name).is_file())
                return excluded

            shutil.copytree(original, entry, symlinks=True, ignore=ignored)
            sources = {str(path.relative_to(entry)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in entry.rglob('*') if path.is_file() and path.suffix in {'.thor', '.odin'}}
            case = {'name': original.name, 'source_sha256': sources, 'cold': {}, 'warm': {name: [] for name in engines}}
            invocations = {name: 0 for name in engines}

            def invoke(name, command):
                invocations[name] += 1
                env = dict(environment)
                profile = None
                if options.profile_dir and name != 'python':
                    profile = options.profile_dir.resolve()/f'{case_index}-{original.name}-{name}-{invocations[name]}-{command}.json'
                    profile.parent.mkdir(parents=True, exist_ok=True)
                    env['THOR_PROFILE'] = str(profile)
                arguments = [str(engines[name]), command, str(entry), '--cache-dir',
                             str(work/'cache'/name), '--output-dir', str(work/'output'/name),
                             '--cache-report']
                started = time.perf_counter()
                result = subprocess.run(arguments, capture_output=True, text=True,
                                        cwd=work, env=env, timeout=options.timeout)
                elapsed = time.perf_counter()-started
                if result.returncode:
                    raise RuntimeError(name+' '+command+' failed:\n'+result.stdout+result.stderr)
                cache = next((json.loads(line) for line in result.stdout.splitlines()
                              if line.startswith('{"thor_cache"')), None)
                run = {'seconds': elapsed, 'cache': cache}
                if profile and profile.is_file():
                    run['profile'] = json.loads(profile.read_text())
                return run

            for name in engines:
                case['cold'][name] = invoke(name, 'transpile')
            for iteration in range(options.runs):
                order = list(engines) if iteration % 2 == 0 else list(reversed(engines))
                for name in order:
                    case['warm'][name].append(invoke(name, 'transpile'))
            for name in engines:
                invoke(name, 'check')
            after = {str(path.relative_to(entry)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in entry.rglob('*') if path.is_file() and path.suffix in {'.thor', '.odin'}}
            if after != sources:
                raise RuntimeError('a compiler modified the copied project sources')
            if any(hashlib.sha256((original/path).read_bytes()).hexdigest() != digest
                   for path, digest in sources.items()):
                raise RuntimeError('original project sources changed during the benchmark')
            case['median_seconds'] = {name: statistics.median(run['seconds'] for run in runs)
                                      for name, runs in case['warm'].items()}
            report['projects'].append(case)
            print(json.dumps({'project': original.name, 'median_seconds': case['median_seconds']}), flush=True)
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, indent=2)+'\n')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--procedures', type=int, default=200)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--engine', action='append', default=[], metavar='NAME=EXECUTABLE',
                        help='additional compiler, such as a historical Rust binary')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--project', action='append', type=Path, default=[],
                        help='benchmark a copy of this project instead of synthetic code (repeatable)')
    parser.add_argument('--timeout', type=float, default=120,
                        help='per-command limit in seconds for real projects')
    parser.add_argument('--profile-dir', type=Path,
                        help='write native stage/counter reports for real projects (adds measurement overhead)')
    options = parser.parse_args(argv)
    if options.procedures < 1 or options.runs < 1 or not 0 < options.timeout < float('inf'):
        parser.error('--procedures, --runs and --timeout must be positive finite values')
    if options.profile_dir and not options.project:
        parser.error('--profile-dir requires --project')
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
    for project in options.project:
        if not project.is_dir():
            parser.error('--project must name a project directory: '+str(project))
    if options.project:
        return project_benchmark(options, engines)
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

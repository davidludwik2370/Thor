#!/usr/bin/env python3
"""Paired opt-in fn safety build costs, using disposable cold/warm caches.

Cold means an empty compiler data cache, with the installed frontend already
available. OS caches are left alone. No project sources or executable outputs
are overwritten. Compiler and benchmark outputs must be outside source projects.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def command(compiler, source, mode, cache, output):
    return [str(compiler), 'build', str(source), '--fn-safety='+mode,
            '--cache-dir', str(cache), '--output-dir', str(output/'generated'),
            '-out:'+str(output/('program.exe' if os.name=='nt' else 'program')),
            '-thread-count:1', '-json-errors']


def invoke(compiler, source, mode, cache, output, profile=None):
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop('THOR_PROFILE', None)
    if profile is not None:
        env['THOR_PROFILE'] = str(profile)
    start = time.perf_counter()
    result = subprocess.run(command(compiler, source, mode, cache, output),
                            capture_output=True, text=True, env=env, timeout=180)
    elapsed = time.perf_counter()-start
    packet = json.loads(result.stderr) if result.stderr.strip() else {'errors': []}
    if result.returncode:
        raise RuntimeError(f'{source.name} ({mode}) failed: {result.stderr[-6000:]}')
    return elapsed, packet


def interval(values):
    rng = random.Random(0)
    samples = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(2000))
    return [samples[49], samples[1949]]


def measure(compiler, source, directory, repeats):
    result = {}
    caches = {mode: directory/('warm-'+mode) for mode in ('off', 'prototype')}
    outputs = {mode: directory/('output-'+mode) for mode in ('off', 'prototype')}
    for mode in caches:
        invoke(compiler, source, mode, caches[mode], outputs[mode])
    for temperature in ('cold', 'warm'):
        pairs = []
        for repeat in range(repeats):
            pair = {}
            order = ('off', 'prototype') if repeat % 2 == 0 else ('prototype', 'off')
            for mode in order:
                if temperature == 'cold':
                    with tempfile.TemporaryDirectory(prefix='cold-', dir=directory) as cache:
                        seconds, _ = invoke(compiler, source, mode, Path(cache), outputs[mode])
                else:
                    seconds, _ = invoke(compiler, source, mode, caches[mode], outputs[mode])
                pair[mode] = seconds
            pair['overhead_percent'] = 100*(pair['prototype']/pair['off']-1)
            pair['extra_ms'] = 1000*(pair['prototype']-pair['off'])
            pairs.append(pair)
        ratios = [pair['overhead_percent'] for pair in pairs]
        result[temperature] = {
            'off_median_seconds': statistics.median(p['off'] for p in pairs),
            'prototype_median_seconds': statistics.median(p['prototype'] for p in pairs),
            'paired_overhead_percent': statistics.median(ratios),
            'paired_extra_ms': statistics.median(p['extra_ms'] for p in pairs),
            'bootstrap_95_percent_interval': interval(ratios),
            'median_within_5_percent': statistics.median(ratios) <= 5,
            'pairs': pairs,
        }
    profile = directory/'profile.json'
    _, diagnostics = invoke(compiler, source, 'prototype', caches['prototype'], outputs['prototype'], profile)
    data = json.loads(profile.read_text())
    result['analysis'] = {
        'seconds': data.get('stages', {}).get('analysis.fn_safety', {}).get('inclusive_seconds', 0),
        'passes': data.get('stages', {}).get('analysis.fn_safety', {}).get('calls', 0),
        'counters': {k:v for k,v in data.get('counters', {}).items() if k.startswith('fn_safety.')},
        'gaps': [{'file': Path(d['pos']['file']).name, 'line': d['pos']['line'], 'message': d['msgs'][0]}
                 for d in diagnostics['errors'] if d.get('code')=='thor/fn-safety-unverified'],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, default=ROOT/'rust/target/release/thor-rust')
    parser.add_argument('--project', action='append', default=[], metavar='NAME=PATH')
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error('--repeats must be at least 3')
    compiler = args.compiler.resolve()
    projects = dict(item.split('=', 1) for item in args.project)
    projects = {name: Path(path).expanduser().resolve() for name,path in projects.items()}
    projects.setdefault('parallel', ROOT/'examples/parallel')
    results = {}
    with tempfile.TemporaryDirectory(prefix='thor-fast-fn-benchmark-') as temporary:
        base = Path(temporary)
        plain = base/'plain'; plain.mkdir()
        (plain/'main.thor').write_text('package main\nmain::proc(){v:=7;if v!=7{panic("value")}}\n')
        projects['plain'] = plain
        stress = base/'fn_stress'; stress.mkdir()
        source = 'package main\nimport "core:os"\n'
        for i in range(200):
            source += f'f{i}::fn(xs:[]int)->int{{alias:=xs[:];alias[0]+{i}}}\n'
        source += 'main::proc(){xs:=[len(os.args)];total:=0;'
        source += ''.join(f'total+=f{i}(xs[:]);' for i in range(200))
        source += 'if total<0{panic("total")}}\n'
        (stress/'main.thor').write_text(source)
        projects['fn_stress'] = stress
        owned = base/'owned_callbacks'; owned.mkdir()
        source = 'package main\nimport "core:os"\nimport "core:slice"\n'
        source += 'duplicate::fn(xs:[]$T)->[]T{slice.clone(xs)}\n'
        source += 'apply::fn(xs:[]int,callback:fn([]int)->[]int)->[]int{callback(xs)}\n'
        for i in range(40):
            source += f'f{i}::fn(xs:[]int)->int{{owned:=apply(xs,duplicate);alias:=owned[:];value:=alias[0]+{i};delete(owned);return value}}\n'
        source += 'main::proc(){xs:=[len(os.args)];total:=0;'
        source += ''.join(f'total+=f{i}(xs[:]);' for i in range(40))
        source += 'if total<0{panic("total")}}\n'
        (owned/'main.thor').write_text(source)
        projects['owned_callbacks'] = owned
        for name,path in projects.items():
            with tempfile.TemporaryDirectory(prefix=name+'-', dir=base) as workspace:
                results[name] = measure(compiler, path, Path(workspace), args.repeats)
            print(name, {t:round(results[name][t]['paired_overhead_percent'],2) for t in ('cold','warm')}, flush=True)
    report = {
        'schema': 1, 'compiler_sha256': hashlib.sha256(compiler.read_bytes()).hexdigest(),
        'host': {'system': platform.system(), 'machine': platform.machine(), 'release': platform.release()},
        'method': {'paired_repeats': args.repeats, 'order': 'alternating off/prototype',
                   'cold': 'empty temporary compiler data cache; installed frontend and OS caches retained',
                   'warm': 'one warmup per mode, independent reusable temporary caches',
                   'runtime': 'generated source unchanged; no runtime instrumentation',
                   'target_percent': 5, 'comptime': 'normal (default)', 'odin_workers': 1},
        'workloads': results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')


if __name__=='__main__':
    main()

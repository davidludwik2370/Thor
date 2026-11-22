#!/usr/bin/env python3
"""Benchmark real project builds and reproducible simulation copies, serially.

Original source is only read. Cold means an empty project cache, with shared
SDK/parser/toolchain caches retained. Profiling runs are separate from timings.
"""
import argparse
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MODES = ('required', 'normal', 'aggressive', 'override')
FLAGS = ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']


class CommandFailure(RuntimeError):
    def __init__(self, command, result, seconds):
        super().__init__(f'{command}\n{result.stdout}\n{result.stderr}')
        self.stdout, self.stderr, self.seconds = result.stdout, result.stderr, seconds


def digest(data):
    return hashlib.sha256(data).hexdigest()


def invoke(command, *, env=None, timeout=180):
    started = time.perf_counter()
    result = subprocess.run(list(map(str, command)), cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=timeout)
    seconds = time.perf_counter() - started
    if result.returncode:
        raise CommandFailure(command, result, seconds)
    return seconds, result.stdout, result.stderr


def summary(values):
    quartiles = statistics.quantiles(values, n=4) if len(values) > 1 else values * 3
    return {'median': statistics.median(values), 'min': min(values), 'max': max(values),
            'q1': quartiles[0], 'q3': quartiles[2]}


def text_section(binary):
    if platform.system() != 'Darwin':
        return None
    _, output, _ = invoke(['otool', '-l', binary])
    match = re.search(r'sectname __text\s+segname __TEXT\s+addr \S+\s+size (\S+)\s+offset (\d+)', output)
    if not match:
        raise RuntimeError('Mach-O executable has no __TEXT,__text section')
    size, offset = int(match[1], 16), int(match[2])
    return {'bytes': size, 'sha256': digest(binary.read_bytes()[offset:offset+size])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--poker', type=Path, default=Path.home()/'Code/Thor/poker_pmap')
    parser.add_argument('--hilo', type=Path, default=Path.home()/'Code/Thor/hilo_test')
    parser.add_argument('--compiler', type=Path, default=ROOT/'rust/target/release/thor-rust')
    parser.add_argument('--artifacts', type=Path)
    parser.add_argument('--resume', action='store_true', help='resume a matching saved report without repeating accepted samples')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--cold-builds', type=int, default=3)
    parser.add_argument('--warm-builds', type=int, default=5)
    parser.add_argument('--runs', type=int, default=8)
    parser.add_argument('--target-seconds', type=float, default=1.5)
    args = parser.parse_args()
    if min(args.cold_builds, args.warm_builds, args.runs) < 1 or not 0 < args.target_seconds < 30:
        parser.error('sample counts must be positive; target seconds must be between 0 and 30')
    args.compiler = args.compiler.resolve()
    work = args.artifacts.resolve() if args.artifacts else Path(tempfile.mkdtemp(prefix='thor-comptime-modes-'))
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()) and not args.resume:
        parser.error('--artifacts must be empty so cold samples cannot reuse earlier caches')
    environment = {**os.environ, 'THOR_ROOT': str(ROOT)}
    environment.pop('THOR_PROFILE', None)
    report = {'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
              'compiler_sha256': digest(args.compiler.read_bytes()),
              'flags': FLAGS, 'cold_build_samples': args.cold_builds,
              'warm_build_samples': args.warm_builds, 'runtime_samples': args.runs,
              'target_seconds': args.target_seconds,
              'artifacts': str(work), 'projects': {}}
    if args.resume:
        previous = json.loads(args.report.read_text())
        for key in ['compiler_sha256', 'flags', 'cold_build_samples', 'warm_build_samples', 'runtime_samples', 'target_seconds', 'artifacts']:
            if previous[key] != report[key]:
                parser.error('resume configuration differs: '+key)
        report = previous
    originals = {'poker_pmap': args.poker.resolve(), 'hilo_test': args.hilo.resolve()}
    snapshots = {}
    for project, original in originals.items():
        snapshots[project] = {p.name: digest(p.read_bytes()) for p in original.glob('*.thor')}
        if args.resume and str(original) != report['projects'][project]['original']:
            parser.error('source path differs from the saved run: '+project)
        if args.resume and snapshots[project] != report['projects'][project]['source_sha256']:
            parser.error('original source changed since the saved run: '+project)
        destination = work/'original'/project
        destination.mkdir(parents=True, exist_ok=True)
        for name in snapshots[project]:
            shutil.copyfile(original/name, destination/name)
        if not args.resume:
            report['projects'][project] = {'original': str(original), 'source_sha256': snapshots[project],
                'entry_kind': 'explicit file' if project == 'hilo_test' else 'package',
                'modes': {mode: {'cold_build_seconds': [], 'warm_build_seconds': [],
                                 'runtime_seconds': []} for mode in MODES}}

    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+'\n')

    def build(project, mode, *, variant='original', cache_name='warm', profile_name=None, capture=False):
        source = work/variant/project
        entry = source/'hilo_test.thor' if project == 'hilo_test' else source
        # Identical backend paths prevent directory-name length from moving
        # diagnostic strings and changing machine-code address relocations.
        active = work/'active'/variant/project
        active.mkdir(parents=True, exist_ok=True)
        executable = active/'program'
        output = active/'odin'
        cache = work/'cache'/variant/project/mode/cache_name
        env = dict(environment)
        profile = active/(profile_name+'.json') if profile_name else None
        if profile:
            env['THOR_PROFILE'] = str(profile)
        for attempt in range(3):
            # A failed cold build may already have populated helper results.
            # Give any retry a fresh owned cache rather than timing a warm retry.
            used_cache = cache if not attempt or cache_name == 'warm' else cache.with_name(cache.name+f'-retry-{attempt}')
            try:
                elapsed, stdout, stderr = invoke([args.compiler, 'build', entry, '--comptime='+mode,
                    *FLAGS, '--output-dir', output, '--cache-dir', used_cache, '-out:'+str(executable), '--cache-report'], env=env)
                break
            except CommandFailure as error:
                # Retain an intermittent native compiler assertion separately;
                # never retry source errors, output mismatches or program failures.
                if not ('Odin compiler terminated' in error.stderr and 'src/array.cpp' in error.stderr and 'Assertion Failure' in error.stderr):
                    raise
                report.setdefault('native_failures', []).append({'project': project, 'mode': mode,
                    'variant': variant, 'cache': cache_name, 'seconds': error.seconds, 'stderr': error.stderr})
                save()
                print(f'{project} {mode}: recorded native backend failure; retrying unchanged flags', flush=True)
                if attempt == 2:
                    raise
        detail = {'seconds': elapsed}
        if profile:
            detail['profile'] = json.loads(profile.read_text())
        detail['cache'] = next((json.loads(line) for line in stdout.splitlines()
                                if line.startswith('{"thor_cache"')), None)
        if stderr:
            detail['stderr'] = stderr
        if capture:
            saved = work/'binaries'/variant/project/mode
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(executable, saved)
            detail['binary'] = str(saved)
            detail['binary_sha256'] = digest(saved.read_bytes())
            detail['text_section'] = text_section(saved)
            emitted = {str(p.relative_to(output)): p.read_text()
                       for p in output.rglob('*.odin') if not p.is_symlink()}
            generated = work/'generated'/variant/project/mode
            for name, text in emitted.items():
                path = generated/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            detail['generated_sha256'] = {name: digest(text.encode()) for name, text in emitted.items()}
        print(f'{project} {mode} {variant} {profile_name or cache_name}: {elapsed:.3f}s', flush=True)
        return detail

    # Use rotating mode order to distribute shared-cache and thermal effects.
    for project in originals:
        case = report['projects'][project]
        for index in range(args.cold_builds):
            order = MODES[index % 4:] + MODES[:index % 4]
            for mode in order:
                if len(case['modes'][mode]['cold_build_seconds']) > index:
                    continue
                result = build(project, mode, cache_name=f'cold-{index}')
                case['modes'][mode]['cold_build_seconds'].append(result['seconds'])
                save()
        for mode in MODES:
            if not case['modes'][mode]['warm_build_seconds']:
                build(project, mode)  # Hydrate before timing warm builds.
        for index in range(args.warm_builds):
            order = MODES[index % 4:] + MODES[:index % 4]
            if index % 2:
                order = tuple(reversed(order))
            for mode in order:
                if len(case['modes'][mode]['warm_build_seconds']) > index:
                    continue
                result = build(project, mode)
                case['modes'][mode]['warm_build_seconds'].append(result['seconds'])
                save()
        for mode in MODES:
            data = case['modes'][mode]
            if 'cold_profile' not in data:
                data['cold_profile'] = build(project, mode, cache_name='profile-cold', profile_name='cold-profile')
                save()
            if 'warm_profile' not in data:
                data['warm_profile'] = build(project, mode, profile_name='warm-profile', capture=True)
            data['cold_build_summary'] = summary(data['cold_build_seconds'])
            data['warm_build_summary'] = summary(data['warm_build_seconds'])
            save()

    for project in originals:
        case = report['projects'][project]
        if case.get('runtime_outputs_identical') and all(len(v['runtime_seconds']) == args.runs for v in case['modes'].values()):
            continue
        destination = work/'runtime'/project
        shutil.copytree(work/'original'/project, destination, dirs_exist_ok=True)
        entry = destination/('poker.thor' if project == 'poker_pmap' else 'hilo_test.thor')
        original = entry.read_text()
        if project == 'poker_pmap':
            pattern = r'(TRIAL_COUNT\s*::\s*)[\d_]+'
            marker = 'run_trials :: proc(results: ^Result_Map, trials, player_count: int) {'
            seeded = original.replace(marker, marker+'\n    rand.reset(u64(0x243f6a8885a308d3) + u64(player_count)*97 + u64(trials))', 1)
            initial, ceiling = 100_000, 1_000_000
        else:
            pattern = r'(NUM_TRIALS\s*::\s*)[\d_]+'
            marker = 'simulate_batch :: proc(trials: int) -> (wins: int) {'
            seeded = original.replace(marker, marker+'\n    rand.reset(u64(0x243f6a8885a308d3) + u64(trials))', 1)
            initial, ceiling = 1_000_000, 50_000_000
        if seeded == original:
            raise RuntimeError('Worker definition changed; review the seeding patch')

        def set_trials(trials):
            text, count = re.subn(pattern, lambda match: match[1]+str(trials), seeded)
            if count != 1:
                raise RuntimeError('Trial count definition changed')
            entry.write_text(text)

        previous_workload = case.get('runtime_workload')
        if previous_workload:
            trials, elapsed = previous_workload['trials'], previous_workload['pilot_seconds']
        else:
            set_trials(initial)
            trial_binary = build(project, 'normal', variant='runtime', cache_name='calibrate', capture=True)['binary']
            elapsed, _, _ = invoke([trial_binary])
            multiple = math.lcm(10_000, os.cpu_count() or 1)
            trials = max(multiple, min(ceiling, math.ceil(initial*args.target_seconds/max(elapsed, .001)/multiple)*multiple))
        set_trials(trials)
        case['runtime_workload'] = {'trials': trials, 'initial_trials': initial,
            'pilot_seconds': elapsed, 'target_seconds': args.target_seconds,
            'seed': '0x243f6a8885a308d3 plus trials (plus 97*player_count for poker)',
            'seed_scope': 'worker entry; identical worker streams for equal-sized batches',
            'source_sha256': {p.name: digest(p.read_bytes()) for p in destination.glob('*.thor')}}
        if previous_workload and previous_workload['source_sha256'] != case['runtime_workload']['source_sha256']:
            raise RuntimeError('Runtime source patch changed since the saved run')
        save()
        (work/(project+'-runtime.patch')).write_text(''.join(difflib.unified_diff(
            original.splitlines(True), entry.read_text().splitlines(True),
            fromfile=str(originals[project]/entry.name), tofile=str(entry))))
        stdout_path = work/(project+'-runtime.stdout')
        expected = stdout_path.read_text() if stdout_path.is_file() else None
        for mode in MODES:
            result = case['modes'][mode].get('runtime_build')
            if result is None or not Path(result['binary']).is_file():
                result = build(project, mode, variant='runtime', capture=True)
            case['modes'][mode]['runtime_build'] = result
            save()
            # An untimed warmup checks deterministic full output, not assertions.
            _, stdout, _ = invoke([result['binary']])
            if expected is None:
                expected = stdout
                stdout_path.write_text(stdout)
            elif stdout != expected:
                raise RuntimeError(f'{project} {mode} changed the simulation output')
        case['runtime_stdout_sha256'] = digest(expected.encode())
        for index in range(args.runs):
            order = list(MODES[index % 4:] + MODES[:index % 4])
            if index % 2:
                order.reverse()
            for mode in order:
                data = case['modes'][mode]
                if len(data['runtime_seconds']) > index:
                    continue
                elapsed, stdout, stderr = invoke([data['runtime_build']['binary']])
                if stdout != expected or stderr:
                    raise RuntimeError(f'{project} {mode} produced inconsistent output')
                data['runtime_seconds'].append(elapsed)
                print(f'{project} {mode} run {index+1}: {elapsed:.3f}s', flush=True)
                save()
        for mode in MODES:
            case['modes'][mode]['runtime_summary'] = summary(case['modes'][mode]['runtime_seconds'])
        case['runtime_outputs_identical'] = True
        save()
    for project, original in originals.items():
        after = {p.name: digest(p.read_bytes()) for p in original.glob('*.thor')}
        if after != snapshots[project]:
            raise RuntimeError('Original project changed during benchmarking: '+str(original))
    report['original_sources_unchanged'] = True
    save()


if __name__ == '__main__':
    main()

"""Opt-in, pinned upstream Odin -> Thor -> vet-applied integration runs.

Run from any directory with --projects REGEX to select repositories. Sources and
logs live below integration-projects, outside the ordinary regression fixtures.
No upstream test verdict or source is rewritten by this runner.
"""
import argparse
import ctypes
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'integration-projects'
MANIFEST = Path(__file__).with_name('odin_projects.json')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from thorlang.hygiene import tokens as source_tokens


@contextmanager
def project_lock(directory):
    import fcntl
    with (directory / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def usable_baseline(result, recipe):
    if not result:
        return False
    return result['status'] == 0 or (
        recipe.get('compare_native_test_failures', False)
        and result['status'] == 1
        and bool(result.get('test_results', {}).get('cases')))


def vet_baselines(tokens, commands, values):
    """Select the same source and compile configuration as this vet recipe."""
    def source_path(value):
        path = Path(value).resolve()
        return path.with_suffix('.thor') if path.suffix == '.odin' else path

    def configuration(arguments):
        return sorted(argument for argument in arguments
            if argument.startswith(('-target:', '-define:', '-collection:'))
            or argument in ('-file', '-no-entry-point'))

    target = source_path(tokens[0])
    flags = configuration(tokens[1:])
    test_mode = '-build-mode:test' in tokens
    result = []
    for command in commands:
        arguments = [argument.format(**{'artifact':'', **values}) for argument in command['args']]
        if len(arguments) < 2 or source_path(arguments[1]) != target:
            continue
        if configuration(arguments[2:]) != flags:
            continue
        if test_mode != (arguments[0] == 'test' or '-build-mode:test' in arguments):
            continue
        result.append(command)
    return result


def fixture_source(raw, native):
    """Keep explicitly selected source-file fixtures rooted in the native tree."""
    tokens = list(source_tokens(raw))
    edits = []
    for (start, end, token), (next_start, next_end, name) in zip(tokens, tokens[1:]):
        if token == b'#' and end == next_start and name in (b'file', b'directory'):
            value = native if name == b'file' else native.parent
            edits.append((start, next_end, json.dumps(str(value)).encode()))
    for start, end, replacement in reversed(edits):
        raw = raw[:start]+replacement+raw[end:]
    return raw


def fixture_overlay(project, stage, tree, source, directory):
    replacements = {}
    for index, relative in enumerate(project.get('source_fixtures', [])):
        native = source/relative
        path = (tree/relative).with_suffix('.thor')
        snapshot = directory/f'{stage}-fixture-{index}.thor'
        snapshot.write_bytes(fixture_source(path.read_bytes(), native))
        replacements[str(path)] = str(snapshot)
    if not replacements:
        return None
    overlay = directory/f'{stage}-fixtures.json'
    overlay.write_text(json.dumps({'Replace':replacements},indent=2)+'\n')
    return overlay


def test_result_summary(packet):
    return {
        'total': packet.get('total'), 'success': packet.get('success'),
        'cases': sorted([package, case['name'], case.get('success')]
            for package, cases in packet.get('packages', {}).items() for case in cases),
    }


def execute(arguments, cwd, log, environment, timeout=900):
    with execution_slot(environment):
        return execute_locked(arguments, cwd, log, environment, timeout)


@contextmanager
def execution_slot(environment):
    import fcntl
    count = max(1, int(environment.get('THOR_INTEGRATION_JOBS', '2')))
    locks = [(WORK / f'command-{index}.lock').open('a') for index in range(count)]
    try:
        while True:
            for lock in locks:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                yield
                return
            time.sleep(0.1)
    finally:
        for lock in locks:
            lock.close()


class DarwinUsage(ctypes.Structure):
    # rusage_info_v0 in the macOS SDK's sys/resource.h.
    _fields_ = [('uuid', ctypes.c_uint8*16), *[(name, ctypes.c_uint64) for name in (
        'user_time', 'system_time', 'idle_wakeups', 'interrupt_wakeups', 'pageins',
        'wired_size', 'resident_size', 'physical_footprint', 'start_time', 'exit_time')]]


try:
    DARWIN_USAGE = ctypes.CDLL('/usr/lib/libproc.dylib').proc_pid_rusage if sys.platform == 'darwin' else None
except OSError:
    DARWIN_USAGE = None
if DARWIN_USAGE is not None:
    DARWIN_USAGE.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    DARWIN_USAGE.restype = ctypes.c_int


def physical_footprint_kib(pid):
    if DARWIN_USAGE is None:
        return None
    usage = DarwinUsage()
    if DARWIN_USAGE(pid, 0, ctypes.byref(usage)) != 0:
        return None
    return usage.physical_footprint//1024


def process_tree(pid):
    snapshot = subprocess.check_output(['ps', '-axo', 'pid,ppid,rss'], text=True)
    processes = [tuple(map(int, line.split())) for line in snapshot.splitlines()[1:]]
    descendants = {pid}
    while True:
        children = {child for child, parent, _ in processes if parent in descendants}
        if children <= descendants:
            break
        descendants.update(children)
    return descendants, sum(max(rss, physical_footprint_kib(child) or 0)
        for child, _, rss in processes if child in descendants)


def process_tree_rss(pid):
    return process_tree(pid)[1]


def terminate_process_tree(pid):
    descendants, _ = process_tree(pid)
    groups = {pid}
    for child in descendants:
        try:
            group = os.getpgid(child)
            if group in descendants:
                groups.add(group)
        except ProcessLookupError:
            pass
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for child in descendants:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


def execute_locked(arguments, cwd, log, environment, timeout=900):
    started = time.monotonic()
    maximum_rss = int(environment.get('THOR_INTEGRATION_MAX_RSS_MB', '3072')) * 1024
    peak_rss = 0
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.with_suffix('.stdout').open('wb') as stdout_file, log.with_suffix('.stderr').open('wb') as stderr_file:
        process = subprocess.Popen(arguments, cwd=cwd, env=environment,
            stdout=stdout_file, stderr=stderr_file, start_new_session=True)
        try:
            while True:
                try:
                    status = process.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    peak_rss = max(peak_rss, process_tree_rss(process.pid))
                    if maximum_rss and peak_rss > maximum_rss:
                        status = 'memory-limit'
                        descendants, _ = process_tree(process.pid)
                        snapshot = subprocess.check_output(['ps','-axo','pid,ppid,rss,command'],text=True)
                        rows = [line.split(None,3) for line in snapshot.splitlines()[1:]]
                        details = [{'pid':int(pid),'parent':int(parent),'resident_kib':int(resident),
                            'footprint_kib':physical_footprint_kib(int(pid)),'command':command}
                            for pid,parent,resident,command in rows if int(pid) in descendants]
                        log.with_suffix('.memory.json').write_text(json.dumps(details,indent=2)+'\n')
                    elif time.monotonic() - started > timeout:
                        status = 'timeout'
                    else:
                        continue
                    terminate_process_tree(process.pid)
                    process.wait()
                    break
        except BaseException:
            if process.poll() is None:
                terminate_process_tree(process.pid)
                process.wait()
            raise
    stdout = log.with_suffix('.stdout').read_bytes()
    stderr = log.with_suffix('.stderr').read_bytes()
    text = ANSI.sub('', (stdout + stderr).decode(errors='replace'))
    counts = re.findall(r'Finished\s+(\d+)\s+tests?', text)
    cases = re.findall(r'\[(?:OK|FAIL|SKIP|SUCCESS|ERROR)\]\s+([^\r\n]+)', text)
    return {'command': arguments, 'cwd': str(cwd), 'status': status,
            'seconds': round(time.monotonic() - started, 3),
            'peak_rss_mb': round(peak_rss / 1024, 1),
            'memory_metric': 'resident-or-physical-footprint' if DARWIN_USAGE else 'resident',
            'finished': list(map(int, counts)), 'cases': cases,
            'stdout': str(log.with_suffix('.stdout')),
            'stderr': str(log.with_suffix('.stderr'))}


def run_project(project, args):
    slug = project['repo'].replace('/', '__')
    source = WORK / 'odin' / slug
    directory = WORK / 'results' / slug
    directory.mkdir(parents=True, exist_ok=True)
    with project_lock(directory):
        run_locked(project, args, source, directory)


def run_locked(project, args, source, directory):
    slug = project['repo'].replace('/', '__')
    report_path = directory / 'report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    report.update(repo=project['repo'], commit=project['commit'])
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if revision != project['commit']:
        raise RuntimeError(f'{slug} has revision {revision}, expected {project["commit"]}')
    if project.get('patch'):
        patch = MANIFEST.parent / 'patches' / project['patch']
        applied = subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)],
                                 cwd=source, capture_output=True).returncode == 0
        if not applied:
            subprocess.run(['git', 'apply', str(patch)], cwd=source, check=True)
        report['preparation_patch'] = str(patch)
    environment = os.environ.copy()
    environment.setdefault('THOR_FRONTEND_ODIN', str(Path(args.odin).resolve()))
    environment.update({key: value.format(source=str(source), work=str(WORK), root=str(ROOT))
                        for key, value in project.get('environment', {}).items()})
    native = project.get('odin', args.odin)
    native = native.format(work=str(WORK), root=str(ROOT))
    native = str(Path(shutil.which(native) or native).resolve())
    environment['PATH'] = str(Path(native).parent) + os.pathsep + environment['PATH']
    report['odin'] = subprocess.check_output([native, 'version'], text=True).strip()
    report['thor'] = str(args.thor)

    def save():
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    def commands(stage, tree):
        results = []
        fixtures = fixture_overlay(project,stage,tree,source,directory) if stage != 'baseline' else None
        command_environment = environment.copy()
        if stage != 'baseline' and project.get('translate_runtime_commands'):
            proxy = WORK / 'tools' / 'runtime-command-proxy'
            proxy.mkdir(parents=True, exist_ok=True)
            launcher = proxy / 'odin'
            target = MANIFEST.with_name('odin_runtime_proxy.py')
            if not launcher.exists():
                launcher.symlink_to(target)
            target.chmod(0o755)
            command_environment.update(THOR_INTEGRATION_ODIN=native, THOR_INTEGRATION_THOR=str(args.thor))
            command_environment['PATH'] = str(proxy) + os.pathsep + command_environment['PATH']
        for index, recipe in enumerate(project['commands']):
            if recipe.get('skip_repeated_runtime'):
                previous = next((result for result in report.get(stage, []) if result['name'] == recipe['name']), None)
                results.append({**(previous or {'name':recipe['name'], 'status':'native-runtime-unavailable'}),
                    'runtime_note':recipe['runtime_note']})
                continue
            artifact = directory / f'{stage}-{index}-program'
            values = {'source': str(source), 'tree': str(tree), 'artifact': str(artifact),
                      'work': str(WORK), 'root': str(ROOT)}
            tokens = [value.format(**values) for value in recipe['args']]
            if tokens[0] == 'test':
                tokens += [f'-define:ODIN_TEST_JSON_REPORT={directory / f"{stage}-{index}-tests.json"}',
                           '-define:ODIN_TEST_RANDOM_SEED=582019']
            cwd = tree / recipe.get('cwd', '.').format(**values)
            if stage == 'baseline':
                invocation = [native, *tokens]
            else:
                baseline = next((r for r in report.get('baseline', []) if r['name'] == recipe['name']), None)
                if not usable_baseline(baseline, recipe):
                    results.append({'name': recipe['name'], 'status': 'baseline-unavailable'})
                    continue
                translation = execute([str(args.thor), 'migrate', '--command', shlex.join([native, *tokens])],
                    cwd, directory / f'{stage}-{index}-translate', environment, args.timeout)
                results.append({'name': recipe['name'] + '-translation', **translation})
                if translation['status']:
                    continue
                lines = Path(translation['stdout']).read_text().splitlines()
                if len(lines) != 1:
                    raise RuntimeError(f'Expected one translated command, got {lines!r}')
                invocation = [str(args.thor), *shlex.split(lines[0])[1:]]
                if fixtures:
                    boundary = invocation.index('--') if '--' in invocation else len(invocation)
                    invocation[boundary:boundary] = ['--overlay',str(fixtures)]
            test_report = directory / f'{stage}-{index}-tests.json'
            test_report.unlink(missing_ok=True)
            result = execute(invocation, cwd, directory / f'{stage}-{index}', command_environment,
                             recipe.get('timeout', args.timeout))
            result['name'] = recipe['name']
            if tokens[0] == 'test' and test_report.exists():
                packet = json.loads(test_report.read_text())
                result['test_results'] = test_result_summary(packet)
            baseline = next((r for r in report.get('baseline', []) if r['name'] == recipe['name']), None)
            if stage != 'baseline' and baseline:
                result['matches_baseline'] = (
                    result['status'] == baseline['status']
                    and result['finished'] == baseline['finished'])
                if 'test_results' in baseline:
                    result['matches_baseline'] &= result.get('test_results') == baseline['test_results']
                if recipe.get('exact_output') and baseline['status'] == result['status'] == 0:
                    result['matches_baseline'] &= Path(result['stdout']).read_bytes() == Path(baseline['stdout']).read_bytes()
            results.append(result)
            print(f'{slug} {stage} {recipe["name"]} {result["status"]} {result["seconds"]}s {result["finished"]}', flush=True)
            report[stage] = results
            save()
        return results

    if args.stage == 'verify':
        vetted = WORK / 'vetted' / slug
        if not vetted.exists():
            raise RuntimeError(f'{slug} has no vetted copy to verify')
        report['vetted'] = commands('vetted', vetted)
        save()
        return
    if args.stage in ('all', 'baseline'):
        if args.fresh or 'baseline' not in report:
            report['baseline'] = commands('baseline', source)
            save()
    if args.stage == 'baseline':
        return
    tree = WORK / 'migrated' / slug
    if not tree.exists():
        tree.parent.mkdir(parents=True, exist_ok=True)
        invocation = [str(args.thor), 'migrate', str(source), '--output-dir', str(tree.parent), '--odin', native]
        for index, recipe in enumerate(project['commands']):
            baseline = next((r for r in report.get('baseline', []) if r['name'] == recipe['name']), None)
            if not usable_baseline(baseline, recipe):
                continue
            if project.get('initial_commands') and recipe['name'] not in project['initial_commands']:
                continue
            values = {'tree': str(source), 'source': str(source), 'work': str(WORK), 'root': str(ROOT),
                      'artifact': str(directory / f'migration-{index}-program')}
            tokens = [value.format(**values) for value in recipe['args']]
            invocation += ['--command', shlex.join([native, *tokens])]
        report['migration'] = execute(invocation, source, directory / 'migration', environment, args.timeout)
        save()
    if not tree.exists():
        return
    if args.stage in ('all', 'migrated'):
        report['migrated'] = commands('migrated', tree)
        save()
    if args.stage in ('all', 'vetted'):
        vetted = WORK / 'vetted' / slug
        if not vetted.exists():
            shutil.copytree(tree, vetted, ignore=shutil.ignore_patterns('.thor-cache', '.thor-build'))
        vet_results = []
        for index, recipe in enumerate(project.get('vet', [])):
            values = {'tree': str(vetted), 'source': str(source), 'work': str(WORK), 'root': str(ROOT)}
            tokens = [value.format(**values) for value in recipe]
            matching = vet_baselines(tokens, project['commands'], values)
            if matching and not any(usable_baseline(next((baseline for baseline in report.get('baseline', [])
                if baseline['name'] == command['name']), None), command) for command in matching):
                vet_results.append({'status':'baseline-unavailable', 'entry':tokens[0]})
                report['vet'] = vet_results
                save()
                continue
            vet_results.append(execute([str(args.thor), 'vet', *tokens, '--apply', '--odin-compatible', '--odin', native],
                vetted, directory / f'vet-{index}', environment, args.timeout))
            report['vet'] = vet_results
            save()
        report['vetted'] = commands('vetted', vetted)
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects', default='.*')
    parser.add_argument('--stage', choices=['all', 'baseline', 'migrated', 'vetted', 'verify'], default='all')
    parser.add_argument('--thor', type=Path, default=ROOT / 'rust/target/release/thor-rust')
    parser.add_argument('--odin', default=shutil.which('odin'))
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--fresh', action='store_true')
    args = parser.parse_args()
    args.thor = args.thor.resolve()
    projects = [p for p in json.loads(MANIFEST.read_text()) if re.search(args.projects, p['repo'])]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(lambda project: run_project(project, args), projects))


if __name__ == '__main__':
    main()

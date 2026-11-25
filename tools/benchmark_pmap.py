#!/usr/bin/env python3
"""Compare pmap with native Odin threads/pools using identical, checked workloads.

Sources and binaries stay in a temporary directory unless --artifacts is given.
Only one benchmark process runs at a time. Compilation and verification are not
included in the reported mapping times. No runtime/compiler sources are changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import re
import statistics
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FLAGS = ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']

HARNESS = r'''package main
import "core:fmt"
import "core:os"
import "core:strconv"
import "core:thread"
import "core:time"
@IMPORTS@
Job :: struct { count: int, seed: u64 }
calculate :: proc(job: Job) -> u64 { @CALCULATE@ }
runtime_callback: proc(Job)->u64 = calculate
Work :: struct { jobs: []Job, results: []u64, start, end: int }
run_work :: proc(work: ^Work) {
    for i in work.start..<work.end { work.results[i] = calculate(work.jobs[i]) }
}
entry :: proc(t: ^thread.Thread) { run_work((^Work)(t.data)) }
task_entry :: proc(task: thread.Task) { run_work((^Work)(task.data)) }
make_work :: proc(jobs: []Job, results: []u64, count: int) -> []Work {
    work := make([]Work, count)
    start := 0
    for &w, i in work {
        end := start + len(jobs)/count + int(i < len(jobs)%count)
        w = {jobs, results, start, end}
        start = end
    }
    return work
}
serial :: proc(jobs: []Job) -> []u64 {
    result := make([]u64, len(jobs))
    for job, i in jobs { result[i] = calculate(job) }
    return result
}
native_threads :: proc(jobs: []Job, workers: int) -> []u64 {
    result := make([]u64, len(jobs))
    work := make_work(jobs, result, workers)
    defer delete(work)
    handles := make([]^thread.Thread, workers-1)
    defer delete(handles)
    for &handle, i in handles {
        handle = thread.create(entry)
        if handle == nil { panic("thread creation failed") }
        handle.data = &work[i+1]
        thread.start(handle)
    }
    run_work(&work[0])
    for handle in handles { thread.join(handle); thread.destroy(handle) }
    return result
}
wait_pool :: proc(pool: ^thread.Pool) {
    for thread.pool_num_outstanding(pool) > 0 {
        if task, ok := thread.pool_pop_waiting(pool); ok { thread.pool_do_work(pool, task) }
        else { thread.yield() }
    }
    // Completed task records are owned by the pool until removed.
    for { _, ok := thread.pool_pop_done(pool); if !ok { break } }
}
pooled :: proc(jobs: []Job, workers: int, existing: ^thread.Pool = nil, per_job := false) -> []u64 {
    if workers == 1 { return serial(jobs) }
    result := make([]u64, len(jobs))
    work := make_work(jobs, result, per_job ? len(jobs) : workers)
    defer delete(work)
    local: thread.Pool
    pool := existing
    if pool == nil {
        pool = &local
        thread.pool_init(pool, context.allocator, workers-1)
        thread.pool_start(pool)
    }
    for &w in work { thread.pool_add_task(pool, context.allocator, task_entry, &w) }
    if existing == nil { thread.pool_finish(pool); thread.pool_destroy(pool) }
    else { wait_pool(pool) }
    return result
}
argument :: proc(index: int) -> int {
    value, ok := strconv.parse_int(os.args[index])
    if !ok { panic("invalid argument") }
    return value
}
main :: proc() {
    if len(os.args) != 7 { panic("mode jobs work-per-job workers rounds skew") }
    mode := os.args[1]
    count, amount, workers, rounds, skew := argument(2), argument(3), argument(4), argument(5), argument(6)
    if count < 1 || workers < 1 || workers > count || rounds < 1 { panic("invalid counts") }
    jobs := make([]Job, count)
    defer delete(jobs)
    for &job, i in jobs { job = {amount, u64(i+1)*7919} }
    if skew != 0 {
        for &job, i in jobs { job.count = (i < count/10) ? amount*10 : 0 }
    }
    // Deterministic seed per job makes every output exactly comparable,
    // regardless of scheduling, worker count, or the ambient RNG stream.
    expected := serial(jobs)
    defer delete(expected)
    persistent: thread.Pool
    if mode == "reuse" && workers > 1 {
        thread.pool_init(&persistent, context.allocator, workers-1)
        thread.pool_start(&persistent)
    }
    defer if mode == "reuse" && workers > 1 {
        thread.pool_finish(&persistent); thread.pool_destroy(&persistent)
    }
    total_ns: i64
    checksum: u64
    for _ in 0..<rounds {
        started := time.tick_now()
        result: []u64
        switch mode {
        case "serial": result = serial(jobs)
        case "threads": result = native_threads(jobs, workers)
        case "pool": result = pooled(jobs, workers)
        case "reuse": result = pooled(jobs, workers, &persistent)
        case "dynamic": result = pooled(jobs, workers, per_job=true)
        @PMAP@
        case: panic("unknown mode")
        }
        total_ns += i64(time.tick_since(started))
        if len(result) != len(expected) { panic("mapping changed the result length") }
        for value, i in result {
            if value != expected[i] { panic("mapping changed an output") }
            checksum += value
        }
        // Both APIs return caller-owned results. Destruction, like validation,
        // is outside the timer; result allocation is inside the timer.
        delete(result)
    }
    fmt.printf("%d %d\n", total_ns, checksum)
}
'''

CALCULATIONS = {
    'hilo': 'rand.reset(job.seed); wins: u64; for _ in 0..<job.count { if game.simulate_game() { wins += 1 } }; return wins',
    'cheap': 'return (job.seed*6364136223846793005 + 1442695040888963407) ~ (job.seed >> 17)',
}


def command(argv, **kwargs):
    result = subprocess.run([str(a) for a in argv], text=True, capture_output=True, timeout=180, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{argv}\n{result.stdout}\n{result.stderr}')
    return result.stdout


def game_source(hilo, pmap_project):
    def body(path):
        text = path.read_text()
        return text[text.index('NUM_STACKS ::'):text.index('Batch ::')]
    source = body(hilo)
    if source != body(pmap_project):
        raise ValueError('Simulation kernels differ; review before benchmarking')
    # This shared kernel is ordinary Odin in BOTH binaries. It removes Thor
    # purity/lowering differences from the scheduler comparison.
    source = re.sub(r'\bfn\(', 'proc(', source).replace(':>', ':=')
    source = re.sub(r'\n[ \t]+(?=: drawn_card)', ' ', source)
    return 'package game\nimport "core:fmt"\nimport "core:math/rand"\n'+source


def build(folder, kernel, thor, odin, game, flags, native_pmap=False):
    programs = {}
    logs = {}
    for engine in ('odin', 'thor'):
        directory = folder/f'{kernel}-{engine}'
        directory.mkdir(parents=True, exist_ok=True)
        if kernel == 'hilo':
            (directory/'game').mkdir(exist_ok=True)
            (directory/'game/game.odin').write_text(game)
        imports = ('import "game"\nimport "core:math/rand"' if kernel == 'hilo' else '')
        if engine == 'thor': imports += '\nimport "thor:parallel"'
        if native_pmap: imports += '\nimport engine "thor:parallel/native"\nimport "base:runtime"'
        source = HARNESS.replace('@IMPORTS@', imports).replace('@CALCULATE@', CALCULATIONS[kernel])
        mapping = ('case "pmap": result = parallel.pmap(jobs, calculate, workers=workers)\n'
                   'case "runtime": result = parallel.pmap(jobs, runtime_callback, workers=workers)') if engine == 'thor' else ''
        if native_pmap:
            mapping += '\ncase "generic": result = engine.map_values(jobs, calculate, u64, workers, context.allocator, runtime.heap_allocator(), #location(), false)'
            mapping += '\ncase "specialized": result = engine.map_values_static(jobs, calculate, u64, workers, context.allocator, runtime.heap_allocator(), #location(), false)'
        source = source.replace('@PMAP@', mapping)
        entry = directory/('main.thor' if engine == 'thor' else 'main.odin')
        entry.write_text(source)
        executable = directory/('benchmark.exe' if os.name == 'nt' else 'benchmark')
        argv = [thor if engine == 'thor' else odin, 'build', entry if engine == 'thor' else directory, *flags, '-out:'+str(executable)]
        if engine == 'thor': argv += ['--output-dir', directory/'generated']
        elif native_pmap: argv += ['-collection:thor='+str(ROOT/'runtime')]
        logs[engine] = command(argv, cwd=directory)
        programs[kernel, engine] = executable
    return programs, logs


def sample(program, mode, jobs, work, workers, rounds, skew):
    started = time.perf_counter()
    output = command([program, mode, jobs, work, workers, rounds, skew])
    elapsed_ns, checksum = map(int, output.strip().split())
    return {'ns_per_call': elapsed_ns/rounds, 'checksum': checksum, 'process_seconds': time.perf_counter()-started}


def snapshots(options, folder, flags):
    """Whole-process confirmation against normalized copies of the actual projects."""
    programs = {}
    for name, path, native in [('hilo-odin',options.hilo,True), ('hilo-thor',options.hilo,False),
                               ('pmap-thor',options.pmap_project,False),
                               ('pmap-runtime-thor',options.pmap_project,False)]:
        directory=folder/name
        directory.mkdir(exist_ok=True)
        source=re.sub(r'NUM_TRIALS\s*::\s*[\d_]+', 'NUM_TRIALS :: 1_000_000', path.read_text())
        if native:
            source=re.sub(r'\bfn\(', 'proc(', source).replace(':>', ':=')
            source=re.sub(r'\n[ \t]+(?=: drawn_card)', ' ', source)
        if name=='pmap-runtime-thor':
            call='pmap(trial_counts, simulate_batch)'
            if source.count(call)!=1:raise ValueError('Review pmap snapshot callback control')
            source=source.replace(call,'pmap(trial_counts, benchmark_runtime_callback)')
            source+='\nbenchmark_runtime_callback:proc(int)->int=simulate_batch\n'
        entry=directory/('main.odin' if native else 'main.thor')
        entry.write_text(source)
        executable=directory/('benchmark.exe' if os.name=='nt' else 'benchmark')
        argv=[options.odin if native else options.thor.resolve(),'build',directory if native else entry,
              *flags,'-out:'+str(executable)]
        if not native:argv+=['--output-dir',directory/'generated']
        command(argv,cwd=directory)
        programs[name]=executable
    values={name:[] for name in programs}
    rng=random.Random(7919)
    for index in range(options.samples+1):
        order=list(programs);rng.shuffle(order)
        for name in order:
            started=time.perf_counter()
            output=command([programs[name]])
            seconds=time.perf_counter()-started
            match=re.search(r'Wins:\s*([\d,]+)\s*/\s*([\d,]+)',output)
            if not match or int(match[2].replace(',',''))!=1_000_000:raise RuntimeError(output)
            wins=int(match[1].replace(',',''))
            if not 0<=wins<=1_000_000:raise RuntimeError(output)
            if index:values[name].append({'seconds':seconds,'wins':wins})
    for name, samples in values.items():print('snapshot',name,statistics.median(v['seconds'] for v in samples),'s',flush=True)
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hilo', type=Path, required=True, help='traditional hilo.thor source')
    parser.add_argument('--pmap-project', type=Path, required=True, help='hilo_test.thor source')
    parser.add_argument('--thor', type=Path, default=ROOT/'thor')
    parser.add_argument('--odin', default='odin')
    parser.add_argument('--samples', type=int, default=7)
    parser.add_argument('--compiler-threads', type=int, help='use this Odin compiler worker count for both builds')
    parser.add_argument('--workers', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--artifacts', type=Path)
    parser.add_argument('--save', type=Path)
    parser.add_argument('--quick', action='store_true', help='short calibration run')
    parser.add_argument('--checked', action='store_true', help='retain assertions and bounds/type checks')
    parser.add_argument('--case', action='append', help='run only this named controlled case; repeatable')
    parser.add_argument('--snapshots', action='store_true', help='also compare actual projects normalized to one million trials')
    parser.add_argument('--native-pmap', action='store_true', help='also call the native pmap engine directly from both frontends')
    options = parser.parse_args()
    if options.samples < 1 or options.workers < 1: parser.error('samples and workers must be positive')
    if options.compiler_threads is not None and options.compiler_threads<1:parser.error('compiler threads must be positive')
    flags=['-o:speed'] if options.checked else FLAGS
    if options.compiler_threads is not None:flags=[*flags,f'-thread-count:{options.compiler_threads}']
    kernel = game_source(options.hilo, options.pmap_project)
    cases = [
        ('hilo-balanced', 'hilo', options.workers, max(1,1_000_000//options.workers), options.workers, 1, 0),
        ('hilo-one-worker', 'hilo', 1, 100_000, 1, 1, 0),
        ('hilo-many-jobs', 'hilo', options.workers*10, max(1,100_000//options.workers), options.workers, 1, 0),
        ('hilo-skewed', 'hilo', options.workers*10, max(1,100_000//options.workers), options.workers, 1, 1),
        ('cheap-startup', 'cheap', options.workers, 0, options.workers, 100, 0),
        ('cheap-many', 'cheap', 1_000_000, 0, options.workers, 10, 0),
        ('cheap-one-worker', 'cheap', 1_000_000, 0, 1, 10, 0),
    ]
    if options.quick:
        cases = [(name, k, j, min(w, 1000), n, min(r, 3), s) for name,k,j,w,n,r,s in cases]
    if options.case:
        known={case[0] for case in cases}
        if set(options.case)-known:parser.error('unknown case')
        cases=[case for case in cases if case[0] in options.case]
    with tempfile.TemporaryDirectory(prefix='thor-pmap-benchmark-') as temporary:
        folder = options.artifacts.resolve() if options.artifacts else Path(temporary)
        folder.mkdir(parents=True, exist_ok=True)
        programs = {}
        for k in dict.fromkeys(case[1] for case in cases):
            built, _ = build(folder, k, options.thor.resolve(), options.odin, kernel, flags, options.native_pmap)
            programs.update(built)
        results = []
        rng = random.Random(7919)
        for name,k,j,w,n,r,s in cases:
            modes = [('thor','pmap'), ('thor','runtime'), ('odin','threads'), ('odin','pool'), ('odin','reuse')]
            if k == 'hilo' and j > 10: modes.append(('odin','dynamic'))
            # Same manual scheduler through Thor proves a language/codegen
            # control, distinct from differences between scheduling APIs.
            modes.append(('thor','threads'))
            if options.native_pmap:modes.extend([('odin','generic'),('thor','generic'),('odin','specialized')])
            cells = {(e,m):[] for e,m in modes}
            # Warm every binary/mode before interleaving measured samples.
            for e,m in modes: sample(programs[k,e], m,j,w,n,1,s)
            for _ in range(options.samples):
                order = list(modes); rng.shuffle(order)
                for e,m in order: cells[e,m].append(sample(programs[k,e],m,j,w,n,r,s))
            checksums = {v['checksum'] for values in cells.values() for v in values}
            if len(checksums) != 1: raise RuntimeError(f'{name}: checksums differ')
            for (engine, mode), values in cells.items():
                times = [value['ns_per_call'] for value in values]
                row = {'case': name, 'kernel': k, 'engine': engine, 'mode': mode,
                       'jobs': j, 'work_per_job': w, 'workers': n, 'rounds': r, 'skew': bool(s),
                       'median_ns': statistics.median(times), 'min_ns': min(times), 'max_ns': max(times), 'samples': values}
                results.append(row)
                print(f'{name:20} {engine:5} {mode:8} {row["median_ns"]/1e6:10.4f} ms', flush=True)
        report = {'platform': platform.platform(), 'machine': platform.machine(),
                  'odin': command([options.odin,'version']).strip(), 'thor': command([options.thor,'version']).strip(),
                  'flags': flags, 'samples': options.samples,
                  'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (options.hilo,options.pmap_project)},
                  'kernel_sha256': hashlib.sha256(kernel.encode()).hexdigest(), 'results': results}
        if options.snapshots:report['snapshots']=snapshots(options,folder,flags)
        if options.save:
            options.save.parent.mkdir(parents=True,exist_ok=True)
            options.save.write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()

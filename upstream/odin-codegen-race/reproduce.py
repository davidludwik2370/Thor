#!/usr/bin/env python3
"""Capture native Odin codegen results and narrowly classify name-cache races."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odin', default='odin')
    parser.add_argument('--odin-root', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--single-module', action='store_true')
    parser.add_argument('--thread-count', type=int)
    parser.add_argument('--expect-name-cache-race', choices=['yes', 'no'])
    options = parser.parse_args()
    if options.runs < 1 or options.thread_count is not None and options.thread_count < 1:
        parser.error('run and thread counts must be positive')
    output = options.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error('--output-dir must be empty; existing evidence will not be overwritten')
    env = dict(os.environ)
    if options.odin_root:
        env['ODIN_ROOT'] = str(options.odin_root.resolve())
    flags = ['-use-single-module'] if options.single_module else []
    if options.thread_count is not None:
        flags.append(f'-thread-count:{options.thread_count}')
    source = Path(__file__).resolve().parent
    results = []
    for index in range(options.runs):
        directory = output / f'run-{index}'
        directory.mkdir()
        command = [options.odin, 'build', str(source), *flags,
                   '-build-mode:llvm-ir', f'-out:{directory}', '-json-errors']
        try:
            result = subprocess.run(command, env=env, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            print('Odin compilation timed out; evidence directory:', directory)
            return 1
        (output / f'run-{index}.stderr').write_bytes(result.stderr)
        (output / f'run-{index}.stdout').write_bytes(result.stdout)
        reports = result.stderr.decode(errors='replace').split('WARNING: ThreadSanitizer:')[1:]
        # Count the first access stack only. A report about some other reader
        # can also mention lb_get_entity_name in its previous-writer stack.
        name_reports = sum(bool(re.search(r'^    #0 lb_get_entity_name\(',
                           report.split('  Previous ')[0], re.M)) for report in reports)
        entry = {'run': index, 'status': result.returncode, 'tsan_reports': len(reports),
                 'name_cache_read_reports': name_reports,
                 'ir_files': len(list(directory.glob('*.ll'))), 'command': command}
        results.append(entry)
        (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps({k: v for k, v in entry.items() if k != 'command'}), flush=True)
    if options.expect_name_cache_race is not None:
        # This checks one race family, not a completely clean TSan build.
        expected = options.expect_name_cache_race == 'yes'
        return 0 if all(r['ir_files'] > 0 and bool(r['name_cache_read_reports']) == expected
                        for r in results) else 1
    return 0 if all(r['status'] == 0 for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

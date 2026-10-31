#!/usr/bin/env python3
"""Run Thor's VS Code integration tests in a temporary editor profile."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code', default=shutil.which('code') or '/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code')
    parser.add_argument('--standalone-run', action='store_true', help='test running a file with no workspace folder')
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='thor-vscode-') as directory:
        temporary = Path(directory)
        result_file = temporary / 'result.json'
        target=ROOT
        suite='vscode.js'
        if options.standalone_run:
            target=temporary/'standalone file.thor'
            target.write_text('package main\nimport "core:os"\nmain::proc(){assert(os.write_entire_file("standalone-run.txt", []u8{\'o\',\'k\'}) == nil)}\n')
            suite='run-vscode.js'
        args = [options.code, '--wait', '--new-window', '--user-data-dir', str(temporary / 'profile'), '--extensions-dir', str(temporary / 'extensions'),
                '--disable-extensions', '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust',
                f'--extensionDevelopmentPath={ROOT / "editors/vscode"}',
                f'--extensionTestsPath={ROOT / "editors/vscode/test" / suite}', str(target)]
        completed = subprocess.run(args, env={**os.environ, 'THOR_VSCODE_TEST_RESULT': str(result_file),
                                              'THOR_VSCODE_TEST_SOURCE':str(target)}, timeout=180)
        if not result_file.exists():
            raise SystemExit(f'VS Code did not report test results (exit {completed.returncode})')
        result = json.loads(result_file.read_text())
        print(json.dumps(result, indent=2))
        if not result.get('success'):
            for log in (temporary / 'profile' / 'logs').rglob('*Thor*.log'):
                print(log.read_text(errors='replace')[-10000:])
        return 0 if result.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())

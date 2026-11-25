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
    parser.add_argument('--timeout', type=float, default=180, help='maximum editor test duration in seconds')
    parser.add_argument('--standalone-run', action='store_true', help='test running a file with no workspace folder')
    parser.add_argument('--scope', choices=['full','slice-access','auto-closing','hover-size'], default='full', help='select the full editor suite or focused checks')
    parser.add_argument('--interactive-typing', action='store_true', help='also exercise the suggestion widget; requires a foreground editor window')
    parser.add_argument('--trace', action='store_true', help='record verbose language-client protocol messages')
    parser.add_argument('--artifacts', type=Path, help='retain test results and isolated editor logs in this directory')
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='thor-vscode-') as directory:
        temporary = Path(directory)
        result_file = temporary / 'result.json'
        compiler = os.environ.get('THOR_TEST_COMPILER')
        if compiler or options.trace:
            settings = temporary / 'profile' / 'User' / 'settings.json'
            settings.parent.mkdir(parents=True)
            values={'tls.compiler.path': str(Path(compiler).resolve())} if compiler else {}
            if options.trace: values['tls.trace.server']='verbose'
            settings.write_text(json.dumps(values))
        # Keep workspace indexing within the editor fixture. Integration clones
        # and generated SDK copies elsewhere in the repository are unrelated.
        target=ROOT/'demo'
        suite='vscode.js'
        if options.scope == 'hover-size':
            target=temporary/'hover-size-workspace'
            target.mkdir()
            suite='hover-size-vscode.js'
        if options.scope == 'auto-closing':
            suite='auto-closing-vscode.js'
        if options.standalone_run:
            target=temporary/'standalone file.thor'
            target.write_text('package main\nimport "core:os"\nmain::proc(){err:=os.write_entire_file("standalone-run.txt", []u8{\'o\',\'k\'});assert(err == nil)}\n')
            suite='run-vscode.js'
        args = [options.code, '--wait', '--new-window', '--user-data-dir', str(temporary / 'profile'), '--extensions-dir', str(temporary / 'extensions'),
                '--disable-extensions', '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust',
                f'--extensionDevelopmentPath={ROOT / "editors/vscode"}',
                f'--extensionTestsPath={ROOT / "editors/vscode/test" / suite}', str(target)]
        debug_port=os.environ.get('THOR_VSCODE_DEBUG_PORT')
        if debug_port:
            args.extend(['--remote-debugging-address=127.0.0.1',f'--remote-debugging-port={int(debug_port)}'])
        completed = subprocess.run(args, env={**os.environ, 'THOR_VSCODE_TEST_RESULT': str(result_file),
                                              'THOR_VSCODE_TEST_SCOPE':options.scope,
                                              'THOR_VSCODE_INTERACTIVE_TYPING':'1' if options.interactive_typing else '0',
                                              'THOR_VSCODE_TEST_SOURCE':str(target)}, timeout=options.timeout)
        if not result_file.exists():
            raise SystemExit(f'VS Code did not report test results (exit {completed.returncode})')
        result = json.loads(result_file.read_text())
        if options.artifacts:
            options.artifacts.mkdir(parents=True,exist_ok=True)
            shutil.copy2(result_file,options.artifacts/'result.json')
            logs=temporary/'profile'/'logs'
            if logs.is_dir():shutil.copytree(logs,options.artifacts/'logs',dirs_exist_ok=True)
        print(json.dumps(result, indent=2))
        if not result.get('success'):
            for log in (temporary / 'profile' / 'logs').rglob('*Thor*.log'):
                print(log.read_text(errors='replace')[-10000:])
        return 0 if result.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())

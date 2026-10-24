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
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='thor-vscode-') as directory:
        temporary = Path(directory)
        result_file = temporary / 'result.json'
        args = [options.code, '--wait', '--new-window', '--user-data-dir', str(temporary / 'profile'), '--extensions-dir', str(temporary / 'extensions'),
                '--disable-extensions', '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust',
                f'--extensionDevelopmentPath={ROOT / "editors/vscode"}',
                f'--extensionTestsPath={ROOT / "editors/vscode/test/vscode.js"}', str(ROOT)]
        completed = subprocess.run(args, env={**os.environ, 'THOR_VSCODE_TEST_RESULT': str(result_file)}, timeout=90)
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

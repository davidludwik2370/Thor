#!/usr/bin/env python3
"""Create a mkthor project and exercise real VS Code/CodeLLDB debugging."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code',default=shutil.which('code') or '/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code')
    parser.add_argument('--codelldb',type=Path)
    options=parser.parse_args()
    matches=list((Path.home()/'.vscode/extensions').glob('vadimcn.vscode-lldb-*'))
    backend=options.codelldb or (max(matches,key=lambda p:p.stat().st_mtime) if matches else None)
    if not backend:parser.error('CodeLLDB is required; pass --codelldb or install its VS Code extension')
    with tempfile.TemporaryDirectory(prefix='thor-vscode-debug-') as temporary:
        temporary=Path(temporary)
        subprocess.run([str(ROOT/'mkthor'),'debug_project','--parent',str(temporary),'--no-open',
                        '--template',str(ROOT/'tests/support/project_debug.thor')],check=True)
        (temporary/'debug_project/helper.thor').write_text('''package main
import "core:os"
confirm_run::proc(){when !ODIN_DEBUG {assert(os.write_entire_file("normal-run.txt", []u8{'o','k'}) == nil)}}
''')
        extensions=temporary/'extensions'
        extensions.mkdir()
        (extensions/backend.name).symlink_to(backend,target_is_directory=True)
        result_file=temporary/'result.json'
        args=[options.code,'--wait','--new-window','--user-data-dir',str(temporary/'profile'),
            '--extensions-dir',str(extensions),'--skip-welcome','--skip-release-notes','--disable-workspace-trust',
            f'--extensionDevelopmentPath={ROOT/"editors/vscode"}',
            f'--extensionTestsPath={ROOT/"editors/vscode/test/debug-vscode.js"}',str(temporary/'debug_project')]
        try:
            completed=subprocess.run(args,env={**os.environ,'THOR_VSCODE_TEST_RESULT':str(result_file)},timeout=180)
            if not result_file.exists():
                raise RuntimeError(f'VS Code did not report results (exit {completed.returncode})')
            result=json.loads(result_file.read_text())
            print(json.dumps(result,indent=2))
            if not result.get('success'):
                for log in (temporary/'profile/logs').rglob('*.log'):
                    contents=log.read_text(errors='replace')
                    if any(term in contents for term in ['Thor','thor','CodeLLDB']):
                        print(str(log),contents[-7000:])
            return 0 if result.get('success') else 1
        except Exception:
            for log in (temporary/'profile/logs').rglob('*.log'):
                print(str(log),log.read_text(errors='replace')[-5000:])
            raise


if __name__=='__main__':
    sys.exit(main())

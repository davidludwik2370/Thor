"""Create a minimal Thor project; mkthor is a convenience wrapper around new."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = ROOT / 'templates/main.thor'


def project_files(name, starter, odin=None, *, gitignore=False):
    binary = 'debug.exe' if os.name == 'nt' else 'debug'
    generated = '${workspaceFolder}/.vscode/bin/generated'
    command = str(ROOT / 'thor')
    native = ['--odin', odin] if odin else []
    files = {
        name + '.thor': starter,
        '.vscode/tasks.json': {'version': '2.0.0', 'tasks': [
            {'label': 'build-thor-debug', 'type': 'process', 'command': command,
             'args': ['build', '.', '-debug', '-o:none', '--output-dir', generated,
                      '-out:${workspaceFolder}/.vscode/bin/' + binary, *native],
             'options': {'cwd': '${workspaceFolder}'}, 'problemMatcher': []}]},
        '.vscode/launch.json': {'version': '0.2.0', 'configurations': [{
            'name': 'Debug Thor (LLDB)', 'type': 'thor-lldb', 'request': 'launch',
            'program': '${workspaceFolder}/.vscode/bin/' + binary,
            'manifest': generated + '/thor-manifest.json',
            'cwd': '${workspaceFolder}', 'args': [], 'preLaunchTask': 'build-thor-debug'}]},
    }
    if gitignore:
        files['.gitignore'] = (f'.vscode/bin/\n.thor-build/\n.thor-rust-build/\n.thor-cache/\n*.dSYM/\n'
                               f'/{name}\n/{name}.exe\n/{name}.app/\n')
    return {path: content if isinstance(content, (str, bytes)) else json.dumps(content, indent=2) + '\n'
            for path, content in files.items()}


def open_project(project):
    code = shutil.which('code')
    if code:
        argv = [code, '--new-window', str(project), str(project / (project.name + '.thor'))]
    elif sys.platform == 'darwin':
        argv = ['open', '-a', 'Visual Studio Code', str(project)]
    else:
        print(f'Open {project} in VS Code to run or debug it.')
        return
    try:
        result = subprocess.run(argv, check=False)
        if result.returncode:
            print(f'Project created, but VS Code exited with status {result.returncode}.', file=sys.stderr)
    except OSError as error:
        print(f'Project created, but VS Code could not open: {error}', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='thor new', add_help=False, description=__doc__)
    parser.add_argument('name', help='new project folder and source filename')
    parser.add_argument('--parent', type=Path, default=Path.cwd(), help='parent directory (default: current directory)')
    opening = parser.add_mutually_exclusive_group()
    opening.add_argument('--open', action='store_true', help='open the new project in VS Code')
    opening.add_argument('--no-open', action='store_true', help='create without opening VS Code (the default)')
    parser.add_argument('--template', type=Path, default=DEFAULT_TEMPLATE, help='starter source (default: templates/main.thor in the Thor installation)')
    parser.add_argument('--gitignore', action='store_true', help='also create a .gitignore for build output and caches')
    parser.add_argument('--odin', help='optional Odin executable for the debug build task')
    options = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]*', options.name):
        parser.error('project name must contain only letters, digits, underscores or hyphens')
    project = options.parent.expanduser().resolve() / options.name
    if project.exists() or project.is_symlink():
        parser.error(f'project already exists: {project}')
    try:
        starter = options.template.expanduser().read_bytes()
        starter.decode('utf-8')
    except (OSError, UnicodeError) as error:
        parser.error(f'cannot read UTF-8 template {options.template}: {error}')
    odin = (shutil.which(options.odin) or options.odin) if options.odin else None
    if odin and (Path(odin).is_absolute() or Path(odin).parent != Path('.')):
        odin = os.path.abspath(os.path.expanduser(odin))
    files = project_files(options.name, starter, odin, gitignore=options.gitignore)
    try:
        project.parent.mkdir(parents=True, exist_ok=True)
        project.mkdir()
        for name, content in files.items():
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(content.encode('utf-8') if isinstance(content, str) else content)
    except OSError as error:
        print(f'thor new: {error}', file=sys.stderr)
        return 1
    print(f'Created {project} with {options.name}.thor and VS Code run/debug configuration.')
    suffix = options.name.rsplit('_', 1)[-1].lower() if '_' in options.name else ''
    if suffix in {'windows', 'darwin', 'linux', 'freebsd', 'openbsd', 'netbsd', 'dragonfly', 'haiku',
                  'wasi', 'js', 'freestanding', 'essence', 'orca', '386', 'amd64', 'arm32', 'arm64',
                  'wasm32', 'wasm64', 'wasm64p32', 'riscv32', 'riscv64', 'test'}:
        print(f'Warning: {options.name}.thor retains Odin filename filtering; it may be excluded '
              'on other targets or outside test builds. Use a neutral project name for a portable starter.', file=sys.stderr)
    if options.open:
        open_project(project)
    return 0


def mkthor_main(argv=None):
    from .driver import main as thor_main
    argv = list(sys.argv[1:] if argv is None else argv)
    opening = [] if '--open' in argv or '--no-open' in argv else ['--open']
    return thor_main(['new', '--parent', str(Path.home() / 'Code/Thor'), *opening, *argv])

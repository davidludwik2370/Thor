"""Create a Thor project with local TLS and source-mapped CodeLLDB debugging."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent

STARTER = '''package main

import "core:fmt" / println

double :: fn(value: int) -> int {
    result := value * 2
    return result
}

main :: proc() {
    // Set a breakpoint here, then press Cmd+Shift+R (F5 also works).
    numbers := [3, 6, 9]
    last := numbers[-1]

    // Step Into can enter double through the interpolation's generated helper.
    message := f"Last: {last}; doubled: {double(last)}"
    defer delete(message)

    println(message)
}
'''


def project_files(name, odin):
    root = str(ROOT)
    binary = 'debug.exe' if os.name == 'nt' else 'debug'
    generated = '${workspaceFolder}/.vscode/bin/generated'
    command = str(ROOT/'thor')
    args = ['build', '.', '-debug', '-o:none', '--output-dir', generated,
            '-out:${workspaceFolder}/.vscode/bin/'+binary]
    if odin:
        args += ['--odin', odin]
    settings = {'files.associations': {'*.thor': 'thor'},
        'tls.server.path': str(ROOT/'tls/bin'/('tls.exe' if os.name == 'nt' else 'tls')),
        'tls.compiler.path': command, '[thor]': {'editor.formatOnSave': False}}
    if odin:
        result = subprocess.run([odin, 'root'], capture_output=True, text=True)
        if result.returncode == 0:
            settings['tls.odinRoot'] = result.stdout.strip()
    files = {
        'main.thor': STARTER,
        '.gitignore': '.vscode/bin/\n.thor-build/\n.thor-cache/\n*.dSYM/\n',
        '.vscode/tasks.json': {'version': '2.0.0', 'tasks': [{
            'label': 'build-thor-debug', 'type': 'process', 'command': command,
            'args': args, 'options': {'cwd': '${workspaceFolder}'}, 'problemMatcher': []}]},
        '.vscode/launch.json': {'version': '0.2.0', 'configurations': [{
            'name': 'Debug Thor (LLDB)', 'type': 'thor-lldb', 'request': 'launch',
            'program': '${workspaceFolder}/.vscode/bin/'+binary,
            'manifest': generated+'/thor-manifest.json',
            'cwd': '${workspaceFolder}', 'args': [], 'preLaunchTask': 'build-thor-debug'}]},
        '.vscode/settings.json': settings,
        '.vscode/extensions.json': {'recommendations': ['vadimcn.vscode-lldb']},
        'README.md': f'''# {name}

Open `main.thor` in VS Code, set a breakpoint, and press **Cmd+Shift+R** on
macOS (**Ctrl+Shift+R** elsewhere), or **F5**. The build task runs Thor with
`-debug -o:none`; the Thor debug adapter maps breakpoints and stepping back to
the original files while CodeLLDB handles the executable.

Requires the locally installed **Thor Language** extension and **CodeLLDB**.
The project configuration points at the Thor checkout at `{root}`.

```sh
thor run .
thor build . -o:speed
```

Debug builds retain ordinary runtime `fn` calls so their bodies can be stepped
through. Required constants and explicit `#run` still execute during compilation.
Save edits before debugging; the pre-launch task rebuilds the executable and maps.
''',
    }
    return {name: content if isinstance(content,str) else json.dumps(content,indent=2)+'\n'
            for name,content in files.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', help='new project folder name')
    parser.add_argument('--parent', type=Path, default=Path.home()/'Code/Odin', help='parent directory (default: ~/Code/Odin)')
    parser.add_argument('--no-open', action='store_true', help='create the project without opening VS Code')
    parser.add_argument('--odin', default=os.environ.get('THOR_ODIN','odin'), help='Odin executable')
    options = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]*',options.name):
        parser.error('project name must contain only letters, digits, underscores or hyphens')
    project = options.parent.expanduser().resolve()/options.name
    if project.exists() or project.is_symlink():
        parser.error(f'project already exists: {project}')
    odin = shutil.which(options.odin)
    files = project_files(options.name, odin)
    try:
        project.parent.mkdir(parents=True,exist_ok=True)
        project.mkdir()
        for name,content in files.items():
            target = project/name
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('x',encoding='utf8') as stream:
                stream.write(content)
    except OSError as error:
        print(f'mkthor: {error}',file=sys.stderr)
        return 1
    print(f'Created {project} with main.thor and VS Code debugging configuration.')
    if not odin:
        print('Odin was not found on PATH; install it or configure the build task before compiling.',file=sys.stderr)
    if not options.no_open:
        if sys.platform == 'darwin':
            subprocess.run(['open','-a','Visual Studio Code',str(project)],check=False)
        elif (code := shutil.which('code')):
            subprocess.Popen([code,str(project)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:
            print(f'Open {project} in VS Code to start debugging.')
    return 0

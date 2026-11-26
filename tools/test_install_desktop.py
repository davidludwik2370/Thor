#!/usr/bin/env python3
"""Exercise an installed release in an isolated, real desktop editor session.

Run as the logged-in desktop user (Windows SSH requires an interactive scheduled
task). The test keeps logs but removes its private toolchain/editor directories.
On macOS, only editor processes retain the real HOME for Keychain access; a
backed-up discovery record is temporarily selected and restored afterward.
The user's VS Code windows and settings are left alone.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import shlex
import plistlib
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]


def owned_editor_pids(work, rows):
    """Include native debugger children, which can outlive a test Code window."""
    work = str(work)
    processes = {}
    owned = set()
    for row in rows.splitlines():
        parts = row.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid, parent, command = parts
        if not pid.isdigit() or not parent.isdigit():
            continue
        pid, parent = int(pid), int(parent)
        processes[pid] = parent
        if work not in command:
            continue
        def argument(flag, value):
            return re.search(r'(?:^|\s)'+re.escape(flag)+r'(?:=|\s+)'+re.escape(value)+r'(?=\s|$)', command)
        # Some Electron versions rewrite their displayed argv with switches
        # first and values afterward. Require both exact disposable paths in
        # that form; a project argument under our root is not ownership proof.
        reordered = (re.search(r'(?:^|\s)--user-data-dir(?=\s+--)', command)
            and re.search(r'(?:^|\s)--extensions-dir(?=\s+--)', command)
            and re.search(r'(?:^|\s)'+re.escape(work+'/home/code-data')+r'(?=\s|$)', command)
            and re.search(r'(?:^|\s)'+re.escape(work+'/home/.vscode/extensions')+r'(?=\s|$)', command))
        debug = work+'/project/.vscode/bin/debug'
        if (argument('--user-data-dir', work+'/home/code-data') or reordered
                or argument('--database', work+'/home/code-data/Crashpad')
                or command.startswith(work+'/home/.vscode/extensions/') and '/adapter/codelldb ' in command
                or command == debug or command.startswith(debug+' ')):
            owned.add(pid)
    while True:
        children = {pid for pid, parent in processes.items() if parent in owned}
        if children <= owned:
            return sorted(owned)
        owned.update(children)


@contextmanager
def mac_editor_registration(private_home, recovery_root):
    """Mac VS Code's native document service needs the actual account HOME.

    Substitute only the discovery record while the isolated editor test runs;
    restore its original bytes, mode and timestamps; preserve concurrent edits.
    No original installed files or editor settings are changed.
    """
    target = Path.home()/'Library/Application Support/Thor/installation.json'
    if target.is_symlink():
        raise RuntimeError('Refusing to substitute a symlinked installation record')
    info = target.stat() if target.exists() else None
    before = target.read_bytes() if info is not None else None
    value = (private_home/'Library/Application Support/Thor/installation.json').read_bytes()
    recovery = Path(tempfile.mkdtemp(prefix='macos-record-recovery-',dir=recovery_root))
    (recovery/'metadata.json').write_text(json.dumps({'target':str(target),'existed':info is not None,
        'mode':info.st_mode & 0o777 if info else None,'atime_ns':info.st_atime_ns if info else None,
        'mtime_ns':info.st_mtime_ns if info else None}))
    if before is not None:
        (recovery/'original.json').write_bytes(before)
        (recovery/'original.json').chmod(0o600)
    target.parent.mkdir(parents=True, exist_ok=True)
    def replace(data):
        with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.thor-validation-',delete=False) as stream:
            staged=Path(stream.name)
            stream.write(data)
        staged.chmod(info.st_mode & 0o777 if info else 0o600)
        staged.replace(target)
    replace(value)
    try:
        yield
    finally:
        if not target.exists() or target.read_bytes()!=value:
            raise RuntimeError('Installation record changed during the test; concurrent changes were preserved. Original backup: '+str(recovery))
        if before is None:
            target.unlink()
        else:
            replace(before)
            os.utime(target,ns=(info.st_atime_ns,info.st_mtime_ns))
        shutil.rmtree(recovery)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--code', required=True, type=Path, help='native VS Code CLI')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'platform': os.sys.platform, 'checks': []}
    started = time.monotonic()
    def user_path():
        return subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
            "[Environment]::GetEnvironmentVariable('Path','User')"], text=True).strip()
    original_user_path = user_path() if os.name == 'nt' else None
    # macOS's default /var/folders temporary root can exceed Unix-domain socket
    # path limits once VS Code appends its IPC socket name. Use a short root.
    with tempfile.TemporaryDirectory(prefix='thor-desktop-', dir='/tmp' if os.sys.platform=='darwin' else None) as temporary:
        work = Path(temporary).resolve()
        home = work / 'home'
        home.mkdir()
        data = home / 'code-data'
        extensions = home / '.vscode/extensions'
        prefix = home / 'installed'
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                   APPDATA=str(home / 'AppData/Roaming'), LOCALAPPDATA=str(home / 'AppData/Local'),
                   XDG_CONFIG_HOME=str(home / '.config'), XDG_DATA_HOME=str(home / '.local/share'))
        for key in ('THOR_ROOT', 'THOR_ODIN', 'ODIN_ROOT', 'THOR_INSTALL_PREFIX', 'THOR_RELEASE_BASE',
                    'ELECTRON_RUN_AS_NODE', 'ZDOTDIR'):
            env.pop(key, None)
        if os.name != 'nt':
            env['SHELL'] = '/bin/zsh' if os.sys.platform == 'darwin' else '/bin/bash'
            env['PATH'] = '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
            if os.sys.platform != 'darwin':
                for name in ('.profile', '.bashrc'):
                    source = Path('/etc/skel') / name
                    if source.is_file():
                        shutil.copy2(source, home / name)
        defaults = data / 'User'
        defaults.mkdir(parents=True)
        seeded = {'settings.json': b'{"files.autoSave":"off","editor.fontSize":17}\n',
                  'keybindings.json': b'[{"key":"ctrl+r","command":"workbench.action.files.openFile","when":"editorLangId == python"}]\n'}
        for name, content in seeded.items():
            (defaults / name).write_bytes(content)
        launcher = [str(ROOT / 'thor-install')]
        if os.name == 'nt':
            launcher = ['cmd.exe', '/d', '/c', str(ROOT / 'thor-install.cmd')]
        common = ['--prefix', str(prefix)]
        code = args.code.resolve()
        if os.sys.platform=='darwin':
            # Even profile creation can access the Keychain. Keep native Code
            # in the real account HOME; only installer state uses private HOME.
            wrapper=work/'code-cli'
            wrapper.write_text('#!/bin/sh\nHOME='+shlex.quote(str(Path.home()))+'\nexport HOME\nexec '+shlex.quote(str(code))+' "$@"\n')
            wrapper.chmod(0o700)
            code=wrapper
        install = [*common, '--vscode', '--profile', 'Thor', '--path', '--code', str(code),
                   '--code-user-data', str(data), '--extensions-dir', str(extensions)]
        if args.bundle:
            install += ['--bundle', str(args.bundle.resolve(strict=True))]

        def run(command, label, timeout=600, child_env=env, cwd=ROOT):
            (output / 'stage.json').write_text(json.dumps({'stage': label}))
            command = list(map(str, command))
            shell = os.name == 'nt' and command[0].lower().endswith('.cmd')
            if shell:
                command = subprocess.list2cmdline(command)
            with (output / (label + '.log')).open('wb') as log:
                value = subprocess.run(command, shell=shell, cwd=cwd, env=child_env,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            if value.returncode:
                raise RuntimeError(label + ' failed; see retained log')

        def close_test_editor():
            # Match only this test's unique data directory; user windows have
            # different process arguments. No system-wide editor termination.
            if os.name == 'nt':
                script = "$needle='" + str(work).replace("'", "''") + "'; Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'Code.exe' -and $_.CommandLine -and $_.CommandLine.Contains($needle) -and -not $_.CommandLine.Contains('--type=')} | ForEach-Object {taskkill.exe /PID $_.ProcessId /T /F | Out-Null}"
                subprocess.run(['powershell.exe', '-NoProfile', '-Command', script], capture_output=True, timeout=30)
            else:
                def owned_roots():
                    rows = subprocess.check_output(['ps', '-axww', '-o', 'pid=,ppid=,command='], text=True)
                    return owned_editor_pids(work, rows)
                for pid in owned_roots():
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                deadline = time.monotonic() + 3
                while owned_roots() and time.monotonic() < deadline:
                    time.sleep(.1)
                for pid in owned_roots():
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            time.sleep(1)

        try:
            run([*launcher, *install], 'install')
            run([*launcher, *install], 'reinstall')
            run([*launcher, *common, '--doctor'], 'doctor')
            result['checks'].append('install-reinstall-doctor')
            if os.name != 'nt':
                for label, mode in [('login', '-lic'), ('interactive', '-ic')]:
                    run([env['SHELL'], mode, 'command -v thor; thor version; thor-install --doctor'], 'shell-' + label)
                    if str(prefix / 'bin/thor') not in (output / ('shell-' + label + '.log')).read_text():
                        raise RuntimeError('Ordinary shell did not select the installed command')
                result['checks'].append('ordinary-login-and-interactive-shell-path')
            else:
                updated = user_path()
                if str(prefix / 'bin').lower() not in [part.lower() for part in updated.split(';')]:
                    raise RuntimeError('User PATH does not contain the installed command directory')
                fresh = dict(env, PATH=updated + os.pathsep + env['PATH'])
                run(['cmd.exe', '/d', '/c', 'where thor && thor version'], 'ordinary-terminal', child_env=fresh, cwd=home)
                if str(prefix / 'bin').lower() not in (output / 'ordinary-terminal.log').read_text().lower():
                    raise RuntimeError('Ordinary Windows terminal did not find the installed command')
                result['checks'].append('ordinary-windows-terminal-path')
            fixture = work / 'project'
            fixture.mkdir()
            shutil.copy2(ROOT / 'tools/install-ubuntu/fixture.thor', fixture / 'editor.thor')
            shutil.copy2(ROOT / 'tools/install-ubuntu/odin_highlight.odin', fixture / 'odin_highlight.odin')
            thor = prefix / 'bin' / ('thor.cmd' if os.name == 'nt' else 'thor')
            compiler = [str(thor)] if os.name != 'nt' else ['cmd.exe', '/d', '/c', str(thor)]
            for label, flags in [('checked', []), ('unchecked', ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert'])]:
                run([*compiler, 'run', fixture, '-thread-count:2', *flags], label, cwd=fixture)
                if (fixture / 'ran.txt').read_text() != 'ok':
                    raise RuntimeError(label + ' runtime verdict failed')
                (fixture / 'ran.txt').unlink()
            result['checks'].append('checked-and-unchecked-execution')
            close_test_editor()
            verification = work / 'verification'
            shutil.copytree(ROOT / 'tools/install-ubuntu/verification-extension', verification)
            editor_env = dict(env, THOR_INSTALL_GUI_RESULT=str(output / 'editor-result.json'))
            if os.sys.platform.startswith('linux'):
                editor_env['DONT_PROMPT_WSL_INSTALL']='1'
            command=[args.code.resolve(), '--wait', '--new-window', '--profile', 'Thor', '--user-data-dir', data,
                 '--extensions-dir', extensions, '--disable-workspace-trust', '--skip-welcome', '--skip-release-notes',
                 '--extensionDevelopmentPath=' + str(verification),
                 '--extensionTestsPath=' + str(verification / 'test.js'), fixture]
            if os.sys.platform=='darwin':
                for parent in args.code.resolve().parents:
                    info_file=parent/'Info.plist'
                    if parent.name=='Contents' and info_file.is_file():
                        with info_file.open('rb') as stream:executable=plistlib.load(stream)['CFBundleExecutable']
                        native=parent/'MacOS'/executable
                        if native.is_file():command=[native,*command[2:]]
                        break
                editor_env['HOME']=str(Path.home())
                with mac_editor_registration(home,output):
                    try:
                        run(command,'editor',timeout=240,child_env=editor_env)
                    finally:
                        close_test_editor()
                result['checks'].append('original-installation-record-restored')
            else:
                run(command,'editor',timeout=240,child_env=editor_env)
            editor = json.loads((output / 'editor-result.json').read_text())
            if not editor.get('success'):
                raise RuntimeError('Installed editor check failed: ' + editor.get('error', 'unknown'))
            result['checks'].extend(editor['checks'])
            for name, content in seeded.items():
                if (defaults / name).read_bytes() != content:
                    raise RuntimeError('Default profile changed: ' + name)
            result['checks'].append('default-profile-preserved')
            run([*launcher, *common, '--uninstall'], 'uninstall')
            run([*launcher, *common, '--uninstall'], 'uninstall-again')
            if thor.exists() or not (fixture / 'editor.thor').exists():
                raise RuntimeError('Uninstall command/source preservation verdict failed')
            result['checks'].append('repeated-uninstall-project-preserved')
            if os.name == 'nt' and user_path() != original_user_path:
                raise RuntimeError('Uninstall did not restore the original user PATH')
            result['success'] = True
        except Exception as error:
            result['error'] = str(error)
        finally:
            for directory in (data, work/'editor-data'):
                logs = directory / 'logs'
                if logs.is_dir() and not result['success']:
                    shutil.copytree(logs, output / ('editor-startup-logs-'+directory.name), dirs_exist_ok=True)
            close_test_editor()
            try:
                run([*launcher, *common, '--uninstall'], 'cleanup-uninstall')
            except Exception as error:
                result['cleanup_error'] = str(error)
            # Keep the original verdict even if a late editor writer prevents
            # TemporaryDirectory's final removal. Cleanup must not hide it.
            result['seconds'] = round(time.monotonic() - started, 2)
            (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    result['seconds'] = round(time.monotonic() - started, 2)
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['success'] and not result.get('cleanup_error') else 1


if __name__ == '__main__':
    raise SystemExit(main())

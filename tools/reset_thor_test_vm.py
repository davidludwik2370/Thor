#!/usr/bin/env python3
"""Reset the authorized Ubuntu test VM, then exercise the normal installer CLI.

This is a test-VM utility, not the Thor uninstaller. It preserves source/projects,
VS Code itself, OS prerequisites and unrelated editor profiles/extensions.
Removed state is moved out of active locations into a dated recovery directory.
"""
import argparse
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time

IDS={'thor-local.thor-language','danielgavin.ols','vadimcn.vscode-lldb','aesl.odin'}

def read(path):
    return json.loads(path.read_text())

def owned_extension(value):
    return value.lower() in IDS

def package_name(name):
    return any(name.lower().startswith(identifier+'-') for identifier in IDS)

def profiles(storage):
    value=storage.get('userDataProfiles',[])
    return json.loads(value) if isinstance(value,str) else value

def without_path_block(text):
    start='# >>> thor-install PATH >>>';end='# <<< thor-install PATH <<<'
    if text.count(start)!=text.count(end):
        raise RuntimeError('Incomplete Thor PATH block; no reset was performed.')
    return re.sub(r'(?m)^'+re.escape(start)+r'\r?\n.*?^'+re.escape(end)+r'\r?\n?', '',text,flags=re.S)

def editor_processes():
    result=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:args=(entry/'cmdline').read_bytes().split(bytes([0]))
        except OSError:continue
        if not args or Path(os.fsdecode(args[0])).name!='code':continue
        if any(b'--type=' in arg or b'out/cli.js' in arg or b'--ms-enable-electron-run-as-node'==arg for arg in args):continue
        result.append(int(entry.name))
    return result

def validate(home,repo):
    marker=home/'.config/thor-validation/test-vm.json'
    if not marker.is_file() or read(marker)!={'schema':1,'purpose':'Thor install reset test','home':str(home)}:
        raise RuntimeError('This VM has not been authorized for the installation reset helper.')
    if repo.resolve()!=(home/'Thor').resolve():raise RuntimeError('This helper targets ~/Thor only.')
    for path in [home/'.config/Code',home/'.vscode/extensions',home/'.local/share/ols',home/'.cache/thor-validation']:
        if path.is_symlink() or not path.resolve().is_relative_to(home.resolve()):
            raise RuntimeError('Reset target is linked outside its expected location; preserved: '+str(path))
    prefix=home/'.local/share/thor';record=home/'.config/thor/installation.json'
    if prefix.is_symlink():raise RuntimeError('Installation prefix is a symlink; it was preserved.')
    if record.exists():
        value=read(record)
        if value.get('schema')!=1 or value.get('managed_by')!='thor-install' or value.get('prefix')!=str(prefix):
            raise RuntimeError('Unexpected installation owner/prefix; it was preserved.')
    if prefix.exists() and (not (prefix/'.thor-installation.json').is_file() or
                           read(prefix/'.thor-installation.json').get('managed_by')!='thor-install'):
        raise RuntimeError('Installation directory is not owned by thor-install.')
    version=(repo/'release/version').read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9._-]+',version):raise RuntimeError('Invalid release version.')
    arch={'aarch64':'aarch64','arm64':'aarch64','x86_64':'x86_64'}.get(platform.machine())
    if not arch:raise RuntimeError('Unsupported VM architecture.')
    bundle=repo/'dist'/f'thor-{version}-linux-{arch}.tar.gz'
    expected=Path(str(bundle)+'.sha256').read_text().split()[0].lower()
    with bundle.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if not re.fullmatch(r'[a-f0-9]{64}',expected) or expected!=actual:raise RuntimeError('Release checksum mismatch; nothing reset.')
    with tarfile.open(bundle,'r:gz') as archive:
        first=next(iter(archive))
        if first.name!='THOR-DISTRIBUTION.json' or first.size>32*1024*1024:raise RuntimeError('Invalid distribution manifest.')
        manifest=json.load(archive.extractfile(first))
    if manifest.get('target')!='linux-'+arch or manifest.get('product')!='Thor':raise RuntimeError('Wrong native release bundle.')
    if arch=='aarch64' and 'ols/src/main.odin' not in manifest['files']:
        raise RuntimeError('Bundle lacks automatic native ARM64 OLS source.')
    required=sum(value['size'] for value in manifest['files'].values())+bundle.stat().st_size+256*1024*1024
    if shutil.disk_usage(home).free<required:raise RuntimeError('Not enough space for a fresh installation and its recovery backup.')
    for path in [home/'.profile',home/'.zprofile']:
        if path.exists():
            if path.is_symlink():raise RuntimeError('Shell profile is a symlink; it was preserved.')
            without_path_block(path.read_text())
    storage=home/'.config/Code/User/globalStorage/storage.json'
    if storage.exists():
        entries=profiles(read(storage))
        if not isinstance(entries,list):raise RuntimeError('Unknown VS Code profile registry format.')
        for entry in entries:
            if entry.get('name')=='Thor' and not re.fullmatch(r'[A-Za-z0-9_-]+',entry.get('location','')):
                raise RuntimeError('Unknown VS Code profile location; nothing reset.')
    extension_registry=home/'.vscode/extensions/extensions.json'
    if extension_registry.exists() and not isinstance(read(extension_registry),list):raise RuntimeError('Unknown extension registry format.')
    return bundle

class Backup:
    def __init__(self,home):
        self.home=home
        self.directory=home/'Thor-install-backups'/(time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid()))
        self.directory.mkdir(parents=True,exist_ok=False)
    def destination(self,path):
        path=Path(path)
        relative=path.relative_to(self.home)
        if '..' in relative.parts:raise RuntimeError('Backup path escapes the VM home.')
        dest=self.directory/'home'/relative;dest.parent.mkdir(parents=True,exist_ok=True)
        return dest
    def move(self,path):
        if path.exists() or path.is_symlink():shutil.move(path,self.destination(path))
    def copy(self,path):
        if path.exists():shutil.copy2(path,self.destination(path))

def write_json(path,value):
    temporary=path.with_name(path.name+'.thor-reset-tmp')
    temporary.write_text(json.dumps(value,indent=4)+'\n');temporary.replace(path)

def reset(home,backup):
    storage_path=home/'.config/Code/User/globalStorage/storage.json'
    storage=read(storage_path) if storage_path.exists() else {}
    entries=profiles(storage);thor=[entry for entry in entries if entry.get('name')=='Thor']
    # VS Code must be closed. This disposable-VM reset removes only named
    # registrations, retaining their original state and packages for recovery.
    if storage_path.exists():
        storage=read(storage_path);entries=profiles(storage);thor=[entry for entry in entries if entry.get('name')=='Thor']
        backup.copy(storage_path)
        removed={entry['location'] for entry in thor}
        remaining=[entry for entry in entries if entry.get('name')!='Thor']
        storage['userDataProfiles']=json.dumps(remaining) if isinstance(storage.get('userDataProfiles'),str) else remaining
        for mappings in storage.get('profileAssociations',{}).values():
            if isinstance(mappings,dict):
                for key,value in list(mappings.items()):
                    if isinstance(value,str) and value in removed:del mappings[key]
        write_json(storage_path,storage)
        for entry in thor:backup.move(home/'.config/Code/User/profiles'/entry['location'])
    extensions=home/'.vscode/extensions'
    if extensions.exists():
        for path in list(extensions.iterdir()):
            if path.is_dir() and package_name(path.name):backup.move(path)
        registry=extensions/'extensions.json'
        if registry.exists():
            backup.copy(registry);write_json(registry,[entry for entry in read(registry)
                if not owned_extension(entry.get('identifier',{}).get('id',''))])
        obsolete=extensions/'.obsolete'
        if obsolete.exists():
            backup.copy(obsolete);write_json(obsolete,{key:value for key,value in read(obsolete).items() if not package_name(key)})
    for identifier in IDS:
        backup.move(home/'.config/Code/User/globalStorage'/identifier)
    cached=home/'.config/Code/CachedExtensionVSIXs'
    if cached.exists():
        for path in list(cached.iterdir()):
            if package_name(path.name):backup.move(path)
    for path in [home/'.profile',home/'.zprofile']:
        if path.exists():
            original=path.read_text();clean=without_path_block(original)
            if clean!=original:backup.copy(path);path.write_text(clean)
    for path in [home/'.local/share/thor',home/'.config/thor',home/'.local/share/ols',home/'.cache/thor-validation']:
        backup.move(path)

@contextlib.contextmanager
def local_release(bundle,log):
    paths={f'/{bundle.name}':bundle,f'/{bundle.name}.sha256':Path(str(bundle)+'.sha256')}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            source=paths.get(self.path)
            if source is None:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Length',str(source.stat().st_size));self.end_headers()
            with source.open('rb') as stream:shutil.copyfileobj(stream,self.wfile)
        def log_message(self,format,*args):log.write('local release: '+format%args+'\n');log.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}'
    finally:server.shutdown();server.server_close();thread.join(timeout=3)

def execute(command,repo,env,log):
    print('\n$ '+' '.join(command),flush=True)
    process=subprocess.Popen(command,cwd=repo,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        for line in process.stdout:print(line,end='',flush=True);log.write(line);log.flush()
        if process.wait()!=0:raise RuntimeError('Command failed: '+' '.join(command))
    finally:
        process.stdout.close()
        if process.poll() is None:process.terminate();process.wait(timeout=10)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--dry-run',action='store_true');options=parser.parse_args()
    if platform.system()!='Linux' or not Path('/etc/os-release').is_file() or 'ID=ubuntu' not in Path('/etc/os-release').read_text():
        parser.error('This helper is for the authorized Ubuntu test VM only.')
    home=Path.home();repo=home/'Thor';bundle=validate(home,repo)
    for program in ['code','curl','tar','clang']:
        if not shutil.which(program):raise RuntimeError('Required setup prerequisite is missing: '+program+'. Nothing reset.')
    if not os.access(repo/'thor-install',os.X_OK):raise RuntimeError('~/Thor/thor-install must be executable. Nothing reset.')
    print('Reset scope: Thor toolchain/cache, Thor VS Code profile, Thor/OLS/CodeLLDB and legacy Odin highlighting extensions, and the managed PATH block.')
    print('Preserved: ~/Thor source/projects, VS Code, OS prerequisites, unrelated editor data, and VM idle preferences.')
    print('Old state will be moved to ~/Thor-install-backups, outside all active installation paths.')
    if options.dry_run:print('Dry run only. Installer command: ./thor-install --vscode --profile Thor');return
    if os.environ.get('TERM_PROGRAM')=='vscode' or editor_processes():
        raise RuntimeError('Save your work and close VS Code first, then run this script in Ubuntu Terminal. No files changed.')
    backup=Backup(home);print('Recovery directory:',backup.directory,flush=True)
    log_path=backup.directory/'reinstall.log'
    with log_path.open('w') as log:
        print('\n[1/3] Removing the old active installation...',flush=True);reset(home,backup)
        env=os.environ.copy()
        for name in ['THOR_ROOT','THOR_ODIN','ODIN','THOR_RELEASE_BASE','OLS_BUILTIN_FOLDER','VSCODE_IPC_HOOK_CLI']:env.pop(name,None)
        env['PATH']=os.pathsep.join(part for part in env.get('PATH','').split(os.pathsep) if part!=str(home/'.local/share/thor/bin'))
        with local_release(bundle,log) as url:
            env['THOR_RELEASE_BASE']=url
            print('\n[2/3] Running the full installer using the local release mirror...',flush=True)
            execute(['./thor-install','--vscode','--profile','Thor'],repo,env,log)
        print('\n[3/3] Checking the installed toolchain...',flush=True)
        execute([str(home/'.local/share/thor/bin/thor-install'),'--doctor'],repo,env,log)
    print('\nComplete. No confirmation prompts were required. Log:',log_path)
    print('VS Code may open during setup. Open your project with its Thor profile when you are ready.')

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,subprocess.SubprocessError) as error:
        print('Thor VM reinstall:',error,file=sys.stderr);sys.exit(1)

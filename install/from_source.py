#!/usr/bin/env python3
"""Build the checked-out Thor source, then use the normal inventoried installer.

Only source installation needs Python/Rust/Node. Builds use a disposable target
directory and two compiler jobs; neither the checkout nor global build history
is used for intermediate output. All upstream versions are explicitly pinned.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HOSTS = {
    'Darwin': ('macos', {'arm64': 'aarch64', 'x86_64': 'x86_64'}),
    'Linux': ('linux', {'aarch64': 'aarch64', 'arm64': 'aarch64', 'x86_64': 'x86_64'}),
    'Windows': ('windows', {'AMD64': 'x86_64', 'x86_64': 'x86_64'}),
    'FreeBSD': ('freebsd', {'amd64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'aarch64', 'aarch64': 'aarch64'}),
    'NetBSD': ('netbsd', {'amd64': 'x86_64', 'x86_64': 'x86_64', 'aarch64': 'aarch64', 'evbarm': 'aarch64'}),
    'OpenBSD': ('openbsd', {'amd64': 'x86_64', 'x86_64': 'x86_64'}),
}


def host(system=None, machine=None):
    system, machine = system or platform.system(), machine or platform.machine()
    if system=='NetBSD' and machine=='evbarm':
        machine=platform.processor()
    try:
        name, architectures = HOSTS[system]
        return name + '-' + architectures[machine]
    except KeyError:
        raise ValueError(f'Unsupported Odin host: {system} {machine}') from None


def run(args, *, env, cwd=None, capture=False):
    print('+ ' + ' '.join(map(str, args)), flush=True)
    result = subprocess.run(list(map(str, args)), env=env, cwd=cwd, check=True,
                            stdout=subprocess.PIPE if capture else None, text=capture)
    return result.stdout.strip() if capture else None


def llvm_config(env):
    explicit = env.get('LLVM_CONFIG')
    candidates = [explicit] if explicit else []
    for version in range(22, 16, -1):
        candidates.extend([shutil.which(f'llvm-config-{version}'), shutil.which(f'llvm-config{version}'),
                           f'/usr/local/llvm{version}/bin/llvm-config', f'/usr/lib/llvm-{version}/bin/llvm-config'])
    candidates += [shutil.which('llvm-config'), '/opt/homebrew/opt/llvm/bin/llvm-config',
                   '/usr/local/opt/llvm/bin/llvm-config', '/usr/pkg/bin/llvm-config']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise ValueError('LLVM development tools were not found. Set LLVM_CONFIG to a compatible llvm-config executable.')


def ensure_rust(env):
    version=subprocess.check_output(['rustc','--version'],env=env,text=True).split()[1]
    if tuple(map(int,version.split('-')[0].split('.')[:2])) >= (1,85):
        return
    if not shutil.which('rustup',path=env['PATH']):
        raise ValueError('Source installation requires Rust 1.85+. Update the system Rust package and rerun.')
    # Install alongside existing toolchains; never change the user's default.
    run(['rustup','toolchain','install','1.89.0','--profile','minimal'],env=env)
    env['RUSTUP_TOOLCHAIN']='1.89.0'


def ensure_node(work, env):
    """Older distro Node packages cannot run VSCE. Use a disposable official
    build rather than replacing a user's existing Node installation."""
    try:
        version = subprocess.check_output(['node','--version'],env=env,text=True)
        if int(version.strip().lstrip('v').split('.')[0]) >= 20:
            return
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    system = {'Darwin':'darwin','Linux':'linux','Windows':'win'}.get(platform.system())
    if system is None:
        raise ValueError('Source editor packaging needs Node 20+ from the BSD package manager.')
    arch = 'arm64' if host().endswith('aarch64') else 'x64'
    version = 'v22.14.0'
    name = f'node-{version}-{system}-{arch}'
    filename = name + ('.zip' if os.name=='nt' else '.tar.gz')
    base = f'https://nodejs.org/dist/{version}/'
    with urllib.request.urlopen(base+'SHASUMS256.txt',timeout=60) as response:
        hashes = response.read(1024*1024).decode()
    expected = next((line.split()[0] for line in hashes.splitlines() if line.split()[-1]==filename),None)
    if expected is None:
        raise ValueError('Official Node checksum inventory lacks this host')
    archive = work/filename
    with urllib.request.urlopen(base+filename,timeout=60) as response, archive.open('wb') as output:
        shutil.copyfileobj(response,output)
    with archive.open('rb') as stream:
        actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=expected:
        raise ValueError('Official Node archive checksum mismatch')
    if os.name=='nt':
        with zipfile.ZipFile(archive) as incoming:
            if any(Path(n).is_absolute() or '..' in Path(n).parts for n in incoming.namelist()):
                raise ValueError('Unsafe Node archive path')
            incoming.extractall(work)
        directory = work/name
    else:
        # Pinned official archive, verified before extraction. Check paths and
        # link destinations explicitly for Python 3.11 compatibility.
        with tarfile.open(archive) as incoming:
            for member in incoming:
                p=Path(member.name)
                if p.is_absolute() or '..' in p.parts or not(member.name==name or member.name.startswith(name+'/')):
                    raise ValueError('Unsafe Node archive path')
                if member.issym() and (Path(member.linkname).is_absolute() or '..' in Path(member.linkname).parts):
                    # Official npm links use ../lib, which remain inside the
                    # single verified top-level Node directory.
                    resolved=(work/p.parent/member.linkname).resolve()
                    if not resolved.is_relative_to(work/name):raise ValueError('Unsafe Node archive link')
                if not(member.isfile() or member.isdir() or member.issym()):raise ValueError('Unsupported Node archive entry')
            incoming.extractall(work,filter='data')
        directory = work/name/'bin'
    env['PATH']=str(directory)+os.pathsep+env['PATH']


def build(work, forward, odin=None):
    target = host()
    env = dict(os.environ)
    paths = [str(Path.home()/'.cargo/bin'), '/usr/local/bin', '/usr/pkg/bin', env.get('PATH', '')]
    env['PATH'] = os.pathsep.join(paths)
    if os.name=='nt':
        # Preserve the caller's environment, but give cmd-based build tools a
        # compact child PATH. Hosted/dev shells can exceed cmd's 8191-byte limit.
        directories=[str(Path(sys.executable).parent),str(Path(env.get('SystemRoot','C:/Windows'))/'System32')]
        for tool in ('cargo','rustc','git','git-lfs','node','npm.cmd','cl.exe','link.exe','lib.exe','rc.exe','mt.exe','powershell.exe','tar.exe'):
            executable=shutil.which(tool,path=env['PATH'])
            if executable:directories.append(str(Path(executable).parent))
        env['PATH']=os.pathsep.join(dict.fromkeys(directories))
    env.update(CARGO_TARGET_DIR=str(work/'target'), CARGO_INCREMENTAL='0', CARGO_BUILD_JOBS='2')
    ensure_rust(env)
    profile = json.loads((ROOT/'rust/data'/('noalloc-io-sdk-windows.json' if os.name == 'nt' else 'noalloc-io-sdk.json')).read_text())
    revision = profile.get('revision', profile.get('reviewed_sdk_revision'))
    if revision == '9a69916':
        revision = '9a6991685ddf663956d9836602eb421e141fc443'
    if odin:
        odin = Path(odin).resolve(strict=True)
    else:
        checkout = work/'Odin'
        run(['git', 'init', checkout], env=env)
        run(['git', '-C', checkout, 'remote', 'add', 'origin', 'https://github.com/odin-lang/Odin.git'], env=env)
        run(['git', '-C', checkout, 'fetch', '--depth=1', 'origin', revision], env=env)
        run(['git', '-C', checkout, 'checkout', '--detach', 'FETCH_HEAD'], env=env)
        actual = run(['git', '-C', checkout, 'rev-parse', 'HEAD'], env=env, capture=True)
        if not actual.startswith(revision):
            raise ValueError('Downloaded Odin revision does not match the reviewed SDK')
        if os.name == 'nt':
            run(['git', '-C', checkout, 'lfs', 'pull'], env=env)
            run(['cmd.exe', '/d', '/c', 'build.bat', 'release'], cwd=checkout, env=env)
        else:
            env['LLVM_CONFIG'] = llvm_config(env)
            env['PATH'] = str(Path(env['LLVM_CONFIG']).parent)+os.pathsep+env['PATH']
            make = 'gmake' if platform.system() in ('FreeBSD', 'NetBSD', 'OpenBSD') else 'make'
            run([make, 'release-native'], cwd=checkout, env=env)
        odin = checkout/('odin.exe' if os.name == 'nt' else 'odin')
    # The release builder checks revision AND the SDK file hashes before staging.
    for manifest in ('rust/Cargo.toml', 'install/Cargo.toml'):
        run(['cargo', 'build', '--release', '--locked', '--manifest-path', ROOT/manifest, '-j', '2'], env=env, cwd=ROOT)
    suffix = '.exe' if os.name == 'nt' else ''
    version = (ROOT/'release/version').read_text().strip()
    tls = work/('tls'+suffix)
    run([odin, 'build', ROOT/'tls/src', f'-collection:src={ROOT / "tls/src"}',
         f'-collection:thor={ROOT / "frontend"}', '-o:speed', '-thread-count:2',
         f'-define:VERSION=thor-{version}+ols-0c9438f',f'-out:{tls}'], env=env)
    editor = work/'vscode'
    ensure_node(work,env)
    shutil.copytree(ROOT/'editors/vscode', editor,
                    ignore=shutil.ignore_patterns('node_modules', '*.vsix', '.git'))
    npm = 'npm.cmd' if os.name == 'nt' else 'npm'
    run([npm, 'ci', '--no-audit', '--no-fund'], cwd=editor, env=env)
    vsix = work/'thor-language.vsix'
    run([npm, 'exec', '--', 'vsce', 'package', '--no-dependencies',
         '--baseContentUrl', 'https://github.com/davidludwik2370/Thor/blob/main/editors/vscode/',
         '--baseImagesUrl', 'https://raw.githubusercontent.com/davidludwik2370/Thor/main/editors/vscode/',
         '--out', vsix], cwd=editor, env=env)
    output = work/'distribution'
    run([sys.executable, ROOT/'tools/build_distribution.py', '--odin', odin,
         '--compiler', work/'target/release'/('thor-rust'+suffix),
         '--installer', work/'target/release'/('thor-install'+suffix),
         '--tls', tls, '--vsix', vsix, '--output', output], env=env)
    bundle = output/f'thor-{version}-{target}.tar.gz'
    run([work/'target/release'/('thor-install'+suffix), '--bundle', bundle, *forward], env=env)


def main():
    if sys.version_info < (3,11):
        raise ValueError('Source installation requires Python 3.11 or newer. Install it and rerun; binary installation does not require Python.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odin', type=Path, help='reuse a reviewed Odin executable instead of building it')
    options, forward = parser.parse_known_args()
    host()
    if '--dry-run' in forward:
        print('Source install plan: pinned Odin/LLVM, release Rust transpiler/installer, TLS, VSIX; inventoried install and final checks. No changes made.')
        return 0
    with tempfile.TemporaryDirectory(prefix='thor-source-') as temporary:
        build(Path(temporary), forward, options.odin)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'thor-install: source build failed: {error}', file=sys.stderr)
        raise SystemExit(1)

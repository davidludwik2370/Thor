#!/usr/bin/env python3
"""Build the checked-out Thor source, then use the normal inventoried installer.

Only source installation needs Python/Rust/Node. Builds use a disposable target
directory and two compiler jobs; neither the checkout nor global build history
is used for intermediate output. All upstream versions are explicitly pinned.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
if __package__:
    from . import source_tools
else:
    import source_tools

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
    if explicit:
        explicit=shutil.which(explicit,path=env.get('PATH')) or explicit
        if not Path(explicit).is_file():raise ValueError('LLVM_CONFIG does not name an installed executable')
    candidates = [explicit] if explicit else []
    for version in range(22, 16, -1):
        candidates.extend([shutil.which(f'llvm-config-{version}', path=env.get('PATH')), shutil.which(f'llvm-config{version}', path=env.get('PATH')),
                           f'/usr/local/llvm{version}/bin/llvm-config', f'/usr/lib/llvm-{version}/bin/llvm-config',
                           f'/opt/homebrew/opt/llvm@{version}/bin/llvm-config',f'/usr/local/opt/llvm@{version}/bin/llvm-config'])
    candidates += [shutil.which('llvm-config', path=env.get('PATH')), '/opt/homebrew/opt/llvm/bin/llvm-config',
                   '/usr/local/opt/llvm/bin/llvm-config', '/usr/pkg/bin/llvm-config']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                version=int(subprocess.check_output([candidate,'--version'],env=env,text=True).strip().split('.')[0])
            except (OSError,ValueError,subprocess.CalledProcessError):
                if candidate==explicit:raise ValueError('LLVM_CONFIG cannot report a usable LLVM version') from None
                continue
            if 17<=version<=22:return str(Path(candidate).resolve())
            if candidate==explicit:raise ValueError('The reviewed Odin compiler requires LLVM 17–22; select another LLVM_CONFIG')
    raise ValueError('LLVM development tools were not found. Set LLVM_CONFIG to a compatible llvm-config executable.')


def ensure_node(work, env):
    source_tools.ensure_node(work, env, host())


def build(work, forward, odin=None, export_bundle=None):
    target = host()
    install_env = dict(os.environ)
    env = dict(install_env)
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
    # Windows environment keys are case-insensitive; do not leave an inherited
    # uppercase npm cache alias pointing outside this disposable workspace.
    for key in list(env):
        if key.lower() in ('npm_config_cache', 'node_compile_cache'):
            del env[key]
    env.update(CARGO_TARGET_DIR=str(work/'target'), CARGO_INCREMENTAL='0', CARGO_BUILD_JOBS='2',
               npm_config_cache=str(work/'npm-cache'), NODE_COMPILE_CACHE=str(work/'node-compile-cache'))
    if odin:
        odin = Path(odin).resolve(strict=True)
    # Acquire binary build tools before compiling anything. Missing optional
    # packaging tools must not cause a late failure after expensive builds.
    source_tools.ensure_rust(work, env, target)
    ensure_node(work, env)
    if os.name != 'nt' and not odin:
        try:
            env['LLVM_CONFIG'] = llvm_config(env)
        except ValueError:
            if env.get('LLVM_CONFIG') or platform.system() != 'Darwin':
                raise
            env['LLVM_CONFIG'] = source_tools.acquire_macos_llvm(work, env, target)
        env['PATH'] = str(Path(env['LLVM_CONFIG']).parent)+os.pathsep+env['PATH']
    profile = json.loads((ROOT/'rust/data'/('noalloc-io-sdk-windows.json' if os.name == 'nt' else 'noalloc-io-sdk.json')).read_text())
    revision = profile.get('revision', profile.get('reviewed_sdk_revision'))
    if revision == '9a69916':
        revision = '9a6991685ddf663956d9836602eb421e141fc443'
    if not odin:
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
            make = 'gmake' if platform.system() in ('FreeBSD', 'NetBSD', 'OpenBSD') else 'make'
            run([make, 'release-native'], cwd=checkout, env=env)
            source_tools.retain_llvm_notices(env['LLVM_CONFIG'], checkout)
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
    shutil.copytree(ROOT/'editors/vscode', editor,
                    ignore=shutil.ignore_patterns('node_modules', '*.vsix', '.git'))
    npm = 'npm.cmd' if os.name == 'nt' else 'npm'
    # VSIX packaging needs JavaScript dependencies, not native keychain/signing
    # helpers or their post-install downloads and compiler toolchains.
    run([npm, 'ci', '--no-audit', '--no-fund', '--omit=optional', '--ignore-scripts'], cwd=editor, env=env)
    vsix = work/'thor-language.vsix'
    run([npm, 'exec', '--offline', '--', 'vsce', 'package',
         '--baseContentUrl', 'https://github.com/davidludwik2370/Thor/blob/main/editors/vscode/',
         '--baseImagesUrl', 'https://raw.githubusercontent.com/davidludwik2370/Thor/main/editors/vscode/',
         '--out', vsix], cwd=editor, env=env)
    output = work/'distribution'
    run([sys.executable, ROOT/'tools/build_distribution.py', '--odin', odin,
         '--compiler', work/'target/release'/('thor-rust'+suffix),
         '--installer', work/'target/release'/('thor-install'+suffix),
         '--tls', tls, '--vsix', vsix, '--output', output], env=env)
    bundle = output/f'thor-{version}-{target}.tar.gz'
    # Code can outlive this process. Its terminals must inherit the caller's
    # environment, not Cargo/Rustup/npm paths that disappear with work/.
    run([work/'target/release'/('thor-install'+suffix), '--bundle', bundle, *forward], env=install_env)
    if export_bundle:
        export_bundle=Path(export_bundle).resolve()
        export_bundle.mkdir(parents=True,exist_ok=True)
        for file in (bundle,bundle.with_name(bundle.name+'.sha256')):
            shutil.copy2(file,export_bundle/file.name)
        print(f'Verified release bundle exported to {export_bundle}',flush=True)


def main():
    if sys.version_info < (3,9):
        raise ValueError('Source installation requires Python 3.9 or newer. Install it and rerun; binary installation does not require Python.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odin', type=Path, help='reuse a reviewed Odin executable instead of building it')
    parser.add_argument('--export-bundle',type=Path,help='retain the verified native bundle/checksum in this directory (maintainers)')
    options, forward = parser.parse_known_args()
    host()
    if '--dry-run' in forward:
        print('Source install plan: pinned Odin/LLVM, release Rust transpiler/installer, TLS, VSIX; inventoried install and final checks. No changes made.')
        return 0
    with tempfile.TemporaryDirectory(prefix='thor-source-') as temporary:
        build(Path(temporary), forward, options.odin,options.export_bundle)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'thor-install: source build failed: {error}', file=sys.stderr)
        raise SystemExit(1)

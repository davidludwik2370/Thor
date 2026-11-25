"""Private binary build tools. Compatible with Apple's bundled Python 3.9.

Only the source install's temporary directory is changed. Never fall back to
compiling LLVM/Node/Rust or replacing user tools after a download failure.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import time
import urllib.request
import zipfile

MAX_DOWNLOAD = 2 * 1024 * 1024 * 1024


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download(url, destination, expected):
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
        raise ValueError('Invalid binary tool SHA-256')
    if not url.startswith('https://'):
        raise ValueError('Binary tool downloads require HTTPS')
    print('Downloading binary tool: ' + url, flush=True)
    progress = time.monotonic()
    received = 0
    with urllib.request.urlopen(url, timeout=60) as response, Path(destination).open('xb') as output:
        if not response.geturl().startswith('https://'):
            raise ValueError('Binary tool redirected outside HTTPS')
        for block in iter(lambda: response.read(1024 * 1024), b''):
            received += len(block)
            if received > MAX_DOWNLOAD:
                raise ValueError('Binary tool download exceeds 2 GiB')
            output.write(block)
            if time.monotonic() - progress >= 10:
                print(f'  downloaded {received // (1024 * 1024)} MiB', flush=True)
                progress = time.monotonic()
    if sha256(destination) != expected.lower():
        raise ValueError('Binary tool SHA-256 mismatch: ' + url)


def official_hash(base, filename):
    with urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=60) as response:
        inventory = response.read(1024 * 1024).decode()
    values = [line.split()[0] for line in inventory.splitlines()
              if len(line.split()) == 2 and line.split()[1] == filename]
    if len(values) != 1:
        raise ValueError('Official checksum inventory lacks a unique entry for ' + filename)
    return values[0]


def archive_path(name, root):
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or '..' in path.parts or
            path.parts[0] != root or any(c in name for c in ('\\', ':', '\0'))):
        raise ValueError('Unsafe binary archive path: ' + name)
    return path


def extract_tar(archive, destination, root, select=lambda path: True):
    """One streaming pass, bounded output, and no symlink-following writes.

    Links are installed only after all regular files. This also works on older
    vendor Pythons without tarfile's newer extraction-filter API.
    """
    destination = Path(destination).resolve()
    destination.mkdir()
    seen, links = set(), []
    total = 0
    with tarfile.open(archive, 'r|*') as incoming:
        for member in incoming:
            path = archive_path(member.name, root)
            name = path.as_posix()
            if name in seen or len(seen) >= 100000:
                raise ValueError('Duplicate or excessive binary archive entries')
            seen.add(name)
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError('Unsupported binary archive entry: ' + name)
            target = destination.joinpath(*path.parts)
            if member.issym():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or any(c in member.linkname for c in ('\\', ':', '\0')):
                    raise ValueError('Unsafe binary archive link: ' + name)
                resolved = (target.parent / member.linkname).resolve()
                if not resolved.is_relative_to(destination / root):
                    raise ValueError('Escaping binary archive link: ' + name)
            if not select(PurePosixPath(*path.parts[1:])):
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.issym():
                links.append((target, member.linkname))
            else:
                total += member.size
                if total > 2 * 1024 * 1024 * 1024:
                    raise ValueError('Binary tool extraction exceeds 2 GiB')
                target.parent.mkdir(parents=True, exist_ok=True)
                with incoming.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    # A link's parent must remain an ordinary directory, including when another
    # deferred link would otherwise redirect it. resolve() also detects cycles.
    for target, link in links:
        if target.parent.resolve() != target.parent:
            raise ValueError('Binary archive link has a symlink parent')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(link)
    for target, _ in links:
        if not target.resolve().is_relative_to(destination / root) or not target.exists():
            raise ValueError('Unresolved or escaping binary archive link')
    return destination / root


def extract_zip(archive, destination, root):
    destination = Path(destination).resolve()
    destination.mkdir()
    seen, total = set(), 0
    with zipfile.ZipFile(archive) as incoming:
        for member in incoming.infolist():
            path = archive_path(member.filename, root)
            name = path.as_posix().casefold()
            if name in seen or len(seen) >= 100000:
                raise ValueError('Duplicate or excessive binary archive entries')
            seen.add(name)
            mode = stat.S_IFMT(member.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('Unsupported binary ZIP entry')
            total += member.file_size
            if total > 512 * 1024 * 1024:
                raise ValueError('Binary ZIP extraction exceeds 512 MiB')
            target = destination.joinpath(*path.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with incoming.open(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
    return destination / root


def ensure_node(work, env, target):
    env.setdefault('NODE_COMPILE_CACHE', str(work / 'node-compile-cache'))
    npm = 'npm.cmd' if os.name == 'nt' else 'npm'
    try:
        version = subprocess.check_output(['node', '--version'], env=env, text=True, stderr=subprocess.DEVNULL)
        npm_version = subprocess.check_output([npm, '--version'], env=env, text=True, stderr=subprocess.DEVNULL)
        if int(version.strip().lstrip('v').split('.')[0]) >= 20 and npm_version.strip():
            return
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    system = {'macos': 'darwin', 'linux': 'linux', 'windows': 'win'}.get(target.rsplit('-', 1)[0])
    if system is None:
        raise ValueError('Source editor packaging needs Node 20+ and npm from the BSD package manager.')
    arch = 'arm64' if target.endswith('aarch64') else 'x64'
    version = 'v22.14.0'
    root = f'node-{version}-{system}-{arch}'
    filename = root + ('.zip' if os.name == 'nt' else '.tar.gz')
    base = f'https://nodejs.org/dist/{version}/'
    archive = work / filename
    download(base + filename, archive, official_hash(base, filename))
    directory = (extract_zip if os.name == 'nt' else extract_tar)(archive, work / 'node', root)
    archive.unlink()
    directory = directory if os.name == 'nt' else directory / 'bin'
    env['PATH'] = str(directory) + os.pathsep + env['PATH']
    node_version = subprocess.check_output(['node', '--version'], env=env, text=True).strip()
    npm_version = subprocess.check_output([npm, '--version'], env=env, text=True).strip()
    if node_version != version or not npm_version:
        raise ValueError('Private Node/npm failed its version check')
    print('PASS: private prebuilt Node/npm (no package-manager dependency build)', flush=True)


def llvm_files(path):
    return (path.as_posix().startswith(('bin/llvm-config', 'bin/llvm-symbolizer', 'include/', 'lib/clang/')) or
            path.parent == PurePosixPath('lib') and path.suffix == '.a' and
            (path.name.startswith(('libLLVM', 'libPolly')) or path.name in ('libRemarks.a', 'libLTO.a')) or
            path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE', 'COPYRIGHT')))


def acquire_macos_llvm(work, env, target):
    tools = json.loads(Path(__file__).with_name('source-tools.json').read_text())
    config = tools.get(target)
    if config is None:
        raise ValueError('No verified binary LLVM for this host; select an installed LLVM_CONFIG.')
    def version_tuple(value):
        return tuple((list(map(int, value.split('.'))) + [0, 0, 0])[:3])
    current = version_tuple(platform.mac_ver()[0])
    minimum = version_tuple(config['minimum_macos'])
    if current < minimum:
        raise ValueError(f"The pinned LLVM binary requires macOS {config['minimum_macos']}; select a compatible LLVM_CONFIG.")
    print(f"Acquiring LLVM {config['version']} binary; LLVM itself will not be compiled.", flush=True)
    archive = work / 'llvm.tar.xz'
    download(config['url'], archive, config['sha256'])
    print('Extracting LLVM development components and runtime support', flush=True)
    directory = extract_tar(archive, work / 'llvm', config['root'], llvm_files)
    archive.unlink()
    binary = directory / 'bin/llvm-config'
    actual = subprocess.check_output([str(binary), '--version'], env=env, text=True).strip()
    if actual != config['version']:
        raise ValueError('Downloaded LLVM reports the wrong version')
    # Official Mac packages ship static component libraries. Odin expects a
    # single -lLLVM archive; combine existing objects with Apple's libtool.
    # This indexes/copies precompiled objects, never compiles LLVM sources.
    libraries = shlex.split(subprocess.check_output(
        [str(binary), '--link-static', '--libfiles', 'all'], env=env, text=True))
    if not libraries or any(not Path(p).is_file() or not Path(p).resolve().is_relative_to(directory) for p in libraries):
        raise ValueError('Pinned LLVM package is missing its static component libraries')
    system_libs = shlex.split(subprocess.check_output([str(binary), '--link-static', '--system-libs', 'all'], env=env, text=True))
    used_zstd = '-lzstd' in system_libs
    if used_zstd:
        # LLVM's package needs zstd, which older Apple SDKs do not provide.
        # Build only this small C library with existing tools (two jobs, no
        # CMake/Python/package-manager dependency chain), then fold its objects
        # into LLVM's archive so no new runtime library is required.
        zstd = tools['zstd']
        zstd_archive = work / 'zstd.tar.gz'
        download(zstd['url'], zstd_archive, zstd['sha256'])
        zstd_root = extract_tar(zstd_archive, work / 'zstd', zstd['root'])
        zstd_archive.unlink()
        print('Building small private zstd C library (two jobs)', flush=True)
        result = subprocess.run(['make', '-C', str(zstd_root / 'lib'), '-j', '2', 'libzstd.a'],
                                env=env, capture_output=True, text=True)
        if result.returncode:
            raise ValueError('Private zstd build failed: ' + result.stderr[-4000:])
        libraries.append(str(zstd_root / 'lib/libzstd.a'))
        for name in ('LICENSE', 'COPYING'):
            shutil.copy2(zstd_root / name, directory / ('LICENSE-zstd-' + name + '.txt'))
        system_libs.remove('-lzstd')
    print('Indexing precompiled LLVM libraries (not compiling LLVM)', flush=True)
    result = subprocess.run(['xcrun', 'libtool', '-static', '-o', str(directory / 'lib/libLLVM.a'), *libraries],
                            env=env, capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Could not index precompiled LLVM libraries: ' + result.stderr[-4000:])
    # Keep transitive system linker flags from the package's own configuration.
    env['LDFLAGS'] = (env.get('LDFLAGS', '') + ' ' + ' '.join(system_libs)).strip()
    download(config['license_url'], directory / 'LICENSE-LLVM.txt', config['license_sha256'])
    metadata = dict(config)
    if used_zstd:
        metadata.update(zstd=tools['zstd'], zstd_license='BSD-3-Clause')
    (directory / 'THOR-BINARY-TOOL.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print('PASS: private LLVM development libraries, headers and symbolizer', flush=True)
    return str(binary)


def retain_llvm_notices(binary, checkout):
    root = Path(binary).resolve().parents[1]
    metadata = root / 'THOR-BINARY-TOOL.json'
    if not metadata.is_file():
        return
    # SDK bin/ is inventoried by the distribution builder and survives later
    # repackaging. Static LLVM's license must survive even without a dylib.
    destination = checkout / 'bin/llvm-notices'
    for path in root.rglob('*'):
        if path.is_file() and path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE', 'COPYRIGHT')):
            target = destination / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(metadata, destination / 'UPSTREAM.json')


def ensure_rust(work, env, target):
    try:
        version = subprocess.check_output(['rustc', '--version'], env=env, text=True, stderr=subprocess.DEVNULL).split()[1]
        cargo = subprocess.check_output(['cargo', '--version'], env=env, text=True, stderr=subprocess.DEVNULL)
        if tuple(map(int, version.split('-')[0].split('.')[:2])) >= (1, 89) and cargo.strip():
            return
    except (OSError, ValueError, IndexError, subprocess.CalledProcessError):
        pass
    targets = {'macos-aarch64': 'aarch64-apple-darwin', 'macos-x86_64': 'x86_64-apple-darwin',
               'linux-aarch64': 'aarch64-unknown-linux-gnu', 'linux-x86_64': 'x86_64-unknown-linux-gnu',
               'windows-x86_64': 'x86_64-pc-windows-msvc'}
    if target not in targets:
        raise ValueError('Source installation needs Rust 1.89+ and Cargo from the BSD package manager.')
    filename = 'rustup-init' + ('.exe' if os.name == 'nt' else '')
    base = 'https://static.rust-lang.org/rustup/archive/1.28.2/' + targets[target] + '/'
    with urllib.request.urlopen(base + filename + '.sha256', timeout=60) as response:
        values = response.read(4096).decode().split()
    if not values:
        raise ValueError('Official Rust bootstrap checksum is empty')
    expected = values[0]
    executable = work / filename
    download(base + filename, executable, expected)
    executable.chmod(0o700)
    env.update(CARGO_HOME=str(work / 'cargo'), RUSTUP_HOME=str(work / 'rustup'), RUSTUP_TOOLCHAIN='1.89.0')
    env['PATH'] = str(work / 'cargo/bin') + os.pathsep + env['PATH']
    print('Installing private prebuilt Rust 1.89.0 (minimal profile, no global PATH changes)', flush=True)
    subprocess.run([str(executable), '-y', '--profile', 'minimal', '--default-toolchain', '1.89.0',
                    '--no-modify-path'], env=env, check=True, stdout=subprocess.DEVNULL)
    rust = subprocess.check_output(['rustc', '--version'], env=env, text=True).split()
    cargo = subprocess.check_output(['cargo', '--version'], env=env, text=True).split()
    if len(rust) < 2 or rust[1] != '1.89.0' or len(cargo) < 2 or cargo[1] != '1.89.0':
        raise ValueError('Private Rust/Cargo failed its version check')

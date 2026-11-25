#!/usr/bin/env python3
"""Build an inventoried native release from prebuilt tools; never copy a checkout wholesale."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
if __package__:
    from .ols_source import stage_source
else:
    from ols_source import stage_source

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def capture(*args):
    result=subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'{args[0]} failed ({result.returncode}): {result.stderr.strip()}')
    return result.stdout


def copy(source, destination):
    source = source.resolve(strict=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def tree(source, destination):
    # Dereference SDK/library links into independent files. A source tree is an
    # explicit builder input, not an untrusted archive, and is never modified.
    shutil.copytree(source, destination, symlinks=False,
                    ignore=shutil.ignore_patterns('.git', '__pycache__', '.DS_Store',
                                                  '.thor-cache', '.thor-build', '.thor-rust-build'))


def verify_sdk(odin, sdk):
    windows = os.name == 'nt'
    profile = json.loads((ROOT/'rust/data'/('noalloc-io-sdk-windows.json' if windows else 'noalloc-io-sdk.json')).read_text())
    revision = profile.get('revision', profile.get('reviewed_sdk_revision'))
    version = capture(odin, 'version').strip()
    if revision[:7] not in version:
        raise ValueError(f'Odin {version!r} is not the reviewed revision {revision}. Select the tested SDK with --odin.')
    mismatches = []
    for relative, expected in profile['sdk'].items():
        path = sdk/relative
        data = path.read_bytes() if path.is_file() else b''
        if hashlib.sha256(data).hexdigest() != expected and hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest() != expected:
            mismatches.append(relative)
    if mismatches:
        raise ValueError('Reviewed SDK files differ: '+', '.join(mismatches[:12]))
    # Odin includes argv[0] in this output on some builds; do not put a builder's
    # installation path into a release's content identity.
    return 'odin version '+version.split(' version ',1)[1] if ' version ' in version else version


def rust_licenses(stage):
    inventory = {}
    host=next(line.split(': ',1)[1] for line in capture('rustc','-vV').splitlines() if line.startswith('host: '))
    for manifest in (ROOT/'rust/Cargo.toml', ROOT/'install/Cargo.toml'):
        metadata = json.loads(capture('cargo', 'metadata', '--offline', '--locked', '--format-version', '1', '--filter-platform',host,'--manifest-path', manifest))
        for package in metadata['packages']:
            if package.get('source') is None:
                continue
            key = package['name']+'-'+package['version']
            if key in inventory:
                continue
            directory = Path(package['manifest_path']).parent
            candidates = [p for p in directory.iterdir() if p.is_file() and re.match(r'(?i)^(license|copying|copyright|notice)', p.name)]
            if package.get('license_file'):
                candidates.append(directory/package['license_file'])
            if not candidates:
                raise ValueError(f'No license text found for {key}; add its notice before releasing.')
            names = []
            for path in sorted(set(candidates)):
                destination = stage/'licenses/rust'/key/path.name
                copy(path, destination)
                names.append(destination.relative_to(stage).as_posix())
            inventory[key] = dict(license=package.get('license'), notices=names)
    sysroot = Path(capture('rustc', '--print', 'sysroot').strip())
    for path in (sysroot/'share/doc/rust').glob('*'):
        if path.is_file() and re.match(r'(?i)^(license|copyright)', path.name):
            copy(path, stage/'licenses/rust-standard-library'/path.name)
    (stage/'licenses/index.json').write_text(json.dumps(inventory, indent=2)+'\n')


def library_notices(source, stage):
    # Repackaging a tested installed toolchain must retain its original notices;
    # copied LLVM libraries no longer have a system package ownership record.
    for parent in list(source.parents)[:4]:
        notices=parent/'licenses/native'
        if (parent/'THOR-DISTRIBUTION.json').is_file() and notices.is_dir():
            for notice in notices.rglob('*'):
                if notice.is_file():copy(notice,stage/'licenses/native'/notice.relative_to(notices))
            return
    if platform.system() == 'Linux':
        # Keep the distribution's actual copyright/license inventory with every
        # copied LLVM support library; core system libraries are not bundled.
        result = subprocess.run(['dpkg-query', '-S', str(source)], capture_output=True, text=True) if shutil.which('dpkg-query') else None
        if result is not None and result.returncode == 0:
            package = result.stdout.split(': ', 1)[0].split(':', 1)[0]
            notice = Path('/usr/share/doc')/package/'copyright'
            if notice.is_file():
                copy(notice, stage/'licenses/native'/package/'copyright')
                return
        # RPM/Arch systems keep the same upstream notices outside Debian's doc
        # layout. Never silently omit native-library redistribution notices.
        for directory in (Path('/usr/share/licenses/llvm'), Path('/usr/share/licenses/llvm-libs'),
                          *Path('/usr/share/licenses').glob('llvm*'), *Path('/usr/share/doc').glob('llvm*')):
            candidates = [p for p in directory.rglob('*') if p.is_file() and re.match(r'(?i)^(license|copyright|copying|notice)',p.name)] if directory.is_dir() else []
            if candidates:
                for notice in candidates: copy(notice,stage/'licenses/native'/directory.name/notice.name)
                return
        raise ValueError(f'No redistribution notice found for {source}; install the LLVM license/copyright package.')
    # Homebrew bottles preserve their formula's licenses under the Cellar root.
    parents = [source.parent, *source.parents]
    found = False
    for parent in parents[:5]:
        for pattern in ('LICENSE*', 'COPYING*', 'NOTICE*', 'share/doc/llvm/LICENSE*'):
            for notice in parent.glob(pattern):
                if notice.is_file():
                    copy(notice, stage/'licenses/native'/source.name/notice.name)
                    found = True
        if found:
            return
    raise ValueError(f'No redistribution license text found for {source}.')


def relocate_odin(stage):
    directory = stage/'odin'
    odin = directory/('odin.exe' if os.name == 'nt' else 'odin')
    if os.name == 'nt':
        return  # The builder copies DLLs shipped next to the selected Odin binary.
    system = platform.system()
    if system in ('FreeBSD','NetBSD','OpenBSD'):
        # Initial BSD distributions are native source installs on the same host.
        # Keep the package-manager LLVM dependency installed by prerequisites;
        # do not apply Linux patchelf/loader assumptions to BSD ELF binaries.
        return
    if system not in ('Darwin', 'Linux'):
        raise ValueError('Unsupported native release builder host.')
    pending = [(odin, odin)]
    symbolizer=directory/'libexec/llvm-symbolizer'
    if symbolizer.exists():pending.append((symbolizer,symbolizer))
    copied = {}
    while pending:
        source, output = pending.pop()
        if system == 'Linux':
            deps = re.findall(r'^\s*\S+ => (/\S+)', capture('ldd', source), re.M)
            deps = [Path(p) for p in deps if not re.match(r'^lib(c|m|dl|pthread|rt|gcc_s|stdc\+\+)\.', Path(p).name)]
        else:
            deps = [Path(line.strip().split(' (', 1)[0]) for line in capture('otool', '-L', source).splitlines()[1:]]
            deps = [p for p in deps if p.is_absolute() and not str(p).startswith(('/usr/lib/', '/System/Library/')) and p != source]
        for dep in deps:
            resolved = dep.resolve(strict=True)
            target = directory/'lib'/dep.name
            if dep.name in copied and copied[dep.name] != sha(resolved):
                raise ValueError(f'Conflicting native library names: {dep.name}')
            if dep.name not in copied:
                copied[dep.name] = sha(resolved)
                copy(resolved, target)
                target.chmod(target.stat().st_mode | 0o200)
                library_notices(resolved, stage)
                pending.append((resolved, target))
            if system == 'Darwin':
                relative = '@loader_path/'+os.path.relpath(target,output.parent)
                subprocess.run(['install_name_tool', '-change', str(dep), relative, str(output)], check=True)
        if system == 'Linux' and (deps or output != odin):
            relative=os.path.relpath(directory/'lib',output.parent)
            subprocess.run(['patchelf', '--set-rpath', '$ORIGIN' if relative=='.' else '$ORIGIN/'+relative, str(output)], check=True)
    if system == 'Darwin':
        for file in [*sorted((directory/'lib').glob('*')), *([symbolizer] if symbolizer.exists() else []), odin]:
            subprocess.run(['codesign', '--force', '--sign', '-', str(file)], check=True)


def build(options):
    version = (ROOT/'release/version').read_text().strip()
    suffix = '.exe' if os.name == 'nt' else ''
    odin = Path(shutil.which(str(options.odin)) or options.odin).resolve(strict=True)
    sdk = Path(capture(odin, 'root').strip()).resolve()
    odin_version = verify_sdk(odin, sdk)
    compiler = options.compiler.resolve(strict=True)
    if capture(compiler, 'version').strip() != 'Thor '+version:
        raise ValueError('Compiler and release/version differ. Rebuild the selected compiler.')
    system = {'Darwin':'macos', 'Linux':'linux', 'Windows':'windows', 'FreeBSD':'freebsd', 'NetBSD':'netbsd', 'OpenBSD':'openbsd'}[platform.system()]
    arch = {'arm64':'aarch64', 'aarch64':'aarch64', 'AMD64':'x86_64', 'amd64':'x86_64', 'evbarm':'aarch64', 'x86_64':'x86_64'}[platform.machine()]
    options.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='thor-distribution-') as temporary:
        stage = Path(temporary)
        for source, name in ((compiler, 'thor'+suffix), (options.installer, 'thor-install'+suffix),
                             (options.tls, 'tls/bin/tls'+suffix), (options.vsix, 'editors/thor-language.vsix'),
                             (odin, 'odin/odin'+suffix)):
            copy(source, stage/name)
            if name.endswith(suffix) and source in (compiler, options.installer, options.tls, odin):
                (stage/name).chmod(0o755)
        for name in ('runtime','frontend','templates'):
            tree(ROOT/name, stage/name)
        if os.name!='nt':copy(ROOT/'llvm-symbolizer-thor',stage/'llvm-symbolizer-thor')
        if system=='macos':
            symbolizer=shutil.which('llvm-symbolizer') or next((str(p) for p in [Path('/opt/homebrew/opt/llvm/bin/llvm-symbolizer'),Path('/usr/local/opt/llvm/bin/llvm-symbolizer')] if p.is_file()),None)
            if not symbolizer:raise ValueError('A macOS distribution needs llvm-symbolizer for native source-mapped ASan reports.')
            copy(Path(symbolizer),stage/'odin/libexec/llvm-symbolizer')
            resources=Path(symbolizer).resolve().parents[1]/'lib/clang'
            candidates=sorted(p for p in resources.glob('*/lib/darwin') if (p/'libclang_rt.asan_osx_dynamic.dylib').is_file())
            if not candidates:raise ValueError('LLVM must include the macOS compiler-rt sanitizer runtime.')
            tree(candidates[-1],stage/'odin/clang-resource/lib/darwin')
        tree(ROOT/'tls/builtin', stage/'tls/builtin')
        stage_source(stage/'ols',options.ols_source)
        for name in ('base','core','vendor','shared'):
            if (sdk/name).is_dir(): tree(sdk/name, stage/'odin'/name)
        for p in sdk.iterdir():
            native_lib=(system=='windows' and p.suffix.lower()=='.dll' or system=='macos' and p.suffix.lower()=='.dylib' or system=='linux' and '.so' in p.name)
            if p.is_file() and (native_lib or re.match(r'(?i)^(license|copying|notice)',p.name)):
                copy(p, stage/'odin'/p.name)
        if system=='windows' and (stage/'odin/LLVM-C.dll').is_file():
            # The reviewed Windows SDK commit updates this vendored runtime to
            # LLVM 22.1.8, but Odin's root license covers Odin itself.
            copy(ROOT/'install/licenses/LLVM-22.1.8.txt',stage/'licenses/native/LLVM-C.dll/LICENSE.txt')
        for name in ('LICENSE','NOTICE','cli/LICENSE','tls/LICENSE','editors/vscode/LICENSE','install/profiles/Thor.code-profile'):
            copy(ROOT/name, stage/name)
        rust_licenses(stage)
        relocate_odin(stage)
        files = {}
        for p in sorted(stage.rglob('*')):
            if p.is_file():
                files[p.relative_to(stage).as_posix()] = dict(size=p.stat().st_size,sha256=sha(p),executable=bool(p.stat().st_mode & 0o111))
        manifest = dict(schema=1,product='Thor',version=version,target=system+'-'+arch,odin_version=odin_version,files=files)
        raw = (json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
        output = options.output/f'thor-{version}-{system}-{arch}.tar.gz'
        temporary_output = output.with_suffix('.tmp')
        with temporary_output.open('wb') as archive_stream, gzip.GzipFile(fileobj=archive_stream,mode='wb',filename='',mtime=0,compresslevel=6) as compressed, tarfile.open(fileobj=compressed,mode='w',format=tarfile.PAX_FORMAT) as archive:
            info=tarfile.TarInfo('THOR-DISTRIBUTION.json');info.size=len(raw);info.mode=0o644
            archive.addfile(info,io.BytesIO(raw))
            for name in files:
                info=archive.gettarinfo(str(stage/name),arcname=name)
                info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
                with (stage/name).open('rb') as stream: archive.addfile(info,stream)
        temporary_output.replace(output)
        output.with_name(output.name+'.sha256').write_text(sha(output)+'  '+output.name+'\n')
        if options.output.resolve()==(ROOT/'dist').resolve():
            if __package__:from .storage import prune
            else:from storage import prune
            prune(ROOT,apply=True)
        print(output)
        return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    suffix='.exe' if os.name=='nt' else ''
    parser.add_argument('--odin',default=os.environ.get('THOR_ODIN','odin'))
    parser.add_argument('--compiler',type=Path,default=ROOT/'rust/target/release'/('thor-rust'+suffix))
    parser.add_argument('--installer',type=Path,default=ROOT/'install/target/release'/('thor-install'+suffix))
    parser.add_argument('--tls',type=Path,default=ROOT/'tls/bin'/('tls'+suffix))
    parser.add_argument('--vsix',type=Path,default=ROOT/'editors/vscode/thor-language.vsix')
    parser.add_argument('--ols-source',type=Path,help='Pinned upstream OLS checkout (otherwise download and verify pinned source)')
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    options=parser.parse_args()
    try:build(options)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:parser.exit(1,f'distribution build failed: {error}\n')


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Report/prune disposable Thor development output; never traverse linked trees.

Dry-run by default. --apply removes obsolete caches, native object files, copied
release bundles and generated integration binaries, preserving source, reports,
current release tools and the current/previous distribution archives. This does
not modify Docker, virtual machines, editor settings or unrelated toolchains.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
LEGACY={'parse-v1','rust-parse-v1','rust-syntax-v1','sdk-parser','eval-v1','prepared-v1','identity-v1','scalar-v1','request-v1','lifetime-v1','benchmarks','pure-inactive','string-repeat-probe'}
MAGIC={b'\x7fELF',b'\xcf\xfa\xed\xfe',b'\xce\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xfe\xed\xfa\xce',b'\xca\xfe\xba\xbe',b'BC\xc0\xde',b'\xde\xc0\x17\x0b'}
CACHE_NAMES={'rust-parse-v1','rust-syntax-v1','eval-v1','prepared-v1','identity-v1','scalar-v1','request-v1','lifetime-v1'}

def cache_tree(path):
    for base,dirs,files in os.walk(path,followlinks=False):
        if any((Path(base)/n).is_symlink() or not re.fullmatch('[a-f0-9]{64}',n) for n in dirs):return False
        if any(not re.fullmatch(r'[a-f0-9]{64}\.(json|bin)',n) or (Path(base)/n).is_symlink() for n in files):return False
    return True

def allocated(path):
    total=0;seen=set()
    for base,dirs,files in os.walk(path,followlinks=False):
        dirs[:]=[name for name in dirs if not (Path(base)/name).is_symlink()]
        for name in files:
            try:s=(Path(base)/name).lstat()
            except OSError:continue
            identity=(s.st_dev,s.st_ino)
            if identity in seen:continue
            seen.add(identity);total+=getattr(s,'st_blocks',(s.st_size+511)//512)*512
    return total

def candidates(root,*,reset_builds=False):
    """Only enumerated generated locations; unknown directories are preserved."""
    result=[]
    for base,dirs,_ in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name not in {'.git','node_modules','target','dist','lib','artifacts'} and not (Path(base)/name).is_symlink()]
        if '.thor-cache' in dirs:
            cache=Path(base)/'.thor-cache';dirs.remove('.thor-cache')
            frontends=sorted((p for p in cache.iterdir() if re.fullmatch(r'frontend-[a-f0-9]{20}(\.exe)?',p.name) and p.is_file() and not p.is_symlink()),key=lambda p:p.stat().st_mtime,reverse=True)
            for entry in cache.iterdir():
                if entry.name in LEGACY:result.append(entry)
            result.extend(p for p in frontends if reset_builds or (p not in frontends[:4] and time.time()-p.stat().st_mtime>7*86400))
    for package in ('rust','install'):
        target=root/package/'target'
        # Reduced debug profiles are small. If a history crosses the development
        # budget, discard it before the next build rather than keeping variants.
        debug=target/'debug'
        if debug.is_dir() and not debug.is_symlink() and (reset_builds or allocated(debug)>1536*1024**2):result.append(debug)
        if (target/'permission-probe').exists():result.append(target/'permission-probe')
    library=root/'lib'
    if library.is_dir() and not library.is_symlink():
        for entry in library.iterdir():
            if re.fullmatch(r'runtime-[a-f0-9]{24}-(basic|native)',entry.name):result.append(entry)
    dist=root/'dist'
    if dist.is_dir() and not dist.is_symlink():
        for entry in dist.iterdir():
            if entry.is_dir() and not entry.is_symlink() and (entry.name.startswith('before-') or entry.name in {'editor-update','odin-editor-update','ols-auto-install','standard-ols-update','tls-hover-update'}):result.append(entry)
        publication=dist/'github-publication'
        if publication.is_dir() and not publication.is_symlink():
            result.extend(p for p in publication.iterdir() if p.name.startswith('fresh-github-') and p.is_dir() and not p.is_symlink())
        archives=sorted((p for p in dist.glob('thor-*.tar.gz') if p.is_file() and not p.is_symlink()),key=lambda p:p.stat().st_mtime,reverse=True)
        groups={}
        for archive in archives:
            match=re.fullmatch(r'thor-.+-(macos|linux|windows)-(aarch64|x86_64)\.tar\.gz',archive.name)
            if not match:continue
            group=match.groups();groups[group]=groups.get(group,0)+1
            if groups[group]>2:
                result.append(archive)
                checksum=Path(str(archive)+'.sha256')
                if checksum.exists():result.append(checksum)
    for artifacts in [root/'thor_test/artifacts',root/'integration-projects/results']:
        if not artifacts.is_dir() or artifacts.is_symlink():continue
        result.extend(artifacts/name for name in ['cache','runtime-cache','vet-cache','test-cache'] if (artifacts/name).is_dir() and not (artifacts/name).is_symlink())
        for base,dirs,files in os.walk(artifacts,followlinks=False):
            result.extend(Path(base)/name for name in dirs if name in CACHE_NAMES and not (Path(base)/name).is_symlink() and cache_tree(Path(base)/name))
            dirs[:]=[n for n in dirs if not (Path(base)/n).is_symlink() and (Path(base)/n) not in result]
            for name in files:
                path=Path(base)/name
                if path.is_symlink():continue
                with path.open('rb') as stream:head=stream.read(8)
                if head[:4] in MAGIC or head.startswith(b'!<arch>\n') or head[:2]==b'MZ':result.append(path)
    return sorted(set(result))

def prune(root,*,apply=False,reset_builds=False):
    root=root.resolve()
    tracked=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).split(b'\0')
    protected={root/os.fsdecode(name) for name in tracked if name}
    protected_directories={parent for source in protected for parent in source.parents}
    paths=candidates(root,reset_builds=reset_builds)
    records=[]
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root):continue
        if path in protected or path in protected_directories:
            raise RuntimeError('Refusing to remove tracked source: '+str(path))
        records.append({'path':str(path.relative_to(root))})
    if apply:
        for record in records:
            path=root/record['path']
            if path.is_symlink():raise RuntimeError('Cleanup target became a link: '+str(path))
            if path.name=='debug' and path.parent.name=='target' and (path.parent.parent/'Cargo.toml').is_file():
                # Cargo coordinates with active builds using its native locks.
                subprocess.run(['cargo','clean','--profile','dev','--manifest-path',str(path.parent.parent/'Cargo.toml')],check=True)
            elif path.is_dir():shutil.rmtree(path)
            elif path.exists():path.unlink()
    return {'applied':apply,'time':time.time(),'root':str(root),'removed' if apply else 'candidates':records}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--reset-builds',action='store_true',help='also discard existing debug build histories')
    parser.add_argument('--output',type=Path,help='save a cleanup inventory')
    options=parser.parse_args()
    result=prune(ROOT,apply=options.apply,reset_builds=options.reset_builds)
    text=json.dumps(result,indent=2)+'\n'
    if options.output:options.output.parent.mkdir(parents=True,exist_ok=True);options.output.write_text(text)
    print(text,end='')
    return 0

if __name__=='__main__':raise SystemExit(main())

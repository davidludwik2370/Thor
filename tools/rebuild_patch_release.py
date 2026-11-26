#!/usr/bin/env python3
"""Rebuild a version-only compiler/installer patch using a verified published SDK."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    subprocess.run(list(map(str,args)),check=True,cwd=ROOT,**kwargs)


def output(args):
    return subprocess.check_output(list(map(str,args)),cwd=ROOT,text=True).strip()


def version_only(previous,current,old,new):
    token='version = "'+old+'"'
    return previous.count(token)==1 and previous.replace(token,'version = "'+new+'"')==current


def guard(base_source,base_tag):
    if not re.fullmatch(r'[a-f0-9]{40}',base_source):raise ValueError('A full base source commit is required')
    run(['git','fetch','--depth=1','origin',base_source])
    old=output(['git','show',base_source+':release/version'])
    new=(ROOT/'release/version').read_text().strip()
    if not all(re.fullmatch(r'\d+\.\d+\.\d+',v) for v in (old,new)):raise ValueError('Invalid release version')
    if base_tag!='v'+old:raise ValueError('Base tag does not match the reviewed base source version')
    a,b=tuple(map(int,old.split('.'))),tuple(map(int,new.split('.')))
    if a[:2]!=b[:2] or b[2]<=a[2]:raise ValueError('Only a newer patch version may reuse the SDK')
    for file in ('rust/Cargo.toml','rust/Cargo.lock','install/Cargo.toml','install/Cargo.lock'):
        previous=subprocess.check_output(['git','show',base_source+':'+file],cwd=ROOT,text=True)
        if not version_only(previous,(ROOT/file).read_text(),old,new):raise ValueError('Manifest/dependencies changed: '+file)
    paths=['rust',':!rust/Cargo.toml',':!rust/Cargo.lock','runtime','frontend','tls','editors','templates','assets',
           '.cargo','llvm-symbolizer-thor','release/linux-compatibility.json','release/macos-minimum',
           'install/linux-vendor.json','install/ols-source.json','install/source-tools.json',
           'install/git-lfs-source.json','install/cmake-source.json']
    run(['git','diff','--exit-code',base_source,'--',*paths])
    release=json.loads(output(['gh','release','view',base_tag,'--json','isDraft']))
    if release['isDraft']:raise ValueError('The reusable base must be published')
    return old,new


def bootstrap(args):
    launcher=[str(ROOT/'thor-install')]
    if os.name=='nt':launcher=[os.environ['ComSpec'],'/d','/c',str(ROOT/'thor-install.cmd')]
    run([*launcher,*args])


def build(base_source,base_tag,destination):
    old,new=guard(base_source,base_tag)
    system={'Linux':'linux','Darwin':'macos','Windows':'windows'}[platform.system()]
    arch={'x86_64':'x86_64','AMD64':'x86_64','arm64':'aarch64','aarch64':'aarch64'}[platform.machine()]
    temporary=Path(os.environ['RUNNER_TEMP']);previous=temporary/'thor-patch-base';previous.mkdir()
    filename=f'thor-{old}-{system}-{arch}.tar.gz'
    run(['gh','release','download',base_tag,'--pattern',filename+'*','--dir',previous])
    prefix=temporary/'thor-patch-install'
    bootstrap(['--bundle',previous/filename,'--prefix',prefix,'--no-install-deps'])
    roots=list((prefix/'versions').iterdir())
    if len(roots)!=1:raise ValueError('Expected one verified installed base')
    base=roots[0]
    manifest=json.loads((base/'THOR-DISTRIBUTION.json').read_text())
    if manifest['version']!=old or manifest['target']!=system+'-'+arch:raise ValueError('Base identity mismatch')
    target=temporary/'thor-patch-target';os.environ['CARGO_TARGET_DIR']=str(target)
    os.environ['CARGO_INCREMENTAL']='0';os.environ['CARGO_BUILD_JOBS']='2'
    for package in ('rust','install'):
        run(['cargo','build','--release','--locked','--manifest-path',ROOT/package/'Cargo.toml','-j','2'])
    suffix='.exe' if os.name=='nt' else ''
    run([sys.executable,ROOT/'tools/build_distribution.py','--odin',base/('odin/odin'+suffix),
         '--compiler',target/('release/thor-rust'+suffix),'--installer',target/('release/thor-install'+suffix),
         '--tls',base/('tls/bin/tls'+suffix),'--vsix',base/'editors/thor-language.vsix',
         '--output',destination,'--portable'])
    archive=destination/f'thor-{new}-{system}-{arch}.tar.gz'
    bootstrap(['--bundle',archive,'--prefix',prefix,'--no-install-deps'])
    bootstrap(['--doctor','--prefix',prefix])
    bootstrap(['--uninstall','--prefix',prefix]);bootstrap(['--uninstall','--prefix',prefix])
    if system!='windows':
        run([sys.executable,ROOT/'tools/release_compatibility.py',archive,'--report',destination/'native-abi.json'])
    (destination/'patch-build.json').write_text(json.dumps({'base_tag':base_tag,'base_source':base_source,
        'version':new,'target':system+'-'+arch,'compiler_implementation_unchanged':True,
        'verified_sdk_tls_reused':True,'upgrade_doctor_checked_unchecked_uninstall':True},indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-source',required=True);p.add_argument('--base-tag',required=True)
    p.add_argument('--guard-only',action='store_true');p.add_argument('--output',type=Path)
    o=p.parse_args()
    if o.guard_only:
        old,new=guard(o.base_source,o.base_tag);print(json.dumps({'base':old,'version':new}));return
    if not o.output:p.error('--output is required for a build')
    o.output.mkdir(parents=True,exist_ok=True);build(o.base_source,o.base_tag,o.output.resolve())


if __name__=='__main__':main()

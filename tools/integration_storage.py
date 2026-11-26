#!/usr/bin/env python3
"""Archive inactive integration replay workspaces, with verified restoration.

Canonical upstream checkouts, reports, access settings and credentials remain in
place. Converted copies and historical toolchains can be restored on demand.
Close integration runners/editors before explicitly archiving their workspaces.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
MANIFEST='THOR-WORKSPACES.json'

def allowed(name):
    return name in {'migrated','vetted','toolchains','results/repros','runtime-source','generated'} or bool(re.fullmatch(r'memory-vet-(final-)?\d{4}-\d{2}-\d{2}',name))

def stream_digest(stream):
    result=hashlib.sha256()
    for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()

def digest(path):
    with path.open('rb') as stream:return stream_digest(stream)

def junction(path,metadata=None):
    if metadata is None:
        try:metadata=path.lstat()
        except FileNotFoundError:return False
    # IO_REPARSE_TAG_MOUNT_POINT: junctions and volume mount points must not be
    # recursively inventoried or removed as ordinary source directories.
    return getattr(metadata,'st_reparse_tag',0)==0xA0000003

def inventory(area,roots):
    entries={}
    def visit(path):
        metadata=path.lstat();name=path.relative_to(area).as_posix()
        if junction(path,metadata):raise RuntimeError('Linked workspace preserved: '+str(path))
        record={'mode':stat.S_IMODE(metadata.st_mode),'mtime_ns':metadata.st_mtime_ns}
        if stat.S_ISLNK(metadata.st_mode):record={'kind':'link','target':os.readlink(path)}
        elif stat.S_ISDIR(metadata.st_mode):record['kind']='dir'
        elif stat.S_ISREG(metadata.st_mode):record.update(kind='file',size=metadata.st_size,sha256=digest(path))
        else:raise RuntimeError('Special file preserved; cannot archive: '+str(path))
        entries[name]=record
        if record['kind']=='dir':
            for child in sorted(path.iterdir()):visit(child)
    for name in roots:
        path=area/name
        if path.is_symlink() or not path.resolve().is_relative_to(area.resolve()):raise RuntimeError('Linked workspace preserved: '+str(path))
        visit(path)
    return {'schema':1,'roots':roots,'entries':entries}

def valid_name(name,roots):
    path=PurePosixPath(name)
    return bool(name) and not path.is_absolute() and path.as_posix()==name and '\\' not in name and '..' not in path.parts and any(name==r or name.startswith(r+'/') for r in roots)

def header(archive):
    member=archive.next()
    if member is None or member.name!=MANIFEST or not member.isfile() or member.size>32*1024**2:raise RuntimeError('Invalid workspace inventory')
    value=json.load(archive.extractfile(member));roots=value.get('roots',[]);entries=value.get('entries',{})
    if value.get('schema')!=1 or not roots or len(set(roots))!=len(roots) or not all(isinstance(r,str) and allowed(r) for r in roots) or not isinstance(entries,dict):raise RuntimeError('Invalid workspace roots')
    links={name for name,record in entries.items() if record.get('kind')=='link'}
    for name,record in entries.items():
        if not valid_name(name,roots) or record.get('kind') not in {'file','dir','link'} or any(parent.as_posix() in links for parent in PurePosixPath(name).parents):raise RuntimeError('Unsafe workspace path: '+name)
    return value

def verify(path):
    with tarfile.open(path,'r|gz') as archive:
        value=header(archive);seen={}
        for member in archive:
            if member.name==MANIFEST:continue
            record=value['entries'].get(member.name)
            if record is None or member.name in seen:raise RuntimeError('Unknown/duplicate workspace member')
            kind=record['kind']
            if member.isfile() and kind=='file':
                stream=archive.extractfile(member);hash=stream_digest(stream)
                if hash!=record['sha256'] or member.size!=record['size']:raise RuntimeError('Workspace checksum mismatch')
            elif member.islnk() and kind=='file':
                original=seen.get(member.linkname)
                if original is None or original.get('kind')!='file' or original['sha256']!=record['sha256']:raise RuntimeError('Unsafe workspace hard link')
            elif not (member.isdir() and kind=='dir' or member.issym() and kind=='link' and member.linkname==record['target']):raise RuntimeError('Invalid workspace member type')
            seen[member.name]=record
        if set(seen)!=set(value['entries']):raise RuntimeError('Incomplete workspace archive')
    return value

def archive_workspaces(area):
    area=area.resolve();destination=area/'archived-workspaces.tar.gz'
    roots=sorted(p.relative_to(area).as_posix() for p in area.iterdir() if allowed(p.name) and p.is_dir())
    if (area/'results/repros').is_dir():roots.append('results/repros')
    if not roots:return {'archived':[]}
    if destination.exists():
        old=verify(destination)
        if not set(old['roots']).issubset(roots):raise RuntimeError('Restore all archived workspaces before replacing their archive; existing archive preserved')
    value=inventory(area,roots);raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
    with tempfile.TemporaryDirectory(prefix='.archive-',dir=area) as temporary:
        path=Path(temporary)/'workspaces.tar.gz';content={}
        with tarfile.open(path,'w:gz',compresslevel=5) as archive:
            info=tarfile.TarInfo(MANIFEST);info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
            for name,record in value['entries'].items():
                source=area/name;info=archive.gettarinfo(str(source),arcname=name)
                info.uid=info.gid=0;info.uname=info.gname=''
                if record['kind']=='file':
                    old=content.get(record['sha256'])
                    if old is not None:info.type=tarfile.LNKTYPE;info.linkname=old;info.size=0;archive.addfile(info)
                    else:
                        with source.open('rb') as stream:archive.addfile(info,stream)
                        content[record['sha256']]=name
                else:archive.addfile(info)
        verified=verify(path)
        if verified!=value or inventory(area,roots)!=value:raise RuntimeError('Workspace changed during archiving; original files preserved')
        path.chmod(0o600);path.replace(destination)
    for name in roots:shutil.rmtree(area/name)
    return {'archived':roots,'files':len(value['entries']),'bytes':destination.stat().st_size,'sha256':digest(destination)}

def restore_workspaces(area):
    area=area.resolve();path=area/'archived-workspaces.tar.gz';value=verify(path)
    for name in value['roots']:
        destination=area/name
        if destination.exists() or destination.is_symlink() or junction(destination):raise RuntimeError('Existing workspace preserved: '+str(destination))
        if not destination.parent.resolve().is_relative_to(area):raise RuntimeError('Linked workspace parent preserved')
    with tempfile.TemporaryDirectory(prefix='.restore-',dir=area) as temporary:
        stage=Path(temporary)
        with tarfile.open(path,'r|gz') as archive:
            header(archive)
            for member in archive:
                if member.name==MANIFEST:continue
                record=value['entries'][member.name];target=stage/member.name
                if record['kind']=='dir':target.mkdir(parents=True,exist_ok=True)
                elif record['kind']=='file':
                    target.parent.mkdir(parents=True,exist_ok=True)
                    # Copy deduplicated members, so separate source workspaces
                    # never acquire shared mutable backing files after restore.
                    if member.islnk():shutil.copyfile(stage/member.linkname,target)
                    else:
                        with target.open('wb') as output:shutil.copyfileobj(archive.extractfile(member),output)
        for name,record in value['entries'].items():
            if record['kind']=='link':target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.symlink_to(record['target'])
        for name,record in reversed(list(value['entries'].items())):
            target=stage/name
            if record['kind']!='link':target.chmod(record['mode']);os.utime(target,ns=(record['mtime_ns'],record['mtime_ns']))
        if inventory(stage,value['roots'])!=value:raise RuntimeError('Restored workspace failed verification')
        moved=[]
        try:
            for name in value['roots']:
                destination=area/name
                if destination.exists() or destination.is_symlink() or junction(destination):raise RuntimeError('Workspace appeared during restoration; preserved')
                destination.parent.mkdir(parents=True,exist_ok=True);(stage/name).rename(destination);moved.append(name)
        except BaseException:
            for name in reversed(moved):(area/name).rename(stage/name)
            raise
    return {'restored':value['roots'],'files':len(value['entries'])}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['archive','restore','verify'])
    parser.add_argument('--area',choices=['integration','runtime'],default='integration',help='integration workspaces or captured runtime replay inputs')
    options=parser.parse_args()
    area=ROOT/('integration-projects' if options.area=='integration' else 'thor_test/artifacts')
    result=archive_workspaces(area) if options.action=='archive' else restore_workspaces(area) if options.action=='restore' else {'verified_roots':verify(area/'archived-workspaces.tar.gz')['roots']}
    print(json.dumps(result,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())

"""Original-source adapters for native documentation and cleanup commands."""
from __future__ import annotations
import json
import os
from pathlib import Path
import struct
import subprocess
from .compiler import ThorError


def remap_text(project, text):
    text = project.diagnostic_adapter().remap_references(text) if hasattr(project.diagnostic_adapter(), 'remap_references') else text
    paths = {}
    for original, generated in project.outputs.items():
        paths[str(generated)] = str(original)
        paths[str(generated.parent)] = str(original.parent)
    for generated in sorted(paths, key=len, reverse=True):
        text = text.replace(generated.replace(' ', '\\ '), paths[generated].replace(' ', '\\ '))
        text = text.replace(generated, paths[generated])
    return text


def documentation(project, native, short=False):
    declarations, basenames = {}, {}
    for original, generated in project.outputs.items():
        source = project.sources[original]
        basenames[generated.name] = original.name
        package = source.nodes[0].data['name']
        for declaration in source.nodes:
            if declaration.kind != 'Declaration' or any(n.kind in {'Procedure','StructType','UnionType'} for n in declaration.ancestors()):
                continue
            end = declaration.end
            values = declaration.group('values')
            if values:
                value = values[0]
                if value.kind == 'Procedure' and value.role('body'):
                    end = value.role('body').start
                elif short and value.kind in {'StructType','UnionType','EnumType'} and '{' in value.text:
                    end = value.start+len(value.text.split('{',1)[0].encode())
            signature = source.text(declaration.start,end).rstrip('; \t\r\n')
            for name in declaration.group('names'):
                declarations.setdefault((package,name.text),[]).append(signature)
    output=[]; package=''; body=False
    for line in native.splitlines():
        if line.startswith('package '):package=line[8:].strip()
        if body:
            if line.startswith('\t\t}'): body=False
            continue
        if line.startswith('\t\t') and not line.startswith('\t\t\t'):
            name, sep, _ = line.strip().partition(' :')
            values=declarations.get((package,name),[]) if sep else []
            if len(values)==1:
                output.extend('\t\t'+row for row in values[0].splitlines()); body=line.rstrip().endswith('{'); continue
        output.append('\t\t'+basenames[line.strip()] if line.strip() in basenames else line)
    return remap_text(project,'\n'.join(output)+'\n')


def semicolon_edits(project, thor_only=False):
    from .vet import Fix
    root = project.entry.parent if project.file_entry else project.entry
    edits=[]
    for source in project.sources.values():
        if (not source.path.is_relative_to(root) or source.info.get('excluded')
                or thor_only and not source.thor or b';' not in source.raw):
            continue
        args=[str(project.frontend), str(source.path), '--stdin', '--source-tools',
              '--project-name='+project.entry.stem]
        if not source.thor: args.append('--odin')
        if project.test: args.append('--test')
        if project.target: args.extend(['--target-os='+project.target[0], '--target-arch='+project.target[1]])
        result=subprocess.run(args, input=source.raw, capture_output=True)
        if result.returncode:raise ThorError(f'Could not inspect semicolons in {source.path}')
        spans=sorted(set(tuple(span) for span in json.loads(result.stdout).get('optional_semicolons') or []))
        for start,end in spans:
            edits.append(Fix(source,start,end,b'','semicolon','',start,end))
    return edits


def strip_semicolons(project, flags):
    from .vet import apply
    edits=semicolon_edits(project)
    originals={fix.source.path:fix.source.raw for fix in edits}
    if '-keep-temp-files' in flags:
        for path, raw in originals.items():
            backup=Path(str(path)+'~backup.thor-temp')
            with backup.open('xb') as stream:stream.write(raw)
            os.chmod(backup,path.stat().st_mode)
    apply(edits)
    return f'Files stripped of unnecessary semicolons: {len(originals)}\n'


def remap_exports(project, flags):
    for flag in flags:
        if not flag.startswith(('-export-dependencies-file:', '-export-defineables:', '-export-timings-file:', '-export-linked-libs-file:')):continue
        path=Path(flag.split(':',1)[1])
        if not path.is_file():continue
        text=path.read_text()
        try:value=json.loads(text)
        except ValueError:text=remap_text(project,text)
        else:
            def visit(value):
                if isinstance(value,str):return remap_text(project,value)
                if isinstance(value,list):return [visit(item) for item in value]
                if isinstance(value,dict):return {k:visit(v) for k,v in value.items()}
                return value
            text=json.dumps(visit(value),indent=2)+'\n'
        path.write_text(text)


def remap_document(project, output):
    filename=next((line.removeprefix('Wrote .odin-doc file to: ').strip() for line in output.splitlines() if line.startswith('Wrote .odin-doc file to: ')),None)
    if filename is None:raise ThorError('The native compiler did not report a documentation export path')
    path=Path(filename); data=bytearray(path.read_bytes())
    def word(offset):return struct.unpack_from('<I',data,offset)[0]
    def put(offset,value):struct.pack_into('<I',data,offset,value)
    def array(offset,stride):
        base,count=word(offset),word(offset+4)
        if base+count*stride>len(data):raise ThorError('Invalid .odin-doc array')
        return range(base,base+count*stride,stride)
    def string(offset):
        base,count=word(offset),word(offset+4)
        if base+count>len(data):raise ThorError('Invalid .odin-doc string')
        return bytes(data[base:base+count]).decode()
    def set_string(offset,text):
        raw=text.encode();base=len(data);data.extend(raw+b'\0');put(offset,base);put(offset+4,len(raw))
    if data[:8]!=b'odindoc\0' or data[12:15]!=b'\0\3\2' or word(20)!=60 or word(16)!=len(data):
        raise ThorError('Unsupported .odin-doc format from the selected toolchain; use thor doc for text documentation')
    files=list(array(28,12));packages=list(array(36,44));entities=list(array(44,112))
    originals={}; reverse={str(generated):original for original,generated in project.outputs.items()}
    adapter=project.diagnostic_adapter()
    for index,file in enumerate(files):
        name=string(file+4)
        if name in reverse:originals[index]=(project.sources[reverse[name]], name)
        mapped=remap_text(project,name)
        if mapped!=name:set_string(file+4,mapped)
    for entity in entities:
        name=string(entity+32);file=word(entity+16)
        if file not in originals:continue
        source,generated=originals[file];origin=adapter.lookup(generated,word(entity+28))
        if origin is None:continue
        from .sourcemap import position
        line,column=position(source.raw,origin.start)
        put(entity+20,line);put(entity+24,column);put(entity+28,origin.start)
        declaration=next((n for n in source.nodes if n.kind=='Declaration' and n.start<=origin.start<=origin.end<=n.end and any(nm.text==name for nm in n.group('names'))),None)
        if declaration and declaration.group('values'):
            value=declaration.group('values')[0]
            end=value.role('body').start if value.kind=='Procedure' and value.role('body') else value.end
            set_string(entity+44,source.text(value.start,end).strip())
    for package in packages:
        name=string(package);mapped=remap_text(project,name)
        if mapped!=name:set_string(package,mapped)
        entries=list(array(package+36,12));filtered=b''.join(data[e:e+12] for e in entries if not string(e).startswith('__thor_'))
        if len(filtered)!=len(entries)*12:
            while len(data)%4:data.append(0)
            base=len(data);data.extend(filtered);put(package+36,base);put(package+40,len(filtered)//12)
    put(16,len(data));digest=0x811c9dc5
    for byte in data[60:]:digest=((digest^byte)*0x01000193)&0xffffffff
    put(24,digest);path.write_bytes(data)

"""Native vet checks on source-preserving syntax, before ordinary lowering.

Odin remains the authority for tag names, ordering, package scopes and verdicts.
The checking program is compiled but never executed.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from . import cli
from .compiler import ThorError
from .sourcemap import MappingBuilder, position


def tags(source):
    cursor=0
    for tag in source.info.get('file_tags', []):
        if tag.removeprefix('#+').split()[:1]!=['vet']:continue
        encoded=tag.encode()
        start=source.raw.find(encoded,cursor)
        while start>=0 and any(a<=start<b for a,b in source.info.get('comments', [])):
            start=source.raw.find(encoded,start+1)
        if start>=0:
            cursor=start+len(encoded)
            yield start,cursor,tag


def active(project):
    if project.native_policy_probe:return False
    sources=[s for s in project.sources.values() if s.thor and not s.excluded]
    if not sources:return False
    if any(cli.native_source_check(f.lstrip('-').split(':',1)[0]) for f in project.flags):return True
    safe={'no_alloc','semicolon','!semicolon','when-shadowing','!when-shadowing'}
    return any(not tag.removeprefix('#+vet').strip() or
               any(name not in safe for name in tag.removeprefix('#+vet').replace(',',' ').split())
               for source in sources for _,_,tag in tags(source))


def projection(source):
    edits=list(source.edits)
    from . import noalloc
    for node in source.nodes:
        if node.kind!='Attribute':continue
        removed=noalloc.elements(node)
        if not removed:continue
        kept=[item.text for item in node.group('elems') if item not in removed]
        edits.append((node.start,node.end,'@('+', '.join(kept)+')' if kept else ''))
    builder=MappingBuilder(debug=False)
    parts=[];cursor=0
    for start,end,text in sorted(edits):
        if start<cursor:source.error('Overlapping native source-check projections.',start,end)
        parts.append(builder.wrap(source.raw[cursor:start].decode(),source.path,cursor,start,exact=True))
        parts.append(builder.wrap(text,source.path,start,end,'native-policy-syntax'))
        cursor=end
    parts.append(builder.wrap(source.raw[cursor:].decode(),source.path,cursor,len(source.raw),exact=True))
    return builder.finish(''.join(parts))


def validate(project,command,odin):
    if not active(project):return 0,[]
    # Keep one checking representation for Thor-only constructs; ordinary
    # Python lowering and the source-preserving native-only path stay independent.
    from . import unused_attributes, vet_attributes
    if any(any(f['kind']!='PureFunction' for f in s.info.get('features', [])) or
           any(n.kind=='Using' or n.kind=='Field' and n.data.get('using_field') or n.kind=='Attribute' and (unused_attributes.elements(n) or vet_attributes.elements(n)) for n in s.nodes)
           for s in project.sources.values() if s.thor and not s.excluded):
        return shared_validate(project,command,odin)
    from .driver import Project, backend_failure
    views={source.path:projection(source) for source in project.sources.values() if source.thor and not source.excluded}
    def remap(value):
        if isinstance(value,dict):
            pos=value.get('pos')
            if pos and Path(pos.get('file','')) in views:
                path=Path(pos['file']);source=project.sources[path]
                offset=pos.get('offset',0)
                origin=views[path][1].lookup(offset,max(1,pos.get('end_offset',offset+1)-offset))
                if origin:
                    line,column=position(source.raw,origin.start)
                    end_line,end_column=position(source.raw,origin.end)
                    value['pos']={**pos,'offset':origin.start,'end_offset':origin.end,
                                  'line':line,'column':column,'end_line':end_line,'end_column':end_column}
            for key,item in value.items():
                if key!='pos':remap(item)
        elif isinstance(value,list):
            for item in value:remap(item)
    try:
        with tempfile.TemporaryDirectory(prefix='thor-native-check-') as directory:
            flags=[f for f in project.flags if not f.startswith(('-out:','-show-','-export-'))
                   and f not in {'-keep-executable','-keep-temp-files','-print-linker-flags','-build-diagnostics'}]
            if project.test and not any(f.startswith('-build-mode:') for f in flags):flags.append('-build-mode:test')
            probe=Project(project.entry,project.frontend,flags,test=project.test,
                          overlays={**project.overlays,**{path:text.encode() for path,(text,_) in views.items()}},
                          cache_frontend=project.cache_frontend,cache_directory=project.cache_directory,
                          native_policy_probe=True)
            target=probe.emit(Path(directory)/'generated',persist_maps=True)
            action='build' if project.test or command in {'build','run','test','publish'} else 'check'
            args=[odin,action,str(target),*(['-file'] if probe.file_entry else []),*probe.odin_flags(),'-json-errors']
            if action=='build':
                output=Path(directory)/'artifact'
                if any(f in {'-build-mode:llvm','-build-mode:llvm-ir'} for f in flags):output.mkdir()
                args.append('-out:'+str(output))
            result=subprocess.run(args,capture_output=True,text=True)
            if result.returncode<0:return 1,[backend_failure(result.returncode)]
            diagnostics=[d for source in probe.sources.values() for d in source.diagnostics]
            if result.stderr:
                try:diagnostics.extend(json.loads(result.stderr).get('errors', []))
                except ValueError:
                    diagnostics.append({'type':'error' if result.returncode else 'warning',
                                        'stage':'toolchain','pos':None,'msgs':[result.stderr.strip()]})
            diagnostics=probe.diagnostic_adapter().translate(diagnostics)
            remap(diagnostics)
            if result.returncode:return result.returncode,diagnostics
    except ThorError as error:
        if error.diagnostics:remap(error.diagnostics)
        raise
    project.native_policy_checked=True
    for source in project.sources.values():
        if not source.thor:continue
        for start,end,_ in tags(source):
            source.edits=[e for e in source.edits if not(start<=e[0] and e[1]<=end)]
            source.edits.append((start,end,''))
    return 0,diagnostics


def shared_validate(project,command,odin):
    root=Path(__file__).resolve().parents[1]
    compiler=Path(os.environ.get('THOR_NATIVE_CHECK_COMPILER',root/'rust/target/release/thor-rust'))
    if not compiler.is_file():
        raise ThorError('Native vet for Thor expressions requires the Rust checking engine. Build rust/Cargo.toml with cargo build --release.')
    with tempfile.TemporaryDirectory(prefix='thor-native-policy-') as directory:
        directory=Path(directory)
        action='build' if project.test or command in {'build','run','test','publish'} else 'check'
        args=[str(compiler),action,str(project.entry),'--native-policy-only','-json-errors',
              '--odin',str(odin),'--output-dir',str(directory/'generated'),'--track-allocations','off','--no-sanitize',
              '--cache-dir',str(project.cache_directory)]
        args.extend(f for f in project.flags if not f.startswith(('-out:','-show-','-export-'))
                    and f not in {'-keep-executable','-keep-temp-files','-print-linker-flags','-build-diagnostics'})
        if project.test and not any(f.startswith('-build-mode:') for f in project.flags):args.append('-build-mode:test')
        if project.overlays:
            files={}
            for i,(path,data) in enumerate(project.overlays.items()):
                replacement=directory/f'overlay-{i}.thor';replacement.write_bytes(data)
                files[str(path)]=str(replacement)
            overlay=directory/'overlay.json';overlay.write_text(json.dumps({'Replace':files}))
            args.extend(['--overlay',str(overlay)])
        result=subprocess.run(args,capture_output=True,text=True)
        try:diagnostics=json.loads(result.stderr or '{"errors":[]}').get('errors',[])
        except ValueError:
            raise ThorError(result.stderr.strip() or 'Native checking engine failed without diagnostics.')
        if result.returncode:return result.returncode,diagnostics
    project.native_policy_checked=True
    for source in project.sources.values():
        if not source.thor:continue
        for start,end,_ in tags(source):
            source.edits=[e for e in source.edits if not(start<=e[0] and e[1]<=end)]
            source.edits.append((start,end,''))
    return 0,diagnostics

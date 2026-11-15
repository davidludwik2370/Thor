"""Reuse verified straight-line scalar #run sites before SDK AST discovery."""
import hashlib
from pathlib import Path

from .dependencies import Inputs
from .cache import canonical
from .semantics import descendants

PRIMITIVES={'bool','int','uint','uintptr','i8','i16','i32','i64','i128',
            'u8','u16','u32','u64','u128','f16','f32','f64',
            'complex32','complex64','complex128','quaternion64','quaternion128','quaternion256'}


def declaration_text(node):
    start=min([node.start,*[attribute.start for attribute in node.group('attributes')]])
    return node.source.text(start,node.end)


def headers(project):
    from .native import NativeSources
    result=[]
    for source in project.sources.values():
        if NativeSources.support(source):continue
        if source.excluded:
            result.append([str(source.path),True,''])
            continue
        spans=[]
        for node in source.nodes:
            if node.kind!='Declaration' or source.scope(node).kind!='File':continue
            values=node.group('values')
            body=values[0].role('body') if len(values)==1 and values[0].kind=='Procedure' else None
            if body:spans.append((body.start,body.end))
        # Keep all file-level syntax: imports, attributes, file tags, foreign
        # defaults, conditional declarations and type schemas. Only procedure
        # bodies may differ without changing this interface fingerprint.
        chunks=[];cursor=0
        for start,end in sorted(spans):
            chunks.extend([source.text(cursor,start),'{/* procedure body */}']);cursor=end
        chunks.append(source.text(cursor,len(source.raw)))
        result.append([str(source.path),source.excluded,''.join(chunks)])
    return result


class ScalarSites:
    def __init__(self,evaluator):
        self.e=evaluator

    def eligible(self,site):
        e=self.e
        if site.kind!='Run' or not e.cache or not e.cache.enabled or e.p.overlays or e.specializations.relevant(site):return False
        owner=e.purity.owner(site)
        if site.source.scope(site) is not (owner.role('body') if owner else site.source.root):return False
        if owner and any(node.kind in {'When','CompileSwitch','ForeignBlock'} for node in descendants(owner.role('body'))):return False
        if any(node.kind=='Using' or node.kind=='Declaration' and node.data.get('using_field') for node in site.source.nodes):return False
        return True

    def key(self,site):
        owner=self.e.purity.owner(site)
        names=owner.parent.group('names') if owner and owner.parent else []
        context=[name.text for name in names]
        return hashlib.sha256(canonical([str(self.e.p.entry),str(self.e.p.native_sources.root),str(site.source.path),context,site.role('expr').text,
            self.e.p.flags,self.e.p.test,self.e.cache.identity(self.e.odin,self.e.root)])).hexdigest()

    def bindings(self,site,names):
        r=self.e.r;enabled=r.native_enabled;r.native_enabled=False
        try:
            result=[]
            for name in names:
                symbol=r.lookup(name,site)
                if symbol is None:result.append([name,None])
                else:
                    declaration=symbol.declaration
                    local=declaration.source.scope(declaration).kind!='File'
                    result.append([name,str(declaration.source.path),declaration.kind,
                        declaration_text(declaration) if local else [n.text for n in declaration.group('names')]])
            return result
        finally:r.native_enabled=enabled

    def bodies(self,descriptors):
        result=[]
        for path,names in descriptors:
            source=self.e.p.sources.get(Path(path))
            if source is None:return None
            result.append([path,names,[declaration_text(node) for node in source.nodes if node.kind=='Declaration'
                and source.scope(node).kind=='File' and [n.text for n in node.group('names')]==names]])
        return result

    def load(self,site):
        if not self.eligible(site):return None
        record=self.e.cache.read_record('scalar-v1',self.key(site))
        if not isinstance(record,dict):return None
        try:
            if record['type'] not in PRIMITIVES or record['headers']!=headers(self.e.p):return None
            if record['bindings']!=self.bindings(site,record['names']):return None
            if record['bodies']!=self.bodies(record['descriptors']):return None
            if not Inputs(record['inputs']).valid():return None
            if record['witness'] not in {'procedure','expression'}:return None
            value=self.e.cache.get(record['target'])
            if value is None:
                self.e.failed_scalar_targets[site.role('expr')]=record['target']
                return None
            self.e.cache.prepared_hits+=1
            self.e.scalar_witnesses[site.role('expr')]=record['witness']
            return value
        except (OSError,ValueError,KeyError,TypeError):return None

    def save(self,site):
        e=self.e;call=site.role('expr')
        if not self.eligible(site) or call not in e.execution_records:return
        target,keep,procedures=e.execution_records[call]
        typ=e.r.type_of(call)
        if target is None or typ.kind!='scalar' or typ.nominal is not None or typ.name not in PRIMITIVES:return
        if any(e.r.type_of(arg.role('value') if arg.kind=='FieldValue' else arg).kind not in {'scalar','string'}
               or any(node.kind=='Run' for node in descendants(arg)) for arg in call.group('args')):return
        if e.r.lookup(typ.name,site) is not None or typ.name in site.source.imports or typ.name in site.source.selected:return
        if any(e.r.generic_parameters(proc.role('type')) for proc in procedures):return
        if any(node.kind=='Directive' and node.data['name']=='procedure' for item in (call,*keep) for node in descendants(item)):return
        scopes=set(site.ancestors());names=set()
        for item in (call,*keep):
            if item.source not in e.p.sources.values():continue
            for node in descendants(item):
                if not e.purity.reference(node):continue
                symbol=e.r.lookup(node.text,node)
                if e.purity.owner(node) is e.purity.owner(site) or symbol and symbol.scope in scopes:
                    names.add(node.text)
        names=sorted(names|{typ.name})
        descriptors=sorted({(str(node.source.path),tuple(name.text for name in node.group('names')))
            for node in keep if node.kind=='Declaration' and node.source in e.p.sources.values()
            and node.source.scope(node).kind=='File' and any(value.kind=='Procedure' for value in node.group('values'))})
        descriptors=[[path,list(names)] for path,names in descriptors]
        inputs=Inputs()
        for source in e.p.sources.values():inputs.source(source,source_file=False)
        for directory,members in e.p.native_sources.packages.items():
            inputs.package(directory,[member.path for member in members],suffixes=('.odin',))
            for source in members:inputs.source(source)
        if not inputs.valid():return
        e.cache.write_record('scalar-v1',self.key(site),dict(type=typ.name,target=target,
            witness='procedure' if e.r.resolve_call(call) else 'expression',
            headers=headers(e.p),names=names,bindings=self.bindings(site,names),
            descriptors=descriptors,bodies=self.bodies(descriptors),inputs=inputs.data))

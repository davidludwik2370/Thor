"""Checked procedure bodies used only by calls from a declared fn.

Keep ordinary procedure calls, signatures and shallow result ownership intact.
Checked versions stay beside their original declarations, preserving generics,
private types, lexical scopes and native conditional compilation.
"""
import hashlib
import json
import os
from pathlib import Path

from .compiler import ThorError
from .imports import file_private


class PureCalls:
    def __init__(self, purity):
        self.purity=purity
        self.targets={}
        self.names={}
        self.declarations={}
        self.emitting=set()
        self.prepared=False
        self.marker='__thor_checked_call'
        self.wrapping=set()
        self.origins={}

    def queried(self,node):
        return any(parent.kind=='Call' and self.purity.builtin(parent) in self.purity.type_queries
                   and parent.role('callee') is not node for parent in node.ancestors())

    def name(self,proc):
        if proc in self.names:return self.names[proc]
        declaration=proc.parent
        while declaration and declaration.kind=='Paren':declaration=declaration.parent
        if not declaration or declaration.kind!='Declaration':return None
        source=proc.source;scope=source.scope(declaration)
        names=tuple(node.text for node in declaration.group('names'))
        private_file=file_private(declaration)
        lexical=None if scope.kind=='File' and not private_file else (str(source.path),scope.start)
        key=(str(source.path.parent),lexical,names,declaration.group('values').index(proc) if proc in declaration.group('values') else 0)
        prefix=min(unit.prefix for unit in self.purity.r.sources if unit.path.parent==source.path.parent)
        name=prefix+'pure_'+hashlib.sha256(repr(key).encode()).hexdigest()[:20]
        self.names[proc]=name
        self.declarations.setdefault(declaration,[]).append(proc)
        return name

    def prepare(self):
        if self.prepared or self.purity.eval_mode:return
        self.prepared=True
        p=self.purity.project;r=self.purity.r
        queue=[node for source in p.sources.values() if not source.excluded
               for node in source.nodes if self.purity.pure(node)]
        if not queue:return
        enabled=r.native_enabled;r.native_enabled=True
        visited=set()
        try:
            while queue:
                proc=queue.pop()
                if proc in visited:continue
                visited.add(proc)
                if self.purity.io_procedure(proc):continue
                self.purity.analyze(proc)
                if proc in self.purity.contracts:continue
                for node in self.purity.walk(proc):
                    if node.kind!='Call' or self.queried(node) or any(a.kind=='Run' for a in node.ancestors()):continue
                    if self.purity.io_call(node):
                        self.purity.problem(node.role('callee'),self.purity.io_message(node))
                        continue
                    if self.purity.builtin(node) or self.purity.approved(node) or node.source.denotes_type(node.role('callee')):continue
                    targets=self.purity.targets(node)
                    if r.generic_parameters(proc.role('type')):
                        # A resolver approximation for an unbound $T must not
                        # choose a variadic-any overload ahead of an exact
                        # overload available at native instantiation.
                        candidates=r.procedure_candidates(node.role('callee'))
                        implementations=[signature.parent for _,signature in candidates]
                        if len(implementations)>1 and all(target and target.kind=='Procedure' and target.role('body') for target in implementations):
                            targets=list(dict.fromkeys(implementations))
                    conditional=False
                    for target in list(targets):
                        declaration=target.parent
                        if declaration and declaration.kind=='Declaration':
                            for name in declaration.group('names'):
                                symbol=r.by_decl.get((declaration,name.text))
                                for alternative in symbol.candidates if symbol else []:
                                    if alternative.node.source is not node.source and file_private(alternative.declaration):continue
                                    if alternative.value and alternative.value.kind=='Procedure' and any(
                                        a.kind in {'When','CompileSwitch'} for a in alternative.declaration.ancestors()):
                                        conditional=True
                                        if alternative.value not in targets:targets.append(alternative.value)
                    self.targets[node]=targets
                    if targets and all(self.purity.io_procedure(target) for target in targets):
                        self.purity.problem(node.role('callee'),self.purity.io_message(node))
                        continue
                    native=getattr(p,'native_sources',None)
                    if native:
                        try:native.verify_access(node.role('callee'),r,context='pure code')
                        except ThorError as error:
                            message=error.diagnostics[0]['msgs'][0] if error.diagnostics else str(error)
                            self.purity.problem(node.role('callee'),message)
                    for target in targets:
                        self.origins.setdefault(target, self.origins.get(proc,node) if getattr(node.source,'native_inspected',False) else node)
                        checked_group=conditional or any(not self.purity.pure(candidate) for candidate in targets)
                        if checked_group and self.name(target) is None:
                            self.purity.problem(node,'Pure calls require a named, inspectable procedure body.')
                            continue
                        queue.append(target)
            self.marker=next(iter(p.sources.values())).prefix+'checked_call'
            while any(self.marker.encode() in source.raw for source in r.sources):self.marker+='_'
            native=getattr(p,'native_sources',None)
            if native:
                units={proc.source for proc in self.names if getattr(proc.source,'native_inspected',False)}
                for source in sorted(units,key=lambda source:str(source.path)):
                    p.sources[source.path]=source
                    p.packages.setdefault(source.path.parent,list(native.packages[source.path.parent]))
                if units:
                    p.pure_sdk_root=Path(native.root).resolve()
                    paths={source.path for source in units}|{path for path in p.overlays if p.pure_sdk_root in path.parents}
                    files=sorted(path.relative_to(p.pure_sdk_root).as_posix() for path in paths)
                    p.pure_sdk_collections={Path(file).parts[0] for file in files}
                    # Distinct layouts keep an old linked directory from ever
                    # becoming the parent of a newly generated source file.
                    layout=hashlib.sha256(json.dumps(files).encode()).hexdigest()[:12]
                    p.pure_sdk_subdir='.thor-sdk-linked-'+layout
        finally:r.native_enabled=enabled

    def access(self,caller,proc):
        name=self.names.get(proc)
        if name is None:
            name=next((node.text for node in proc.parent.group('names')),'')
        owner=proc.source
        if owner.path.parent==caller.path.parent:return name
        if getattr(owner,'native_inspected',False):
            native=self.purity.project.native_sources
            relative=owner.path.parent.relative_to(Path(native.root).resolve())
            path=relative.parts[0]+':'+ '/'.join(relative.parts[1:])
        else:path=os.path.relpath(owner.path.parent,caller.path.parent).replace(os.sep,'/')
        key=hashlib.sha256(str(owner.path.parent).encode()).hexdigest()[:12]
        return caller.expressions.module('pure_dependency_'+key,path)+'.'+name

    def callee(self,node):
        if self.purity.eval_mode:return None
        call=node.parent
        if not(call and call.kind=='Call' and call.role('callee') is node):return None
        if node in self.purity.problems or call in self.purity.problems:return None
        targets=self.targets.get(call,[])
        if not any(target in self.names for target in targets):return None
        if any(not self.purity.pure(target) and target not in self.names for target in targets):return None
        names=list(dict.fromkeys(self.access(node.source,target) for target in targets))
        source=node.source
        if len(names)==1:name=names[0]
        else:
            name=source.prefix+'pure_group_'+str(call.start)
            source.expressions.helpers[source.scope(call),name]=source.mapping.wrap(
                name+'::proc{'+','.join(names)+'}\n',source.path,node.start,node.end,'pure-call')
        return source.mapping.wrap(name,source.path,node.start,node.end,'pure-call-target')

    def parameters(self,node):
        if self.purity.eval_mode:return None
        proc=self.purity.owner(node)
        if proc not in self.emitting or node is not proc.role('type'):return None
        params=node.role('params')
        source=node.source
        # Delay native body checking until this checked version is instantiated.
        # An inactive fn/when arm must not make an unrelated ordinary definition
        # fail merely because a checked version was generated beside it.
        extra=(',' if params.children else '')+self.marker+':$'+self.marker+'_Type=true'
        code=source.splice(params,[(child.start,child.end,source.render(child)) for child in params.children],[(params.end,extra)])
        replacements=[(child.start,child.end,source.render(child)) for child in node.children if child is not params]
        replacements.append((params.start,params.end,code))
        return source.splice(node,replacements,[(params.start,code)] if params.start==params.end else [])

    def body(self,node,expected,constant):
        proc=self.purity.owner(node)
        if proc not in self.emitting or node is not proc.role('body') or node in self.wrapping:return None
        self.wrapping.add(node)
        try:code=node.source._render(node,expected,constant)
        finally:self.wrapping.remove(node)
        return '{when '+self.marker+'_Type==bool '+code+'}'

    def procedure_name(self,node):
        proc=self.purity.owner(node)
        if proc not in self.emitting:return None
        declaration=proc.parent
        while declaration and declaration.kind=='Paren':declaration=declaration.parent
        names=declaration.group('names') if declaration and declaration.kind=='Declaration' else []
        return json.dumps(names[0].text) if names else None

    def arguments(self,call):
        if self.purity.eval_mode or not self.purity.checked(self.purity.owner(call)):return []
        if self.purity.invalid_default(call):return []
        if call in self.purity.problems or call.role('callee') in self.purity.problems:return []
        targets=self.targets.get(call,[])
        if not any(target in self.names for target in targets):return []
        args=call.group('args');position=args[-1].end if args else call.end-1
        inserts=[(position,(', ' if args else '')+self.marker+'=true')]
        callee=call.role('callee')
        if callee.kind=='Selector' and callee.data.get('op')=='->':
            inserts.append((args[0].start if args else call.end-1,callee.source.render(callee.role('expr'))+', '))
        return inserts

    def helpers(self,declaration):
        if self.purity.eval_mode:return ''
        result=[]
        for proc in self.declarations.get(declaration,[]):
            self.emitting.add(proc)
            try:body=proc.source.render(proc)
            finally:self.emitting.remove(proc)
            attributes=[]
            for attribute in declaration.group('attributes'):
                for item in attribute.group('elems'):
                    name=item.role('field').text if item.kind=='FieldValue' else item.text
                    if name in {'require_results','deprecated'}:attributes.append(proc.source.render(item))
            annotation='@('+','.join(attributes)+')\n' if attributes else ''
            result.append(proc.source.mapping.wrap('\n'+annotation+self.names[proc]+'::'+body+'\n',
                proc.source.path,proc.start,proc.end,'pure-procedure|'+declaration.group('names')[0].text+'|'+self.names[proc]))
        return ''.join(result)

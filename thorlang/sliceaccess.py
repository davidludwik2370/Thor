"""Erased slice access permissions and per-parameter write requirements.

This analysis is deliberately separate from purity: I/O, globals, allocation,
and mutation of independent local storage are ordinary procedure behavior.
Symbolic parameter paths describe writes, writable escapes and returned aliases.
Only slice backing storage gains read-only permission; existing pointer targets,
map tables and dynamic-array backing storage keep their Odin behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from .semantics import Type, Candidate


@dataclass(frozen=True)
class Origin:
    index: int = -1
    path: tuple = ()
    aspect: str = 'data'
    seed: object = None


@dataclass(eq=False)
class Value:
    typ: Type = field(default_factory=Type)
    data: set = field(default_factory=set)
    storage: set = field(default_factory=set)
    fields: dict = field(default_factory=dict)
    element: object = None
    target: object = None
    path: tuple | None = None
    locations: set = field(default_factory=set)
    base: object = None

    def merge(self, other, seen=None):
        if self is other:return False
        seen=set() if seen is None else seen
        key=id(self),id(other)
        if key in seen:return False
        seen.add(key);changed=False
        for key in ('data','storage','locations'):
            ours,theirs=getattr(self,key),getattr(other,key)
            if not theirs.issubset(ours):ours.update(theirs);changed=True
        for key,value in other.fields.items():
            if key not in self.fields:self.fields[key]=value;changed=True
            else:changed|=self.fields[key].merge(value,seen)
        for key in ('element','target'):
            value=getattr(other,key)
            if value:
                ours=getattr(self,key)
                if ours is None:setattr(self,key,value);changed=True
                else:changed|=ours.merge(value,seen)
        return changed


@dataclass
class Summary:
    proc: object
    substitutions: dict
    env: dict = field(default_factory=dict)
    effects: dict = field(default_factory=dict)
    stores: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    issues: dict = field(default_factory=dict)
    expressions: dict = field(default_factory=dict)
    named_results: list = field(default_factory=list)
    active: bool = False
    complete: bool = False
    strong_updates: bool = False
    controls: list = field(default_factory=list)


class SliceAccess:
    # These contracts describe arguments, not procedure purity. In particular,
    # printing has side effects but does not make its slice operands writable.
    readers={'len','cap','size_of','align_of','offset_of','offset_of_by_string',
             'type_of','typeid_of','type_info_of','assert','panic','unreachable',
             'min','max','abs','clamp','swizzle','soa_zip','soa_unzip','delete',
             'complex','quaternion','real','imag','jmag','kmag','conj',
             'make','new','new_clone','free','reserve','resize'}
    fmt_readers={'print','println','printf','printfln','eprint','eprintln','eprintf','eprintfln',
                 'aprint','aprintln','aprintf','aprintfln','tprint','tprintln','tprintf','tprintfln'}
    fmt_buffers={'bprint','bprintln','bprintf','bprintfln'}

    def verified_reader(self,node,library):
        if not library:return False
        path,name=library
        directory=node.source.import_dir(path)
        if path=='core:fmt' and name in self.fmt_readers|self.fmt_buffers:
            return directory is None or self.r.native and self.r.native.root and directory==(Path(self.r.native.root)/'core/fmt').resolve()
        if path=='thor:basic' and name in {'hash','try_hash','_string','print'}:
            return directory==(Path(__file__).resolve().parents[1]/'runtime/basic')
        if path in {'thor:basic','thor:graph'} and name=='deepclone':
            return directory==(Path(__file__).resolve().parents[1]/'runtime'/path.split(':')[1])
        return False

    def native_library(self,node,path):
        directory=node.source.import_dir(path)
        return directory is None or bool(self.r.native and self.r.native.root and directory==(
            Path(self.r.native.root)/path.replace(':','/',1)).resolve())

    def __init__(self,project):
        self.project=project;self.r=project.resolver
        self.summaries={};self.globals={};self.global_active=set();self.pending=[]
        self.changed=False;self.depth=0;self.enabled=False
        self.recursive=False
        self.readonly_access={}
        for source in project.sources.values():
            source.slice_access=self
            self.enabled|=bool(source.info.get('mut_parameters') or source.info.get('immutables'))
        self.validate_annotations()

    @staticmethod
    def parameters(proc):
        signature=proc if proc.kind=='ProcedureType' else proc.role('type')
        return [(name,field) for field in signature.role('params').children if field.kind=='Field'
                for name in field.group('names')]

    def parameter_type(self,field,substitutions):
        annotation=field.role('type')
        if annotation and annotation.kind=='TypeidType':return Type('scalar','typeid',annotation)
        if annotation and annotation.kind=='Ellipsis':
            return Type('slice',elem=self.r.type_node(annotation.role('expr'),substitutions))
        typ=self.r.type_node(annotation,substitutions) if annotation else self.r.type_of(field.role('default'))
        typ=self.substitute_type(typ,substitutions)
        return Type('slice',elem=typ) if field.data.get('variadic') and typ.kind!='slice' else typ

    def validate_annotations(self):
        for source in self.project.sources.values():
            names={name.start:(name,field) for proc in source.nodes if proc.kind=='ProcedureType'
                   for name,field in self.parameters(proc)}
            for offset,start,end in source.info.get('mut_parameters') or []:
                if offset not in names:source.error('#mut is only allowed immediately before a procedure parameter name.',start,end)
                name,field=names[offset];name.data['slice_mut']=True
                typ=self.r.type_node(field.role('type'))
                if typ.kind not in {'slice','generic','unknown'} and not field.data.get('variadic'):
                    source.error('#mut requires a slice parameter; pointer and scalar parameters are not supported.',start,end)

    def prepare(self):
        if not self.enabled:return
        previous=self.r.native_enabled
        suspended=getattr(self.r,'suspend_evaluation',False)
        # Use existing compile-time results for type shapes, but do not
        # schedule evaluations while inspecting inactive or native bodies.
        self.r.suspend_evaluation=True
        self.r.native_enabled=True
        try:self._prepare()
        finally:
            self.r.native_enabled=previous
            self.r.suspend_evaluation=suspended

    def _prepare(self):
        from .native import NativeSources
        if not self.r.native:self.r.native=NativeSources(self.project)
        entry_dir=self.project.entry.parent if self.project.file_entry else self.project.entry
        runtime=Path(__file__).resolve().parents[1]/'runtime'
        global_views={name.text for source in self.project.sources.values() for declaration in source.nodes
                      if declaration.kind=='Declaration' and declaration.data.get('immutable') and
                      source.scope(declaration).kind=='File' for name in declaration.group('names')}
        def runtime_checks(proc):
            # Most shipped helpers have no readonly bindings of their own.
            # Analyze their contracts on demand; still validate edited Thor
            # helper bodies containing a view or referencing a global view.
            return proc.source.thor and any(
                n.kind=='Declaration' and n.data.get('immutable') or n.kind=='Ident' and n.text in global_views
                for n in self.project.purity.walk(proc))
        procedures=[node for source in list(self.project.sources.values()) for node in source.nodes
                    if node.kind=='Procedure' and node.role('body') and
                    (not source.path.is_relative_to(runtime) or source.path.parent==entry_dir or runtime_checks(node))]
        if self.project.purity.eval_mode:
            # Evaluation helpers retain a dependency closure, not the entire
            # source project. Checking every unrelated body for every #run
            # both repeats work and can inspect declarations pruned from that
            # helper. Calls recursively instantiate the retained callees.
            procedures=[proc for proc in self.project.purity.active if proc.kind=='Procedure']
        for proc in procedures:self.summary(proc,{})
        for _ in range(24 if self.recursive else 0):
            self.changed=False
            for context in list(self.summaries.values()):
                if context.complete:self.statement(context.proc.role('body'),context)
            if not self.changed:break
        for context in list(self.summaries.values()):self.publish(context)
        for source in self.project.sources.values():
            context=Summary(None,{})
            for declaration in source.root.children:
                if declaration.kind=='Declaration':
                    retained=source.evaluation_keep
                    if self.project.purity.eval_mode and retained is not None and not any(
                        node is declaration or declaration in node.ancestors() or node in declaration.ancestors()
                        for node in retained):continue
                    self.statement(declaration,context)
            self.publish(context)

    def type(self,node,context):
        if node is None:return Type()
        if node.kind in {'Paren','OrReturn','OrPanic','OrBranch'}:return self.type(node.role('expr'),context)
        if node.kind=='Call':
            builtin=self.project.purity.builtin(node) or self.reference_builtin(node.role('callee'))
            if builtin=='new_clone':
                operand=self.project.purity.arguments(node,['data'])[0]
                return Type('pointer',elem=self.type(operand,context))
            if builtin=='swizzle':
                args=node.group('args')
                if args:
                    source=self.type(args[0],context)
                    return Type('array',elem=source.elem,length=len(args)-1)
            if builtin=='soa_zip':
                members={arg.role('field').text:self.type(arg.role('value'),context).elem or Type()
                         for arg in node.group('args') if arg.kind=='FieldValue'}
                return Type('slice',elem=Type('struct',members=members),soa=True)
            if builtin in {'len','cap','size_of','align_of','offset_of'}:return Type('scalar','int',node)
            if builtin in {'min','max','abs','clamp'}:
                args=node.group('args')
                if args:
                    operand=args[0].role('value') if args[0].kind=='FieldValue' else args[0]
                    result=self.type(operand,context)
                    return result.elem or Type() if node.data.get('roles',{}).get('ellipsis') else result
            if builtin in {'complex','quaternion','real','imag','jmag','kmag','conj'}:
                args=node.group('args')
                operand=args[0] if args else None
                if operand and operand.kind=='FieldValue':operand=operand.role('value')
                component=self.type(operand,context)
                bits={'f16':16,'f32':32,'f64':64}.get(component.name,64)
                if builtin in {'complex','quaternion'}:
                    name=builtin+str(bits*(2 if builtin=='complex' else 4))
                elif builtin=='conj':name=component.name
                else:
                    prefix='complex' if component.name.startswith('complex') else 'quaternion'
                    total=component.name.removeprefix(prefix)
                    name='f'+str(int(total)//(2 if prefix=='complex' else 4)) if total.isdigit() else 'f64'
                return Type('scalar',name,node)
            target=self.substitute_type(self.r.type_node(node.role('callee')),context.substitutions)
            if target.kind not in {'unknown','generic','procedure'}:return target
        if node.kind=='Ident' or node.kind=='Selector' and self.r.symbol(node) is not None:
            symbol=self.r.symbol(node)
            if symbol and (symbol.declaration,symbol.name) in context.env:return context.env[symbol.declaration,symbol.name].typ
        if node.kind=='Selector':
            base=self.type(node.role('expr'),context)
            if base.kind=='pointer':base=base.elem or Type()
            selection=self.r.array_selector_type(base,node.role('field').text)
            if selection is not None:return selection
            member=self.r.field(base,node.role('field').text)
            if member:return member[0]
        if node.kind=='Index':return self.type(node.role('expr'),context).elem or Type()
        if node.kind=='Slice':
            base=self.type(node.role('expr'),context)
            while base.kind=='pointer' and base.elem:base=base.elem
            return base if base.kind=='string' else Type('slice',elem=base.elem,soa=base.soa)
        if node.kind=='Unary' and node.data.get('op')=='&':return Type('pointer',elem=self.type(node.role('expr'),context))
        if node.kind=='Deref':return self.type(node.role('expr'),context).elem or Type()
        typ=self.r.type_of(node)
        if node.kind=='Compound' and typ.kind in {'unknown','generic'}:
            typ=self.project.purity.context_type(node)
        return self.substitute_type(typ,context.substitutions)

    def substitute_type(self,typ,substitutions,seen=None):
        if typ is None:return Type()
        if typ.node and typ.node.kind=='WhenExpr' and typ.kind in {'unknown','scalar'}:
            selected=self.condition(typ.node.role('cond'),Summary(None,substitutions))
            if selected is not None:
                branch=typ.node.role('x' if selected else 'y')
                return self.substitute_type(self.r.type_node(branch,substitutions),substitutions,seen)
        if typ.kind in {'generic','unknown'} and typ.name.lstrip('$') in substitutions:
            value=substitutions[typ.name.lstrip('$')]
            if isinstance(value,Type):return value
        if typ.elem:
            elem=self.substitute_type(typ.elem,substitutions,seen)
            if elem is not typ.elem:
                from dataclasses import replace
                return replace(typ,elem=elem)
        if typ.substitutions:
            from dataclasses import replace
            resolved={name:self.substitute_type(value,substitutions) if isinstance(value,Type) and value is not typ else value
                      for name,value in typ.substitutions.items()}
            if any(resolved[name] is not value for name,value in typ.substitutions.items()):return replace(typ,substitutions=resolved)
        return typ

    @staticmethod
    def struct_names(typ):
        if typ.members:return list(typ.members)
        fields=typ.node.role('fields') if typ.node else None
        return [name.text for field in fields.children if field.kind=='Field' for name in field.group('names')] if fields else []

    def same_type(self,a,b,depth=0):
        if depth>32:return None
        if a.kind in {'generic','unknown'} or b.kind in {'generic','unknown'}:return None
        if a.members or b.members:return True if a is b else None
        if a.kind!=b.kind:return False
        if a.kind in {'struct','union','enum','bitset'} and a.node is not b.node:return False
        if a.substitutions or b.substitutions:
            if a.substitutions.keys()!=b.substitutions.keys():return None
            for name,left in a.substitutions.items():
                right=b.substitutions[name]
                same=self.same_type(left,right,depth+1) if isinstance(left,Type) and isinstance(right,Type) else left==right
                if same is not True:return same
        if a.elem or b.elem:
            if not a.elem or not b.elem:return False
            if a.kind in {'array','fixed'}:
                if not isinstance(a.length,int) or not isinstance(b.length,int):return None
                if a.length!=b.length:return False
            if a.kind=='map':
                if not a.node or not b.node:return None
                same=self.same_type(self.r.type_node(a.node.role('key')),self.r.type_node(b.node.role('key')),depth+1)
                if same is not True:return same
            return self.same_type(a.elem,b.elem,depth+1)
        if a.kind=='scalar' and (not a.name or not b.name):
            return True if a.node is not None and a.node is b.node and a.nominal is b.nominal else None
        if a.kind in {'scalar','enum','struct','union','bitset'}:return a.name==b.name and a.nominal==b.nominal
        return None

    def fresh(self,typ,path=None,storage=()):
        result=Value(typ,storage=set(storage),path=path)
        if path is not None:
            index,steps=path
            if len(steps)>16:
                result.data.add(Origin(index,aspect='all'));return result
            result.locations.add((index,steps))
            if typ.kind in {'slice','generic','unknown','union'}:result.data.add(Origin(index,steps))
        return result

    def qualify(self,value,origins):
        if not origins:return value
        result=Value(value.typ,set(value.data),set(value.storage)|set(origins),dict(value.fields),value.element,
                     value.target,value.path,set(value.locations),value)
        if value.typ.kind in {'slice','generic','unknown','union'}:result.data.update(origins)
        return result

    def member(self,value,name):
        if value.typ.kind=='pointer':return self.member(self.deref(value),name)
        selection=self.r.array_selector_type(value.typ,name)
        if selection is not None:
            element=self.item(value)
            if len(name)==1:return element
            # Swizzle assignments address the source array; value copies copy
            # the selected inline elements and retain any nested slice views.
            return Value(selection,storage=set(value.storage),element=element,
                         locations=set(value.locations))
        if value.base:
            base=self.member(value.base,name)
            return self.qualify(base,value.storage-value.base.storage)
        if value.typ.soa and name in self.struct_names(value.typ.elem or Type()):
            if name not in value.fields:
                typ=self.r.field(value.typ.elem,name)[0]
                path=(value.path[0],(*value.path[1],('field',name))) if value.path else None
                value.fields[name]=self.fresh(Type('slice',elem=typ),path,value.storage)
            return value.fields[name]
        if name not in value.fields:
            member=self.r.field(value.typ,name)
            typ=member[0] if member else Type()
            path=(value.path[0],(*value.path[1],('field',name))) if value.path else None
            value.fields[name]=self.fresh(typ,path,value.storage)
        return value.fields[name]

    def item(self,value):
        if value.typ.soa:
            if value.base:return self.qualify(self.item(value.base),value.storage-value.base.storage)
            if value.element is None:
                path=(value.path[0],(*value.path[1],('item',))) if value.path else None
                value.element=self.fresh(value.typ.elem or Type(),path,value.data|value.storage)
                for name in self.struct_names(value.element.typ):
                    value.element.fields[name]=self.item(self.member(value,name))
            return value.element
        if value.base:
            item=self.item(value.base)
            return self.qualify(item,value.data|value.storage)
        if value.element is None:
            path=(value.path[0],(*value.path[1],('item',))) if value.path else None
            value.element=self.fresh(value.typ.elem or Type(),path,
                value.data if value.typ.kind in {'slice','generic','unknown'} else value.storage)
        if value.typ.elem and value.typ.elem.kind not in {'unknown','generic'}:
            value.element.typ=value.typ.elem
        return self.qualify(value.element,value.data) if value.typ.kind in {'slice','generic','unknown'} else value.element

    def deref(self,value):
        # Pointers stored inside slice elements retain their existing behavior.
        if value.target is None:
            path=(value.path[0],(*value.path[1],('deref',))) if value.path else None
            storage={Origin(path[0],path[1],'storage')} if path else set()
            value.target=self.fresh(value.typ.elem or Type(),path,storage)
        return value.target

    def copied(self,value,seen=None):
        seen={} if seen is None else seen
        if id(value) in seen:return seen[id(value)]
        data=set(value.data) if value.typ.kind in {'slice','generic','unknown','union'} else set()
        path=value.path if value.typ.kind in {'slice','pointer','dynamic','map','set','generic','unknown','union'} else None
        result=Value(value.typ,data,element=value.element,target=value.target,path=path)
        seen[id(value)]=result
        if value.typ.kind=='struct':
            for name in self.struct_names(value.typ):result.fields[name]=self.copied(self.member(value,name),seen)
        elif value.typ.kind in {'array','fixed'}:result.element=self.copied(self.item(value),seen)
        else:result.fields=dict(value.fields)
        # A copied header must preserve the view's element qualifier.
        if value.typ.kind=='slice':result.element=self.item(value)
        return result

    def freeze(self,value,seed,seen=None):
        seen={} if seen is None else seen
        if id(value) in seen:return seen[id(value)]
        result=self.copied(value);seen[id(value)]=result
        if result.typ.kind in {'slice','generic','unknown','union'}:return self.qualify(result,{Origin(seed=seed)})
        if result.typ.kind in {'struct','array','fixed'}:result.storage.add(Origin(seed=seed,aspect='inline'))
        if result.typ.kind=='struct':
            for name in self.struct_names(result.typ):result.fields[name]=self.freeze(self.member(result,name),seed,seen)
        elif result.typ.kind in {'array','fixed'}:result.element=self.freeze(self.item(result),seed,seen)
        return result

    def narrowed(self,value,typ):
        result=self.copied(value);result.typ=typ
        if typ.kind not in {'slice','generic','unknown','union'}:result.data.clear()
        if typ.kind in {'scalar','enum','bitset','string'}:
            result.fields={};result.element=None;result.target=None;result.path=None
        if typ.kind in {'struct','array','fixed'}:result=self.qualify(result,value.data)
        return result

    def snapshot(self,value,seen=None):
        """Detach summary facts while retaining aliasing inside the fact graph."""
        seen={} if seen is None else seen
        if id(value) in seen:return seen[id(value)]
        result=Value(value.typ,set(value.data),set(value.storage),path=value.path,
                     locations=set(value.locations))
        seen[id(value)]=result
        result.fields={name:self.snapshot(child,seen) for name,child in value.fields.items()}
        for key in ('element','target','base'):
            child=getattr(value,key)
            if child:setattr(result,key,self.snapshot(child,seen))
        return result

    def references(self,value,seen=None):
        seen=set() if seen is None else seen
        if id(value) in seen:return set()
        seen.add(id(value));result=set(value.data)
        if any(origin.aspect=='all' for origin in result):return result
        if value.path and self.contains_slice(value.typ):
            if value.typ.kind=='pointer':self.deref(value)
            elif value.typ.kind in {'array','fixed','dynamic','map','set'}:self.item(value)
            elif value.typ.kind=='struct':
                for name in self.struct_names(value.typ):self.member(value,name)
        for child in [*value.fields.values(),value.element,value.target]:
            if child:result.update(self.references(child,seen))
        return result

    def require(self,origins,node,context,reason,related=()):
        for origin in origins:
            if origin.seed is not None:
                if origin.aspect=='inline':continue # Existing immutable-storage checks own these diagnostics.
                key=node,reason
                context.issues.setdefault(key,(origin.seed,tuple(related)))
            else:
                if origin not in context.effects:
                    context.effects[origin]=(node,reason,tuple(related));self.changed=True

    def local_header(self,node,context):
        if node.kind=='Ident':
            symbol=self.r.symbol(node)
            return bool(symbol and (symbol.declaration.kind=='Declaration' and symbol.scope.kind!='File' or
                                    (symbol.declaration,symbol.name) in context.named_results))
        if node.kind=='Selector':
            owner=node.role('expr')
            return self.type(owner,context).kind=='struct' and self.local_header(owner,context)
        return False

    def header_key(self,node):
        if node.kind=='Ident':
            symbol=self.r.symbol(node)
            return (symbol.declaration,symbol.name) if symbol else None
        if node.kind=='Selector':
            owner=self.header_key(node.role('expr'))
            return (*owner,node.role('field').text) if owner else None
        return None

    def independent_header(self,node,context):
        if not self.local_header(node,context):return False
        declaration=self.header_key(node)[0]
        ancestors=set(declaration.ancestors())
        return all(control in ancestors for control in context.controls)

    def terminal_allocations(self,node,context):
        """Definite fresh header replacements at every continuing branch end."""
        if node is None:return []
        if node.kind=='Block':return self.terminal_allocations(node.children[-1],context) if node.children else []
        if node.kind=='If':
            left=self.terminal_allocations(node.role('body'),context)
            right=self.terminal_allocations(node.role('else'),context)
            return [*left,*right] if left and right else []
        if node.kind!='Assignment' or node.data.get('op')!='=' or len(node.group('lhs'))!=1 or len(node.group('rhs'))!=1:return []
        lhs,rhs=node.group('lhs')[0],node.group('rhs')[0]
        if not self.local_header(lhs,context):return []
        if rhs.kind=='Comprehension':return [node]
        if rhs.kind!='Call':return []
        builtin=self.project.purity.builtin(rhs)
        library=self.project.purity.library(rhs)
        if builtin in {'make','new'} or library==('core:slice','clone') and self.native_library(rhs,library[0]):return [node]
        return []

    def write(self,target,value,context,node,strong=False):
        self.require(target.storage,node,context,'modify slice elements')
        if strong and not target.storage and not target.locations and self.independent_header(node,context) and self.references(target):
            # Replacing an independent local header changes that header, not
            # the old backing allocation or copies of its previous value.
            incoming=self.copied(value)
            for key in ('data','fields','element','target','path','base'):
                setattr(target,key,getattr(incoming,key))
            context.strong_updates=True
            return
        self.changed|=(target.base or target).merge(value)
        for location in target.locations:
            if location not in context.stores:context.stores[location]=self.copied(value);self.changed=True
            else:self.changed|=context.stores[location].merge(self.copied(value))

    def resolve(self,origin,bindings):
        if origin.seed is not None:return {origin}
        if origin.index>=len(bindings):return set()
        value=bindings[origin.index]
        if origin.aspect=='all':return self.references(value)
        for step in origin.path:
            if step[0]=='field':value=self.member(value,step[1])
            elif step[0]=='item':value=self.item(value)
            else:value=self.deref(value)
        return set(value.data if origin.aspect=='data' else value.storage)

    def instantiate(self,value,bindings,seen=None,depth=0):
        seen={} if seen is None else seen
        if id(value) in seen:return seen[id(value)]
        if value.path and value.path[0]<len(bindings):
            index,path=value.path;actual=bindings[index]
            for step in path:
                actual=self.member(actual,step[1]) if step[0]=='field' else self.item(actual) if step[0]=='item' else self.deref(actual)
            result=self.copied(actual);seen[id(value)]=result
            for origin in value.data:result.data.update(self.resolve(origin,bindings))
            return result
        result=Value(value.typ);seen[id(value)]=result
        for origin in value.data:result.data.update(self.resolve(origin,bindings))
        for origin in value.storage:result.storage.update(self.resolve(origin,bindings))
        if depth>16:return result
        result.fields={name:self.instantiate(child,bindings,seen,depth+1) for name,child in value.fields.items()}
        if value.element:result.element=self.instantiate(value.element,bindings,seen,depth+1)
        if value.target:result.target=self.instantiate(value.target,bindings,seen,depth+1)
        return result

    def bindings(self,node,params,context):
        args=node.group('args');positional=[]
        for arg in args:
            if arg.kind=='FieldValue':continue
            if arg.kind=='Unary' and arg.data.get('op')=='**':
                base=self.eval(arg.role('expr'),context)
                if base.typ.kind=='struct' and base.typ.node:
                    for field in base.typ.node.role('fields').children:
                        if field.kind=='Field':
                            for name in field.group('names'):positional.append((arg,self.member(base,name.text)))
                elif base.typ.kind in {'array','fixed'} and isinstance(base.typ.length,int):
                    positional.extend((arg,self.item(base)) for _ in range(base.typ.length))
                else:positional.append((arg,base))
            else:
                values=self.expression_results(arg,context,len(params)) if len(args)==1 else [self.eval(arg,context)]
                positional.extend((arg,value) for value in values)
        receiver=node.role('callee')
        if receiver.kind=='Selector' and receiver.data.get('op')=='->':positional.insert(0,(receiver.role('expr'),self.eval(receiver.role('expr'),context)))
        named={a.role('field').text:a.role('value') for a in args if a.kind=='FieldValue'}
        result=[];sites=[];index=0
        ellipsis=node.data.get('roles',{}).get('ellipsis')
        for name,field in params:
            key=name.text.lstrip('$');arg=named.get(key)
            if field.data.get('variadic'):
                values=[(arg,self.eval(arg,context))] if arg else positional[index:];index=len(positional)
                if len(values)==1 and ellipsis and values[0][0].start>=ellipsis[1]:value=values[0][1]
                else:
                    element=None
                    for _,incoming in values:
                        incoming=self.copied(incoming)
                        if element is None:element=incoming
                        else:
                            if element.typ.untyped and not incoming.typ.untyped:element.typ=incoming.typ
                            element.merge(incoming)
                    element=element or Value()
                    value=Value(Type('slice',elem=element.typ),element=element)
                result.append(self.copied(value));sites.append(values[0][0] if values else node);continue
            incoming=None
            if arg is None and index<len(positional):arg,incoming=positional[index];index+=1
            arg=arg or field.role('default')
            result.append(self.copied(incoming if incoming is not None else self.eval(arg,context)));sites.append(arg or node)
        return result,sites

    @staticmethod
    def type_key(typ,depth=0):
        if not isinstance(typ,Type):return str(typ)
        head=(typ.kind,typ.name,id(typ.node),typ.length,id(typ.nominal),typ.soa)
        if depth>8:return head
        return (*head,SliceAccess.type_key(typ.elem,depth+1) if typ.elem else None,
                tuple((name,SliceAccess.type_key(value,depth+1)) for name,value in sorted(typ.substitutions.items())),
                tuple((name,SliceAccess.type_key(value,depth+1)) for name,value in sorted(typ.members.items())))

    def summary(self,proc,substitutions):
        key=proc,tuple(sorted((name,self.type_key(typ)) for name,typ in substitutions.items()))
        if key in self.summaries:
            if self.summaries[key].active:self.recursive=True
            return self.summaries[key]
        context=Summary(proc,substitutions);self.summaries[key]=context
        params=self.parameters(proc)
        for index,(name,field) in enumerate(params):
            typ=self.substitute_type(self.parameter_type(field,substitutions),substitutions)
            context.env[field,name.text.lstrip('$')]=self.fresh(typ,(index,()))
            if name.data.get('slice_mut'):
                self.require({Origin(index)},name,context,'satisfy explicit #mut')
        result_fields=proc.role('type').role('results')
        for name,typ_node in self.r.fields(result_fields):
            typ=self.r.type_node(typ_node,substitutions)
            result=Value(typ);context.results.append(result)
            field=next((field for field in result_fields.children if field.kind=='Field' and any(n.text==name and n.data.get('name')!='_' for n in field.group('names'))),None)
            key=(field,name) if field and name else None
            context.named_results.append(key)
            if key:context.env[key]=result
        context.active=True;self.depth+=1
        if self.depth>40:
            for index,(name,_) in enumerate(params):self.require({Origin(index,aspect='all')},name,context,'verify a recursive slice call')
        else:
            # Aliases and loop-carried reference stores form a finite monotone
            # graph. Revisit until no storage provenance changes.
            state=None
            for _ in range(24):
                previous=self.changed;self.changed=False
                self.statement(proc.role('body'),context)
                changed=self.changed;self.changed|=previous
                if context.strong_updates:
                    current=self.state(context)
                    if current==state:break
                    state=current
                if not changed:break
            else:
                for index,(name,_) in enumerate(params):
                    self.require({Origin(index,aspect='all')},name,context,'verify a complex slice reference graph')
        self.depth-=1;context.active=False;context.complete=True
        self.publish(context)
        return context

    @staticmethod
    def state(context):
        """Compare final permission graphs, not transient local rebindings."""
        seen={};nodes=[]
        def visit(value):
            if value is None:return None
            if id(value) in seen:return seen[id(value)]
            index=len(nodes);seen[id(value)]=index;nodes.append(None)
            typ=value.typ
            fields=tuple((name,visit(child)) for name,child in sorted(value.fields.items()))
            children=tuple(visit(getattr(value,key)) for key in ('element','target','base'))
            nodes[index]=(SliceAccess.type_key(typ),
                          frozenset(value.data),frozenset(value.storage),frozenset(value.locations),value.path,fields,children)
            return index
        roots=tuple(visit(value) for value in [*context.env.values(),*context.results,*context.stores.values()])
        return roots,tuple(nodes),frozenset(context.effects),frozenset(context.issues)

    def call(self,node,context):
        callee=node.role('callee');args=node.group('args')
        library=self.project.purity.library(node)
        if library==('thor:sets','insert') and node.source.import_dir(library[0])==(Path(__file__).resolve().parents[1]/'runtime/sets'):
            key=self.project.purity.arguments(node,['set','value'])[1]
            self.require(self.references(self.eval(key,context)),key or node,context,
                         'preserve read-only slice references in a Set key')
        if self.verified_reader(node,library):
            for arg in args:self.eval(arg.role('value') if arg.kind=='FieldValue' else arg,context)
            path,name=library
            if path=='core:fmt':
                if name in self.fmt_buffers:
                    candidates=self.r.procedure_candidates(node.role('callee'))
                    if len(candidates)==1:
                        signature=candidates[0][1]
                        bindings,sites=self.bindings(node,self.parameters(signature),context)
                        if bindings:self.require(bindings[0].data,sites[0],context,'modify formatting buffer')
                    return Value(Type('string','string'))
                return Value(Type('string','string') if name.startswith(('aprint','tprint')) else Type('scalar','int'))
            if path=='thor:basic' and name in {'print','hash','try_hash','_string'}:
                result=Value(Type('string','string') if name=='_string' else Type('scalar','u64'))
                if name in {'try_hash','_string'}:
                    return self.fresh_call_results(node,context,result)
                return result
            result=Value(self.type(node,context))
            # deepclone returns an independent graph and a reference-free
            # library error, including when both names are immutable.
            return self.fresh_call_results(node,context,result)
        builtin=self.project.purity.builtin(node) or self.reference_builtin(callee)
        if builtin or library:
            values=[self.eval(a.role('value') if a.kind=='FieldValue' else a,context) for a in args]
            if builtin=='new_clone' and values:
                operand=self.project.purity.arguments(node,['data'])[0]
                result=Value(self.type(node,context),target=self.copied(self.eval(operand,context)))
                return self.record_results(node,context,[result,Value(Type('enum','Allocator_Error'))],optional=True)
            if builtin=='swizzle' and values:
                return Value(self.type(node,context),element=self.copied(self.item(values[0])))
            if builtin=='soa_zip':
                result=Value(self.type(node,context))
                for arg,value in zip(args,values):
                    if arg.kind=='FieldValue':result.fields[arg.role('field').text]=self.copied(value)
                    result.data.update(value.data)
                return result
            if builtin=='soa_unzip' and values:
                source=values[0]
                return self.record_results(node,context,[self.copied(self.member(source,name))
                    for name in self.struct_names(source.typ.elem or Type())])
            if builtin=='expand_values' and values:
                source=values[0]
                expanded=[self.member(source,name) for name in self.struct_names(source.typ)] if source.typ.kind=='struct' else (
                    [self.item(source)]*source.typ.length if source.typ.kind=='array' and isinstance(source.typ.length,int) else [])
                return self.record_results(node,context,[self.copied(value) for value in expanded])
            if builtin in self.readers:
                result=Value(self.type(node,context))
                if builtin in {'make','new'}:
                    self.record_results(node,context,[result,Value(Type('enum','Allocator_Error'))],optional=True)
                elif builtin in {'min','max'} and node.source.expressions.extrema.plan(node) and node.source.expressions.extrema.plan(node)[2]:
                    self.record_results(node,context,[result,Value(Type('scalar','bool'))],optional=True)
                return result
            if builtin=='copy':
                byname=self.project.purity.arguments(node,['dst','src'])
                dst,src=[self.eval(arg,context) for arg in byname]
                self.require(dst.data,node,context,'modify destination slice elements')
                self.write(self.item(dst),self.copied(self.item(src)),context,node)
                return Value(self.type(node,context))
            if builtin=='raw_data' and values:
                self.require(values[0].data,node,context,'obtain a writable pointer into a read-only slice')
                return Value(Type('pointer',elem=values[0].typ.elem),target=self.item(values[0]))
            if library==('core:slice','clone') and self.native_library(node,library[0]) and values:
                argument=self.project.purity.arguments(node,['a'])[0]
                source=self.eval(argument,context)
                return Value(self.type(node,context),element=self.copied(self.item(source)))
            if builtin in {'clear','append','pop','pop_safe','pop_front','pop_front_safe'} and values:
                target=self.deref(values[0]) if values[0].typ.kind=='pointer' else values[0]
                self.require(target.data,node,context,'modify slice storage')
                if builtin=='append':
                    ellipsis=node.data.get('roles',{}).get('ellipsis')
                    for arg in args[1:]:
                        if arg.kind=='FieldValue' and arg.role('field').text in {'loc','allocator'}:continue
                        expr=arg.role('value') if arg.kind=='FieldValue' else arg
                        expanded=expr.kind=='Unary' and expr.data.get('op')=='**' or ellipsis and expr.start>=ellipsis[1]
                        incoming=self.eval(expr,context)
                        if expanded and incoming.typ.kind=='struct' and incoming.typ.node:
                            elements=[self.member(incoming,name.text) for field in incoming.typ.node.role('fields').children if field.kind=='Field' for name in field.group('names')]
                        else:elements=[self.item(incoming)] if expanded else [incoming]
                        for element in elements:self.write(self.item(target),self.copied(element),context,node)
                return self.copied(self.item(target)) if builtin.startswith('pop') else Value(self.type(node,context))
        if node.source.denotes_type(callee):
            value=self.eval(args[0],context) if args else Value()
            result=self.copied(value);result.typ=self.type(node,context)
            if result.typ.kind=='slice':result.data.update(value.storage)
            if result.typ.kind in {'pointer','scalar'} and self.references(value):
                self.require(self.references(value),node,context,'erase read-only slice permissions in a conversion')
            return result
        if self.indirect_callee(callee):
            for arg in args:self.require(self.references(self.eval(arg,context)),arg,context,
                                          'verify an indirect call with a read-only slice')
            return self.opaque_call(node,context)
        plan=self.r.resolve_call(node)
        if plan is None:
            candidates=self.r.procedure_candidates(callee)
            if len(candidates)==1:
                symbol,signature=candidates[0];plan=Candidate(symbol,signature,{})
        candidates=[plan] if plan else []
        if not candidates:
            # Unknown callbacks and foreign prototypes cannot promise read-only
            # behavior. Scalars and ordinary pointers carry no slice permission.
            for arg in args:
                self.require(self.references(self.eval(arg,context)),node,context,
                    'verify an indirect or foreign call with a read-only slice')
            return self.opaque_call(node,context)
        plan=candidates[0]
        alternatives=plan.symbol.candidates
        if any(any(a.kind in {'When','CompileSwitch'} for a in symbol.declaration.ancestors()) for symbol in alternatives):
            candidates=[Candidate(symbol,symbol.value.role('type'),dict(plan.substitutions)) for symbol in alternatives
                        if symbol.value and symbol.value.kind=='Procedure' and self.active_definition(symbol.value,context)]
        result=None;results=[]
        for plan in candidates:
            value=self.apply_call(node,context,plan)
            if result is None:result=value
            else:result.merge(value)
            for index,incoming in enumerate(context.expressions.get((node,'results'),[])):
                if index>=len(results):results.append(self.copied(incoming))
                else:results[index].merge(incoming)
        if results:context.expressions[node,'results']=results
        if plan.signature.data.get('optional_ok') or plan.signature.data.get('optional_allocator_error'):
            context.expressions[node,'optional']=True
        return result or Value(self.type(node,context))

    def reference_builtin(self,callee,seen=None):
        """Reference-producing builtins have effects independent of purity."""
        if callee is None:return None
        seen=set() if seen is None else seen
        if callee in seen:return None
        seen.add(callee)
        if callee.kind=='Paren':return self.reference_builtin(callee.role('expr'),seen)
        imported=self.project.purity.imported(callee)
        names={'raw_data','new_clone','free','soa_zip','soa_unzip','expand_values'}
        if imported and imported[0]=='base:builtin' and imported[1] in names and callee.source.import_dir(imported[0]) is None:return imported[1]
        symbol=self.r.symbol(callee)
        if symbol:
            if not symbol.declaration.data.get('mutable') and not symbol.immutable and symbol.value:
                return self.reference_builtin(symbol.value,seen)
            return None
        if callee.kind=='Ident' and not imported and callee.text in names:return callee.text
        return None

    def indirect_callee(self,callee,seen=None):
        seen=set() if seen is None else seen
        if callee is None or callee in seen:return False
        seen.add(callee)
        if callee.kind=='Paren':return self.indirect_callee(callee.role('expr'),seen)
        symbol=self.r.symbol(callee)
        if not symbol:return False
        if symbol.declaration.data.get('mutable') and not symbol.immutable or symbol.declaration.kind in {'Field','Range','TypeSwitch'}:return True
        if symbol.value and symbol.value.kind in {'Ident','Selector','Paren'}:return self.indirect_callee(symbol.value,seen)
        return False

    def opaque_result(self,typ,node,seen=None):
        """An erased callback cannot promise that returned slice storage is writable."""
        if typ.kind in {'unknown','generic'} or not self.contains_slice(typ):return Value(typ)
        seen={} if seen is None else seen
        key=self.type_key(typ)
        if key in seen:return seen[key]
        result=Value(typ);seen[key]=result
        if typ.kind in {'slice','union'}:result.data.add(Origin(seed=node))
        if typ.kind=='pointer':result.target=self.opaque_result(typ.elem or Type(),node,seen)
        elif typ.kind in {'slice','array','fixed','dynamic','map','set'}:result.element=self.opaque_result(typ.elem or Type(),node,seen)
        elif typ.kind=='struct':
            for name in self.struct_names(typ):result.fields[name]=self.opaque_result(self.r.field(typ,name)[0],node,seen)
        return self.qualify(result,{Origin(seed=node)}) if typ.soa else result

    def opaque_call(self,node,context):
        plan=self.r.resolve_call(node)
        candidates=self.r.procedure_candidates(node.role('callee'))
        signature=plan.signature if plan else candidates[0][1] if len(candidates)==1 else None
        if signature:
            substitutions={**context.substitutions,**(plan.substitutions if plan else {})}
            values=[self.opaque_result(self.r.type_node(annotation,substitutions),node)
                    for _,annotation in self.r.fields(signature.role('results'))]
            if values:return self.record_results(node,context,values,optional=bool(
                signature.data.get('optional_ok') or signature.data.get('optional_allocator_error')))
        return self.opaque_result(self.type(node,context),node)

    def fresh_call_results(self,node,context,first):
        plan=self.r.resolve_call(node)
        candidates=self.r.procedure_candidates(node.role('callee'))
        signature=plan.signature if plan else candidates[0][1] if len(candidates)==1 else None
        if signature:
            substitutions=plan.substitutions if plan else context.substitutions
            fields=self.r.fields(signature.role('results'))
            values=[first,*[Value(self.r.type_node(annotation,substitutions)) for _,annotation in fields[1:]]]
            return self.record_results(node,context,values,optional=bool(
                signature.data.get('optional_ok') or signature.data.get('optional_allocator_error')))
        return first

    def active_definition(self,node,context):
        for parent in node.ancestors():
            if parent.kind!='When':continue
            selected=self.condition(parent.role('cond'),context)
            if selected is None:continue
            arm=parent.role('body') if selected else parent.role('else')
            if arm is None or not arm.start<=node.start<node.end<=arm.end:return False
        return True

    def apply_call(self,node,context,plan):
        callee=node.role('callee');signature=plan.signature
        params=[(name,field) for field in signature.role('params').children if field.kind=='Field' for name in field.group('names')]
        bindings,sites=self.bindings(node,params,context)
        target=signature.parent if signature.parent and signature.parent.kind=='Procedure' else None
        for index,(name,field) in enumerate(params):
            formal=self.parameter_type(field,plan.substitutions)
            opaque=formal.elem if field.data.get('variadic') and formal.elem else formal
            if opaque.name in {'any','rawptr'} and index<len(bindings):
                self.require(self.references(bindings[index]),sites[index],context,
                    'preserve read-only slice permissions through an opaque parameter', [name])
            if name.data.get('slice_mut') and index<len(bindings):
                if bindings[index].typ.kind not in {'slice','generic','unknown'}:
                    token=node.source.prefix+'slice_parameter_type_'+str(node.start)
                    message='#mut requires a slice parameter in this specialization.'
                    node.source.blocked[node]=(token,message);node.source.error_messages[token]=message
                self.require(bindings[index].data,sites[index],context,'pass a read-only slice to #mut parameter '+repr(name.text),[name])
        if not target or not target.role('body'):
            for value in bindings:self.require(self.references(value),node,context,'verify a foreign or bodyless slice call')
            return self.opaque_call(node,context)
        result_references=any(self.contains_slice(self.r.type_node(annotation,plan.substitutions))
                              for _,annotation in self.r.fields(signature.role('results')))
        local_checks=bool(target.source.info.get('immutables') and self.r.generic_parameters(signature)
                          and any(n.kind=='Declaration' and n.data.get('immutable')
                                  for n in self.project.purity.walk(target)))
        if not any(self.references(value) for value in bindings) and not result_references and not local_checks:
            return self.fresh_call_results(node,context,Value(self.type(node,context)))
        substitutions=dict(context.substitutions)
        substitutions.update({key:self.substitute_type(value,context.substitutions) if isinstance(value,Type) else value
                              for key,value in plan.substitutions.items()})
        for value,(_,field) in zip(bindings,params):
            annotation=field.role('type')
            if annotation and annotation.kind=='Ellipsis':self.r.unify(annotation.role('expr'),value.typ.elem or Type(),substitutions)
            elif annotation:self.r.unify(annotation,value.typ,substitutions)
            if annotation and annotation.kind=='PolyType':substitutions[annotation.data['name']]=value.typ
        summary=self.summary(target,substitutions)
        for origin,(operation,reason,chain) in list(summary.effects.items()):
            origins=self.resolve(origin,bindings)
            site=sites[origin.index] if origin.index<len(sites) else node
            self.require(origins,site,context,'pass a read-only slice to '+repr(callee.text)+': it may '+reason,
                         [operation,*chain])
        for (index,path),stored in list(summary.stores.items()):
            if index>=len(bindings):continue
            out=bindings[index]
            for step in path:out=self.member(out,step[1]) if step[0]=='field' else self.item(out) if step[0]=='item' else self.deref(out)
            incoming=self.instantiate(stored,bindings)
            self.changed|=(out.base or out).merge(incoming)
            for location in out.locations:
                if location not in context.stores:context.stores[location]=incoming;self.changed=True
                else:self.changed|=context.stores[location].merge(incoming)
        result=Value(self.type(node,context))
        if summary.results:
            # Declared fn results cross the existing deep-copy boundary: their
            # owned result storage is independent even when the source body
            # returned an input view. Ordinary proc results remain shallow.
            seen={}
            results=[Value(value.typ) if self.project.purity.pure(target) else self.instantiate(value,bindings,seen)
                     for value in summary.results]
            context.expressions[node,'results']=results
            result=results[0]
            typ=self.type(node,context)
            if typ.kind not in {'unknown','generic'}:result.typ=typ
        return result

    def contains_slice(self,typ,seen=None):
        seen=set() if seen is None else seen
        if typ.kind in {'slice','generic','unknown'}:return True
        if typ.kind in {'array','fixed','dynamic','map','set','pointer'}:return self.contains_slice(typ.elem or Type(),seen)
        if typ.kind=='struct' and typ.members:return any(self.contains_slice(member,seen.copy()) for member in typ.members.values())
        if typ.kind=='struct' and typ.node and typ.node not in seen:
            seen.add(typ.node)
            return any(self.contains_slice(self.r.type_node(field.role('type'),typ.substitutions),seen)
                       for field in typ.node.role('fields').children if field.kind=='Field')
        if typ.kind=='union' and typ.node and typ.node not in seen:
            seen.add(typ.node)
            return any(self.contains_slice(self.r.type_node(variant,typ.substitutions),seen) for variant in typ.node.group('variants'))
        return False

    def eval(self,node,context):
        if node is None:return Value()
        if node.kind=='Call':return self.call(node,context)
        typ=self.type(node,context)
        if node.kind=='Ident' or node.kind=='Selector' and self.r.symbol(node) is not None:
            symbol=self.r.symbol(node)
            if symbol and symbol.projection:
                owner,name=symbol.projection;return self.member(self.eval(owner,context),name)
            if not symbol:return Value(typ)
            key=symbol.declaration,symbol.name
            if key in context.env:return context.env[key]
            if symbol.declaration.kind=='Declaration':
                if key not in self.globals and key not in self.global_active:
                    self.global_active.add(key)
                    owner=Summary(None,{})
                    self.statement(symbol.declaration,owner)
                    self.globals.update(owner.env);self.global_active.remove(key)
                    self.publish(owner)
                return self.globals.get(key,Value(typ))
            return Value(typ)
        if node.kind in {'Paren','OrReturn','OrBranch','OrPanic','AutoCast'}:
            inner=node.role('expr');value=self.eval(inner,context)
            values=context.expressions.get((inner,'results'))
            if values is not None:
                unwrap=node.kind in {'OrReturn','OrBranch','OrPanic'}
                return self.record_results(node,context,values[:-1] if unwrap else values,
                    optional=not unwrap and context.expressions.get((inner,'optional'),False))
            return value
        if node.kind=='TypeAssertion':
            original=self.eval(node.role('expr'),context)
            result=self.narrowed(original,typ)
            self.record_results(node,context,[result,Value(Type('scalar','bool'))],optional=True)
            return result
        if node.kind=='FieldValue':return self.eval(node.role('value'),context)
        if node.kind=='Selector':
            value=self.member(self.eval(node.role('expr'),context),node.role('field').text)
            if any(origin.seed is not None for origin in value.storage):
                self.readonly_access.setdefault(str(node.source.path),set()).add(node.role('field').start)
            return value
        if node.kind=='Index':
            self.eval(node.role('index'),context)
            container=self.eval(node.role('expr'),context);result=self.item(container)
            if container.typ.kind=='map':self.record_results(node,context,[result,Value(Type('scalar','bool'))],optional=True)
            return result
        if node.kind=='Deref':return self.deref(self.eval(node.role('expr'),context))
        if node.kind=='Slice':
            value=self.eval(node.role('expr'),context)
            while value.typ.kind=='pointer':value=self.deref(value)
            for role in ('low','high'):self.eval(node.role(role),context)
            storage={Origin(origin.index,origin.path,'data',origin.seed) if origin.seed is not None and origin.aspect=='inline' else origin
                     for origin in value.storage}
            fields={name:self.copied(self.member(value,name)) for name in self.struct_names(value.typ.elem or Type())} if value.typ.soa else {}
            return Value(typ,set(value.data)|storage,fields=fields,element=self.item(value))
        if node.kind=='Unary':
            value=self.eval(node.role('expr'),context)
            if node.data.get('op')=='**':return value
            if node.data.get('op')=='&':
                self.require(value.storage,node,context,'take a writable address of a read-only slice element')
                return Value(typ,target=value)
            return Value(typ)
        if node.kind=='Cast':
            value=self.eval(node.role('expr'),context)
            if node.data.get('op')=='transmute':self.require(self.references(value),node,context,'erase read-only slice permissions with transmute')
            result=self.copied(value);result.typ=typ;return result
        if node.kind=='Compound':
            value=Value(typ);fields=[]
            if typ.kind=='struct' and typ.node:
                fields=[name.text for field in typ.node.role('fields').children if field.kind=='Field' for name in field.group('names')]
            for index,element in enumerate(node.group('elems')):
                if typ.kind=='map' and element.kind=='FieldValue':
                    key=element.role('field')
                    self.require(self.references(self.eval(key,context)),key,context,
                                 'preserve read-only slice references in a map key')
                incoming=self.copied(self.eval(element,context))
                if typ.kind=='set':self.require(self.references(incoming),element,context,
                                                'preserve read-only slice references in a Set key')
                if element.kind=='FieldValue' and typ.kind not in {'map','array','fixed'}:value.fields[element.role('field').text]=incoming
                elif index<len(fields):value.fields[fields[index]]=incoming
                elif value.element is None:value.element=incoming
                else:value.element.merge(incoming)
                expected=None
                if typ.kind=='struct' and typ.node:
                    name=element.role('field').text if element.kind=='FieldValue' else fields[index] if index<len(fields) else None
                    member=self.r.field(typ,name) if name else None
                    expected=member[0] if member else None
                elif typ.elem:expected=typ.elem
                if expected and expected.name in {'any','rawptr'}:
                    self.require(self.references(incoming),element,context,'erase read-only slice permissions in an opaque element')
            return value
        if node.kind=='OrElse':
            first=node.role('x');value=self.eval(first,context)
            values=context.expressions.get((first,'results'))
            values=[self.copied(item) for item in values[:-1]] if values is not None else [self.copied(value)]
            fallback=self.expression_results(node.role('y'),context,len(values))
            for index,item in enumerate(fallback):
                if index<len(values):values[index].merge(item)
            return self.record_results(node,context,values)
        if node.kind in {'Ternary','WhenExpr'}:
            self.eval(node.role('cond'),context)
            selected=self.condition(node.role('cond'),context) if node.kind=='WhenExpr' else None
            if selected is not None:
                values=self.expression_results(node.role('x' if selected else 'y'),context)
                return self.record_results(node,context,[self.copied(value) for value in values])
            result=Value(typ)
            for role in ('x','y'):result.merge(self.copied(self.eval(node.role(role),context)))
            return result
        if node.kind in {'SwitchExpr','CompileSwitchExpr'}:
            self.eval(node.role('selector'),context);results=[]
            for case in self.switch_cases(node,context):
                for match in case['matches']:self.eval(node.source.at(match,node),context)
                arm=node.source.at(case['result'],node)
                values=self.expression_results(arm,context)
                if arm.kind=='SwitchBreak' or self.r.is_diverging_call(arm):continue
                for index,value in enumerate(values):
                    if index>=len(results):results.append(self.copied(value))
                    else:results[index].merge(value)
            if results and typ.kind not in {'unknown','generic'}:results[0].typ=typ
            return self.record_results(node,context,results)
        if node.kind=='SwitchResults':
            return self.record_results(node,context,[self.eval(arg,context) for arg in node.group('args')])
        if node.kind=='Comprehension':
            loop=node.role('loop');self.bind_loop(loop,context)
            self.eval(node.role('cond'),context)
            key=node.role('key')
            if key:self.require(self.references(self.eval(key,context)),key,context,
                                'preserve read-only slice references in a map key')
            value=self.eval(node.role('output'),context)
            if node.data.get('spread'):value=self.item(value)
            if typ.kind=='set':self.require(self.references(value),node.role('output'),context,
                                           'preserve read-only slice references in a Set key')
            return Value(typ,element=self.copied(value))
        # Operators, interpolation fields and other value expressions may call
        # mutating helpers even when their own result contains no references.
        for child in node.children:
            if child.kind not in {'Procedure','ProcedureType','ArrayType','StructType'}:self.eval(child,context)
        return Value(typ)

    @staticmethod
    def record_results(node,context,values,optional=False):
        context.expressions[node,'results']=list(values)
        context.expressions[node,'optional']=optional
        return values[0] if values else Value()

    def expression_results(self,node,context,count=None):
        first=self.eval(node,context)
        values=context.expressions.get((node,'results'),[first])
        return values[:1] if count==1 and context.expressions.get((node,'optional')) else values

    def switch_cases(self,node,context):
        cases=node.data['cases']
        if node.kind!='CompileSwitchExpr':return cases
        selector=node.role('selector')
        def constant(value):
            boolean=self.condition(value,context)
            return boolean if boolean is not None else value.source.constant_int(value)
        selected=constant(selector) if selector else None
        if selector and selected is None:return cases
        default=None
        for case in cases:
            if not case['matches']:default=case;continue
            for span in case['matches']:
                match=node.source.at(span,node)
                if selector:
                    value=constant(match)
                    matched=value==selected if value is not None else None
                else:matched=self.condition(match,context)
                # Keep conservative joins when specialization/native checking
                # must select the branch. Never execute #run work in this pass.
                if matched is None:return cases
                if matched:return [case]
        return [default] if default else []

    def bind_loop(self,node,context):
        value=self.eval(node.role('expr'),context)
        names=node.group('names')
        for index,name in enumerate(names):
            key=node,name.data.get('name') or name.text.lstrip('&')
            if value.typ.kind=='map':
                incoming=self.item(value) if index==1 else Value(self.r.type_of(name))
            else:incoming=self.item(value) if index==0 else Value(self.r.type_of(name))
            annotation=node.role('value_type' if index==0 else 'index_type')
            if annotation:incoming.typ=self.r.type_node(annotation,context.substitutions)
            elif incoming.typ.kind in {'unknown','generic'} and value.typ.kind not in {'slice','array','fixed','dynamic','map'}:
                incoming.typ=self.r.type_of(name)
            if name.kind=='Unary' and name.data.get('op')=='&':
                self.require(value.data,node.role('expr'),context,'iterate over a read-only slice by writable reference')
                incoming=Value(Type('pointer',elem=incoming.typ),target=incoming)
            else:incoming=self.copied(incoming)
            if key not in context.env:context.env[key]=incoming
            else:self.changed|=context.env[key].merge(incoming)

    def condition(self,node,context):
        if node is None:return None
        if node.kind=='Paren':return self.condition(node.role('expr'),context)
        if node.kind=='Call':
            callee=node.role('callee');args=node.group('args')
            if callee.kind=='Directive' and callee.data.get('name')=='config' and len(args)==2:
                from .compiler import unquote
                name=unquote(args[0].text) if args[0].text.startswith(('"','`')) else args[0].text
                value=next((flag.split('=',1)[1] for flag in self.project.flags
                            if flag.startswith('-define:'+name+'=')),None)
                if value in {'true','false'}:return value=='true'
                if value is None:return self.condition(args[1],context)
            library=self.project.purity.library(node)
            if library and library[0]=='base:intrinsics' and library[1].startswith('type_is_') and args:
                typ=context.substitutions.get(args[0].text) or self.r.type_node(args[0],context.substitutions)
                if isinstance(typ,Type) and typ.kind not in {'generic','unknown'}:
                    trait=library[1][8:]
                    if trait in {'slice','pointer','struct','union','map','array','enum','string'}:return typ.kind==trait
                    if trait=='dynamic_array':return typ.kind=='dynamic'
                    if trait=='integer':return typ.kind=='scalar' and typ.name in {'int','uint','uintptr','i8','i16','i32','i64','i128','u8','u16','u32','u64','u128','rune','byte'}
                    if trait=='float':return typ.kind=='scalar' and typ.name in {'f16','f32','f64'}
            return None
        if node.kind=='Unary' and node.data.get('op')=='!':
            value=self.condition(node.role('expr'),context)
            return not value if value is not None else None
        if node.kind=='Ident':
            if node.text in {'true','false'}:return node.text=='true'
            return None
        if node.kind=='Binary':
            left,right=node.role('left'),node.role('right');op=node.data['op']
            if op in {'==','!=','<','<=','>','>='}:
                def value(n):
                    if n.kind=='Ident' and n.text in context.substitutions:return context.substitutions[n.text]
                    if n.kind=='Ident' and n.text in {'true','false'}:return n.text=='true'
                    if n.kind=='Ident' and n.text=='ODIN_DEBUG' and self.r.symbol(n) is None:return '-debug' in self.project.flags
                    if n.kind=='Ident' and n.text=='ODIN_OS' and self.r.symbol(n) is None:
                        os=self.project.target[0] if self.project.target else {'darwin':'darwin','win32':'windows'}.get(sys.platform,sys.platform)
                        return {'darwin':'Darwin','windows':'Windows','linux':'Linux','freebsd':'FreeBSD'}.get(os)
                    if n.kind=='ImplicitSelector':return n.text.lstrip('.')
                    if n.kind=='Call' and n.role('callee').kind=='Directive' and n.role('callee').data.get('name')=='config':return self.condition(n,context)
                    if n.source.denotes_type(n):return self.r.type_node(n,context.substitutions)
                    if n.kind=='Call':
                        typ=self.r.type_node(n,context.substitutions)
                        if typ.kind not in {'generic','unknown'}:return typ
                    return n.source.constant_int(n)
                a,b=value(left),value(right)
                if op not in {'==','!='}:
                    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
                        return {'<':a<b,'<=':a<=b,'>':a>b,'>=':a>=b}[op]
                    return None
                if isinstance(a,Type) and isinstance(b,Type):same=self.same_type(a,b)
                elif a is not None and b is not None:same=a==b
                else:return None
                if same is None:return None
                return same if op=='==' else not same
            if op in {'&&','||'}:
                a,b=self.condition(left,context),self.condition(right,context)
                if op=='&&':return False if a is False or b is False else True if a is True and b is True else None
                return True if a is True or b is True else False if a is False and b is False else None
        return None

    def statement(self,node,context):
        if node is None:return
        if node.kind=='Procedure':return
        if node.kind=='Declaration':
            names,values=node.group('names'),node.group('values')
            if any(value.kind in {'Procedure','ProcedureType','StructType','EnumType','UnionType'} for value in values):return
            incoming_values=self.expression_results(values[0],context,len(names)) if len(values)==1 else [self.eval(value,context) for value in values]
            for index,name in enumerate(names):
                rhs=values[index] if index<len(values) else values[0] if len(values)==1 else None
                incoming=incoming_values[index] if index<len(incoming_values) else Value()
                value=self.copied(incoming)
                typ=self.r.type_node(node.role('type'),context.substitutions) if node.role('type') else value.typ
                value.typ=self.substitute_type(typ,context.substitutions)
                if node.data.get('immutable'):value=self.freeze(value,name)
                elif value.typ.kind in {'slice','generic','unknown'}:
                    self.require(value.data,rhs or name,context,'create a writable slice alias; use :> or copy the backing storage with core:slice.clone')
                elif value.typ.name in {'any','rawptr'}:
                    self.require(self.references(value),rhs or name,context,'erase read-only slice permissions in an opaque value')
                key=node,name.text
                if key not in context.env:context.env[key]=value;self.changed=True
                else:self.changed|=context.env[key].merge(value)
            return
        if node.kind=='Assignment' and node.data.get('op')!='in':
            left,right=node.group('lhs'),node.group('rhs')
            incoming=[self.copied(value) for value in (self.expression_results(right[0],context,len(left)) if len(right)==1 else
                                                      [self.eval(value,context) for value in right])]
            for index,lhs in enumerate(left):
                if lhs.text=='_':continue
                target=self.eval(lhs,context)
                value=incoming[index] if index<len(incoming) else incoming[0] if incoming else Value()
                if target.typ.kind=='set' and node.data.get('op')=='+=':
                    self.require(self.references(value),lhs,context,'preserve read-only slice references in a Set key')
                if lhs.kind=='Index' and self.type(lhs.role('expr'),context).kind=='map':
                    key=lhs.role('index')
                    self.require(self.references(self.eval(key,context)),key,context,
                                 'preserve read-only slice references in a map key')
                symbol=self.r.symbol(lhs) if lhs.kind=='Ident' else None
                named_result=symbol and (symbol.declaration,symbol.name) in context.named_results
                if target.typ.kind in {'slice','generic','unknown'} and not named_result:
                    self.require(value.data,right[min(index,len(right)-1)] if right else lhs,context,'store a read-only slice in writable storage')
                if target.typ.name in {'any','rawptr'}:
                    self.require(self.references(value),lhs,context,'erase read-only slice permissions in opaque storage')
                if lhs.kind=='Ident':
                    symbol=self.r.symbol(lhs)
                    if symbol and symbol.scope.kind=='File':
                        self.require(self.references(value),lhs,context,'expose a read-only slice through global writable storage')
                self.write(target,value,context,lhs,strong=node.data.get('op')=='=')
            return
        if node.kind=='IncDec':
            self.require(self.eval(node.role('expr'),context).storage,node,context,'modify slice elements');return
        if node.kind=='Return':
            values=node.group('results') or node.group('values')
            if not values:
                values=[child for child in node.children if child.kind not in {'Comment','Directive'}]
            if not values:
                for index,key in enumerate(context.named_results):
                    if key:self.changed|=context.results[index].merge(self.copied(context.env[key]))
            returned=self.expression_results(values[0],context,len(context.results)) if len(values)==1 else [self.eval(value,context) for value in values]
            copies={};snapshots={} # Keep copies alive while the snapshot memo uses identity keys.
            for index,value in enumerate(returned):
                incoming=self.snapshot(self.copied(value,copies),snapshots)
                while len(context.results)<=index:context.results.append(Value(incoming.typ))
                self.changed|=context.results[index].merge(incoming)
            return
        if node.kind in {'When','If'}:
            self.statement(node.role('init'),context)
            self.eval(node.role('cond'),context)
            selected=self.condition(node.role('cond'),context) if node.kind=='When' else None
            if selected is None:context.controls.append(node)
            if selected is not False:self.statement(node.role('body'),context)
            if selected is not True:self.statement(node.role('else'),context)
            if selected is None:context.controls.pop()
            if selected is None:
                replacements=self.terminal_allocations(node,context)
                keys={self.header_key(assignment.group('lhs')[0]) for assignment in replacements}
                if len(keys)==1:
                    incoming=None
                    for assignment in replacements:
                        value=self.snapshot(self.copied(self.eval(assignment.group('rhs')[0],context)))
                        if incoming is None:incoming=value
                        else:incoming.merge(value)
                    lhs=replacements[0].group('lhs')[0]
                    self.write(self.eval(lhs,context),incoming,context,lhs,strong=True)
            return
        if node.kind=='Range':
            self.bind_loop(node,context);context.controls.append(node)
            self.statement(node.role('body'),context);context.controls.pop();return
        if node.kind=='TypeSwitch':
            value=self.eval(node.role('expr'),context)
            names=node.group('names');body=node.role('body')
            context.controls.append(node)
            for case in body.children if body else []:
                patterns=case.group('types')
                variant=self.r.type_node(patterns[0],context.substitutions) if len(patterns)==1 else value.typ
                for name in names:
                    incoming=self.narrowed(value,variant)
                    if name.kind=='Unary' and name.data.get('op')=='&':
                        self.require(value.storage,node.role('expr'),context,'take a writable union reference into a read-only slice')
                        incoming=Value(Type('pointer',elem=variant),target=incoming)
                    context.env[node,name.text.lstrip('&')]=incoming
                for statement in case.group('body'):self.statement(statement,context)
            context.controls.pop()
            return
        if node.kind=='Defer':
            context.controls.append(node)
            for child in node.children:self.statement(child,context)
            context.controls.pop()
            return
        if node.kind=='ExpressionStatement':
            for child in node.children:self.statement(child,context) if child.kind.endswith('Statement') else self.eval(child,context)
            return
        if node.kind in {'Call','Binary','Unary','Comprehension'}:self.eval(node,context);return
        conditional=node.kind in {'For','Switch'}
        if conditional:context.controls.append(node)
        for child in node.children:self.statement(child,context)
        if conditional:context.controls.pop()

    def publish(self,context):
        if not self.context_ready(context):return
        for (node,reason),(seed,related) in context.issues.items():
            source=node.source
            if getattr(source,'native_inspected',False):continue
            symbol=self.r.by_decl.get((seed.parent,seed.text)) if seed else None
            marker=self.r.markers.get((symbol.declaration,symbol.name)) if symbol else None
            if marker:
                marker=self.r.access(source,seed.source,marker)
                message=f"Cannot {reason}: read-only slice access from '{seed.text}'."
                statement=next((p for p in [node,*node.ancestors()] if p.kind in {
                    'Assignment','Declaration','Return','ExpressionStatement','Range','If','For','Switch','When'}),node)
                if not hasattr(source,'slice_guard_related'):source.slice_guard_related={}
                if (node.start,node.end) in source.slice_guard_related:continue
                source.slice_guard_related[node.start,node.end]=[seed,*related]
                control=statement.parent if statement.parent and statement.parent.kind in {'If','For'} else statement if statement.kind in {'If','For'} else None
                converted=next((p for p in statement.ancestors() if p.kind=='Block'),None)
                if control:source.guard_block(control.role('body'),marker,message,node)
                elif converted and source.raw[converted.start:converted.start+1]!=b'{':source.guard_block(converted,marker,message,node)
                else:source.guards.setdefault(statement,[]).append((marker,message,node))
                source.guard_messages[node.start,node.end]=message
                continue
            if node not in source.blocked:
                token=source.prefix+'slice_access_error_'+str(node.start)
                name=seed.text if seed else 'value'
                message=f"Cannot {reason}: read-only slice access from '{name}'."
                source.blocked[node]=(token,message);source.error_messages[token]=message
                if not hasattr(source,'slice_related'):source.slice_related={}
                source.slice_related[token]=[seed,*related]

    def context_ready(self,context):
        def unresolved(typ,seen=None):
            seen=set() if seen is None else seen
            if id(typ) in seen:return False
            seen.add(id(typ))
            if typ.kind in {'generic','unknown'}:return True
            return (bool(typ.elem and unresolved(typ.elem,seen)) or
                    any(unresolved(value,seen) for value in [*typ.members.values(),*typ.substitutions.values()] if isinstance(value,Type)))
        if not context.proc:return True
        for name,kind in self.r.generic_parameters(context.proc.role('type')).items():
            if kind=='typeid' and (name not in context.substitutions or unresolved(context.substitutions[name])):return False
        return not any(unresolved(context.env[field,name.text.lstrip('$')].typ)
                       for name,field in self.parameters(context.proc))

    def metadata(self):
        result={}
        for source in self.project.sources.values():
            for signature in source.nodes:
                if signature.kind!='ProcedureType':continue
                for name,_ in self.parameters(signature):
                    if name.data.get('slice_mut'):
                        key=str(source.path),name.start
                        result[key]={'file':key[0],'offset':name.start,'end':name.end,'name':name.text,'explicit':True}
        for context in self.summaries.values():
            if not context.proc:continue
            params=self.parameters(context.proc)
            bindings=[self.freeze(Value(context.env[field,name.text.lstrip('$')].typ),name) for name,field in params]
            ready=self.context_ready(context)
            for origin in context.effects:
                if origin.index>=len(params):continue
                name,field=params[origin.index]
                typ=self.parameter_type(field,context.substitutions)
                if typ.kind!='slice':continue
                if not ready:
                    if origin.path or origin.aspect!='data':continue
                    operation,reason,_=context.effects[origin]
                    direct_write=(reason=='modify slice elements' and operation.kind=='Index' and
                                  self.type(operation.role('expr'),context).kind=='slice')
                    direct_alias=reason.startswith('create a writable slice alias') and self.type(operation,context).kind=='slice'
                    if not direct_write and not direct_alias:continue
                if not self.resolve(origin,bindings):continue
                key=str(name.source.path),name.start
                result[key]={'file':key[0],'offset':name.start,'end':name.end,'name':name.text,
                             'explicit':bool(name.data.get('slice_mut'))}
        return list(result.values())

    def editor_info(self):
        parameters=self.metadata();result=[]
        files={item['file'] for item in parameters}|{str(context.proc.source.path) for context in self.summaries.values() if context.proc}
        sources={str(source.path):source for source in self.r.sources
                 if str(source.path) in files or source.path in self.project.sources}
        for source in sources.values():
            if not source.thor and str(source.path) not in files:continue
            # Same byte hash used by TLS. Results from saved files must never
            # be attached to a different unsaved document snapshot.
            fingerprint=14695981039346656037
            for byte in source.raw:fingerprint=((fingerprint^byte)*1099511628211)&0xffffffffffffffff
            result.append({'file':str(source.path),'source_hash':fingerprint,
                           'parameters':[item for item in parameters if item['file']==str(source.path)],
                           'readonly_access':sorted(self.readonly_access.get(str(source.path),()))})
        return result

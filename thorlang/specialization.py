"""Lexical generic bindings and native, lazy per-specialization substitutions."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json

from .semantics import Type, descendants
from .compiler import ThorError
from . import values


class Unresolved(ThorError):pass


@dataclass
class Binding:
    symbol: object
    typ: Type
    value: object = None
    canonical: str = ''
    condition: str = ''

    @property
    def key(self):return self.symbol.declaration,self.symbol.name


def type_code(typ,source,resolver):
    if isinstance(typ,int):return str(typ)
    if typ.nominal is None and typ.kind in {'scalar','string'} and typ.name:return typ.name
    if typ.nominal:
        node=typ.nominal
    else:node=typ.node
    declaration=node.parent if node else None
    if declaration and declaration.kind=='Declaration' and len(declaration.group('names'))==1:
        scope=declaration.source.scope(declaration)
        context=getattr(source,'evaluation_context',None)
        if scope.kind!='File' and (context is None or not any(a.kind==scope.kind and a.start==scope.start and a.source.path==scope.source.path for a in context.ancestors())):
            raise Unresolved('A local generic type needs its original lexical scope.')
        name=resolver.access(source,declaration.source,declaration.group('names')[0].text)
        if declaration.source.path.parent!=source.path.parent and name==declaration.group('names')[0].text:
            if hasattr(source,'evaluation_import'):name=source.evaluation_import(declaration.source)+name
            else:raise Unresolved('The generic type is not visible from this package.')
        if typ.kind=='struct' and typ.substitutions and node.role('params'):
            name+='('+','.join(type_code(typ.substitutions[n.lstrip('$')],source,resolver)
                                for n,_ in resolver.fields(node.role('params')))+')'
        return name
    if typ.kind in {'scalar','string'} and typ.name:return typ.name
    if typ.kind=='array' and typ.length is not None:return f'[{typ.length}]'+type_code(typ.elem,source,resolver)
    if typ.kind in {'slice','pointer','dynamic'}:return {'slice':'[]','pointer':'^','dynamic':'[dynamic]'}[typ.kind]+type_code(typ.elem,source,resolver)
    if typ.kind in {'struct','enum','union','bitset'} and typ.node:return resolver.template(typ.node)
    raise Unresolved('The generic type could not be resolved.')


class Specializations:
    def __init__(self,evaluator):
        self.e=evaluator;self.bindings={};self.visited=set();self.running=set();self.origin=None;self.origin_bindings={};self.frames=[]

    def relevant(self,n):
        owners=set(n.ancestors())|{n}
        return {key:b for key,b in self.bindings.items() if b.symbol.scope in owners and
                (visible:=self.e.r.lookup(b.symbol.name,n)) and (visible.declaration,visible.name)==key}

    def fingerprint(self,bindings=None):
        bindings=self.bindings if bindings is None else bindings
        entries=[]
        for (node,name),b in bindings.items():
            try:typ=type_code(b.typ,node.source,self.e.r)
            except Unresolved:
                def shape(t):
                    if isinstance(t,int):return t
                    return (t.kind,t.name,str(t.node and t.node.source.path),t.node and t.node.start,
                            sorted((k,shape(v)) for k,v in t.substitutions.items()),
                            sorted((str(d.source.path),d.start,name,shape(v),data) for (d,name),(v,data) in t.lexical.items()))
                typ=shape(b.typ)
            entries.append((str(node.source.path),node.start,name,typ,b.value))
        return hashlib.sha256(json.dumps(sorted(entries),sort_keys=True).encode()).hexdigest()[:20] if entries else ''

    def key(self,n):return n,self.fingerprint(self.relevant(n))

    @contextmanager
    def using(self,bindings,origin=None,origin_bindings=None):
        r=self.e.r;previous=self.bindings,r.bound_types,r.bound_constants
        old_origin=self.origin,self.origin_bindings,self.frames
        if origin is not None:self.origin=origin
        if origin_bindings is not None:self.origin_bindings=origin_bindings
        if origin is not None:self.frames=self.frames+[(origin,self.origin_bindings)]
        self.bindings=bindings
        r.bound_types={k:b.typ for k,b in bindings.items() if b.value is None}
        r.bound_constants={k:b.value for k,b in bindings.items() if b.value is not None}
        try:yield
        finally:
            self.bindings,r.bound_types,r.bound_constants=previous
            self.origin,self.origin_bindings,self.frames=old_origin

    def declarations(self,source,bindings):
        out=[]
        for b in bindings.values():
            typ=type_code(b.typ,source,self.e.r)
            if b.value is None:out.append(b.symbol.name+' :: '+typ)
            else:
                source.needs_intrinsics=True
                out.append(b.symbol.name+' :: '+values.expression(b.value,typ,source.prefix+'meta'))
        return '\n'.join(out)+'\n'

    def predicate(self,n,bindings):
        n.source.evaluation_context=n
        out=[]
        for b in bindings.values():
            try:typ=type_code(b.typ,n.source,self.e.r)
            except Unresolved:
                if b.value is not None or not b.canonical:raise
                n.source.needs_intrinsics=True
                out.append('('+n.source.prefix+'meta.type_canonical_name('+b.symbol.name+') == '+json.dumps(b.canonical)+')')
                continue
            value=typ if b.value is None else values.expression(b.value,typ,n.source.prefix+'meta')
            out.append('('+b.symbol.name+' == '+value+')')
        return ' && '.join(out)

    def process(self,call):
        e=self.e;r=e.r
        if e.unbound_generic(call):return
        plan=r.resolve_call(call)
        if not plan:return
        proc=plan.signature.parent
        if not proc or proc.kind!='Procedure' or not proc.role('body'):return
        if proc.source.path.is_relative_to(e.root/'runtime'):return
        parameters=r.generic_parameters(plan.signature)
        if not parameters:return
        if e.active(call,specialization=True) is not True:return
        bindings={k:b for k,b in self.bindings.items() if b.symbol.scope in set(proc.ancestors())}
        fields=r.fields(plan.signature.role('params'))
        args={}
        for i,arg in enumerate(call.group('args')):
            if arg.kind=='FieldValue':args[arg.role('field').text]=arg.role('value')
            elif i<len(fields):args[fields[i][0].lstrip('$')]=arg
        symbols={name:group[0] for name,group in r.scopes.get(proc,{}).items()}
        for name,kind in parameters.items():
            symbol=symbols.get(name)
            if symbol is None:return
            actual=plan.substitutions.get(name)
            arg=args.get(name)
            if kind=='typeid':
                if not isinstance(actual,Type) or actual.kind in {'generic','unknown'}:return
                lexical={key:Binding(r.by_decl[key],typ,value) for key,(typ,value) in actual.lexical.items() if key in r.by_decl}
                condition=self.predicate(actual.node,lexical) if actual.node and lexical else self.predicate(actual.node,self.relevant(actual.node)) if actual.node and self.relevant(actual.node) else ''
                bindings[symbol.declaration,name]=Binding(symbol,actual,condition=condition)
            else:
                typ=r.type_node(symbol.annotation,plan.substitutions) if symbol.annotation else Type('scalar','int')
                if arg is None and isinstance(actual,int):data={'kind':'integer','value':str(actual)}
                elif arg is None:return
                elif not e.known(arg):return
                elif arg.kind=='Literal' and arg.data['op']=='integer':
                    data={'kind':'integer','value':str(arg.source.constant_int(arg))}
                elif arg.text in {'true','false'}:data={'kind':'bool','value':arg.text=='true'}
                else:data=e.execute(arg)
                bindings[symbol.declaration,name]=Binding(symbol,typ,data)
        key=proc,self.fingerprint(bindings)
        if key in self.visited or key in self.running:return
        if len(self.running)>128:e.fail(call,'Compile-time specialization depth exceeds 128; check recursive constant parameters.')
        self.running.add(key)
        try:
            with self.using(bindings,call,self.bindings.copy()):
                for n in descendants(proc.role('body')):
                    if n.kind=='Run' or e.automatic(n):e.evaluate(n)
                    if n.kind=='Call' and n not in e.busy:e.specializations.process(n)
            self.visited.add(key)
        finally:self.running.remove(key)

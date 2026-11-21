"""RNG identities; standard implementations are source-pinned, callbacks checked."""
from pathlib import Path
from .semantics import descendants
from .noallocflow import UNKNOWN

DEFAULT=('default_random',)
AMBIENT=('ambient_random',)
AMBIENT_CALLBACK=('ambient_random_callback',)
NIL=('nil',)

def generator(procedure=DEFAULT,data=NIL):return 'record',(('procedure',procedure),('data',data))
def field(flow,value,name):
    value=flow.deref(value)
    if value and value[0]=='record':return dict(value[1]).get(name,UNKNOWN)
    if value==AMBIENT and name=='procedure':return AMBIENT_CALLBACK
    return UNKNOWN

class Random:
    def __init__(self,check):self.c=check;self.r=check.r;self.ready=False;self.variants=[];self.unknown=None
    def context(self,node):return bool(node and node.kind in {'Ident','Implicit'} and node.text=='context' and self.r.symbol(node) is None)
    def record(self,typ,name):
        node=typ.node
        if typ.kind!='struct' or node is None or node.parent is None:return False
        root=node.source.info.get('odin_root')
        return bool(root and node.source.path==Path(root)/'base/runtime/core.odin' and any(n.text==name for n in node.parent.group('names')))
    def identity(self,node,seen=None):
        if node is None:return None
        seen=set() if seen is None else seen
        if node in seen:return None
        seen.add(node)
        if node.kind=='Paren':return self.identity(node.role('expr'),seen)
        if node.kind=='Procedure':
            root=node.source.info.get('odin_root');names=node.parent.group('names') if node.parent else []
            return names[0].text if names and root and node.source.path.parent==Path(root)/'base/runtime' else None
        symbol=self.r.symbol(node)
        if symbol:
            if symbol.declaration.data.get('mutable') or symbol.immutable or symbol.declaration.kind=='Field':return None
            if symbol.value:
                if symbol.value.kind=='Procedure':return self.identity(symbol.value,seen)
                if symbol.value.kind not in {'Ident','Selector','Paren'}:return None
                found=self.identity(symbol.value,seen)
                if found:return found
        imported=self.c.p.purity.imported(node)
        if imported and imported[0]=='base:runtime':
            root=node.source.info.get('odin_root');override=node.source.collections.get('base')
            if root and (override is None or Path(override)==Path(root)/'base'):return imported[1]
        return None
    def zero(self,node):
        typ=self.r.type_of(node)
        if self.record(typ,'Random_Generator'):return generator(NIL)
        if self.record(typ,'Context'):return 'record',(('random_generator',generator(NIL)),)
        return None
    def value(self,node,depth):
        flow=self.c.flow
        if self.context(node):return 'record',(('random_generator',AMBIENT),)
        if node.kind in {'Ident','Selector','Procedure'}:
            return DEFAULT if self.identity(node)=='default_random_generator_proc' else None
        if node.kind=='Call':
            if self.identity(node.role('callee'))!='default_random_generator':return None
            args=node.group('args');state=next((a.role('value') for a in args if a.kind=='FieldValue' and a.role('field').text=='state'),None)
            if state is None:state=next((a for a in args if a.kind!='FieldValue'),None)
            return generator(DEFAULT,flow.fact(state,depth+1) if state else NIL)
        if node.kind=='Compound':
            typ=self.r.type_of(node);gen=self.record(typ,'Random_Generator');context=self.record(typ,'Context')
            if not gen and not context:return None
            values={'procedure':NIL,'data':NIL} if gen else {'random_generator':generator(NIL)}
            fields=self.r.fields(typ.node.role('fields'))
            for i,item in enumerate(node.group('elems')):
                name=item.role('field').text if item.kind=='FieldValue' else fields[i][0] if i<len(fields) else None
                if name is None:return None
                if gen or name=='random_generator':values[name]=flow.fact(item.role('value') if item.kind=='FieldValue' else item,depth+1)
            return 'record',tuple(values.items())
        return None
    def context_place(self,node):
        for _ in range(24):
            if node is None:return False
            if self.context(node):return True
            if node.kind=='Paren':node=node.role('expr');continue
            symbol=self.r.symbol(node)
            if symbol and symbol.projection:node=symbol.projection[0];continue
            if node.kind=='Selector':
                if node.role('field').text not in {'random_generator','procedure','data'}:return False
                typ=self.r.type_of(node.role('expr'))
                if typ.kind=='pointer' and self.record(typ.elem,'Context'):return True
                node=node.role('expr');continue
            if node.kind=='Deref':return self.record(self.r.type_of(node),'Context')
            return False
        return False
    def root(self,node):
        for _ in range(24):
            if node is None:return None
            symbol=self.r.symbol(node)
            if symbol and symbol.projection:node=symbol.projection[0];continue
            if node.role('expr'):node=node.role('expr');continue
            if (key:=self.c.flow.key(node)) is not None:return key
            return None
        return None
    def static_value(self,node,changed,depth=0):
        if depth>24:return UNKNOWN
        # Caller-local bindings cannot establish facts in unrelated procedures.
        # Evaluate without the current call frame, restoring it afterwards.
        before=self.c.flow.snapshot();self.c.flow.values={}
        try:value=self.c.flow.fact(node)
        finally:self.c.flow.restore(before)
        if value:return value
        symbol=self.r.symbol(node)
        if symbol and symbol.declaration.kind=='Declaration' and symbol.node not in changed and symbol.value:
            return self.static_value(symbol.value,changed,depth+1)
        return UNKNOWN
    def world(self,site):
        if self.ready:return
        queue=[];reachable=set();ordered=[]
        for source in list(self.r.sources):
            root=source.info.get('odin_root')
            if source.excluded or root and source.path.is_relative_to(root):continue
            for node in source.nodes:
                if node.kind=='Procedure':
                    decl=node.parent;names=decl.group('names') if decl and decl.kind=='Declaration' else []
                    attrs=decl.group('attributes') if decl else []
                    if any(n.text=='main' for n in names) or any((e.role('field') if e.kind=='FieldValue' else e).text in {'init','test','export','no_alloc'} for a in attrs for e in a.group('elems')) or list(self.c.module_tags(source)):queue.append(node)
                elif node.kind=='Declaration' and node.data.get('mutable') and not any(a.kind=='Procedure' for a in node.ancestors()):queue.append(node)
        while queue:
            node=queue.pop();root=node.source.info.get('odin_root')
            if root and node.source.path.is_relative_to(root):continue
            for at in [node,*descendants(node)]:
                if at in reachable or not self.c.active(at):continue
                reachable.add(at)
                ordered.append(at)
                if at.kind in {'Ident','Selector'} and (at.kind!='Ident' or self.c.p.purity.reference(at)):
                    for _,signature in self.r.procedure_candidates(at):
                        if signature.parent and signature.parent.kind=='Procedure':queue.append(signature.parent)
        changed=set()
        for node in ordered:
            if node.kind=='Assignment':
                for lhs in node.group('lhs'):
                    if (key:=self.root(lhs)) is not None:changed.add(key)
            if node.kind=='Unary' and node.data.get('op')=='&':
                if (key:=self.root(node.role('expr'))) is not None:changed.add(key)
        variants=[generator()];unknown=None
        for node in ordered:
            if not self.c.active(node):continue
            if node.kind=='Assignment':
                values=node.group('rhs')
                for i,lhs in enumerate(node.group('lhs')):
                    if not self.context_place(lhs):continue
                    if i>=len(values) or node.data.get('op')!='=':unknown=node;continue
                    value=self.static_value(values[i],changed)
                    if self.context(lhs):value=field(self.c.flow,value,'random_generator')
                    elif not lhs.role('field') or lhs.role('field').text!='random_generator':unknown=node;continue
                    if value==AMBIENT:continue
                    if value and value[0]=='record':variants.append(value)
                    else:unknown=node
            elif node.kind in {'Cast','AutoCast'} or node.kind=='Unary' and node.data.get('op')=='&':
                if self.context_place(node.role('expr')):unknown=node
        self.variants=variants;self.unknown=unknown;self.ready=True
    def call(self,node):
        callee=node.role('callee');direct=self.identity(callee)
        if direct in {'default_random_generator','default_random_generator_proc','rand_bytes'}:
            self.c.io.verify_sdk(node);return True
        from .parallelcompile import known_callback
        if known_callback(self.r,callee):return False
        target=self.c.flow.fact(callee)
        if target==DEFAULT:self.c.io.verify_sdk(node);return True
        if target==AMBIENT_CALLBACK:
            self.c.io.verify_sdk(node);self.world(node)
            if self.unknown:self.c.error(node,'the ambient RNG can be replaced or escape through an unverified context; pass a known generator explicitly',self.unknown)
            args=node.group('args');facts=[self.c.flow.fact(arg) for arg in args]
            for value in self.variants:
                procedure=field(self.c.flow,value,'procedure')
                if procedure in {NIL,DEFAULT}:continue
                if procedure and procedure[0]=='function':
                    arguments=(field(self.c.flow,value,'data'),facts[1] if len(facts)>1 else UNKNOWN,facts[2] if len(facts)>2 else UNKNOWN)
                    self.c.queue.append((procedure[1],dict(self.c.work[1]),self.c.work[2],[*self.c.work[3],node],True,arguments))
                else:self.c.error(node,'the ambient RNG callback target is unknown and may allocate')
            for value in facts:self.c.flow.forget(value)
            return True
        if callee.kind=='Selector' and callee.role('field').text=='procedure':
            typ=self.r.type_of(callee.role('expr'))
            while typ.kind=='pointer':typ=typ.elem
            if self.record(typ,'Random_Generator') and not target:self.c.error(node,'RNG callback target is unknown and may allocate')
        return False

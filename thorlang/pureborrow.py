"""Call-specific storage permissions for checked ordinary procedures.

Permissions are compile-time metadata, not runtime ownership tracking. A checked
callee uses symbolic parameter paths; callers instantiate them from their local
provenance. Reference stores are propagated to protect later uses of aliases.
"""
from dataclasses import dataclass, field
import hashlib
import json


@dataclass(frozen=True)
class Access:
    proc: object
    index: int
    path: tuple = ()
    aspect: str = 'borrowed'

    def step(self, operation, aspect=None):
        return Access(self.proc,self.index,(*self.path,operation),aspect or self.aspect)

    @property
    def key(self):
        identity=repr((str(self.proc.source.path),self.proc.start,self.index,self.path,self.aspect)).encode()
        return 'p'+str(self.index)+'_'+hashlib.sha256(identity).hexdigest()[:16]


@dataclass(frozen=True)
class Permission:
    kind: str
    args: tuple = ()

    def __bool__(self):return self!=OWNED

    def order(self):
        if self.kind=='access':
            access=self.args[0]
            return (self.kind,str(access.proc.source.path),access.proc.start,access.index,access.path,access.aspect)
        if self.kind=='type':
            trait,node=self.args
            return (self.kind,trait,str(node.source.path),node.start,node.end)
        return (self.kind,tuple(part.order() if isinstance(part,Permission) else str(part) for part in self.args))

    def __or__(self,other):
        other=permission(other)
        if self==READONLY or other==READONLY:return READONLY
        if self==OWNED:return other
        if other==OWNED or self==other:return self
        parts=set(self.args if self.kind=='or' else (self,))
        parts.update(other.args if other.kind=='or' else (other,))
        return Permission('or',tuple(sorted(parts,key=lambda part:part.order())))

    def __and__(self,other):
        other=permission(other)
        if self==OWNED or other==OWNED:return OWNED
        if self==READONLY:return other
        if other==READONLY or self==other:return self
        return Permission('and',(self,other))

    def project(self,step,aspect=None):
        if self.kind=='access':
            if len(self.args[0].path)>=24:return READONLY
            return Permission('access',(self.args[0].step(step,aspect),))
        if self.kind=='or':
            result=OWNED
            for part in self.args:result|=part.project(step,aspect)
            return result
        return self

    def accesses(self):
        if self.kind=='access':return {self.args[0]}
        return {access for part in self.args if isinstance(part,Permission) for access in part.accesses()}

    def substitute(self,resolve):
        if self.kind=='access':return resolve(self.args[0])
        if self.kind in {'or','and'}:
            result=OWNED if self.kind=='or' else READONLY
            for part in self.args:
                value=part.substitute(resolve)
                result=result|value if self.kind=='or' else result&value
            return result
        return self

    def render(self,purity,source):
        if self.kind=='constant':return str(self.args[0]).lower()
        if self.kind=='access':
            source.needs_intrinsics=True
            key=json.dumps(self.args[0].key)
            return f'({source.prefix}meta.type_field_type({purity.calls.ownership_type},{key})==[0]bool)'
        if self.kind in {'or','and'}:
            operator=' || ' if self.kind=='or' else ' && '
            return '('+operator.join(part.render(purity,source) for part in self.args)+')'
        if self.kind=='type':
            trait,node=self.args
            source.needs_intrinsics=True
            value=node.source.render(node)
            typ=f'type_of({value})';meta=source.prefix+'meta'
            if trait=='pointer':return f'{meta}.type_is_pointer({typ})'
            if trait=='sequence':return f'({meta}.type_is_slice({typ}) || {meta}.type_is_dynamic_array({typ}) || {meta}.type_is_string({typ}))'
            if trait in {'big','not-big'}:
                big=source.big.module();test=f'({typ}=={big}.Integer || {typ}=={big}.Rational)'
                return '!'+test if trait=='not-big' else test
        raise RuntimeError('Unknown pure storage permission')


OWNED=Permission('constant',(False,))
READONLY=Permission('constant',(True,))


def permission(value):return value if isinstance(value,Permission) else READONLY if value else OWNED


@dataclass
class Store:
    target: Access
    value: object


class Borrowing:
    def __init__(self,purity):
        self.p=purity;self.r=purity.r
        self.envs={};self.nodes={};self.stores={};self.requirements={}
        self.preparing=False;self.prepared=set()
        self.escapes={}
        self.instances={};self.instance_changed=False
        self.mutates=set()

    def parameters(self,proc):
        return [(name,field) for field in proc.role('type').role('params').children if field.kind=='Field'
                for name in field.group('names')]

    def internal(self,proc):
        return not self.p.pure(proc) and proc not in self.p.entry_roots

    def environment(self,proc):
        if proc not in self.prepared:self.prepare([proc])
        return self.envs[proc]

    def prepare(self,roots):
        from .purity import Flow
        if self.preparing:return
        self.preparing=True
        try:
            queue=list(roots);added=[]
            while queue:
                proc=queue.pop()
                if proc in self.prepared:continue
                self.prepared.add(proc);added.append(proc)
                self.nodes[proc]=self.p.walk(proc)
                self.envs[proc]={};self.stores[proc]={};self.requirements[proc]=set()
                for index,(name,field) in enumerate(self.parameters(proc)):
                    symbol=self.r.by_decl.get((field,name.data.get('name') or name.text.lstrip('$')))
                    if symbol:
                        access=Access(proc,index)
                        value=Permission('access',(access,)) if self.internal(proc) else READONLY
                        self.envs[proc][symbol.declaration,symbol.name]=Flow(value,location=READONLY,
                            locations={access} if self.internal(proc) else set(),owner=proc,
                            kind=self.r.type_node(field.role('type')).kind)
                for node in self.nodes[proc]:
                    if node.kind!='Call' or self.p.calls.queried(node):continue
                    if self.p.builtin(node) or self.p.approved(node) or node.source.denotes_type(node.role('callee')) or self.p.io_call(node):continue
                    queue.extend(target for target in self.p.targets(node) if not self.p.io_procedure(target))
            if not added:return
            # All joins are conservative and monotone. Bound path expansion in
            # recursive pointer schemas; native pure type validation reports the
            # unsupported recursive shape rather than trusting an unknown path.
            for _ in range(max(4,sum(len(self.nodes[proc]) for proc in self.prepared)+1)):
                changed=False
                for proc in self.prepared:changed|=self.pass_(proc)
                if not changed:break
            else:
                for proc in added:
                    self.p.contracts.setdefault(proc,(proc,'Pure storage provenance did not converge; simplify the reference graph.'))
        finally:self.preparing=False

    def pass_(self,proc):
        from .purity import Flow
        changed=False;env=self.envs[proc]
        for node in self.nodes[proc]:
            if node.kind=='IncDec' or node.kind=='Assignment' and node.data['op']!='in':
                targets=node.group('lhs') if node.kind=='Assignment' else [node.role('expr')]
                if any(self.p.flow(target,env).locations for target in targets) and proc not in self.mutates:
                    self.mutates.add(proc);changed=True
            if node.kind=='Declaration' and not node.group('values'):
                for name in node.group('names'):
                    key=node,name.data.get('name') or name.text
                    if key not in env:
                        env[key]=Flow(owner=proc,kind=self.r.type_node(node.role('type')).kind);changed=True
            if node.kind=='Declaration':names,values=node.group('names'),node.group('values')
            elif node.kind=='Assignment' and node.data['op']=='=':names,values=node.group('lhs'),node.group('rhs')
            else:names,values=[],[]
            pairs=list(zip(names,values))
            from .switches import unwrap
            if len(values)==1 and unwrap(values[0]).kind in {'SwitchExpr','CompileSwitchExpr'}:pairs=[(lhs,values[0]) for lhs in names]
            for index,(lhs,rhs) in enumerate(pairs):
                if lhs.text=='_':continue
                value=self.p.copied(self.p.result_flow(rhs,index,env) if len(values)==1 else self.p.flow(rhs,env),rhs)
                if node.kind=='Declaration':
                    key=node,lhs.data.get('name') or lhs.text
                    if key not in env:
                        env[key]=value.header_copy(self.r.type_of(lhs),rhs,owner=proc);changed=True
                    else:changed|=env[key].merge(value)
                else:
                    target=self.p.flow(lhs,env)
                    changed|=target.write(value)
                    changed|=self.store(proc,target,value,node)
            if node.kind=='Call':
                builtin=self.p.builtin(node)
                if builtin in {'copy','delete'}:
                    target=self.p.arguments(node,['dst'])[0] if builtin=='copy' else next(
                        (arg.role('value') if arg.kind=='FieldValue' else arg for arg in node.group('args')
                         if arg.kind!='FieldValue' or arg.role('field').text in {'array','str','m'}),None)
                    if target and self.p.flow(target,env).origins and proc not in self.mutates:self.mutates.add(proc);changed=True
                if self.p.builtin(node)=='copy' and len(node.group('args'))>=2:
                    dst,src=self.p.arguments(node,['dst','src'])
                    if dst and src:
                        target=self.p.flow(dst,env).item();value=self.p.flow(src,env).item()
                        changed|=target.write(value)
                        changed|=self.store(proc,target,value,node)
                elif not(self.p.builtin(node) or self.p.approved(node) or node.source.denotes_type(node.role('callee')) or self.p.io_call(node)):
                    for callee in self.p.targets(node):
                        if callee not in self.stores:continue
                        if callee in self.mutates and proc not in self.mutates:self.mutates.add(proc);changed=True
                        bindings=self.bindings(node,callee,env)
                        self.instance_changed=False
                        cache=self.instances.setdefault((node,callee),{})
                        for store in list(self.stores[callee].values()):
                            target=self.project(bindings,store.target)
                            value=self.instantiate(store.value,bindings,cache=cache)
                            changed|=target.write(value)
                            changed|=self.store(proc,target,value,node)
                        changed|=self.instance_changed
            if node.kind in {'Range','TypeSwitch'} and node.group('names'):
                name=node.group('names')[0];key=node,name.data.get('name') or name.text.lstrip('&')
                value=self.p.flow(node.role('expr'),env)
                if node.kind=='Range':value=value.item()
                reference=name.kind=='Unary' and name.data.get('op')=='&'
                if key not in env:
                    env[key]=value if reference else value.header_copy(self.r.type_of(name),name)
                    if not reference:env[key].location=READONLY
                    changed=True
                else:changed|=env[key].merge(value)
        return changed

    def store(self,proc,target,value,node):
        from .purity import Flow
        changed=False
        for location in target.locations:
            if location.proc is not proc or len(location.path)>24:continue
            if proc in value.stack_owners():
                self.escapes[node]='Pure helpers cannot store a reference to their local stack storage through an input; allocate an owned buffer or copy the value instead.'
                continue
            if location not in self.stores[proc]:
                self.stores[proc][location]=Store(location,Flow());changed=True
            changed|=self.stores[proc][location].value.merge(value)
        return changed

    def bindings(self,call,callee,env):
        from .purity import Flow
        params=self.parameters(callee)
        positional=[];named={}
        receiver=call.role('callee')
        if receiver.kind=='Selector' and receiver.data.get('op')=='->':positional.append(receiver.role('expr'))
        for arg in call.group('args'):
            if arg.kind=='FieldValue':named[arg.role('field').text]=arg.role('value')
            else:positional.append(arg)
        ellipsis=call.data.get('roles',{}).get('ellipsis')
        bound=[]
        position=0
        for name,field in params:
            key=name.text.lstrip('$')
            if field.data.get('variadic'):
                values=positional[position:];position=len(positional)
                if key in named:values=[named[key]]
                if len(values)==1 and ellipsis and values[0].start>=ellipsis[1]:
                    flow=self.p.flow(values[0],env).header_copy(self.r.type_of(values[0]),values[0])
                else:
                    elements=[]
                    for value in values:
                        if value.kind=='Unary' and value.data.get('op')=='**':
                            base=value.role('expr');typ=self.r.type_of(base)
                            elements.append(self.p.flow(base,env).item().header_copy(typ.elem,base))
                        else:elements.append(self.p.copied(self.p.flow(value,env),value).header_copy(self.r.type_of(value),value))
                    flow=Flow(element=Flow.joined(elements) if elements else Flow())
                flow.location=READONLY;bound.append(flow);continue
            value=named.get(key)
            if value is None and position<len(positional):value=positional[position];position+=1
            value=value or field.role('default')
            flow=self.p.flow(value,env)
            typ=self.r.type_of(value) if value else self.r.type_node(field.role('type'))
            flow=flow.header_copy(typ,value)
            flow.location=READONLY
            bound.append(flow)
        return bound

    def project(self,bindings,access):
        from .purity import Flow
        value=bindings[access.index] if access.index<len(bindings) else Flow(READONLY)
        for step in access.path:
            if step[0]=='field':value=value.member(step[1])
            elif step[0]=='item':value=value.item()
            elif step[0]=='deref':value=value.deref()
        return value

    def resolve(self,bindings,access):
        value=self.project(bindings,access)
        return value.location if access.aspect=='location' else value.borrowed

    def instantiate(self,value,bindings,seen=None,cache=None):
        from .purity import Flow
        seen={} if seen is None else seen
        cache={} if cache is None else cache
        if id(value) in seen:return seen[id(value)]
        resolve=lambda access:self.resolve(bindings,access)
        if id(value) not in cache:cache[id(value)]=Flow();self.instance_changed=True
        result=cache[id(value)]
        seen[id(value)]=result
        self.instance_changed|=result.merge(Flow(value.borrowed.substitute(resolve),numeric_copy=value.numeric_copy,stack=set(value.stack)))
        location=result.location|value.location.substitute(resolve)
        self.instance_changed|=location!=result.location;result.location=location
        result.owner=value.owner;result.kind=value.kind
        for name,child in value.fields.items():
            result.fields[name]=self.instantiate(child,bindings,seen,cache)
        if value.element:result.element=self.instantiate(value.element,bindings,seen,cache)
        originals=[self.project(bindings,access) for access in value.origins]
        targets=[original.target for original in originals if original.target]
        if not targets and value.target:targets=[self.instantiate(value.target,bindings,seen,cache)]
        for target in targets:self.instance_changed|=result.merge(Flow(target=target))
        # A symbolic reference stored through an out parameter must keep the
        # caller's actual alias targets, not a detached copy of their metadata.
        for original in originals:
            self.instance_changed|=result.merge(Flow(origins=set(original.origins),stack=set(original.stack)))
            if original.element and result.element is None:result.element=original.element
        return result

    def finish(self,procedures):
        procedures=[proc for proc in procedures if proc in self.envs]
        for proc in procedures:
            wanted=self.requirements.setdefault(proc,set())
            for node,conditions in self.p.conditions.items():
                if self.p.owner(node) is proc:
                    for checks,_ in conditions:
                        for check in checks:
                            if isinstance(check,Permission):wanted.update(check.accesses())
        for _ in range(max(64,len(procedures)*25+1)):
            changed=False
            for proc in procedures:
                env=self.envs[proc]
                for node in self.nodes[proc]:
                    if node.kind!='Call':continue
                    for target in self.p.targets(node):
                        if target not in self.requirements:continue
                        bindings=self.bindings(node,target,env)
                        for access in list(self.requirements[target]):
                            for needed in self.resolve(bindings,access).accesses():
                                if needed.proc is proc and len(needed.path)<=24 and needed not in self.requirements[proc]:
                                    self.requirements[proc].add(needed);changed=True
            if not changed:return

    def argument(self,call,callees):
        proc=self.p.owner(call)
        if proc is None:return 'struct{}{}'
        fields=[]
        for callee in callees:
            bindings=self.bindings(call,callee,self.environment(proc))
            for access in sorted(self.requirements.get(callee,()),key=lambda a:a.key):
                condition=self.resolve(bindings,access).render(self.p,call.source)
                fields.append(access.key+':([0]bool when '+condition+' else [0]u8)')
        return 'struct{'+','.join(fields)+'}{}'

"""Opt-in allocation-effect proof. Unknown runtime effects fail closed.

This validator does not alter lowering, allocation policy, or native code.
"""
from collections import deque
import re
from .semantics import descendants

UNEVALUATED={'size_of','align_of','offset_of','type_of','#assert','#config','#caller_expression','result_type_of'}
SAFE={'len','cap','min','max','abs','clamp','complex','quaternion','real','imag','jmag','kmag','conj',
      'swizzle','expand_values','compress_values','soa_zip','soa_unzip','raw_data','copy','transmute','unreachable'}
HEADERS={'len','cap','raw_data','copy','soa_zip','soa_unzip','unreachable'}


def elements(attribute):
    return [item for item in attribute.group('elems')
            if (item.role('field') if item.kind=='FieldValue' else item).text=='no_alloc']


def tags(source):
    for tag in source.info.get('file_tags',[]):
        words=tag.removeprefix('#+').split()
        if words and words[0]=='vet':
            names=[name for name in re.split(r'[\s,]+',' '.join(words[1:])) if name]
            if 'no_alloc' in names:yield tag,[name for name in names if name!='no_alloc']


def tag_edits(source):
    if not source.thor or b'no_alloc' not in source.raw:return []
    result=[]
    cursor=0
    for tag,names in tags(source):
        for match in re.finditer(re.escape(tag.encode()),source.raw):
            if match.start()<cursor:continue
            if any(a<=match.start()<b for a,b in source.info.get('comments',[])):continue
            result.append((match.start(),match.end(),'#+vet '+' '.join(names) if names else ''))
            cursor=match.end()
            break
    return result


def validate(project):
    sources=[s for s in project.sources.values() if s.thor and not s.excluded and b'no_alloc' in s.raw
             and (list(tags(s)) or any(n.kind=='Attribute' and elements(n) for n in s.nodes))]
    if not sources or project.purity.eval_mode:return
    Check(project,sources).run()


class Check:
    def __init__(self,project,sources):
        self.p=project;self.r=project.resolver;self.queue=deque();self.seen=set()
        from .noallocflow import Flow
        from .noallocio import IO
        from .noallocfailure import Failure
        from .noallocrandom import Random
        self.flow=Flow(self);self.io=IO(self);self.failure=Failure(self);self.random=Random(self)
        self.module_tags=tags
        self.budget=max(1024,sum(len(s.nodes) for s in project.sources.values()))
        for source in sources:
            whole=bool(list(tags(source)))
            for node in source.nodes:
                if node.kind=='Attribute' and (items:=elements(node)):
                    if len(items)>1:source.error('Duplicate @(no_alloc) attribute.',items[1].start,items[1].end)
                    if items[0].kind!='Ident':source.error('@(no_alloc) takes no value or arguments.',items[0].start,items[0].end)
                    values=node.parent.group('values') if node.parent and node.parent.kind=='Declaration' else []
                    if node.parent and sum(len(elements(a)) for a in node.parent.group('attributes'))>1:
                        source.error('Duplicate @(no_alloc) attribute.',node.start,node.end)
                    if len(values)!=1 or values[0].kind!='Procedure':
                        source.error('@(no_alloc) requires a procedure or fn definition with an inspectable body; it is not a trusted foreign or callback annotation.',node.start,node.end)
                    self.queue.append((values[0],{},values[0],[],True))
                if whole and node.kind=='Procedure':self.queue.append((node,{},node,[],True))
                if whole and node.kind=='Declaration' and node.data.get('mutable') and not any(a.kind=='Procedure' for a in node.ancestors()):
                    if node.group('values'):self.queue.append((node,{},node,[],False))

    def error(self,node,reason,context_site=None):
        root,path=self.work[2:4]
        names=root.parent.group('names') if root.parent and root.parent.kind=='Declaration' else []
        name=names[0].text if names else 'anonymous fn' if root.kind=='Procedure' else 'file runtime initializer'
        from pathlib import Path
        runtime=Path(__file__).resolve().parents[1]/'runtime'
        def library(at):return getattr(at.source,'native_inspected',False) or at.source.path.is_relative_to(runtime)
        primary=next((at for at in reversed([root,*path]) if not library(at)),node) if library(node) else node
        from .compiler import ThorError
        try:primary.source.error(f"no_alloc: {reason}. Required by '{name}' (allocation-free contract).",primary.start,primary.end)
        except ThorError as error:
            from .sourcemap import position
            error.diagnostics[0]['related']=[]
            for at in [root,*path,*([node] if primary is not node else [])]:
                line,column=position(at.source.raw,at.start)
                end_line,end_column=position(at.source.raw,at.end)
                error.diagnostics[0]['related'].append({'pos':{'file':str(at.source.path),'offset':at.start,'end_offset':at.end,'line':line,'column':column,'end_line':end_line,'end_column':end_column},
                    'message':'Allocation-free contract is required here.' if at is root else 'Unverified or allocating operation in the library implementation.' if at is node and primary is not node else 'Called from this allocation-free path.'})
            if context_site:
                line,column=position(context_site.source.raw,context_site.start)
                end_line,end_column=position(context_site.source.raw,context_site.end)
                error.diagnostics[0]['related'].append({'pos':{'file':str(context_site.source.path),'offset':context_site.start,'end_offset':context_site.end,'line':line,'column':column,'end_line':end_line,'end_column':end_column},
                    'message':'This context write or escape prevents proving the ambient RNG.'})
            raise

    def run(self):
        from .caller_metadata import binding_shape
        saved,saved_constants,enabled=self.r.bound_types,self.r.bound_constants,self.r.native_enabled
        discovery=self.r.native.effect_discovery if self.r.native else False
        if self.r.native:self.r.native.effect_discovery=True
        self.r.native_enabled=True
        try:
            with self.r.cached_queries():
                while self.queue:
                    self.work=self.queue.popleft();node,bindings,_,_,proc=self.work[:5]
                    arguments=self.work[5] if len(self.work)>5 else ()
                    key=node,tuple(sorted((str(k),binding_shape(v)) for k,v in bindings.items())),repr(arguments)
                    if key in self.seen or (*key[:2],repr(())) in self.seen:continue
                    self.seen.add(key)
                    from .semantics import Type
                    pending=list(bindings.values());cost=1
                    while pending:
                        typ=pending.pop();cost+=1
                        if cost>self.budget:break
                        if isinstance(typ,Type):
                            if typ.elem:pending.append(typ.elem)
                            pending.extend(typ.substitutions.values())
                    if cost>self.budget:self.error(node,'cannot prove the allocation effects of expanding generic recursion')
                    self.budget-=cost
                    self.r.bound_types={**saved,**{key:value for key,value in bindings.items() if isinstance(value,Type)}}
                    self.r.bound_constants={**saved_constants,**{key:{'kind':'integer','value':str(int(value))}
                        for key,value in bindings.items() if isinstance(value,(int,bool))}}
                    if not self.active(node):continue
                    if proc:
                        self.flow.begin(node,arguments)
                        self.procedure(node)
                    else:
                        self.flow.values={};self.flow.terminated=False;self.flow.escaped=set()
                        self.walk(node)
        finally:
            self.r.bound_types=saved;self.r.bound_constants=saved_constants;self.r.native_enabled=enabled
            if self.r.native:self.r.native.effect_discovery=discovery

    def shape(self,typ,owned=False,seen=None,unresolved=False):
        if typ is None:return owned
        seen=set() if seen is None else seen
        key=(typ.kind,typ.node,repr(typ.substitutions))
        if key in seen:return False
        seen.add(key)
        if typ.kind=='unknown' and typ.node and typ.node.kind=='Selector':
            imported=self.p.purity.imported(typ.node)
            if imported==('base:runtime','Allocator'):return False
        if typ.kind in {'big_integer','big_rational'}:return True
        if unresolved and typ.kind in {'unknown','generic'}:return True
        if owned and typ.kind in {'string','slice','dynamic','map','set','unknown','generic'}:return True
        if typ.kind in {'array','fixed','nominal'}:return self.shape(typ.elem,owned,seen,unresolved)
        if typ.kind in {'struct','union'}:
            members=list(typ.members.values())
            if typ.node:
                members.extend(self.r.type_node(t,typ.substitutions) for _,t in self.r.fields(typ.node.role('fields')) if t)
                if typ.kind=='union':members.extend(self.r.type_node(t,typ.substitutions) for t in typ.node.group('variants'))
            return any(self.shape(t,owned,seen,unresolved) for t in members)
        return False

    def procedure(self,node):
        if not self.active(node):return
        if not node.role('body'):self.error(node,'foreign or bodyless calls have unknown allocation effects')
        for attr in node.parent.group('attributes') if node.parent else []:
            if any((item.role('field') if item.kind=='FieldValue' else item).text.startswith('deferred_') for item in attr.group('elems')):
                self.error(node,'deferred procedure attributes have unverified implicit callback effects')
        for role in ['params','results']:
            for name,annotation in self.r.fields(node.role('type').role(role)):
                if annotation:
                    from .semantics import binding_name
                    if annotation.kind=='TypeidType' or role=='params' and annotation.parent and any(
                            n.kind=='PolyType' and binding_name(n)==name for n in annotation.parent.group('names')):continue
                    typ=self.r.type_node(annotation)
                    if self.shape(typ):self.error(node,'Integer/Rational parameter or result storage may allocate')
                    if self.shape(typ,unresolved=True):
                        self.error(node,'generic value copies or parameter/result conversions do not yet have provably allocation-free types')
        if node.data.get('pure'):
            for _,typ in self.r.fields(node.role('type').role('results')):
                if self.shape(self.r.type_node(typ,self.work[1]),True):self.error(node,'fn result ownership promotion may allocate')
        self.walk(node.role('body'))

    def condition(self,node,seen=None):
        value=self.flow.condition(node)
        return value if value is not None else self.static_condition(node,seen)

    def static_condition(self,node,seen=None):
        if node is None:return None
        seen=set() if seen is None else seen
        if node in seen:return None
        seen=seen|{node}
        if node.kind=='Paren':return self.static_condition(node.role('expr'),seen)
        if node.kind=='Ident':
            if node.text in {'true','false'}:return node.text=='true'
            symbol=self.r.symbol(node)
            key=(symbol.declaration,symbol.name) if symbol else None
            value=self.work[1].get(key)
            if isinstance(value,(int,bool)):return bool(value)
            if symbol and not symbol.declaration.data.get('mutable',True) and symbol.value:
                return self.static_condition(symbol.value,seen)
        if node.kind=='Unary' and node.data.get('op')=='!':
            value=self.static_condition(node.role('expr'),seen)
            return not value if value is not None else None
        if node.kind=='Binary':
            op=node.data.get('op');left,right=node.role('left'),node.role('right')
            if op in {'&&','||'}:
                a,b=self.static_condition(left,seen),self.static_condition(right,seen)
                if op=='&&':return False if a is False or b is False else True if a is True and b is True else None
                return True if a is True or b is True else False if a is False and b is False else None
            if op in {'==','!=','<','>','<=','>='}:
                a,b=self.r.constant_int(left),self.r.constant_int(right)
                if a is not None and b is not None:return {'==':a==b,'!=':a!=b,'<':a<b,'>':a>b,'<=':a<=b,'>=':a>=b}[op]
        from types import SimpleNamespace
        # The resolver's bindings identify declarations, not spellings. Passing
        # a flattened name map here would mis-select a locally shadowed $T.
        return self.p.slice_access.condition(node,SimpleNamespace(substitutions={}))

    def active(self,node):
        for ancestor in node.ancestors():
            if ancestor.kind!='When':continue
            selected=self.condition(ancestor.role('cond'))
            if selected is None:continue
            branch=ancestor.role('body' if selected else 'else')
            if not branch or not branch.start<=node.start<branch.end:return False
        return True

    def walk(self,node):
        if node is None or self.flow.terminated:return
        kind=node.kind
        if kind in {'Procedure','ProcedureType','Attribute','Import','Directive','PolyType'}:return
        if kind=='Ident' and (node.text in {'_','nil'} or node.text in node.source.imports and not self.r.symbol(node) or node.text.startswith('#') or not self.p.purity.reference(node) or node.source.denotes_type(node)):return
        if kind=='Selector' and node.source.denotes_type(node):return
        if kind.endswith('Type'):return
        if kind=='FieldValue':
            if node.role('value'):self.walk(node.role('value'))
            return
        if kind=='Runtime':
            if node.role('expr'):self.walk(node.role('expr'))
            return
        if kind in {'FallbackList','SwitchResults'}:
            for value in node.group('args'):self.walk(value)
            return
        if kind=='Run' or node in node.source.run_values:
            typ=self.storage_type(node)
            if self.shape(typ,True):self.error(node,'materializing this compile-time result may allocate')
            return
        if kind in {'When','WhenExpr'}:
            selected=self.condition(node.role('cond'))
            if selected is not None:
                branch=node.role('body' if selected else 'else') or node.role('x' if selected else 'y')
                if branch:self.walk(branch)
                return
            before=self.flow.snapshot();joined=before
            for role in ['body','else','x','y']:
                branch=node.role(role)
                if branch and self.active(branch):
                    self.flow.restore(before);self.walk(branch)
                    self.flow.join(joined);joined=self.flow.snapshot()
            self.flow.restore(joined)
            return
        if kind=='Defer':
            before=self.flow.snapshot()
            for child in node.children:self.walk(child)
            self.flow.restore(before)
            return
        if kind=='OrElse':
            self.walk(node.role('x'));before=self.flow.snapshot()
            self.walk(node.role('y'));self.flow.join(before)
            return
        if kind=='If':
            if node.role('init'):self.walk(node.role('init'))
            cond=node.role('cond')
            if cond:self.walk(cond)
            selected=self.condition(cond)
            if selected is not None:
                branch=node.role('body' if selected else 'else')
                if branch:self.walk(branch)
            else:
                before=self.flow.snapshot()
                if node.role('body'):self.walk(node.role('body'))
                left=self.flow.snapshot();self.flow.restore(before)
                if node.role('else'):self.walk(node.role('else'))
                self.flow.join(left)
            return
        if kind=='Ternary':
            cond=node.role('cond')
            if cond:self.walk(cond)
            selected=self.condition(cond)
            if selected is not None:self.walk(node.role('x' if selected else 'y'))
            else:
                before=self.flow.snapshot()
                self.walk(node.role('x'));left=self.flow.snapshot();self.flow.restore(before)
                self.walk(node.role('y'))
                self.flow.join(left)
            return
        if kind=='Binary' and node.data.get('op') in {'&&','||'}:
            left=node.role('left');self.walk(left)
            selected=self.condition(left)
            if selected==(node.data['op']=='||') and selected is not None:return
            before=self.flow.snapshot()
            self.walk(node.role('right'))
            if selected is None:self.flow.join(before)
            return
        if kind in {'Switch','TypeSwitch','SwitchExpr','CompileSwitchExpr'}:
            conditions=[]
            if kind in {'Switch','TypeSwitch'}:
                for case in descendants(node):
                    if case.kind=='Case':conditions.extend(case.group('types'))
            else:
                for case in node.data.get('cases',[]):
                    conditions.extend(node.source.at(span,node) for span in case.get('matches',[]) if span and span[0]<span[1])
            for condition in conditions:
                for call in [condition,*descendants(condition)]:
                    if call.kind=='Call':
                        for arg in call.group('args'):self.flow.forget(self.flow.fact(arg))
        if kind in {'Switch','TypeSwitch'}:
            falls_through=any(child.kind=='Other' and child.text.strip()=='fallthrough'
                and next((a for a in child.ancestors() if a.kind in {'Switch','TypeSwitch'}),None) is node
                for child in descendants(node))
            if falls_through:
                for child in descendants(node):
                    if child.kind=='Assignment':
                        for lhs in child.group('lhs'):self.flow.assign(lhs,())
                    elif child.kind=='Call':
                        for arg in child.group('args'):self.flow.forget(self.flow.fact(arg))
            selector=node.role('selector') or next((n for n in reversed(node.children) if n.kind not in {'Block','Declaration'}),None)
            blocks=[n for n in node.children if n.kind=='Block']
            for initial in node.children:
                if initial is not selector and initial not in blocks and not (kind=='TypeSwitch' and initial in node.group('names')):self.walk(initial)
            if selector and kind=='Switch':
                typ=self.r.type_of(selector);value=self.flow.normalize(self.flow.fact(selector),typ)
                if value:
                    cases=[n for block in node.children if block.kind=='Block' for n in block.children if n.kind=='Case']
                    selected=fallback=None;known=True
                    for case in cases:
                        matches=case.group('types')
                        if not matches:fallback=case
                        for match in matches:
                            candidate=self.flow.normalize(self.flow.fact(match),typ)
                            if not candidate:known=False
                            elif candidate==value:selected=case
                    if known and (selected or fallback):
                        known=not any(n.kind=='Other' and n.text.strip()=='fallthrough'
                            and next((a for a in n.ancestors() if a.kind=='Switch'),None) is node for n in descendants(selected or fallback))
                    if known:
                        self.walk(selector)
                        if selected or fallback:self.walk(selected or fallback)
                        return
            if selector:self.walk(selector)
            before=self.flow.snapshot();joined=before
            for block in blocks:
                for case in block.children:
                    self.flow.restore(before);self.walk(case)
                    self.flow.join(joined);joined=self.flow.snapshot()
            self.flow.restore(joined)
            return
        loop_entry=None
        if kind in {'For','Range'}:
            if kind=='For' and self.static_condition(node.role('cond')) is False:
                if node.role('init'):self.walk(node.role('init'))
                self.walk(node.role('cond'))
                return
            for child in descendants(node):
                if child.kind=='Call' and self.io.identity(child.role('callee')) not in {('core:bufio','writer_to_writer'),('core:bufio','writer_to_stream'),('core:bufio','reader_to_stream')}:
                    for arg in child.group('args'):self.flow.forget(self.flow.fact(arg))
                if child.kind=='Assignment':
                    for lhs in child.group('lhs'):self.flow.assign(lhs,())
            loop_entry=self.flow.snapshot()
        if kind=='OrPanic':
            expression=node.role('expr') or node.role('x') or next(iter(node.children),None)
            self.walk(expression)
            return
        if kind in {'Interpolation','Comprehension'}:self.error(node,'this Thor operation may allocate generated runtime storage')
        if kind=='Other' and re.fullmatch(r'(?:break|continue)(?:\s+[^\W\d]\w*)?|fallthrough',node.text.strip()):return
        if kind=='Other' or kind=='Unsafe' and node.text.strip()!='---':self.error(node,'unverified native operation has unknown allocation effects')
        if kind=='Call':self.call(node);return
        if kind in {'SwitchExpr','CompileSwitchExpr'}:
            selector=node.data.get('selector')
            if selector and selector[0]<selector[1]:self.walk(node.source.at(selector,node))
            before=self.flow.snapshot();joined=before
            for case in node.data.get('cases',[]):
                self.flow.restore(before)
                for span in [*case.get('matches',[]),case.get('result')]:
                    if span and span[0]<span[1]:self.walk(node.source.at(span,node))
                self.flow.join(joined);joined=self.flow.snapshot()
            self.flow.restore(joined)
            return
        if kind in {'Ident','Literal','Selector','Binary','Unary','Compound','Cast','AutoCast','Index','Deref','TypeAssertion'}:
            typ=self.storage_type(node)
            if kind in {'Ident','Selector','Index','Deref','Compound','TypeAssertion'} and typ.kind=='unknown':self.error(node,'cannot prove allocation freedom: value storage has an unresolved type')
            if self.shape(typ):self.error(node,'Integer/Rational value storage or operations may allocate')
            if kind=='Compound' and typ.elem and self.shape(typ.elem,unresolved=True):self.error(node,'Integer/Rational or generic literal elements may allocate')
            if kind=='Compound' and typ.kind in {'map','set','dynamic'}:self.error(node,'collection literals may allocate')
            if kind=='Binary' and typ.kind=='set':self.error(node,'set operations may allocate')
            if kind in {'Ident','Selector','Index','Deref','TypeAssertion','Unary','Cast','AutoCast','Compound'} and typ.kind=='generic' and (kind!='Ident' or self.p.purity.reference(node)):
                self.error(node,'generic value copies do not yet have provably allocation-free types')
            if kind=='Binary' and typ.kind in {'unknown','generic'} and node.data.get('op') not in {'==','!=','<','>','<=','>=','&&','||','..','..<','..='}:
                self.error(node,'generic value operations do not yet have provably allocation-free types')
        if kind=='Assignment':
            for lhs in node.group('lhs'):
                while lhs.kind=='Paren':lhs=lhs.role('expr')
                if lhs.kind=='Index':
                    typ=self.r.type_of(lhs.role('expr'))
                    while typ.kind=='pointer':typ=typ.elem
                    if typ.kind in {'map','unknown','generic'}:self.error(lhs,'map writes may grow storage')
                if self.r.type_of(lhs).kind=='set':self.error(lhs,'set updates may allocate')
        if kind=='Declaration':
            if not node.data.get('mutable'):return
            if node.role('type') and self.shape(self.r.type_node(node.role('type'))):
                self.error(node,'Integer/Rational value storage or implicit cleanup may allocate')
            if node.role('type') and self.shape(self.r.type_node(node.role('type')),unresolved=True):
                self.error(node,'generic value storage or implicit cleanup has unproven allocation effects')
            if node.group('values') and any(self.shape(self.r.type_of(name)) for name in node.group('names') if name.text!='_'):
                self.error(node,'Integer/Rational initialized value storage may allocate')
            if node.group('values') and any(self.shape(self.r.type_of(name),unresolved=True) for name in node.group('names') if name.text!='_'):
                self.error(node,'generic value initializers have unproven allocation effects')
            children=node.group('values')
        else:children=node.children
        for child in children:self.walk(child)
        if kind=='Unary' and node.data.get('op')=='&':self.flow.address_taken(node.role('expr'))
        if kind=='Return':self.flow.terminated=True
        if loop_entry is not None:self.flow.join(loop_entry)
        if kind=='Declaration':self.flow.bind(node)
        if kind=='Assignment':
            values=[self.flow.fact(n) for n in node.group('rhs')]
            for i,lhs in enumerate(node.group('lhs')):self.flow.assign(lhs,values[i] if node.data.get('op')=='=' and i<len(values) else ())

    def storage_type(self,node,depth=0):
        typ=self.r.type_of(node)
        if typ.kind!='unknown' or node.kind not in {'Compound','Paren','Ternary','AutoCast'} or depth>32:return typ
        parent=node.parent;item=node
        if parent and parent.kind=='FieldValue':item=parent;parent=parent.parent
        if not parent:return typ
        if parent.kind in {'Paren','AutoCast'} or parent.kind=='Ternary' and parent.role('cond') is not item:return self.storage_type(parent,depth+1)
        if parent.kind=='Binary':return self.r.type_of(parent.role('right' if parent.role('left') is item else 'left'))
        if parent.kind=='Assignment':
            values=parent.group('rhs')
            if item in values:
                lhs=parent.group('lhs');index=values.index(item)
                if index<len(lhs):return self.r.type_of(lhs[index])
        if parent.kind in {'Declaration','Field'} and parent.role('type'):return self.r.type_node(parent.role('type'))
        if parent.kind=='Return':
            proc=next((a for a in parent.ancestors() if a.kind=='Procedure'),None)
            index=parent.group('values').index(item)
            fields=self.r.fields(proc.role('type').role('results')) if proc else []
            if index<len(fields) and fields[index][1]:return self.r.type_node(fields[index][1],self.work[1])
        if parent.kind=='Compound':
            container=self.storage_type(parent,depth+1)
            if container.elem:return container.elem
            label=item.role('field').text if item.kind=='FieldValue' else None
            if label:
                member=self.r.field(container,label)
                if member:return member[0]
            elif container.node:
                fields=self.r.fields(container.node.role('fields'));index=parent.group('elems').index(item)
                if index<len(fields):return self.r.type_node(fields[index][1],container.substitutions)
        if parent.kind=='Call':
            plan=self.r.resolve_call(parent)
            if plan:
                from .caller_metadata import params
                fields=params(plan.signature)
                if item.kind=='FieldValue':field=next((f for n,f in fields if n.text==item.role('field').text),None)
                else:
                    index=parent.group('args').index(item)
                    field=fields[index][1] if index<len(fields) else fields[-1][1] if fields and fields[-1][1].data.get('variadic') else None
                if field and field.role('type'):
                    annotation=field.role('type')
                    if annotation.kind=='Ellipsis' and item.kind!='FieldValue':annotation=annotation.role('expr')
                    return self.r.type_node(annotation,plan.substitutions)
        return typ

    def builtin(self,callee,seen=None):
        if callee is None:return None
        seen=set() if seen is None else seen
        if callee in seen:return None
        seen.add(callee)
        if callee.kind=='Paren':return self.builtin(callee.role('expr'),seen)
        if callee.kind=='Directive':return '#'+callee.data.get('name','')
        symbol=self.r.symbol(callee)
        if symbol and not symbol.declaration.data.get('mutable') and not symbol.immutable and symbol.value:
            return self.builtin(symbol.value,seen) if symbol.value.kind in {'Ident','Selector','Paren'} else None
        imported=self.p.purity.imported(callee)
        if imported and imported[0]=='base:builtin':return imported[1]
        from .noallocfailure import PANIC,CHECK
        if callee.kind=='Ident' and not symbol and not imported and callee.text in SAFE|UNEVALUATED|self.p.purity.builtins|PANIC|CHECK|{'new','reserve','resize'}:return callee.text
        return self.p.purity.builtin_name(callee)

    def memory_intrinsic(self,callee,seen=None):
        from pathlib import Path
        seen=set() if seen is None else seen
        if callee is None or callee in seen:return False
        seen.add(callee)
        if callee.kind=='Paren':return self.memory_intrinsic(callee.role('expr'),seen)
        imported=self.p.purity.imported(callee)
        symbol=self.r.symbol(callee)
        if not imported and symbol and not symbol.declaration.data.get('mutable') and not symbol.immutable:
            return self.memory_intrinsic(symbol.value,seen)
        if not imported or imported[0]!='base:intrinsics' or imported[1] not in {
                'mem_copy','mem_copy_non_overlapping','mem_zero','mem_zero_volatile'}:return False
        root=callee.source.info.get('odin_root')
        if not root:return False
        directory=Path(callee.source.collections.get('base',Path(root)/'base'))/'intrinsics'
        return directory.resolve()==(Path(root)/'base'/'intrinsics').resolve()

    def call(self,node):
        callee=node.role('callee');name=None if node.source.denotes_type(callee) else self.builtin(callee)
        may_write=not node.source.denotes_type(callee) and (name is None or name=='copy')
        from . import resulttypes,parallelcompile
        if resulttypes.is_query(self.r,node) or name in UNEVALUATED or node.source.denotes_type(node):return
        if self.failure.call(node,name):return
        for arg in node.group('args'):self.walk(arg)
        if self.flow.terminated:return
        if not parallelcompile.known_callback(self.r,callee):
            typ=self.r.type_of(callee)
            decl=typ.node.parent.parent if typ.kind=='procedure' and typ.node.parent and typ.node.parent.parent else None
            if decl and decl.kind=='Declaration' and any((e.role('field') if e.kind=='FieldValue' else e).text.startswith('deferred_') for a in decl.group('attributes') for e in a.group('elems')):
                self.error(node,'deferred procedure attributes have unverified implicit callback effects')
        if self.random.call(node):return
        if self.io.call(node):return
        if name:
            if name=='unreachable':self.flow.terminated=True
            if self.shape(self.r.type_of(node)):self.error(node,'Integer/Rational builtin results may allocate')
            if name in SAFE-HEADERS and self.shape(self.r.type_of(node),unresolved=True):self.error(node,'builtin result type has unproven allocation effects')
            if name not in SAFE:self.error(node,f"builtin '{name}' may allocate or invoke an unverified runtime callback")
        elif node.source.denotes_type(callee):
            if self.shape(self.r.type_of(node)):self.error(node,'big-number conversions may allocate')
            if self.shape(self.r.type_of(node),unresolved=True):self.error(node,'generic conversions have unproven allocation effects')
        elif self.p.purity.primitive(callee) or self.memory_intrinsic(callee):pass
        else:
            fact=self.flow.fact(callee)
            target=fact[1] if fact and fact[0]=='function' else None
            if not target and not parallelcompile.known_callback(self.r,callee):self.error(node,'indirect or unresolved calls have unknown allocation effects')
            plan=self.r.resolve_call(node,targets=[target] if target and not parallelcompile.known_callback(self.r,callee) else None)
            if not plan:self.error(node,'the selected overload or specialization has unknown allocation effects')
            proc=plan.signature.parent
            if not proc or proc.kind!='Procedure':self.error(node,'foreign or bodyless calls have unknown allocation effects')
            from .semantics import descendants
            bindings=dict(self.work[1])
            for poly in descendants(plan.signature):
                if poly.kind=='PolyType' and poly.data.get('name') in plan.substitutions:
                    bindings[poly,poly.data['name']]=plan.substitutions[poly.data['name']]
                    if poly.parent:bindings[poly.parent,poly.data['name']]=plan.substitutions[poly.data['name']]
            from .caller_metadata import params
            from .caller_metadata import arrange
            declared=self.r.type_of(callee)
            signature=declared.node if target and declared.kind=='procedure' else plan.signature
            groups=arrange(signature,node.group('args'))
            if groups is None:self.error(node,'the callback argument layout is unresolved')
            fields=params(plan.signature)
            actuals={i:group[0] for i,group in enumerate(groups) if group}
            supplied={arg.role('value') if arg.kind=='FieldValue' else arg for arg in node.group('args')}
            for i,(name,field) in enumerate(fields):
                if name.kind!='PolyType':continue
                actual=actuals.get(i) or field.role('default')
                if actual is None:continue
                value=self.condition(actual)
                if value is None:value=self.r.constant_int(actual)
                if value is not None:
                    bindings[name,name.data['name']]=value
                    bindings[field,name.data['name']]=value
            for group in groups:
                for arg in group:
                    if arg not in supplied:self.walk(arg)
            arguments=self.flow.arguments(plan.signature,groups) if groups is not None else ()
            self.queue.append((proc,bindings,self.work[2],[*self.work[3],node],True,arguments))
            if plan.signature.data.get('diverging'):self.flow.terminated=True
        if may_write:
            for arg in node.group('args'):self.flow.forget(self.flow.fact(arg))
            self.flow.opaque_write()

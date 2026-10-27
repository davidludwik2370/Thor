"""Purity contracts, borrowed input provenance, and compiler scratch lifetimes.

Native Odin checks exact instantiated types and inactive when branches. This
pass tracks effects and provenance; it never treats contextless as purity.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
from .compiler import Hint
from .semantics import descendants


@dataclass(eq=False)
class Flow:
    borrowed: bool = False
    fields: dict = field(default_factory=dict)
    element: object = None

    def merge(self, other, seen=None):
        if other is self: return False
        seen=set() if seen is None else seen
        if (id(self),id(other)) in seen: return False
        seen.add((id(self),id(other)))
        changed=other.borrowed and not self.borrowed
        self.borrowed |= other.borrowed
        for name,value in other.fields.items():
            if name not in self.fields: self.fields[name]=value;changed=True
            else: changed |= self.fields[name].merge(value,seen)
        if other.element:
            if self.element is None:self.element=other.element;changed=True
            else:changed |= self.element.merge(other.element,seen)
        return changed

    def member(self,name):
        value=self.fields.setdefault(name,Flow())
        value.borrowed |= self.borrowed
        # An elided aggregate may not yet have an exact record type. Its
        # positional contents still taint a subsequently selected member.
        if self.element:value.merge(self.element)
        return value

    def item(self):
        if self.element is None:self.element=Flow()
        self.element.borrowed |= self.borrowed
        return self.element


class Purity:
    builtins={'len','cap','size_of','align_of','offset_of','type_of','min','max','abs','clamp',
              'complex','quaternion','real','imag','jmag','kmag','conj','swizzle',
              'make','copy','delete','assert','assert_contextless'}
    type_queries={'type_of','size_of','align_of','offset_of'}
    libraries={('thor:format','format'),('thor:format','format_plain'),('thor:format','format_options'),
               ('thor:format','group_integer'),('core:strings','clone'),('core:slice','clone')}
    native_constants={('core:math',name) for name in ['PI','TAU','E','SQRT_TWO','SQRT_THREE','SQRT_FIVE']}

    def __init__(self,project):
        self.project=project
        self.r=project.resolver
        self.problems={}
        self.conditions={}
        self.analyzed=set()
        self.eval_mode=False
        self.rendering=set()
        self.active=set()
        self.contracts={}
        self.inner_bodies=set()
        for source in self.r.sources:source.purity=self

    @staticmethod
    def owner(n):return next((a for a in n.ancestors() if a.kind=='Procedure'),None)

    @staticmethod
    def pure(proc):return proc and proc.kind=='Procedure' and proc.data.get('pure',False)

    def checked(self,proc):return bool(proc and (self.pure(proc) or self.eval_mode and proc in self.active))

    def module(self,source):return source.expressions.module('pure')

    def problem(self,n,message,condition=None):
        if condition:
            self.conditions.setdefault(n,[]).append((condition,message))
        else:self.problems.setdefault(n,message)

    def reference(self,n):
        if n.kind!='Ident':return False
        p=n.parent
        return not(p and (p.kind in {'Declaration','Field','Range','TypeSwitch'} and n in p.group('names')
                         or p.kind in {'Selector','FieldValue'} and p.role('field') is n
                         or p.kind in {'PolyType','EnumType','ImplicitSelector'}))

    def imported(self,callee):
        if callee is None:return None
        if callee.kind=='Selector':
            base=callee.role('expr')
            if base.kind=='Ident' and base.text in callee.source.imports and self.r.lookup(base.text,base) is None:
                return callee.source.imports.get(base.text),callee.role('field').text
        if callee.kind=='Ident' and self.r.lookup(callee.text,callee) is None and callee.text in callee.source.selected:
            namespace,name=callee.source.selected[callee.text].split('.',1)
            return callee.source.imports.get(namespace),name
        return None

    def library(self,call):return self.imported(call.role('callee'))

    def approved(self,call):
        library=self.library(call)
        if library not in self.libraries:return False
        package=call.source.import_dir(library[0])
        if library[0]=='thor:format':
            return package==Path(__file__).resolve().parents[1]/'runtime'/'format'
        return package is None # installed native package, not a user override

    def builtin(self,call):
        extrema=call.source.expressions.extrema.plan(call)
        if extrema:return extrema[0]
        callee=call.role('callee')
        if callee.kind=='Directive' and callee.data['name'] in {'assert','config'}:return '#'+callee.data['name']
        if callee.kind=='Ident' and self.r.symbol(callee) is None and callee.text in self.builtins:return callee.text
        return None

    def targets(self,call):
        direct=self.r.symbol(call.role('callee'))
        if direct and direct.declaration.data.get('mutable',False):return []
        plan=self.r.resolve_call(call)
        candidates=[(plan.symbol,plan.signature)] if plan else self.r.procedure_candidates(call.role('callee'))
        result=[]
        for symbol,signature in candidates:
            if symbol.declaration.data.get('mutable',False):return []
            proc=signature.parent
            if proc and proc.kind=='Procedure' and proc.role('body'):result.append(proc)
            else:return []
        return result

    def validator(self,s):
        """Use shared AST field names, and native exact instantiated field types.

        This Odin release cannot unroll struct fields. A count assertion rejects
        external record schemas we cannot inspect instead of trusting them.
        """
        name=s.prefix+'pure_validate';key=s.root,name
        if key in s.expressions.helpers:return name
        names=sorted({n.text for unit in self.r.sources for record in unit.nodes if record.kind=='StructType'
                      for f in record.role('fields').children if f.kind=='Field' for n in f.group('names')})
        s.needs_intrinsics=True;meta=s.prefix+'meta';native=self.module(s)
        parts=[f'when Depth>24 {{#panic("Pure functions do not support recursive reference-containing types")}}',
               f'else when {meta}.type_is_struct(T) {{']
        count='+'.join(f'(1 when {meta}.type_has_field(T,{json.dumps(field)}) else 0)' for field in names) or '0'
        parts.append(f'#assert({meta}.type_struct_field_count(T)==({count}),"Pure record fields could not be fully resolved")')
        raw_shapes={tuple(n.text for f in record.role('fields').children if f.kind=='Field' for n in f.group('names'))
                    for unit in self.r.sources for record in unit.nodes
                    if record.kind=='StructType' and record.data.get('op')=='raw_union'}
        for shape in sorted(raw_shapes):
            matches=' && '.join([f'{meta}.type_struct_field_count(T)=={len(shape)}']+
                                [f'{meta}.type_has_field(T,{json.dumps(name)})' for name in shape])
            parts.append(f'#assert(!({matches}),"Raw-union storage cannot be proved pure")')
        for field in names:
            field=json.dumps(field)
            parts.append(f'when {meta}.type_has_field(T,{field}) {{{name}({meta}.type_field_type(T,{field}),Result,Allow_Owned,Depth+1)}}')
        parts.extend(['}',f'else when {meta}.type_is_array(T)||{meta}.type_is_enumerated_array(T)||{meta}.type_is_slice(T) {{',
                      f'when Result && !Allow_Owned && {meta}.type_is_slice(T) {{#panic("Contextless compile-time procedures cannot return strings/slices; use fn or a contextful proc")}}',
                      f'else {{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Depth+1)}}','}',
                      f'else when {meta}.type_is_union(T) {{',
                      f'{name}_union(T,Result,Allow_Owned,Depth,0)','}',
                      f'else {{{native}.validate(T,Result && !Allow_Owned,Depth)}}'])
        code=f'\n@(private="file")\n{name}::proc "contextless"($T:typeid,$Result:bool,$Allow_Owned:bool,$Depth:int){{\n'+ '\n'.join(parts)+'\n}\n'
        code+=f'\n@(private="file")\n{name}_union::proc "contextless"($T:typeid,$Result:bool,$Allow_Owned:bool,$Depth:int,$Index:int){{\nwhen Index<{meta}.type_union_variant_count(T){{{name}({meta}.type_variant_type_of(T,Index),Result,Allow_Owned,Depth+1);{name}_union(T,Result,Allow_Owned,Depth,Index+1)}}\n}}\n'
        s.expressions.helpers[key]=s.mapping.wrap(code,s.path,0,0,'pure-type-validator')
        return name

    def walk(self,proc):
        def visit(n):
            yield n
            for child in n.children:
                if child.kind!='Procedure':yield from visit(child)
        nodes=list(visit(proc.role('body')))
        for field in proc.role('type').role('params').children:
            default=field.role('default')
            if default:nodes.extend(visit(default))
        return nodes

    def flow(self,n,env):
        if n is None:return Flow()
        if n.kind=='Ident':
            symbol=self.r.symbol(n)
            if symbol and symbol.projection:
                owner,name=symbol.projection
                return self.flow(owner,env).member(name)
            return env.setdefault((symbol.declaration,symbol.name),Flow()) if symbol else Flow()
        if n.kind in {'Paren','Cast','AutoCast','TypeAssertion','OrReturn','OrBranch'}:return self.flow(n.role('expr'),env)
        if n.kind=='FieldValue':return self.flow(n.role('value'),env)
        if n.kind=='Selector':return self.flow(n.role('expr'),env).member(n.role('field').text)
        if n.kind=='Index':return self.flow(n.role('expr'),env).item()
        if n.kind=='Slice':
            base=self.flow(n.role('expr'),env)
            return Flow(base.borrowed,element=base.item())
        if n.kind=='Compound':
            result=Flow()
            typ=self.r.type_of(n)
            if not n.role('type') and n.parent and n.parent.kind=='Declaration' and n.parent.role('type'):
                typ=self.r.type_node(n.parent.role('type'))
            fields=[]
            if typ.kind=='struct' and typ.node:
                for field in typ.node.role('fields').children:
                    if field.kind=='Field':fields.extend(name.text for name in field.group('names'))
            for i,elem in enumerate(n.group('elems')):
                if elem.kind=='FieldValue':result.fields[elem.role('field').text]=self.flow(elem.role('value'),env)
                elif i<len(fields):result.fields[fields[i]]=self.flow(elem,env)
                else:
                    if result.element is None:result.element=Flow()
                    result.element.merge(self.flow(elem,env))
            return result
        if n.kind in {'Ternary','WhenExpr','OrElse'}:
            result=Flow();result.merge(self.flow(n.role('x'),env));result.merge(self.flow(n.role('y'),env));return result
        if n.kind in {'SwitchExpr','CompileSwitchExpr'}:
            result=Flow()
            for case in n.data['cases']:result.merge(self.flow(n.source.at(case['result'],n),env))
            return result
        if n.kind=='Call':
            args=n.group('args')
            if n.source.denotes_type(n.role('callee')):
                return self.flow(args[0],env) if args else Flow()
            if self.library(n)==('core:slice','clone'):
                return Flow(element=self.flow(args[0],env).item()) if args else Flow()
            return Flow() # checked callees promote independent result storage
        return Flow()

    def copied(self,value,n):
        typ=self.r.type_of(n)
        if typ.kind=='array':return Flow(element=value.item())
        if typ.kind=='struct':
            result=Flow()
            fields=typ.node.role('fields') if typ.node else None
            for field in fields.children if fields else []:
                if field.kind=='Field':
                    for name in field.group('names'):result.fields[name.text]=value.member(name.text)
            return result
        return value

    def explicit_allocator(self,n):
        args=n.group('args');library=self.library(n)
        if any(a.kind=='FieldValue' and a.role('field').text=='allocator' for a in args):return True
        positional=[a for a in args if a.kind!='FieldValue']
        if library in {('core:strings','clone'),('core:slice','clone'),('thor:format','group_integer')}:
            return len(positional)>1
        if library and library[0]=='thor:format':
            limit=5 if library[1]=='format_options' else 4
            return len(positional)>limit
        return False

    def borrowed_write(self,lhs,env):
        conditions=[]
        def visit(n):
            if n.kind=='Paren':visit(n.role('expr'))
            elif n.kind in {'Selector','Index','Slice','Deref'}:
                base=n.role('expr');visit(base)
                if self.flow(base,env).borrowed and n.kind in {'Index','Slice','Deref'}:
                    kind=self.r.type_of(base).kind
                    if kind in {'slice','string','dynamic','map','pointer'}:conditions.append('true')
                    elif kind in {'generic','unknown'}:
                        # The predicate is rendered at the write site so helper
                        # captures and native generic type queries remain valid.
                        conditions.append(base)
        visit(lhs)
        return conditions

    def unsafe_storage(self,typ,seen=None):
        """Local annotations must not introduce implicit any/pointer boxing.

        Inferred generics originate in natively validated parameters/types.
        Fresh local dynamic buffers are allowed, but maps/sets and reference
        representations cannot provide a back door around the call whitelist.
        """
        seen=set() if seen is None else seen
        key=typ.kind,typ.node
        if key in seen:return False
        seen.add(key)
        if typ.kind in {'pointer','procedure','map','set'} or typ.name in {'any','typeid','rawptr','cstring'}:return True
        if typ.elem and self.unsafe_storage(typ.elem,seen):return True
        if typ.kind=='struct' and typ.node:
            if typ.node.data.get('op')=='raw_union':return True
            for field in typ.node.role('fields').children:
                if field.kind=='Field' and self.unsafe_storage(self.r.type_node(field.role('type'),typ.substitutions),seen):return True
        if typ.kind=='union' and typ.node:
            return any(self.unsafe_storage(self.r.type_node(v,typ.substitutions),seen) for v in typ.node.group('variants'))
        return False

    def source_location_dependent(self,n,seen=None):
        if n is None:return False
        if n.kind in {'Procedure','ProcedureGroup'}:return False
        seen=set() if seen is None else seen
        if n in seen:return False
        seen.add(n)
        if n.kind=='Directive' and n.data['name'] in {'file','directory','line'}:return True
        symbol=self.r.symbol(n) if n.kind in {'Ident','Selector'} else None
        if symbol and symbol.declaration.kind=='Declaration' and not symbol.declaration.data.get('mutable'):
            if self.source_location_dependent(symbol.value,seen):return True
        return any(self.source_location_dependent(child,seen) for child in n.children)

    def analyze(self,proc):
        if proc in self.analyzed:return
        self.analyzed.add(proc)
        nodes=self.walk(proc);env={}
        for field in proc.role('type').role('params').children:
            if field.kind=='Field':
                if field.data.get('using_field') and self.r.type_node(field.role('type')).kind in {'generic','unknown'}:
                    self.contracts[proc]=(field,'Pure using parameters require a resolved record schema; access members explicitly for unconstrained generic inputs.')
                for name in field.group('names'):
                    symbol=self.r.by_decl.get((field,name.data.get('name') or name.text.lstrip('$')))
                    if symbol:env[symbol.declaration,symbol.name]=Flow(True)
        # Conservative joins reach aliases carried through branches and loops.
        for _ in range(max(2,len(nodes)+1)):
            changed=False
            for n in nodes:
                if n.kind=='Declaration':names,values=n.group('names'),n.group('values')
                elif n.kind=='Assignment' and n.data['op']=='=':names,values=n.group('lhs'),n.group('rhs')
                else:names,values=[],[]
                for lhs,rhs in zip(names,values):
                    if lhs.text=='_':continue
                    value=self.copied(self.flow(rhs,env),rhs)
                    if n.kind=='Declaration':
                        key=n,lhs.data.get('name') or lhs.text
                        if key not in env:env[key]=value;changed=True
                        else:changed |= env[key].merge(value)
                    else:changed |= self.flow(lhs,env).merge(value)
                if n.kind=='Call' and self.builtin(n)=='copy' and len(n.group('args'))>=2:
                    dst,src=n.group('args')[:2]
                    changed |= self.flow(dst,env).item().merge(self.flow(src,env).item())
                if n.kind=='Range' and n.group('names'):
                    name=n.group('names')[0]
                    key=n,name.data.get('name') or name.text.lstrip('&')
                    value=self.flow(n.role('expr'),env).item()
                    if key not in env:env[key]=value;changed=True
                    else:changed |= env[key].merge(value)
                if n.kind=='TypeSwitch' and n.group('names'):
                    name=n.group('names')[0]
                    key=n,name.data.get('name') or name.text
                    value=self.flow(n.role('expr'),env)
                    if key not in env:env[key]=value;changed=True
                    else:changed |= env[key].merge(value)
            if not changed:break
        cc=proc.role('type').data.get('calling_convention','')
        if cc not in {'','odin'} and not(cc=='contextless' and not self.pure(proc)):
            self.problem(proc,'Pure functions require the Odin calling convention; eligible ordinary procedures may also be contextless.')
        declaration=proc.parent
        if declaration and declaration.kind=='Declaration':
            for attribute in declaration.group('attributes'):
                names=[elem.role('field').text if elem.kind=='FieldValue' else elem.text for elem in attribute.group('elems')]
                if any(name in {'init','fini'} for name in names):
                    self.problem(proc,'Package initializers/finalizers are not eligible pure call targets.')
                if any(name.startswith('deferred_') for name in names):
                    self.contracts[proc]=(attribute,'Pure calls cannot use implicit deferred procedure callbacks; make eligible calls explicit in the body.')
        for n in nodes:
            typ=self.r.type_node(n.role('type')) if n.kind=='Declaration' and n.role('type') else self.r.type_of(n) if n.kind=='Compound' else None
            if typ and typ.kind=='struct' and typ.node and typ.node.data.get('op')=='raw_union':
                self.problem(n,'Raw-union storage cannot be proved pure.')
            if n.kind=='Compound' or n.kind=='Declaration' and (n.data.get('mutable') or n.data.get('immutable')):
                local_types=[typ] if typ else [self.r.type_of(value) for value in n.group('values')]
                if any(t and self.unsafe_storage(t) for t in local_types):
                    self.problem(n,'Pure local storage cannot contain pointers, any, procedure values, maps, sets or raw unions.')
            if n.kind=='Using' or n.kind=='Declaration' and n.data.get('using_field'):
                owners=n.group('values' if n.kind=='Using' else 'names')
                if any(self.r.type_of(owner).kind in {'generic','unknown'} for owner in owners):
                    self.problem(n,'Pure using requires a resolved record schema; unverified namespaces and unconstrained generic projections are not eligible.')
            if n.kind=='Selector' or n.kind=='Ident' and self.reference(n):
                if any(a.kind=='Call' and self.builtin(a) in self.type_queries and a.role('callee') is not n for a in n.ancestors() if a is not proc):continue
                symbol=self.r.symbol(n)
                if symbol and symbol.declaration.kind=='Declaration' and self.source_location_dependent(symbol.value):
                    self.problem(n,'Source-location-dependent constants are not eligible for pure execution yet; pass ordinary data explicitly.')
                if symbol and not(proc.start<=symbol.declaration.start<proc.end and symbol.declaration.source is proc.source):
                    if symbol.declaration.data.get('mutable',False) or symbol.immutable:
                        self.problem(n,f"Pure code cannot read or write runtime storage '{symbol.name}' outside the function.")
                imported=self.imported(n)
                if imported and n.source.import_dir(imported[0]) is None and imported not in self.native_constants:
                    call=n.parent
                    if not(call and call.kind=='Call' and call.role('callee') is n and
                           (self.approved(call) or call.source.expressions.extrema.plan(call))):
                        self.problem(n,'Pure code cannot read unverified native package storage or values; only approved constants/helpers are eligible.')
            if n.kind=='Implicit':self.problem(n,'Pure code cannot access the ambient context or implicit runtime state.')
            if n.kind=='Unsafe':self.problem(n,'Assembly, uninitialized storage and unsafe operations are not permitted in pure code.')
            if n.kind=='OrReturn' and self.wraps(proc):
                results=self.r.fields(proc.role('type').role('results'))
                if len(results)>1 and any(not name for name,_ in results):
                    self.problem(n,'or_return in a procedure with multiple results requires named return values.')
            if n.kind=='Cast' and n.data['op']=='transmute':self.problem(n,'Pure code cannot transmute storage or observe pointer representations.')
            if n.kind=='Directive' and n.data['name'] not in {'assert','config','type','file','line','directory','procedure'}:
                self.problem(n,'This directive is not permitted in pure code.')
            if n.kind=='Directive' and n.data['name'] in {'file','line','directory'}:
                self.problem(n,'Source-location-dependent constants are not eligible for pure execution yet; pass ordinary data explicitly.')
            if n.kind=='Unary' and n.data['op']=='&':
                # A range binding is a declaration, so replacing just `&value`
                # with an error token silently declares that token instead.
                # Diagnose the statement, keeping its body from cascading.
                binding=n.parent and n.parent.kind=='Range' and n in n.parent.group('names')
                self.problem(n.parent if binding else n,
                    'Pure code cannot iterate by mutable reference; use indexed writes to a fresh local buffer.'
                    if binding else 'Pure code cannot take or expose raw addresses.')
            if n.kind=='Call':
                builtin=self.builtin(n);library=self.library(n)
                if builtin=='make':
                    args=n.group('args')
                    if not args or n.source.resolver.type_node(args[0]).kind not in {'slice','dynamic'}:
                        self.problem(n,'Pure allocation supports fresh slice/dynamic buffers only.')
                    elif len(args)>(3 if n.source.resolver.type_node(args[0]).kind=='dynamic' else 2) or any(a.kind=='FieldValue' and a.role('field').text=='allocator' for a in args):
                        self.problem(n,'Pure code cannot select an allocator or invoke allocator callbacks.')
                elif builtin in {'copy','delete'}:
                    if n.group('args'):
                        target=n.group('args')[0]
                        borrowed=self.flow(target,env).borrowed
                        if borrowed:self.problem(n,'Pure code cannot mutate or delete storage borrowed from an input.')
                        if builtin=='delete' and len(n.group('args'))>1:self.problem(n,'Pure code cannot select an allocator.')
                elif builtin or n.source.denotes_type(n.role('callee')):pass
                elif self.approved(n):
                    if self.explicit_allocator(n):
                        self.problem(n,'Pure code cannot select an allocator.')
                else:
                    targets=self.targets(n)
                    if not targets:self.problem(n,'Pure code requires a resolved direct call to an eligible procedure; indirect, foreign and unknown calls are forbidden.')
                    elif self.pure(proc) and any(not self.pure(p) for p in targets):
                        self.problem(n,'A declared fn may call only declared fn targets or approved deterministic built-ins/helpers.')
            if n.kind=='Assignment':
                for lhs in n.group('lhs'):
                    checks=self.borrowed_write(lhs,env)
                    if checks:self.problem(n,'Pure code cannot mutate storage borrowed from an input.',checks)
            if n.kind=='Cast' or n.kind=='Call' and n.source.denotes_type(n.role('callee')):
                target=n.role('type') if n.kind=='Cast' else n.role('callee')
                typ=self.r.type_node(target)
                if typ.kind in {'pointer','procedure'} or typ.name in {'rawptr','cstring','any','typeid'}:
                    self.problem(n,'Pure code cannot convert values to pointers, procedure values, any or runtime type identities.')

    def predicate(self,checks,source):
        pieces=[]
        for check in checks:
            if isinstance(check,str):pieces.append(check)
            else:
                source.needs_intrinsics=True
                t=f'type_of({source.render(check)})';meta=source.prefix+'meta'
                pieces.append('('+' || '.join(f'{meta}.type_is_{kind}({t})' for kind in ['slice','string','dynamic_array','map','pointer','multi_pointer'])+')')
        return ' || '.join(pieces) or 'false'

    def render(self,n,expected,constant):
        proc=n if n.kind=='Procedure' else self.owner(n)
        if n.kind=='Run':
            return n.source.expressions.error(n,'#run requires compile-time-known arguments and types at this source location.')
        if not self.checked(proc):return None
        self.analyze(proc)
        if n in self.rendering:return None
        if n is proc.role('body') and proc in self.contracts:
            site,message=self.contracts[proc]
            return n.source.mapping.wrap('{#panic('+json.dumps(message)+')}',site.source.path,site.start,site.end,'pure-contract')
        if n.kind=='Directive' and n.data['name']=='procedure' and proc in self.inner_bodies:
            return n.source.prefix+'procedure_name_'+str(proc.start)
        if (n.kind=='Ident' and n.text=='_' and n.parent and n.parent.kind=='Field'
                and n.parent.parent is proc.role('type').role('params') and n in n.parent.group('names')):
            return self.parameter_name(n)
        message=self.problems.get(n)
        if message:
            if n.kind=='Range':
                return '#panic('+json.dumps(message)+')\n'
            if n.kind=='Declaration' and not(n.parent and n.parent.role('init') is n):
                added=set(descendants(n))-self.rendering
                self.rendering.update(added)
                try:code=n.source._render(n,expected,constant)
                finally:self.rendering.difference_update(added)
                return code+'\n#panic('+json.dumps(message)+')\n'
            return n.source.expressions.error(n,message)
        if n.kind=='Return' and proc in self.inner_bodies:
            s=n.source;results=self.r.fields(proc.role('type').role('results'))
            named=self.result_names(proc)
            values_=n.group('values')
            rhs=','.join(s.render(value,expected=s.type_hint(typ) or Hint(s,typ))
                         for value,(_,typ) in zip(values_,results)) if values_ else ','.join(named)
            # Preserve native arity errors, including a single multi-result call.
            if len(values_)>len(results):rhs+=','+','.join(s.render(v) for v in values_[len(results):])
            assignment=','.join(named)+'='+rhs+';' if values_ else ''
            # The native return is ignored. A final defer promotes the named
            # results while locals live. Explicit casts acknowledge those
            # intentionally temporary stack views to Odin's escape checker.
            code='{'+assignment+'return '+','.join(f'transmute(type_of({name})){name}' for name in named)+'}'
            return s.mapping.wrap(code,s.path,n.start,n.end,'pure-return')
        if n is proc.role('body') and proc not in self.inner_bodies and self.wraps(proc):
            return self.wrapper(proc,n)
        if n.kind=='Call' and self.approved(n) and self.library(n)[0]=='thor:format' and n not in self.rendering:
            self.rendering.add(n)
            try:
                source=n.source
                args=n.group('args')
                value=next((a.role('value') for a in args if a.kind=='FieldValue' and a.role('field').text=='value'),args[0] if args else None)
                if value:
                    def body(_):
                        return f'{self.module(source)}.validate_format(type_of({source.render(value)}))\nreturn '+source._render(n,expected,constant)
                    name,args=source.expressions.closed(n,body,result='string',flavor='pure_format')
                    return f'{name}({", ".join(args)})'
            finally:self.rendering.remove(n)
        conditions=self.conditions.get(n)
        if conditions:
            self.rendering.add(n)
            try:
                def body(_):
                    code=n.source._render(n,expected,constant)
                    for checks,message in conditions:
                        predicate=self.predicate(checks,n.source)
                        code=f'when {predicate} {{#panic({json.dumps(message)})}} else {{\n{code}\n}}'
                    return code
                post=n.parent and n.parent.kind=='For' and n.parent.role('post') is n
                if post:
                    name,args=n.source.expressions.closed(n,lambda _:body(None)+'\nreturn true',result='bool',flavor='pure_write')
                    return f'_ = {name}({", ".join(args)})'
                return body(None)
            finally:self.rendering.remove(n)
        return None

    @staticmethod
    def parameter_name(name):
        return name.source.prefix+'unused_'+str(name.start) if name.text=='_' else name.text.lstrip('$')

    def owned_type(self,typ,seen=None):
        seen=set() if seen is None else seen
        key=typ.kind,typ.node
        if key in seen:return True
        seen.add(key)
        if typ.kind in {'string','slice','generic','unknown'}:return True
        if typ.elem:return self.owned_type(typ.elem,seen)
        if typ.kind=='struct' and typ.node:
            return any(self.owned_type(self.r.type_node(f.role('type'),typ.substitutions),seen.copy())
                       for f in typ.node.role('fields').children if f.kind=='Field')
        if typ.kind=='union' and typ.node:
            return any(self.owned_type(self.r.type_node(v,typ.substitutions),seen.copy()) for v in typ.node.group('variants'))
        return False

    def wraps(self,proc):
        return proc.role('type').data.get('calling_convention')!='contextless' and any(
            self.owned_type(self.r.type_node(typ)) for _,typ in self.r.fields(proc.role('type').role('results')) if typ)

    def result_names(self,proc):
        return [name if name and name!='_' else proc.source.prefix+'result_'+str(proc.start)+'_'+str(i)
                for i,(name,_) in enumerate(self.r.fields(proc.role('type').role('results')))]

    def wrapper(self,proc,block):
        s=block.source;prefix=s.prefix+'scratch_'+str(proc.start)
        signature=proc.role('type');params=signature.role('params')
        name=s.prefix+'body_'+str(proc.start)
        hidden=s.prefix+'procedure_name_'+str(proc.start)
        results=self.r.fields(signature.role('results'))
        outputs=[s.prefix+'output_'+str(proc.start)+'_'+str(i) for i in range(len(results))]
        extra=','.join(f'{slot}:^${slot}_T' for slot in outputs)+','+hidden+':string,'+prefix+'_caller:$'+prefix+'_Allocator'
        parameter_code=s.splice(params,[(c.start,c.end,s.render(c)) for c in params.children],
                                [(params.start,extra+(',' if self.r.fields(params) else ''))])
        returns='->('+','.join(name+':'+s.render(typ,constant=True) for name,(_,typ) in zip(self.result_names(proc),results))+')'
        header=s.splice(signature,[(params.start,params.end,parameter_code),(params.end+1,signature.end,returns)],
                        [(params.start,extra)] if params.start==params.end else [])
        clauses=proc.group('where')
        if clauses:header+=' where '+', '.join(s.render(c,constant=True) for c in clauses)
        self.inner_bodies.add(proc)
        try:body=s._render(block)
        finally:self.inner_bodies.remove(proc)
        names=[s.prefix+'returned_'+str(proc.start)+'_'+str(i) for i in range(len(results))]
        args=['&'+value for value in names]+['#procedure',prefix+'_caller']
        for field in params.children:
            if field.kind!='Field':continue
            for param in field.group('names'):
                arg=self.parameter_name(param)
                if field.data.get('variadic'):arg='..'+arg
                args.append(arg)
        code=self.prologue(block)+f'\n{name}::{header} {body}\n'
        code+='\n'.join(f'{value}:{s.render(typ,constant=True)}' for value,(_,typ) in zip(names,results))+'\n'
        code+=f'{name}('+','.join(args)+')\n'
        code+='return '+','.join(names)+'\n'
        return '{'+code+'}'

    def prologue(self,block):
        proc=block.parent
        if not(proc and proc.kind=='Procedure' and proc.role('body') is block and self.checked(proc)):return ''
        if proc in self.inner_bodies:
            s=block.source;prefix=s.prefix+'scratch_'+str(proc.start)
            outputs=[]
            for i,name in enumerate(self.result_names(proc)):
                slot=s.prefix+'output_'+str(proc.start)+'_'+str(i)+'^'
                outputs.append(slot+'='+self.module(s)+'.clone('+name+','+prefix+'_caller)')
            # First installed defer runs last, while local arrays still live.
            return '\ndefer {'+';'.join(outputs)+'}\n'
        self.analyze(proc)
        s=block.source;module=self.module(s);prefix=s.prefix+'scratch_'+str(proc.start)
        code=[]
        for field in proc.role('type').role('params').children:
            if field.kind!='Field':continue
            for name in field.group('names'):
                if name.kind=='PolyType':
                    annotation=field.role('type')
                    if annotation and (annotation.kind=='TypeidType' or annotation.text=='typeid'):
                        code.append(f'{self.validator(s)}({name.text.lstrip("$")},false,true,0)')
                    continue
                code.append(f'{self.validator(s)}(type_of({self.parameter_name(name)}),false,true,0)')
        for _,typ in self.r.fields(proc.role('type').role('results')):
            if typ:code.append(f'{self.validator(s)}({self.r.template(typ)},true,{str(proc.role("type").data.get("calling_convention")!="contextless").lower()},0)')
        if proc.role('type').data.get('calling_convention')=='contextless':
            return s.mapping.wrap('\n'+'\n'.join(code)+'\n',s.path,proc.start,proc.role('type').end,'pure-contract')
        if self.wraps(proc):code.append(f'{prefix}_caller:=context.allocator')
        code += [f'{prefix}: {module}.Scratch',f'{module}.scratch_init(&{prefix})',
                 f'defer {module}.scratch_destroy(&{prefix})',
                 f'context.allocator = {module}.scratch_allocator(&{prefix})',
                 'context.temp_allocator = context.allocator']
        return s.mapping.wrap('\n'+'\n'.join(code)+'\n',s.path,proc.start,proc.role('type').end,'pure-contract')

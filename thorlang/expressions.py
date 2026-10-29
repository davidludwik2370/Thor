"""Expression-local lowering: explicit lexical inputs, lazy calls, typed results.

Odin has no capturing closures. Named generated procedures take explicit references
to runtime locals and a result type supplied at the original expression site.
Their calls stay inside the original branch/argument/loop; declarations do no work.
"""
from __future__ import annotations

import json
import re


class Expressions:
    def __init__(self, source):
        self.source = source
        self.helpers = {}
        self.captures = []
        self.modules = {}
        self.addresses = set()
        self.native_access = set()
        self.assigning = set()
        from .ownership import Ownership
        self.ownership = Ownership(self)
        from .extrema import Extrema
        self.extrema = Extrema(self)

    def module(self, name, path=None):
        alias = self.source.prefix + name
        self.modules[alias] = path or 'thor:' + name
        return alias

    def imports(self):
        return ''.join(self.source.mapping.wrap(f'\nimport {alias} {json.dumps(self.source.output_import(path))}\n',self.source.path,0,0,'support') for alias,path in self.modules.items())

    def declarations(self, scope):
        return ''.join(code for (owner,_),code in list(self.helpers.items()) if owner is scope)

    def convert_do(self, block):
        s=self.source
        if block in s.converted_do_blocks:
            return
        tokens=[span for span in s.info.get('do_tokens') or [] if block.parent.start <= span[0] < block.start]
        if tokens:
            a,b=tokens[-1]
            s.edits.append((a,b,''))
            s.converted_do_blocks.add(block)

    @staticmethod
    def symbol_key(symbol):
        return symbol.declaration,symbol.name

    def variable(self, node):
        s=self.source
        if node.kind!='Ident' or not s.resolver:
            return None
        parent=node.parent
        if parent and (parent.kind in {'Selector','FieldValue'} and parent.role('field') is node
                       or parent.kind in {'Declaration','Field','Range'} and node in parent.group('names')
                       or parent.kind in {'PolyType','ImplicitSelector'}):
            return None
        symbol=s.resolver.symbol(node)
        if not symbol or symbol.scope.kind=='File':
            return None
        decl=symbol.declaration
        if decl.kind=='Field' and any(name.kind=='PolyType' for name in decl.group('names')):
            return None # Native constant parameters remain lexical constants.
        if decl.kind=='Declaration' and not decl.data.get('mutable',True):
            return None
        if decl.kind=='PolyType' or symbol.annotation and symbol.annotation.kind=='TypeidType':
            return None
        return symbol

    def closed(self,node,builder,result='string',pointer=False,flavor='value',optional_ok=False):
        from .compiler import Hint
        from .semantics import descendants
        s=self.source
        name=s.prefix+f'{flavor}_{node.start}_{node.end}'
        scope=next((a for a in node.ancestors() if a.kind=='Case' or a.kind=='Block' and (not a.parent or a.parent.kind not in {'Switch','TypeSwitch','CompileSwitch'})),s.root)
        key=scope,name
        variables={}
        for child in descendants(node):
            symbol=self.variable(child)
            if symbol and not (node.start <= symbol.declaration.start < node.end):
                variables.setdefault(self.symbol_key(symbol),(symbol,child))
        # Evaluate addresses only at the call site. User expressions, including
        # all side effects, remain in the helper body in their source order.
        refs=[self.reference_capture(symbol) for symbol,_ in variables.values()]
        params=[f'{s.prefix}capture_{i}: {"^" if refs[i] else ""}${s.prefix}Capture_{i}' for i in range(len(variables))]
        args=[f'&({s.render(child)})' if refs[i] else s.render(child) for i,(_,child) in enumerate(variables.values())]
        args=[s.mapping.wrap(arg,s.path,node.start,node.end,'debug-capture') for arg in args]
        aliases={k:f'({s.prefix}capture_{i}{"^" if refs[i] else ""})' for i,k in enumerate(variables)}
        if result is False:
            result_type=None
        elif result is None:
            params.append(f'${s.prefix}Result: typeid')
            result_type=('^' if pointer else '')+s.prefix+'Result'
        else:
            result_type=result
        # Even helpers without captures must be instantiated lazily: a call
        # inside an inactive native when expression must leave its body unchecked.
        if not params:
            params.append(f'${s.prefix}Site: bool')
            args.append('true')
        if key not in self.helpers:
            self.helpers[key]='' # Nested helpers may refer back through type queries.
            self.captures.append(aliases)
            try:
                body=builder(Hint(s,node,s.prefix+'Result') if result is None else None)
            finally:
                self.captures.pop()
            convention=' "odin"' if self.has_context(node) else ' "contextless"'
            returns=(' -> (value: '+result_type+', ok: bool) #optional_ok' if optional_ok else ' -> '+result_type) if result_type else ''
            privacy='@(private="file")\n' if scope is s.root else ''
            self.helpers[key]=s.mapping.wrap(f'\n{privacy}{name} :: proc{convention}({", ".join(params)}){returns} {{\n{body}\n}}\n',
                                             s.path,node.start,node.end,'expression-helper')
        return name,args

    def has_context(self,node):
        """Use parsed conventions and lexical context initialization, not text.

        Helpers use Odin/contextless ABI even inside foreign export blocks;
        generic helper procedures cannot use a C ABI.
        """
        owner=next((a for a in node.ancestors() if a.kind=='Procedure'),None)
        if owner is None: return True
        cc=owner.role('type').data.get('calling_convention','')
        if cc=='foreign':
            foreign=next((a for a in owner.ancestors() if a.kind=='ForeignBlock'),None)
            cc=foreign.data.get('calling_convention','c') if foreign else 'c'
        if cc in {'','odin'}: return True
        scopes={a for a in node.ancestors() if a.kind in {'Block','Case','Procedure'}}
        for other in self.source.nodes:
            if other.kind=='Assignment' and other.start<node.start and any(lhs.text=='context' for lhs in other.group('lhs')):
                scope=next((a for a in other.ancestors() if a.kind in {'Block','Case','Procedure'}),None)
                if scope in scopes: return True
        return False

    def validation(self,node):
        """Ask Odin to check constant case matching without checking result arms.

        Nonconstant/unknown conditions stay solely in the lazy when chain. This
        keeps skipped conditions unchecked while native constant folding catches
        duplicate aliases, conversions, strings, ranges and #config values.
        """
        s=self.source
        def constant(n,seen=None):
            seen=set() if seen is None else seen
            if n in seen: return False
            seen.add(n)
            if n.kind in {'Literal','ImplicitSelector'} or n.text in {'true','false'}: return True
            if s.denotes_type(n): return True
            symbol=s.resolver.symbol(n)
            if symbol:
                if node in symbol.declaration.ancestors(): return False
                if symbol.declaration.kind=='PolyType': return True
                if symbol.value and not symbol.declaration.data.get('mutable',True): return constant(symbol.value,seen)
            if n.kind in {'Paren','Unary'}: return constant(n.role('expr'),seen)
            if n.kind=='Binary': return constant(n.role('left'),seen.copy()) and constant(n.role('right'),seen.copy())
            if n.kind=='Selector' and s.resolver.type_node(n.role('expr')).kind=='enum': return True
            if n.kind=='Call':
                callee=n.role('callee')
                if callee.text.lstrip('#')=='config':
                    return all(constant(a,seen.copy()) for a in n.group('args')[1:])
                builtins={'#config','len','size_of','align_of','type_of','max','min','abs','clamp'}
                return (callee.text in builtins or s.denotes_type(callee)) and all(constant(a,seen.copy()) for a in n.group('args'))
            return False
        matches=[]
        for case in node.data['cases']:
            for span in case['matches']:
                match=s.at(span,node)
                if constant(match): matches.append(s.render(match,constant=True))
        if not matches: return 'true'
        name=s.prefix+f'validate_switch_{node.start}_{node.end}'
        scope=next((a for a in node.ancestors() if a.kind=='Case' or a.kind=='Block' and (not a.parent or a.parent.kind not in {'Switch','TypeSwitch','CompileSwitch'})),s.root)
        parameter=s.prefix+'selector'
        cases='\n'.join('case '+m+':' for m in matches)
        privacy='@(private="file")\n' if scope is s.root else ''
        code=f'\n{privacy}{name} :: proc "contextless" (${parameter}: ${s.prefix}Selector) -> bool {{\nswitch {parameter} {{\n{cases}\ncase:\n}}\nreturn true\n}}\n'
        self.helpers[scope,name]=s.mapping.wrap(code,s.path,node.start,node.end,'switch-validation')
        selector=node.role('selector')
        value=s.render(selector,constant=True) if selector else 'true'
        return f'(type_of({name}({value})) == bool)'

    def error(self,node,message):
        s=self.source
        token=s.prefix+f'expression_error_{node.start}'
        s.error_messages[token]=message
        return token

    def type_code(self,hint):
        if hint.expression:
            self.source.needs_intrinsics=True
        return hint.expression or hint.source.render(hint.node)

    @staticmethod
    def reference_capture(symbol):
        if symbol.projection: return True
        if symbol.declaration.kind=='Field': return False
        if symbol.declaration.kind=='Range': return symbol.node.kind=='Unary' and symbol.node.data['op']=='&'
        return True

    def render(self,n,expected,constant):
        s=self.source
        if n.kind=='Ident':
            symbol=self.variable(n)
            if symbol:
                key=self.symbol_key(symbol)
                for aliases in reversed(self.captures):
                    if key in aliases:
                        return aliases[key]
        if not s.thor:
            return None
        if n.kind=='Binary' and n.data['op']=='**':
            return self.power(n,constant)
        if n.kind == 'Call':
            code = self.extrema.render(n, constant)
            if code is not None: return code
        code=self.ownership.discard(n)
        if code is not None: return code
        if n.kind=='Unary' and n.data['op']=='&' and not self.ownership.querying:
            expr=n.role('expr')
            base=expr.role('expr') if expr.kind=='Index' else expr
            if self.ownership.may_own(base) or self.ownership.borrows_temporary(base):
                return self.error(n,'An address into an anonymous allocation needs a named owner; bind the allocation before taking its address.')
        if n.kind=='Interpolation':
            return self.interpolation(n)
        if n.kind in {'SwitchExpr','CompileSwitchExpr','CompileSwitch'}:
            return self.switch(n,expected,constant)
        if n.kind=='SetType':
            return self.module('sets')+'.Set('+s.render(n.group('args')[0],constant=True)+')'
        if n.kind in {'Index','Slice'}:
            if n in self.native_access: return None
            if n.kind=='Index':
                code=self.ownership.index(n)
                if code is not None: return code
            elif not self.ownership.querying and self.ownership.borrows_temporary(n):
                return self.error(n,'A view into an anonymous allocation needs a named owner; bind the allocation before slicing it.')
            return self.access(n,constant)
        if n.kind=='Selector' and not self.ownership.querying and self.ownership.borrows_temporary(n):
            return self.error(n,'A view into an anonymous allocation needs a named owner; bind the allocation before accessing its fields.')
        if n.kind=='Assignment' and n not in self.assigning:
            code=self.assignment(n,constant)
            if code is not None: return code
        if n.kind=='Call' and not self.ownership.querying:
            callee=n.role('callee')
            if callee.kind=='Ident' and callee.text=='raw_data' and s.resolver.symbol(callee) is None:
                if any(self.ownership.may_own(arg) or self.ownership.borrows_temporary(arg) for arg in n.group('args')):
                    return self.error(n,'A pointer into an anonymous allocation needs a named owner; bind the allocation before taking raw_data.')
        if n.kind=='Binary' and n.data['op'] in {'in','not_in'}:
            interval=n.role('right')
            while interval.kind=='Paren':interval=interval.role('expr')
            if interval.kind=='Binary' and interval.data['op'] in {'..','..<','..='}:
                return self.range_membership(n,interval,constant)
        if n.kind=='Binary' and not constant:
            if n.data['op'] in {'in','not_in'}:
                code=self.ownership.membership(n)
            else: code=self.ownership.binary(n,expected,constant)
            if code is not None: return code
        if n.kind=='Range':
            code=self.ownership.iterate(n)
            if code is not None: return code
        set_code=self.sets(n,expected,constant)
        if set_code is not None:
            return set_code
        return None

    def assignment(self,n,constant):
        from .semantics import descendants
        s=self.source
        indices=[child for lhs in n.group('lhs') for child in descendants(lhs)
                 if child.kind=='Index' and self.shape(child.role('expr')).kind in {'generic','unknown'}]
        if not indices or any(child in s.blocked for lhs in n.group('lhs') for child in descendants(lhs)):
            return None
        post=n.parent and n.parent.kind=='For' and n.parent.role('post') is n
        def body(_):
            self.assigning.add(n)
            def branch(i):
                if i==len(indices): return s._render(n,constant=constant)
                index=indices[i]
                original=s.render(index.role('expr'))
                sequence=branch(i+1)
                self.native_access.add(index)
                try: native=branch(i+1)
                finally: self.native_access.remove(index)
                return f'when {self.sequence_trait(original)} {{\n{sequence}\n}} else {{\n{native}\n}}'
            try: return branch(0)+('\nreturn true' if post else '')
            finally: self.assigning.remove(n)
        name,args=self.closed(n,body,result='bool' if post else False,flavor='assignment')
        # Odin requires a post assignment/increment, not a bare call. A blank
        # assignment keeps this expression-local operation in that slot.
        return ('_ = ' if post else '')+f'{name}({", ".join(args)})'

    def address(self,node):
        self.addresses.add(node)
        try:
            return self.source.render(node)
        finally:
            self.addresses.remove(node)

    def addressable(self,node):
        if node.kind=='Paren': return self.addressable(node.role('expr'))
        if node.kind in {'Ident','Selector'}:
            symbol=self.source.resolver.symbol(node)
            if symbol:
                return bool(self.reference_capture(symbol) and (symbol.declaration.kind!='Declaration' or symbol.declaration.data.get('mutable',True)))
        if node.kind=='Ident': return False
        if node.kind=='Deref': return True
        if node.kind not in {'Selector','Index'}: return False
        base=node.role('expr')
        typ=self.shape(base)
        if typ.kind=='pointer' or node.kind=='Index' and typ.kind in {'slice','dynamic'}:
            return True
        inherited=self.addressable(base)
        if typ.kind not in {'generic','unknown'}:
            return inherited if node.kind=='Selector' or typ.kind=='array' else False
        # A generic record may be a pointer, while a generic index base may be
        # an array or a reference container. Let Odin select addressability at
        # instantiation; copying readonly value parameters is valid for reads.
        s=self.source
        s.needs_intrinsics=True
        s.needs_default=True
        meta=s.prefix+'meta'
        t=f'type_of({s.prefix}default_value({s.render(base)}))'
        if node.kind=='Selector':
            if inherited is True:return True
            pointer=f'{meta}.type_is_pointer({t})'
            return f'({pointer} || ({inherited}))' if inherited else pointer
        references=' || '.join(f'{meta}.{test}({t})' for test in
            ['type_is_slice','type_is_dynamic_array','type_is_fixed_capacity_dynamic_array','type_is_pointer','type_is_multi_pointer'])
        if inherited:
            array=f'{meta}.type_is_array({t})'
            references+=' || '+(array if inherited is True else f'({array} && ({inherited}))')
        return '('+references+')'

    def writes(self,node):
        if node in self.addresses: return True
        for parent in node.ancestors():
            if parent.kind=='Assignment':
                return any(lhs.start<=node.start and node.end<=lhs.end for lhs in parent.group('lhs'))
            if parent.kind=='Unary' and parent.data['op']=='&': return True
            if parent.kind=='Range':
                return any(name.kind=='Unary' and name.data.get('op')=='&' for name in parent.group('names'))
            if parent.kind=='Slice': return True
            if parent.kind not in {'Paren','Selector','Index','Deref'}: return False
        return False

    def sequence_type(self,node):
        typ=self.shape(node)
        if typ.kind=='array' and typ.node and typ.node.kind=='ArrayType':
            length=typ.node.role('len')
            if length and typ.node.source.denotes_type(length): return False
        return typ.kind in {'array','slice','dynamic','string','generic','unknown'}

    def sequence_trait(self,code):
        s=self.source
        s.needs_intrinsics=True
        s.needs_default=True
        meta=s.prefix+'meta'
        t=f'type_of({s.prefix}default_value({code}))'
        return f'(({meta}.type_is_array({t}) && !{meta}.type_is_enumerated_array({t})) || {meta}.type_is_slice({t}) || {meta}.type_is_dynamic_array({t}) || {meta}.type_is_fixed_capacity_dynamic_array({t}) || {meta}.type_is_string({t}))'

    def access(self,n,constant):
        s=self.source
        base=n.role('expr')
        if not self.sequence_type(base): return None
        if n.kind == 'Index':
            # Only native map lookups supply an optional status. If an unknown
            # generic/imported record holds the map, wrapping the lookup in a
            # single-result sequence helper would discard that status. Leave
            # optional contexts native; Odin still rejects sequence lookups in
            # those contexts, and map keys need no negative-index normalization.
            value=n
            while value.parent and value.parent.kind=='Paren':value=value.parent
            parent=value.parent
            optional=parent and parent.kind in {'OrElse','OrReturn','OrBranch'}
            if parent and parent.kind in {'Declaration','Assignment'}:
                names=parent.group('names' if parent.kind=='Declaration' else 'lhs')
                values=parent.group('values' if parent.kind=='Declaration' else 'rhs')
                optional=len(names)>1 and len(values)==1
            if parent and parent.kind=='Return' and len(parent.group('values'))==1:
                proc=next((a for a in parent.ancestors() if a.kind=='Procedure'),None)
                optional=bool(proc and len(s.resolver.fields(proc.role('type').role('results')))>1)
            if optional:return None
        bounds=[n.role('index')] if n.kind=='Index' else [n.role('low'),n.role('high')]
        # Positive constants and narrow unsigned indices stay native. Wide
        # unsigned runtime indices need a check before Odin narrows to int.
        def needs(bound):
            if bound is None: return False
            value=s.constant_int(bound)
            if value is not None: return value<0
            typ=self.shape(bound)
            if typ.kind=='scalar' and typ.name.startswith('u128'):return True
            return not (typ.kind=='scalar' and typ.name.startswith('u'))
        if not any(needs(bound) for bound in bounds): return None
        typ=self.shape(base)
        if n.kind=='Index' and typ.kind=='array' and typ.length is not None and s.constant_int(bounds[0]) is not None:
            index=s.constant_int(bounds[0])
            index=typ.length+index if index<0 else index
            if index<0:
                original=s.constant_int(bounds[0])
                return s.mapping.wrap(self.error(bounds[0],f'Negative sequence index {original} is out of bounds for length {typ.length}.'),s.path,bounds[0].start,bounds[0].end,'negative-index')
            return f'({s.render(base)})[{index}]'
        sequence=self.module('sequence')
        if constant:
            b=s.render(base,constant=True)
            def normalized(bound):
                if bound is None: return ''
                i=s.render(bound,constant=True)
                if not needs(bound) or self.shape(bound).name.startswith('u'): return i
                return f'(i128(len({b}))+i128({i}) when i128({i}) < 0 else i128({i}))'
            # Every operand is required to be constant by the surrounding Odin
            # context; there are no duplicated runtime evaluations here.
            if n.kind=='Index': return f'({b})[{normalized(bounds[0])}]'
            return f'({b})[{normalized(bounds[0])}:{normalized(bounds[1])}]'
        pointer=n.kind=='Index' and self.writes(n)
        b=s.render(base)
        if n.kind=='Index':
            result=f'type_of({sequence}.element({b}))'
        else:
            s.needs_default=True
            result=f'type_of({s.prefix}default_value(({b})[:]))'
        local=s.prefix+'access_'
        value,index,low,high=[local+x for x in ['base','index','low','high']]
        def body(_):
            addr=self.addressable(base)
            original=s.render(base)
            # Fixed arrays retain their storage; reference-containing headers
            # are copied before evaluating bounds, matching base evaluation.
            s.needs_intrinsics=True
            meta=s.prefix+'meta'
            s.needs_default=True
            source_type=f'type_of({s.prefix}default_value({original}))'
            array=f'{meta}.type_is_array({source_type}) || {meta}.type_is_enumerated_array({source_type})'
            def branch(byref):
                original=self.address(base) if byref else s.render(base)
                code=[f'{value} := '+('&('+original+')' if byref else original)]
                v=value+'^' if byref else value
                if n.kind=='Index':
                    code.append(f'{index} := {s.render(bounds[0])}')
                    access=f'({v})[{sequence}.normalize(len({v}),{index})]'
                    native=f'({v})[{index}]'
                    op='&' if pointer else ''
                    if pointer and not addr and byref:
                        return self.error(n,'Cannot take a mutable address of an element in a temporary array.')
                    code.append(f'when {self.sequence_trait(original)} {{ return {op}{access} }} else {{ return {op}{native} }}')
                else:
                    for bound,name in zip(bounds,[low,high]):
                        if bound: code.append(f'{name} := {s.render(bound)}')
                    l=f'{sequence}.normalize(len({v}),{low})' if bounds[0] else ''
                    h=f'{sequence}.normalize(len({v}),{high})' if bounds[1] else ''
                    code.append(f'when {self.sequence_trait(original)} {{ return ({v})[{l}:{h}] }} else {{ return ({v})[{low if bounds[0] else ""}:{high if bounds[1] else ""}] }}')
                return '\n'.join(code)
            if addr:
                referenced=branch(True)
                if addr is not True:
                    fallback=self.error(n,'Cannot take a mutable address of an element in a temporary array.') if pointer else branch(False)
                    referenced=f'when {addr} {{\n{referenced}\n}} else {{\n{fallback}\n}}'
                return f'when {array} {{\n{referenced}\n}} else {{\n{branch(False)}\n}}'
            if pointer:
                error=self.error(n,'Cannot take a mutable address of an element in a temporary array.')
                return f'when {array} {{ {error} }} else {{\n{branch(False)}\n}}'
            return branch(False)
        name,args=self.closed(n,body,result=None,pointer=pointer,flavor='address' if pointer else 'access')
        code=f'{name}({", ".join([*args,result])})'
        return f'({code})^' if pointer else code

    def shape(self,node):
        return self.source.resolver.type_of(node) if node and self.source.resolver else None

    def set_trait(self,code):
        s=self.source
        s.needs_intrinsics=True
        meta=s.prefix+'meta'
        sets=self.module('sets')
        return f'(((type_of({code}) == {sets}.Set(type_of({sets}.key_witness(({code}).table)))) when {meta}.type_is_map(type_of(({code}).table)) else false) when {meta}.type_has_field(type_of({code}),"table") else false)'

    def mutation(self,node,emit):
        if node.parent and node.parent.kind=='For' and node.parent.role('post') is node:
            name,args=self.closed(node,lambda _:emit()+'\nreturn true',result='bool',flavor='update')
            return f'_ = {name}({", ".join(args)})'
        return emit()

    def sets(self,n,expected,constant):
        from .compiler import Hint
        s=self.source
        if n.kind=='Compound' and n.role('type') and s.resolver.type_node(n.role('type')).kind=='set':
            sets=self.module('sets')
            target=s.render(n.role('type'))
            element=f'type_of({sets}.element_witness(({target}){{}}))'
            values=n.group('elems')
            if any(v.kind=='FieldValue' for v in values):
                return self.error(n,'A Set literal contains element expressions, without field names.')
            elements=', '.join(s.render(v,Hint(s,n,element),constant) for v in values)
            return f'{sets}.from_values({element},[{len(values)}]{element}{{{elements}}})'
        if n.kind=='Binary':
            op=n.data['op']
            left,right=n.role('left'),n.role('right')
            lt,rt=self.shape(left),self.shape(right)
            names={'|':'union_of','&':'intersection','-':'difference','~':'symmetric_difference','==':'equal','!=':'equal'}
            if op in names and (lt.kind=='set' or rt.kind=='set'):
                value=f'{self.module("sets")}.{names[op]}({s.render(left)},{s.render(right)})'
                return f'(!{value})' if op=='!=' else value
            if (not constant and not s.denotes_type(left) and not s.denotes_type(right) and op in names
                    and lt.kind in {'generic','unknown'} and rt.kind in {'generic','unknown'}
                    and left.text!='nil' and right.text!='nil'):
                name={'|':'union','&':'intersection','-':'difference','~':'symmetric_difference','==':'equal','!=':'equal'}[op]
                value=f'{self.module("sets")}.compiler_{name}({s.render(left)},{s.render(right)})'
                return f'(!{value})' if op=='!=' else value
        if n.kind=='Assignment' and len(n.group('lhs'))==1 and len(n.group('rhs'))==1:
            left,right=n.group('lhs')[0],n.group('rhs')[0]
            if self.shape(left).kind=='set':
                name={'+=':'insert','-=':'subtract_assign','|=':'union_assign','&=':'intersection_assign','~=':'symmetric_difference_assign'}.get(n.data['op'])
                if name:
                    code=self.ownership.update(n,self.module('sets')+'.'+name)
                    if code is not None: return code
                    return self.mutation(n,lambda:f'{self.module("sets")}.{name}(&({s.render(left)}),{s.render(right)})')
            if self.shape(left).kind in {'generic','unknown'} and left not in self.native_access:
                name={'+=':'add','-=':'sub','|=':'union','&=':'intersection','~=':'xor'}.get(n.data['op'])
                if name:
                    code=self.ownership.update(n,self.module('sets')+'.compiler_'+name+'_assign')
                    if code is not None: return code
                    return self.mutation(n,lambda:f'{self.module("sets")}.compiler_{name}_assign(&({s.render(left)}),{s.render(right)})')
        if n.kind=='Call':
            callee=n.role('callee')
            args=n.group('args')
            if callee.kind=='Ident' and s.resolver.symbol(callee) is None and len(args)==1:
                typ=self.shape(args[0])
                if callee.text=='len' and typ.kind in {'set','generic','unknown'}:
                    code=self.ownership.query(n,args[0],lambda value:f'{self.module("sets")}.length({value})')
                    if code is not None: return code
                    return f'{self.module("sets")}.length({s.render(args[0])})'
                if callee.text=='len':
                    code=self.ownership.query(n,args[0],lambda value:f'len({value})')
                    if code is not None: return code
                if callee.text=='delete' and typ.kind=='set':
                    return f'{self.module("sets")}.destroy({s.render(args[0])})'
                if callee.text=='delete' and typ.kind in {'generic','unknown'}:
                    return f'{self.module("sets")}.compiler_delete({s.render(args[0])})'
                if callee.text=='clear':
                    if typ.kind=='set':
                        return f'{self.module("sets")}.clear_set(&({s.render(args[0])}))'
                    if typ.kind=='pointer' and typ.elem.kind=='set':
                        return f'{self.module("sets")}.clear_set({s.render(args[0])})'
                    if typ.kind=='pointer' and typ.elem.kind in {'generic','unknown'}:
                        return f'{self.module("sets")}.compiler_clear({s.render(args[0])})'
                    if typ.kind in {'generic','unknown'} and self.addressable(args[0]):
                        return f'{self.module("sets")}.compiler_clear_value(&({s.render(args[0])}))'
        if n.kind=='Range' and self.shape(n.role('expr')).kind=='set':
            expr=n.role('expr')
            return s.splice(n,[(c.start,c.end,f'{self.module("sets")}.entries({s.render(c)})' if c is expr else s.render(c)) for c in n.children])
        if n.kind=='Range' and self.shape(n.role('expr')).kind in {'generic','unknown'} and not any(name.kind=='Unary' and name.data['op']=='&' for name in n.group('names')):
            expr=n.role('expr')
            # A native iterator call produces (values..., ok) repeatedly. An
            # ordinary value wrapper would evaluate it once and lose that
            # protocol. Keep opaque native calls under Odin's range checker.
            imported = s.purity.imported(expr.role('callee')) if s.purity and expr.kind=='Call' else None
            if imported and s.import_dir(imported[0]) is None:
                return None
            return s.splice(n,[(c.start,c.end,f'{self.module("sets")}.compiler_iterate({s.render(c)})' if c is expr else s.render(c)) for c in n.children])
        return None

    def interpolation(self,n):
        s=self.source
        formatting=self.module('format')
        strings=self.module('strings','core:strings')
        fields=iter(n.group('args'))
        parts=[]
        quote=chr(s.raw[n.start+1])
        for part in n.data['parts']:
            if part['literal']:
                text=s.text(part['start'],part['end']).replace('{{','{').replace('}}','}')
                parts.append((None,quote+text+quote,None))
                continue
            field=next(fields)
            spec=s.text(part['format_start'],part['format_end']) if part['format_start']>=0 else ''
            fill,align,width,group=self.format_spec(spec,part['format_start'],part['format_end'])
            parts.append((field,None,f'grouping={str(group).lower()}, width={width}, align=.{align}, fill={fill}'))
        def body(_):
            prefix=s.prefix+f'format_{n.start}_'
            builder=prefix+'builder'
            code=[f'{builder}: {strings}.Builder',f'{strings}.builder_init(&{builder})',f'defer {strings}.builder_destroy(&{builder})']
            for i,(field,literal,options) in enumerate(parts):
                if field is None:
                    code.append(f'{strings}.write_string(&{builder}, {literal})')
                else:
                    value=self.ownership.evaluate(field,code)
                    expression=value.value
                    if s.purity and (s.purity.eval_mode or s.purity.checked(s.purity.owner(n))):
                        code.append(f'{s.purity.module(s)}.validate_format(type_of({expression}))')
                    field_code='\n'.join([f'{prefix}field_{i} := {formatting}.format({expression}, {options})',
                                         f'{strings}.write_string(&{builder}, {prefix}field_{i})',f'delete({prefix}field_{i})'])
                    code.append(s.mapping.wrap(field_code,s.path,field.start,field.end,'interpolation-field'))
                    self.ownership.release(value,code)
            code.append(f'return {strings}.clone({strings}.to_string({builder}))')
            return '\n'.join(code)
        name,args=self.closed(n,body)
        return f'{name}({", ".join(args)})'

    def switch_condition(self,n,case):
        s=self.source
        selector=n.role('selector')
        if not case['matches']: return 'true'
        values=[]
        for span in case['matches']:
            match=s.at(span,n)
            value=s.render(match,constant=True)
            if selector and match.kind=='Binary' and match.data['op'] in {'..','..<','..='}:
                low,high=match.role('left'),match.role('right')
                op='<=' if match.data['op']=='..=' else '<'
                values.append(f'(({s.render(selector,constant=True)}) >= ({s.render(low,constant=True)}) && ({s.render(selector,constant=True)}) {op} ({s.render(high,constant=True)}))')
            else:
                values.append(f'(({s.render(selector,constant=True)}) == ({value}))' if selector else f'({value})')
        result='false'
        for value in reversed(values): result=f'(true when ({value}) else {result})'
        return result

    def power_type(self,node):
        s=self.source
        s.needs_default=True
        # A native conditional supplies the common operand/coercion rules.
        # type_of does not execute either operand. The support signature also
        # excludes string, enum, boolean and aggregate operands.
        left,right=(s.render(node.role(side)) for side in ['left','right'])
        typ=f'type_of({s.prefix}default_value(true ? ({left}) : ({right})))'
        code=f'type_of({self.module("numeric")}.power(({typ}){{}},({typ}){{}}))'
        return s.mapping.wrap(code,s.path,node.start,node.end,'power-type')

    def power(self,node,constant=False):
        s=self.source
        typ=self.power_type(node)
        exponent=s.resolver.constant_int(node.role('right'))
        if constant and exponent is not None and 0<=exponent<=128:
            # Native constant arithmetic also covers uninstantiated type-level
            # generics (e.g. [N**2]int), which have no executable call site.
            base=s.render(node.role('left'),constant=True)
            product=' * '.join('('+base+')' for _ in range(exponent)) if exponent else '1'
            return f'({typ})({product})'
        if constant and any(a.kind=='StructType' for a in node.ancestors()):
            return self.error(node,'Power in a generic type expression requires a fixed nonnegative integer exponent through 128, or a resolved procedure specialization.')
        owner=next((a for a in node.ancestors() if a.kind=='Procedure'),None)
        if owner and owner.role('body') not in set(node.ancestors()):
            # Signature constraints contain constants, and cannot host a
            # lexical helper that captures their generic parameter names.
            left,right=(s.render(node.role(side)) for side in ['left','right'])
            return f'{self.module("numeric")}.power(({typ})({left}),({typ})({right}))'
        def body(hint):
            code=[]
            values=[self.ownership.evaluate(node.role(side),code,hint) for side in ['left','right']]
            result=s.prefix+'power_result'
            code.append(f'{result} := {self.module("numeric")}.power({values[0].value},{values[1].value})')
            for value in reversed(values):self.ownership.release(value,code)
            code.append('return '+result)
            return '\n'.join(code)
        name,args=self.closed(node,body,result=None,flavor='power')
        args.append(typ)
        return name+'('+', '.join(args)+')'

    def range_membership(self,node,interval,constant):
        s=self.source
        s.needs_default=True
        operands=[node.role('left'),interval.role('left'),interval.role('right')]
        upper='<=' if interval.data['op']=='..=' else '<'
        def predicate(parts):
            value,low,high=parts
            result=f'(({value}) >= ({low}) && ({value}) {upper} ({high}))'
            return '!'+result if node.data['op']=='not_in' else result
        def common_value(parts):
            value,low,high=parts
            return f'(true ? ({value}) : (true ? ({low}) : ({high})))'
        def static(operand,seen=None):
            seen=set() if seen is None else seen
            if operand in seen:return False
            seen.add(operand)
            if operand.kind=='Literal':return True
            if operand.kind in {'Paren','Unary'} and operand.data.get('op') not in {'&','**'}:
                return static(operand.role('expr'),seen)
            if operand.kind=='Binary':return all(static(operand.role(side),seen.copy()) for side in ['left','right'])
            symbol=s.resolver.symbol(operand)
            return bool(symbol and symbol.declaration.kind=='Declaration' and not symbol.declaration.data.get('mutable',True)
                        and symbol.value and static(symbol.value,seen))
        if constant or all(static(operand) for operand in operands):
            parts=[s.render(operand,constant=True) for operand in operands]
            common=common_value(parts)
            # Infer a common operand type without materializing arbitrary-width
            # untyped numeric constants. Only the boolean needs a default type.
            valid=f'type_of({s.prefix}default_value(({common}) == ({common}))) == bool'
            valid=s.mapping.wrap(valid,s.path,node.start,node.end,'range-type')
            return f'(({valid}) && ({predicate(parts)}))'
        def body(_):
            # The type query does not execute operands. Native inference keeps
            # literal/enum contexts and diagnoses incompatible operand types.
            self.ownership.querying+=1
            try:common=common_value([s.render(operand) for operand in operands])
            finally:self.ownership.querying-=1
            typ=f'type_of({s.prefix}default_value({common}))'
            typ=s.mapping.wrap(typ,s.path,node.start,node.end,'range-type')
            hint=self.ownership.hint(typ,node)
            code=[]
            values=[self.ownership.evaluate(operand,code,hint) for operand in operands]
            result=s.prefix+'range_result'
            code.append(f'{result} := {predicate([value.value for value in values])}')
            for value in reversed(values):self.ownership.release(value,code)
            code.append('return '+result)
            return '\n'.join(code)
        name,args=self.closed(node,body,result='bool',flavor='range_contains')
        return name+'('+', '.join(args)+')'

    def format_spec(self,text,start,end):
        fill,align="' '", 'Default'
        if text.startswith("'"):
            # The shared lexer validates one Unicode scalar and Odin escapes.
            # Preserve that literal instead of decoding it a second way here.
            literal=re.match(r"'(?:\\.|[^'\\\r\n])*'",text)
            if not literal or len(text)<=literal.end() or text[literal.end()] not in '<>^':
                self.source.error('Interpolation fill requires a rune literal followed by <, >, or ^',start,end)
            fill=literal[0]
            align,text=text[literal.end()],text[literal.end()+1:]
        elif len(text)>=2 and text[1] in '<>^':
            self.source.error("Interpolation fill must be a single-quoted rune, e.g. '0'>12 or '界'^5",start,end)
        elif text and text[0] in '<>^':
            align,text=text[0],text[1:]
        align={'<':'Left','>':'Right','^':'Center'}.get(align,align)
        grouping=text.endswith(',')
        if grouping:
            text=text[:-1]
        if text and (not text.isascii() or not text.isdigit()):
            self.source.error("Unsupported interpolation format; expected [[rune-literal]align][width][,]",start,end)
        return fill,align,int(text or '0'),grouping

    def switch(self,n,expected,constant):
        s=self.source
        cases=n.data['cases']
        defaults=[c for c in cases if not c['matches']]
        expression=n.kind!='CompileSwitch'
        if len(defaults)>1:
            return self.error(n,'A switch may have only one default arm.')
        if expression and not defaults:
            return self.error(n,'A switch expression requires a default arm.')
        selector=n.role('selector')
        def at(span): return s.at(span,n)
        if n.kind!='SwitchExpr':
            problem=self.validate_compile_cases(n,selector)
            if problem: return self.error(n,problem)
        if n.kind=='SwitchExpr':
            # Native switch checking retains duplicate/range/type validation.
            arms=[at(c['result']) for c in cases]
            if expected:
                result=self.type_code(expected)
            else:
                s.needs_default=True
                values=[s.render(a) for a in arms]
                query=values[-1]
                for value in reversed(values[:-1]): query=f'(false ? ({value}) : ({query}))'
                result=f'type_of({s.prefix}default_value({query}))'
            def body(hint):
                code=['switch '+(s.render(selector) if selector else '')+' {']
                for case in cases:
                    matches=', '.join(s.render(at(v)) for v in case['matches'])
                    code.append(f'case {matches}: return {s.render(at(case["result"]),hint)}')
                code.append('}')
                return '\n'.join(code)
            name,args=self.closed(n,body,result=None,flavor='switch')
            return f'{name}({", ".join([*args,result])})'
        def condition(case): return self.switch_condition(n,case)
        nondefault=[c for c in cases if c['matches']]
        validation=self.validation(n)
        if expression:
            result=s.render(at(defaults[0]['result']),expected,True)
            for case in reversed(nondefault):
                result=f'({s.render(at(case["result"]),expected,True)} when {condition(case)} else {result})'
            return f'({result} when {validation} else {self.error(n,"Invalid compile-time switch cases.")})'
        code=[]
        for case in [*nondefault,*defaults]:
            prefix='else when' if code else 'when'
            statements='\n'.join(s.render(at(span)) for span in case['statements'])
            owner=s.at([case['start'],case['end']],n)
            if owner: statements=self.declarations(owner)+statements
            code.append(f'{prefix} {condition(case)} {{\n{statements}\n}}')
        chain=' '.join(code) or 'when false {}'
        return f'when {validation} {{\n{chain}\n}}'

    def validate_compile_cases(self,n,selector):
        s=self.source
        keys=set()
        intervals=[]
        def key(node,seen=None):
            seen=set() if seen is None else seen
            if node in seen: return None
            seen.add(node)
            integer=s.constant_int(node)
            if integer is not None: return 'integer',integer
            if node.kind=='Paren': return key(node.role('expr'),seen)
            if node.text in {'true','false'}: return 'bool',node.text
            if node.kind=='Literal' and node.data['op']=='string': return 'string',node.text
            if node.kind=='ImplicitSelector' and selector:
                typ=self.shape(selector)
                if typ.kind=='enum': return typ.node,node.text.lstrip('.')
            if node.kind=='Selector':
                typ=s.resolver.type_node(node.role('expr'))
                if typ.kind=='enum': return typ.node,node.role('field').text
            symbol=s.resolver.symbol(node)
            if symbol and symbol.value and not symbol.declaration.data.get('mutable',True):
                return key(symbol.value,seen)
            return None
        for case in n.data['cases']:
            for span in case['matches']:
                node=s.at(span,n)
                value=key(node)
                interval=None
                if value and value[0]=='integer': interval=value[1],value[1]
                elif node.kind=='Binary' and node.data['op'] in {'..','..<','..='}:
                    a,b=s.constant_int(node.role('left')),s.constant_int(node.role('right'))
                    if a is not None and b is not None:
                        b-=node.data['op']!='..='
                        if b<a: return 'A switch case range must not be empty or reversed.'
                        interval=a,b
                if value and value in keys: return 'Duplicate constant switch case.'
                if value: keys.add(value)
                if interval:
                    if any(max(interval[0],a)<=min(interval[1],b) for a,b in intervals):
                        return 'Duplicate or overlapping constant switch cases.'
                    intervals.append(interval)
        return None

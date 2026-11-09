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
        from .switches import Switches
        self.switches = Switches(self)
        from .fstrings import FStringLifetimes
        self.fstrings = FStringLifetimes(self)

    def module(self, name, path=None):
        alias = self.source.prefix + name
        self.modules[alias] = path or 'thor:' + name
        return alias

    def imports(self):
        return ''.join(self.source.mapping.wrap(f'\nimport {alias} {json.dumps(self.source.output_import(path))}\n',self.source.path,0,0,'support') for alias,path in self.modules.items())

    def declarations(self, scope):
        return ''.join(code for (owner,_),code in list(self.helpers.items()) if owner is scope)

    def helper_scope(self, node):
        # A case's match is checked outside its body. Odin also does not collect
        # procedure declarations inside deferred statements. Put helpers in a
        # surrounding declaration scope; their explicit inputs keep evaluation
        # at the original expression site.
        ancestors = list(node.ancestors())
        comprehension = next((a for a in ancestors if a.kind == 'Comprehension'), None)
        if comprehension:
            # Its native AST blocks model lexical scope only. The custom
            # renderer emits the loop; helper declarations belong outside it.
            ancestors = list(comprehension.ancestors())
        deferred = [a for a in ancestors if a.kind == 'Defer']
        if deferred:
            ancestors = list(deferred[-1].ancestors())
        return next((a for a in ancestors
                     if a.kind == 'Case' and node.start >= a.data['roles']['colon'][1]
                     or a.kind == 'Block' and (not a.parent or a.parent.kind not in {'Switch','TypeSwitch','CompileSwitch'})),
                    self.source.root)

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

    def closed(self,node,builder,result='string',pointer=False,flavor='value',optional_ok=False,optional_tag=None,contextless=False):
        from .compiler import Hint
        from .semantics import descendants
        s=self.source
        name=s.prefix+f'{flavor}_{node.start}_{node.end}'
        # An ordinary procedure and its checked pure variant share AST spans,
        # but their helper bodies have different call targets and contracts.
        # Reusing an unchecked helper would bypass transitive purity checks.
        if s.purity and (s.purity.eval_mode or s.purity.checked(s.purity.owner(node))):name+='_pure'
        lifetime=self.fstrings.current
        if lifetime:name+='_fstrings'
        if self.switches.global_context: name += '_global'
        comprehension_query=getattr(getattr(s,'comprehensions',None),'querying',0)
        if comprehension_query:name+='_type_query'
        scope=self.helper_scope(node)
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
        if lifetime:
            params.append(self.fstrings.parameter())
            args.append(lifetime.pointer)
        aliases={k:f'({s.prefix}capture_{i}{"^" if refs[i] else ""})' for i,k in enumerate(variables)}
        if result is False:
            result_type=None
        elif isinstance(result, tuple):
            for i in range(len(result)):
                if result[i] is None:params.append(f'${s.prefix}Result_{i}: typeid')
            result_type='('+', '.join(typ or s.prefix+f'Result_{i}' for i,typ in enumerate(result))+')'
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
                hint = (tuple(Hint(s,node,typ or s.prefix+f'Result_{i}') for i,typ in enumerate(result))
                        if isinstance(result,tuple) else Hint(s,node,s.prefix+'Result') if result is None else None)
                cleanup_call = next((child for child in descendants(node)
                    if child.kind == 'Call' and s.has_scope_cleanup(child)
                    and not any(parent.kind == 'Procedure' for parent in child.ancestors()
                                if node.start <= parent.start and parent.end <= node.end)
                    and not any(parent.kind == 'Call' and s.purity and s.purity.builtin(parent) in s.purity.type_queries
                                for parent in child.ancestors() if parent.start >= node.start and parent.end <= node.end)), None)
                if cleanup_call:
                    token = self.error(cleanup_call,
                        'A procedure with automatic scope cleanup must be called directly in its owning scope. '
                        'Assign its result to a named value first, then use that value in this expression.')
                    body = s.mapping.wrap('#panic('+json.dumps(token)+')',s.path,cleanup_call.start,cleanup_call.end,'scope-cleanup')
                else:
                    body=builder(hint)
            finally:
                self.captures.pop()
            convention=' "odin"' if self.has_context(node) and not self.switches.global_context and not contextless else ' "contextless"'
            if comprehension_query and not any(a.kind=='Procedure' for a in node.ancestors()):
                # Package-level type queries have no caller context. These
                # helpers are never executed and must not share runtime bodies.
                convention=' "contextless"'
                body=f'context = {self.module("comprehension_runtime","base:runtime")}.default_context()\n'+body
            returns=(' -> (value: '+result_type+', ok: bool) #optional_ok' if optional_ok else ' -> '+result_type) if result_type else ''
            if returns and optional_tag:returns+=' #'+optional_tag
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
        selector=node.role('selector')
        if selector and self.shape(selector).kind in {'big_integer','big_rational'}:
            # These constants have Thor-evaluated contents and rodata views;
            # they cannot be native Odin generic constant arguments.
            return 'true'
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
        scope=self.helper_scope(node)
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

    def reference_capture(self,symbol):
        if symbol.projection:
            owner, name = symbol.projection
            typ = self.shape(owner)
            member = self.source.resolver.field(typ, name)
            if typ.kind == 'pointer' or member and member[2]: return True
            original = self.source.resolver.symbol(owner)
            return bool(original and self.reference_capture(original))
        if symbol.declaration.kind=='Field':
            field = symbol.declaration
            signature = next((a for a in field.ancestors() if a.kind == 'ProcedureType'), None)
            return bool(signature and field.parent is signature.role('results'))
        if symbol.declaration.kind=='Range': return symbol.node.kind=='Unary' and symbol.node.data['op']=='&'
        if symbol.declaration.kind=='TypeSwitch':
            return symbol.node.kind=='Unary' and symbol.node.data['op']=='&'
        return True

    def opaque_native(self,node,seen=None):
        seen = set() if seen is None else seen
        if node is None or node in seen: return False
        seen.add(node)
        source = node.source
        imported = source.purity.imported(node) if source.purity else None
        if imported and source.import_dir(imported[0]) is None: return True
        if node.kind == 'Paren': return self.opaque_native(node.role('expr'),seen)
        symbol = source.resolver.symbol(node)
        if symbol and symbol.declaration.kind == 'Declaration' and not symbol.declaration.data.get('mutable',True):
            values = symbol.declaration.group('values')
            if len(values) == 1 and values[0].kind in {'Ident','Selector','Paren'}:
                return self.opaque_native(values[0],seen)
        return False

    def iterate(self,node,value):
        """Select Set iteration without passing native iterables to overloads.

        Native ranges, enum types, iterator calls and SOA values have special
        checker semantics. A compile-time branch preserves the actual loop and
        evaluates its iterable once, in the selected branch only.
        """
        s = self.source
        expr = node.role('expr')
        def loop(iterator):
            return s.splice(node,[(c.start,c.end,iterator if c is expr else s.render(c))
                                  for c in node.children])
        selected = loop(f'{self.module("sets")}.entries({value})')
        if self.shape(expr).kind == 'set': return selected
        return '{\nwhen '+self.set_trait(value)+' {\n'+selected+'\n} else {\n'+loop(value)+'\n}\n}'

    def render(self,n,expected,constant):
        if n in self.switches.replacements:
            return self.switches.replacements[n]
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
        if n.kind == 'OrPanic':
            from .optional import render
            return render(self, n)
        if n.kind == 'Assignment':
            from .semantics import descendants
            if any(child.kind in {'Index','Slice'} and self.type_operand(child)
                   for lhs in n.group('lhs') for child in descendants(lhs)):
                return None
        if n.kind=='Binary' and n.data['op']=='**':
            return self.power(n,constant)
        if n.kind=='Binary' and n.data['op']=='++':
            from .concatenation import render
            return render(self, n, expected)
        if n.kind=='Binary' and n.data['op']=='*':
            from .repetition import render
            repeated = render(self, n)
            if repeated is not None: return repeated
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
            if self.type_operand(n): return None
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
        code=self.switches.statement(n,constant)
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
                 if child.kind=='Index' and not self.type_operand(child)
                 and self.shape(child.role('expr')).kind in {'generic','unknown'}]
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
                trait=s.mapping.wrap(self.sequence_trait(original),s.path,index.start,index.end,'sequence-dispatch')
                return f'when {trait} {{\n{sequence}\n}} else {{\n{native}\n}}'
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
        fixed = typ.kind == 'dynamic' and typ.node and typ.node.role('capacity')
        if typ.kind=='pointer' or node.kind=='Index' and typ.kind in {'slice','dynamic'} and not fixed:
            return True
        inherited=self.addressable(base)
        if typ.kind not in {'generic','unknown'}:
            return inherited if node.kind=='Selector' or typ.kind=='array' or fixed else False
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
            ['type_is_slice','type_is_dynamic_array','type_is_pointer','type_is_multi_pointer'])
        if inherited:
            array=f'({meta}.type_is_array({t}) || {meta}.type_is_fixed_capacity_dynamic_array({t}))'
            references+=' || '+(array if inherited is True else f'({array} && ({inherited}))')
        return '('+references+')'

    def writes(self,node):
        if node in self.addresses: return True
        for parent in node.ancestors():
            if parent.kind=='Assignment':
                if parent.parent and parent.parent.kind == 'TypeSwitch':
                    return any(name.kind == 'Unary' and name.data.get('op') == '&'
                               for name in parent.parent.group('names'))
                return any(lhs.start<=node.start and node.end<=lhs.end for lhs in parent.group('lhs'))
            if parent.kind=='Unary' and parent.data['op']=='&': return True
            if parent.kind=='Range':
                return any(name.kind=='Unary' and name.data.get('op')=='&' for name in parent.group('names'))
            if parent.kind=='Slice': return True
            if parent.kind not in {'Paren','Selector','Index','Deref'}: return False
        return False

    def type_operand(self, node):
        """Leave invalid type operands native, before introducing value helpers.

        Native checking decides whether this occurrence is active. Record its
        original identity for diagnostics, without guessing unknown generic or
        imported values from their approximate shapes.
        """
        base = node.role('expr')
        if not self.source.resolver.known_type_operand(base): return False
        self.source.type_operand_errors[node] = base
        return True

    def sequence_type(self,node):
        typ=self.shape(node)
        if typ.kind=='array' and typ.length is None and typ.node and typ.node.kind=='ArrayType':
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
            optional=parent and parent.kind in {'OrElse','OrReturn','OrBranch','OrPanic'}
            if parent and parent.kind in {'Declaration','Assignment'}:
                names=parent.group('names' if parent.kind=='Declaration' else 'lhs')
                values=parent.group('values' if parent.kind=='Declaration' else 'rhs')
                optional=len(names)>1 and len(values)==1
            if parent and parent.kind=='Return' and len(parent.group('values'))==1:
                proc=next((a for a in parent.ancestors() if a.kind=='Procedure'),None)
                optional=bool(proc and len(s.resolver.fields(proc.role('type').role('results')))>1)
            if optional and not self.writes(n):return None
        bounds=[n.role('index')] if n.kind=='Index' else [n.role('low'),n.role('high')]
        # Positive constants and narrow unsigned indices stay native. Wide
        # unsigned runtime indices need a check before Odin narrows to int.
        def needs(bound):
            if bound is None: return False
            while bound.kind == 'Paren': bound = bound.role('expr')
            value=s.constant_int(bound)
            if value is not None: return value<0
            typ=self.shape(bound)
            if bound.kind == 'ImplicitSelector' or typ.kind == 'enum': return False
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
        def mapped_bound(bound, code):
            role = 'index' if n.kind == 'Index' else 'lower' if bound is bounds[0] else 'upper'
            s.sequence_bounds[bound.start,bound.end] = (n, bound, role)
            return s.mapping.wrap(code,s.path,bound.start,bound.end,'sequence-'+role)
        if constant:
            b=s.render(base,constant=True)
            def normalized(bound):
                if bound is None: return ''
                i=s.render(bound,constant=True)
                if not needs(bound) or self.shape(bound).name.startswith('u'): return i
                return mapped_bound(bound,f'(i128(len({b}))+i128({i}) when i128({i}) < 0 else i128({i}))')
            # Every operand is required to be constant by the surrounding Odin
            # context; there are no duplicated runtime evaluations here.
            if n.kind=='Index': return f'({b})[{normalized(bounds[0])}]'
            return f'({b})[{normalized(bounds[0])}:{normalized(bounds[1])}]'
        pointer=n.kind=='Index' and self.writes(n)
        b=s.render(base)
        if n.kind=='Index':
            result=s.mapping.wrap(f'type_of({sequence}.element({b}))',s.path,n.start,n.end,'sequence-type')
        else:
            s.needs_default=True
            result=s.mapping.wrap(f'type_of({s.prefix}default_value(({b})[:]))',s.path,n.start,n.end,'sequence-type')
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
            array=s.mapping.wrap(f'{meta}.type_is_array({source_type}) || {meta}.type_is_enumerated_array({source_type}) || {meta}.type_is_fixed_capacity_dynamic_array({source_type})',s.path,n.start,n.end,'sequence-dispatch')
            def branch(byref):
                original=self.address(base) if byref else s.render(base)
                code=[f'{value} := '+('&('+original+')' if byref else original)]
                v=value+'^' if byref else value
                if n.kind=='Index':
                    code.append(f'{index} := {s.render(bounds[0])}')
                    normalized=mapped_bound(bounds[0],f'{sequence}.normalize(len({v}),{index})')
                    access=f'({v})[{normalized}]'
                    native=f'({v})[{index}]'
                    op='&' if pointer else ''
                    if pointer and not addr and byref:
                        return self.error(n,'Cannot take a mutable address of an element in a temporary array.')
                    trait=s.mapping.wrap(self.sequence_trait(original),s.path,n.start,n.end,'sequence-dispatch')
                    code.append(f'when {trait} {{ return {op}{access} }} else {{ return {op}{native} }}')
                else:
                    for bound,name in zip(bounds,[low,high]):
                        if bound: code.append(f'{name} := {s.render(bound)}')
                    l=mapped_bound(bounds[0],f'{sequence}.normalize(len({v}),{low})') if bounds[0] else ''
                    h=mapped_bound(bounds[1],f'{sequence}.normalize(len({v}),{high})') if bounds[1] else ''
                    trait=s.mapping.wrap(self.sequence_trait(original),s.path,n.start,n.end,'sequence-dispatch')
                    code.append(f'when {trait} {{ return ({v})[{l}:{h}] }} else {{ return ({v})[{low if bounds[0] else ""}:{high if bounds[1] else ""}] }}')
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
        s.needs_default=True
        meta=s.prefix+'meta'
        sets=self.module('sets')
        # Native len accepts untyped string constants. Default inference gives
        # the trait a concrete type, entirely inside unevaluated type_of.
        typ=f'type_of({s.prefix}default_value({code}))'
        return f'((({typ} == {sets}.Set(type_of({sets}.key_witness(({code}).table)))) when {meta}.type_is_map(type_of(({code}).table)) else false) when {meta}.type_has_field({typ},"table") else false)'

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
                if callee.text == 'len' and (s.denotes_type(args[0]) or self.opaque_native(args[0])): return None
                if callee.text=='len' and typ.kind in {'set','generic','unknown'}:
                    def length(value):
                        return f'(len(({value}).table) when {self.set_trait(value)} else len({value}))'
                    code=self.ownership.query(n,args[0],length)
                    if code is not None: return code
                    return length(s.render(args[0]))
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
            return self.iterate(n,s.render(expr))
        if n.kind=='Range' and self.shape(n.role('expr')).kind in {'generic','unknown'} and not any(name.kind=='Unary' and name.data['op']=='&' for name in n.group('names')):
            expr=n.role('expr')
            operand = expr
            while operand.kind == 'Paren': operand = operand.role('expr')
            if s.denotes_type(expr) or self.opaque_native(expr) or operand.kind == 'Binary' and operand.data['op'] in {'..<','..=','...'}:
                return None
            # A native iterator call produces (values..., ok) repeatedly. An
            # ordinary value wrapper would evaluate it once and lose that
            # protocol. Keep opaque native calls under Odin's range checker.
            imported = s.purity.imported(operand.role('callee')) if s.purity and operand.kind=='Call' else None
            if imported and s.import_dir(imported[0]) is None:
                return None
            if operand.kind == 'Call':
                plan = s.resolver.resolve_call(operand)
                if plan and len(s.resolver.fields(plan.signature.role('results'))) > 1:
                    return None
            return self.iterate(n,s.render(expr))
        return None

    def interpolation(self,n):
        s=self.source
        formatting=self.module('format')
        fields=iter(n.group('args'))
        parts=[]
        quote=chr(s.raw[n.start+(2 if n.data.get('manual_lifetime') else 1)])
        prefix=s.prefix+f'format_{n.start}_'
        for part_index,part in enumerate(n.data['parts']):
            if part['literal']:
                text=s.text(part['start'],part['end']).replace('{{','{').replace('}}','}')
                parts.append((None,quote+text+quote,None,[],None))
                continue
            field=next(fields)
            spec=s.text(part['format_start'],part['format_end']) if part['format_start']>=0 else ''
            from .formatting import WIDTH_MARKER
            widths=[next(fields) for _ in (part.get('format_exprs') or [])]
            raw=spec.encode('utf-8')
            for start,end in reversed(part.get('format_exprs') or []):
                raw=raw[:start-1-part['format_start']]+WIDTH_MARKER.encode()+raw[end+1-part['format_start']:]
            width_name=prefix+f'width_{part_index}' if widths else None
            options=self.format_spec(raw.decode('utf-8'),part['format_start'],part['format_end'],width_name)
            parts.append((field,None,options,widths,width_name))
        def body(_):
            builder=prefix+'builder'
            code=[f'{builder}: {formatting}.Buffer = ---',f'{formatting}.buffer_init(&{builder})',f'defer {formatting}.buffer_destroy(&{builder})']
            for i,(field,literal,options,widths,width_name) in enumerate(parts):
                if field is None:
                    code.append(f'{formatting}.buffer_write_string(&{builder}, {literal})')
                else:
                    value=self.ownership.evaluate(field,code)
                    expression=value.value
                    if widths:
                        snapshot=prefix+f'snapshot_{i}'
                        code.append(s.mapping.wrap(f'{snapshot} := {formatting}.compiler_capture_width_value({expression}, {value.owned})',
                                                   s.path,field.start,field.end,'interpolation-field'))
                        expression=snapshot
                    for width in widths:
                        evaluated_width=self.ownership.evaluate(width,code)
                        code.append(s.mapping.wrap(f'{width_name} := {formatting}.normalize_width({evaluated_width.value})',
                                                   s.path,width.start,width.end,'interpolation-width'))
                        self.ownership.release(evaluated_width,code)
                    if s.purity and (s.purity.eval_mode or s.purity.checked(s.purity.owner(n))):
                        code.append(s.mapping.wrap(f'{formatting}.validate_pure_value(type_of({expression}))',
                                                   s.path,field.start,field.end,'interpolation-field'))
                    field_code=f'{formatting}.write_spec(&{builder}.builder, {expression}, {options})'
                    code.append(s.mapping.wrap(field_code,s.path,field.start,field.end,'interpolation-field'))
                    if widths:
                        code.append(f'{formatting}.compiler_release_width_value({expression}, {value.owned})')
                    self.ownership.release(value,code)
            code.append(f'return {formatting}.buffer_owned_text(&{builder})')
            return '\n'.join(code)
        name,args=self.closed(n,body)
        return f'{name}({", ".join(args)})'

    def switch_condition(self,n,case):
        s=self.source
        numeric=s.big.constants.case(n,case)
        if numeric is not None:return str(numeric).lower()
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

    def format_spec(self,text,start,end,dynamic_width=None):
        from .formatting import parse_format
        return parse_format(text,lambda message:self.source.error(message,start,end),dynamic_width)

    def switch(self,n,expected,constant):
        s=self.source
        if self.switches.special(n,expected):
            return self.switches.render(n,expected,constant)
        if isinstance(expected,tuple): expected=expected[0] if expected else None
        cases=n.data['cases']
        defaults=[c for c in cases if not c['matches']]
        expression=n.kind!='CompileSwitch'
        problem=self.switches.default_error(n) if expression else 'A switch may have only one default arm.' if len(defaults)>1 else None
        if problem:
            return self.error(n,problem)
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
                values=self.switches.queries(n,arms,scalar=True)
                if not values:
                    return self.error(n,'A switch expression with only nonreturning arms requires an expected result type.')
                query=values[-1]
                for value in reversed(values[:-1]): query=f'(false ? ({value}) : ({query}))'
                result=f'type_of({s.prefix}default_value({query}))'
            def body(hint):
                code=[]
                selected,kind=self.switches.runtime_selector(n,code)
                branches=[]
                for case in cases:
                    arm=at(case['result'])
                    result=self.switches.branch(arm,lambda:'return '+s.render(arm,hint))
                    branches.append((case,result))
                code.append(self.switches.runtime_code(n,selected,kind,branches))
                return '\n'.join(code)
            name,args=self.switches.closed(n,body,result=None,flavor='switch')
            return f'{name}({", ".join([*args,result])})'
        def condition(case): return self.switch_condition(n,case)
        nondefault=[c for c in cases if c['matches']]
        validation=self.validation(n)
        if expression:
            def value(case):
                arm=at(case['result'])
                if self.switches.deferred_call(arm) and expected:
                    name,args=self.closed(arm,lambda hint:self.switches.branch(arm,lambda:'return '+s.render(arm,hint)),
                                          result=None,flavor='switch_selected_call')
                    return name+'('+', '.join([*args,self.type_code(expected)])+')'
                condition=self.switches.result_condition(arm)
                if condition is not None:
                    invalid=self.error(arm,'A selected nonreturning arm requires an expected result type.')
                    return f'({s.render(arm,expected,True)} when {condition} else {invalid})'
                if not s.resolver.is_diverging_call(arm): return s.render(arm,expected,True)
                if not expected:
                    # A compile-time switch derives its type only from the
                    # selected arm. Keep this error lazy, just like its values.
                    return self.error(arm,'A selected nonreturning arm requires an expected result type.')
                # Keep constant selection lazy. Only a selected terminal arm calls
                # this runtime helper; inactive arms remain entirely unchecked.
                name,args=self.closed(arm,lambda _:s.render(arm),result=None,flavor='switch_panic')
                return f'{name}({", ".join([*args,self.type_code(expected)])})'
            result=value(defaults[0])
            for case in reversed(nondefault):
                result=f'({value(case)} when {condition(case)} else {result})'
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
        numeric=s.big.constants.validate_cases(n)
        if numeric:return numeric
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

"""Numeric builtins, borrowed sequence membership, and generic components."""
from .compiler import Hint

KINDS={'big_integer','big_rational'}


class BigIntegration:
    def __init__(self,big):self.big=big;self.s=big.s;self.e=big.e;self.busy=set()

    def dynamic_property(self,node):
        if node.kind!='Selector' or node.role('field').text not in {'numerator','denominator'}:return False
        base=node.role('expr')
        if self.s.denotes_type(base) or base.kind=='Ident' and base.text in self.s.imports and self.s.resolver.symbol(base) is None:return False
        typ=self.big.shape(node.role('expr'))
        while typ.kind=='pointer' and typ.elem:typ=typ.elem
        return typ.kind in {'generic','unknown'}

    def target(self,node):
        current=node
        while current.parent and current.parent.kind in {'Selector','Index','Paren'}:current=current.parent
        parent=current.parent
        return bool(parent and (parent.kind=='Assignment' and current in parent.group('lhs') or parent.kind=='Unary' and parent.data['op']=='&'))

    def builtin(self,node):
        if node.kind!='Call':return None
        callee=node.role('callee')
        name=self.e.extrema.builtin(callee)
        if name:return name
        if callee.kind=='Ident' and callee.text in {'abs','clamp'} and self.s.resolver.symbol(callee) is None and callee.text not in self.s.imports and callee.text not in self.s.selected:
            return callee.text
        return None

    def result(self,node):
        if not self.builtin(node):return None
        plan=self.e.extrema.plan(node)
        candidates=[self.big.shape(value).elem if kind!='scalar' else self.big.shape(value) for kind,value in plan[1]] if plan else [self.big.shape(v) for v in node.group('args') if not self.s.denotes_type(v)]
        return next((t for t in candidates if t and t.kind in KINDS),next((t for t in candidates if t and t.kind in {'generic','unknown'}),None))

    def render(self,node,hint,constant):
        if node in self.busy:return None
        if node.kind=='Assignment' or node.kind=='Unary' and node.data['op']=='&':
            from .semantics import descendants
            targets=node.group('lhs') if node.kind=='Assignment' else [node.role('expr')]
            properties=[p for target in targets for p in descendants(target) if self.dynamic_property(p)]
            if properties:
                predicates=[]
                for prop in properties:
                    value=self.s.render(prop.role('expr'));typ=f'type_of({value})';module=self.big.module()
                    predicates.append(f'({typ}=={module}.Rational || {typ}==^{module}.Rational)')
                error=self.big.error(properties[0],'Rational numerator and denominator properties are read-only; use big.set_fraction to change both together.')
                self.busy.add(node)
                try:native=self.s._render(node,hint,constant)
                finally:self.busy.remove(node)
                predicate=' || '.join(predicates)
                return f'when {predicate} {{_={error}}} else {{{native}}}' if node.kind=='Assignment' else f'({error} when {predicate} else {native})'
        if self.dynamic_property(node) and not self.target(node) and not constant:
            base=node.role('expr');field=node.role('field').text
            value=self.s.render(base)
            typ=f'type_of({value})';module=self.big.module()
            result=f'type_of(({module}.Integer{{}} when {typ}=={module}.Rational || {typ}==^{module}.Rational else ({value}).{field}))'
            if self.e.ownership.may_own(base):
                def body(hint):
                    code=[];temporary=self.e.ownership.evaluate(base,code)
                    name=self.s.prefix+f'big_component_{node.start}'
                    code.append(f'{name}:={module}.compiler_{field}({temporary.value},{self.e.type_code(hint)})')
                    self.e.ownership.release(temporary,code)
                    code.append('return '+name)
                    return '\n'.join(code)
                helper,args=self.e.closed(node,body,result=None,flavor='big_component')
                return helper+'('+', '.join([*args,result])+')'
            return f'{module}.compiler_{field}({value},{result})'
        if node.kind=='Binary' and node.data['op'] in {'in','not_in'}:
            return self.membership(node,constant)
        target=self.result(node)
        if target is None:return None
        if constant:return self.big.error(node,'A numeric builtin constant requires compile-time-known matching inputs.') if target.kind in KINDS else None
        return self.numeric(node,hint,target)

    def membership(self,node,constant):
        left,right=node.role('left'),node.role('right')
        sequence=right
        while sequence.kind=='Paren':sequence=sequence.role('expr')
        container=self.big.shape(sequence)
        if not self.s.is_array(sequence):
            if constant or not container.elem or container.elem.kind not in KINDS or container.kind not in {'array','slice','dynamic','fixed'}:return None
            def body(_):
                kind=container.elem.kind;hint=self.big.type_hint(kind);module=self.big.module()
                needle=self.s.prefix+f'big_needle_{node.start}'
                values=self.s.prefix+f'big_values_{node.start}'
                code=[f'{needle}:={self.big.owned(left,hint)}',f'defer {module}.compiler_destroy(&{needle})',f'{values}:={self.s.render(right)}']
                matched='false' if node.data['op']=='in' else 'true'
                other='true' if node.data['op']=='in' else 'false'
                code.append(f'for item in {values} {{if {module}.equal({needle},item) {{return {other}}}}}')
                code.append('return '+matched)
                return '\n'.join(code)
            name,args=self.e.closed(node,body,result='bool',flavor='big_membership')
            return name+'('+', '.join(args)+')'
        elems=sequence.group('elems')
        target=next((n for n in [left,*elems] if self.big.shape(n).kind in KINDS),None)
        if target is None:return None
        kind=self.big.shape(target).kind
        if constant:
            from .bigconstants import NotConstant
            from .bigvalues import number
            try:
                needle=number(self.big.constants.evaluate(left,kind))
                found=any(needle==number(self.big.constants.evaluate(v,kind)) for v in elems)
                return str(found if node.data['op']=='in' else not found).lower()
            except (NotConstant,ValueError):return self.big.error(node,'Compile-time numeric membership requires matching known values.')
        def body(_):
            typ=self.e.ownership.native_type(target);hint=Hint(target.source,target,typ)
            needle=self.s.prefix+f'big_needle_{node.start}'
            code=[f'{needle}:{typ}={self.big.owned(left,hint)}',f'defer {self.big.module()}.compiler_destroy(&{needle})']
            names=[]
            for i,value in enumerate(elems):
                name=self.s.prefix+f'big_member_{node.start}_{i}'
                code.extend([f'{name}:{typ}={self.big.owned(value,hint)}',f'defer {self.big.module()}.compiler_destroy(&{name})'])
                names.append(name)
            comparison=' || '.join(f'{self.big.module()}.equal({needle},{name})' for name in names) or 'false'
            code.append('return '+('!('+comparison+')' if node.data['op']=='not_in' else comparison))
            return '\n'.join(code)
        name,args=self.e.closed(node,body,result='bool',flavor='big_membership')
        return name+'('+', '.join(args)+')'

    def numeric(self,node,hint,target):
        name=self.builtin(node);plan=self.e.extrema.plan(node)
        parts=plan[1] if plan else [('scalar',v) for v in node.group('args')]
        optional=plan and plan[2]
        if not parts or any(v.kind=='FieldValue' for _,v in parts):return None
        if name=='abs' and len(parts)!=1 or name=='clamp' and len(parts)!=3:return None
        witness=next((v for k,v in parts if (self.big.shape(v).elem if k!='scalar' else self.big.shape(v)) and (self.big.shape(v).elem if k!='scalar' else self.big.shape(v)).kind in KINDS|{'generic','unknown'}),parts[0][1])
        chosen=self.e.extrema.result_type(node) if plan else self.e.ownership.native_type(witness)
        def body(result):
            typ=self.e.type_code(result);module=self.big.module()
            code=[f'when {typ}=={module}.Integer || {typ}=={module}.Rational {{']
            args=[]
            for i,(kind,value) in enumerate(parts):
                binding=self.s.prefix+f'big_builtin_{node.start}_{i}'
                if kind=='scalar':
                    origin=self.big.type_hint(target.kind) if target.kind in KINDS else Hint(witness.source,witness)
                    code.extend([f'{binding}:{typ}={self.big.owned(value,Hint(origin.source,origin.node,typ))}',f'defer {module}.compiler_destroy(&{binding})'])
                else:
                    code.append(f'{binding}:={self.s.render(value)}')
                    if kind=='array' and self.big.shape(value).length==0:return self.big.error(value,'An empty fixed array cannot be expanded into min/max.')
                    if self.s.is_array(value) or value.kind=='Compound':code.append(f'defer for item in {binding} {{{module}.compiler_destroy_value(item)}}')
                args.append(binding)
            if name in {'abs','clamp'}:code.append(f'return {module}.compiler_{name}('+','.join(args)+')')
            else:
                value=self.s.prefix+f'big_extreme_{node.start}'
                ok=value+'_ok'
                code.extend([f'{value}:{typ}',f'{ok}:=false'])
                for (kind,_),arg in zip(parts,args):
                    candidate=arg if kind=='scalar' else 'item'
                    step=(f'if !{ok} || {module}.compare({candidate},{value},'+('"<"' if name=='min' else '">"')+') {'
                          f'{module}.compiler_destroy(&{value});{value}={module}.compiler_clone({candidate});{ok}=true'+'}')
                    code.append(step if kind=='scalar' else f'for item in {arg} {{'+step+'}')
                code.append(f'return {value},{ok}' if optional else f'return {value}')
            self.big.busy.add(node)
            try:native=self.s.render(node,hint)
            finally:self.big.busy.remove(node)
            code.append('} else {return '+native+'}')
            return '\n'.join(code)
        helper,args=self.e.closed(node,body,result=None,flavor='big_builtin',optional_ok=bool(optional))
        return helper+'('+', '.join([*args,chosen])+')'

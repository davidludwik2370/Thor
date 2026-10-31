"""Nominal big-number types, construction, and direct value ownership.

Aggregate copies deliberately remain native shallow copies. No native Odin
source is rewritten; generated ownership helpers specialize on the exact type.
"""
import json
import re


KINDS = {'big_integer': 'Integer', 'big_rational': 'Rational'}


def unwrap(node):
    while node is not None and node.kind == 'Paren': node = node.role('expr')
    return node


class BigNumbers:
    def __init__(self, source):
        self.s = source
        self.e = source.expressions
        self.busy = set()
        self.replacements = {}
        self.querying = 0
        self.globalizing = 0
        self.activation = (-1, False)

    @property
    def enabled(self):
        if not self.s.thor or self.s.resolver is None:return False
        sources=self.s.resolver.sources
        if self.activation[0]!=len(sources):
            self.activation=(len(sources),any('thor:big' in source.imports.values() for source in sources))
        return self.activation[1]

    def mapped(self,node,code,kind='big-number'):
        return self.s.mapping.wrap(code,self.s.path,node.start,node.end,kind)

    def error(self,node,message):
        return self.mapped(node,self.e.error(node,message),'big-contract')

    def module(self): return self.e.module('big')
    def shape(self, node): return self.s.resolver.type_of(node)

    def expected(self, hint):
        if hint is None or isinstance(hint, tuple): return None
        return (hint.source.resolver.type_node(hint.node) if hint.source.denotes_type(hint.node)
                else hint.source.resolver.type_of(hint.node))

    def binding(self, node):
        p = node.parent
        return p and (p.kind in {'Declaration','Field','Range','TypeSwitch'} and node in p.group('names')
                      or p.kind in {'Selector','FieldValue'} and node is p.role('field')
                      or p.kind in {'PolyType','ImplicitSelector','EnumType'})

    def literal(self, node):
        node = unwrap(node)
        if node.kind == 'Literal' and node.data['op'] in {'integer','float'}: return node.text
        if node.kind == 'Unary' and node.data['op'] in {'+','-'}:
            inner = self.literal(node.role('expr'))
            if inner is not None: return node.data['op'] + inner
        return None

    def constructor(self, node):
        if node.kind != 'Call': return None
        target = self.s.resolver.type_node(node.role('callee'))
        return target if target.kind in KINDS else None

    def fresh(self, node):
        node = unwrap(node)
        typ = self.shape(node)
        if node.kind in {'Literal','Unary'}: return False
        if node.kind == 'Selector': return self.property(node) is not None
        if node.kind == 'Compound': return typ.kind in KINDS
        if node.kind in {'OrElse','OrReturn','OrBranch'}:return typ.kind in {*KINDS,'generic'}
        if node.kind == 'Call' and not self.s.denotes_type(node):
            signature=self.s.signature(node)
            if signature and not self.s.fields(signature.role('results')): return False
            return typ.kind in KINDS or typ.kind in {'generic','unknown'} and signature is not None
        return False

    def property(self, node):
        if node.kind != 'Selector': return None
        typ = self.shape(node.role('expr'))
        while typ.kind == 'pointer' and typ.elem: typ = typ.elem
        if typ.kind == 'big_rational' and node.role('field').text in {'numerator','denominator'}:
            return node.role('field').text
        return None

    def owned(self, node, hint=None, constant=False):
        from .compiler import Hint
        if not self.enabled or constant: return self.s.render(node, hint, constant)
        if node in self.replacements: return self.replacements[node]
        if node in self.e.switches.replacements: return self.e.switches.replacements[node]
        if node.kind == 'FieldValue':
            value = node.role('value')
            return self.s.splice(node, [(value.start,value.end,self.owned(value,hint,constant))])
        typ, expected = self.shape(node), self.expected(hint)
        wanted = expected if expected and expected.kind in KINDS else typ
        if wanted.kind not in {*KINDS,'generic'} or node.kind in {'ImplicitSelector','Compound'} and wanted.kind=='generic':
            return self.s.render(node,hint,constant)
        if self.s.denotes_type(node) or node.text in {'nil','---','_'}: return self.s.render(node,hint,constant)
        if (wanted.kind in KINDS and not self.globalizing and not any(a.kind=='Procedure' for a in node.ancestors())
                and node.parent and node.parent.kind=='Declaration'):
            def body(_):
                self.globalizing+=1
                saved=self.e.switches.global_context
                self.e.switches.global_context=0
                try: code=self.owned(node,hint,constant)
                finally:
                    self.globalizing-=1
                    self.e.switches.global_context=saved
                runtime=self.e.module('big_context','base:runtime')
                return f'context = {runtime}.default_context()\nreturn {code}'
            self.e.switches.global_context+=1
            try: name,args=self.e.closed(node,body,result=self.module()+'.'+KINDS[wanted.kind],flavor='big_global')
            finally: self.e.switches.global_context-=1
            return name+'('+', '.join(args)+')'
        if node.kind in {'Ternary','WhenExpr','SwitchExpr','CompileSwitchExpr'}:
            def body(result):
                code=[]
                value=self.e.ownership.evaluate(node,code,result)
                code.append(f'return {self.module()}.compiler_own({value.value},{value.owned})')
                return '\n'.join(code)
            name,args=self.e.closed(node,body,result=None,flavor='big_owned')
            target=self.e.type_code(hint) if hint else self.e.ownership.native_type(node)
            return name+'('+', '.join([*args,target])+')'
        code=self.s.render(node,hint,constant)
        if self.fresh(node) or self.literal(node) is not None and expected and expected.kind in KINDS:
            return code
        return self.mapped(node,f'{self.module()}.compiler_clone({code})','big-owned')

    def generic_literal(self,node,hint,constant):
        if constant: return None
        text=self.literal(node)
        if text is None: return None
        def body(result):
            target=self.e.type_code(result)
            return (f'when {target} == {self.module()}.Integer {{return {self.module()}.literal_integer({json.dumps(text)})}} '
                    f'else when {target} == {self.module()}.Rational {{return {self.module()}.literal_rational({json.dumps(text)})}} '
                    f'else {{ value: {target} = {text}; return value }}')
        name,args=self.e.closed(node,body,result=None,flavor='big_literal')
        return name+'('+', '.join([*args,self.e.type_code(hint)])+')'

    def render_literal(self, node, kind, constant):
        text=self.literal(node)
        if text is None: return None
        if constant: return self.error(node,'Compile-time Integer/Rational values are not enabled yet; use a runtime declaration.')
        if kind=='big_integer' and ('.' in text or 'e' in text.lower() and not re.match(r'[+-]?0[xz]',text,re.I)):
            from decimal import Decimal, InvalidOperation
            try:
                number=Decimal(text.replace('_',''))
                digits=number.as_tuple().digits
                exponent=number.as_tuple().exponent
                whole=not any(digits) or exponent>=0 or -exponent<=len(digits) and not any(digits[exponent:])
            except (ValueError,InvalidOperation): whole=False
            if not whole: return self.error(node,'An Integer literal must represent a whole number exactly.')
        method='literal_integer' if kind=='big_integer' else 'literal_rational'
        return f'{self.module()}.{method}({json.dumps(text)})'

    def render_constructor(self, node, typ, constant):
        if constant: return self.error(node,'Compile-time Integer/Rational values are not enabled yet; use a runtime declaration.')
        args=node.group('args')
        values=[a.role('value') if a.kind=='FieldValue' else a for a in args
                if a.kind!='FieldValue' or a.role('field').text not in {'allocator','loc'}]
        if len(values) not in ({1} if typ.kind=='big_integer' else {1,2}):
            return self.error(node,'Integer expects one value; Rational expects one value or a numerator/denominator pair.')
        names=[a.role('field').text for a in args if a.kind=='FieldValue']
        allowed={'allocator','loc','value','text'} if len(values)==1 else {'allocator','loc','numerator','denominator'}
        if len(names)!=len(set(names)) or any(name not in allowed for name in names):
            return self.error(node,'Invalid or repeated Integer/Rational constructor argument name.')
        if len(values)==1 and self.literal(values[0]) is not None and 'text' in names:
            return self.error(values[0],'The text constructor argument must be a string.')
        integer_types={'int','uint','uintptr','byte','rune',*[f'{sign}{width}' for sign in 'iu' for width in (8,16,32,64,128)]}
        for value in values:
            shape=self.shape(value)
            if self.literal(value) is None and (shape.kind=='scalar' and shape.name not in integer_types or
                                               shape.kind=='big_rational' and (typ.kind=='big_integer' or len(values)>1)):
                return self.error(value,'Integer/Rational constructors require exact integer inputs or text; convert floating-point values explicitly first.')
        if len(values)==1 and self.literal(values[0]) is not None:
            code=self.render_literal(values[0],typ.kind,False)
            extras=[self.s.render(a) for a in args if a.kind=='FieldValue' and a.role('field').text in {'allocator','loc'}]
            if extras and code.endswith(')'): code=code[:-1]+', '+', '.join(extras)+')'
            return code
        if typ.kind=='big_rational' and len(values)==2:
            denominator=next((a.role('value') for a in args if a.kind=='FieldValue' and a.role('field').text=='denominator'),values[1])
            if self.statically_zero(denominator):
                return self.error(denominator,'A Rational denominator must not be zero.')
        method='integer' if typ.kind=='big_integer' else 'rational'
        rendered=[]
        for arg in args:
            value=arg.role('value') if arg.kind=='FieldValue' else arg
            literal=self.literal(value)
            # Two-part constructors preserve arbitrary-size numerators and
            # denominators before native default-integer inference can occur.
            code=self.render_literal(value,'big_integer',False) if literal is not None else self.s.render(value)
            if arg.kind=='FieldValue': code=arg.role('field').text+'='+code
            rendered.append(code)
        return self.consume_call(node,f'{self.module()}.{method}',args,rendered)

    def statically_zero(self,node):
        if self.s.resolver.constant_int(node)==0:return True
        if node.kind=='Literal' and node.data.get('op')=='string':
            try:text=node.text[1:-1] if node.text.startswith('`') else json.loads(node.text)
            except ValueError:return False
            return re.fullmatch(r'[+-]?(?:0[bBoOdDzZxX])?0(?:_?0)*',text) is not None
        literal=self.literal(node)
        if literal is not None:
            from decimal import Decimal, InvalidOperation
            try:return Decimal(literal.replace('_','')).is_zero()
            except InvalidOperation:return False
        target=self.constructor(unwrap(node))
        if target and target.kind=='big_integer' and len(node.group('args'))==1:
            return self.statically_zero(node.group('args')[0])
        return False

    def consume_call(self,node,callee,args,rendered):
        """Known constructors borrow their inputs and own all their results."""
        # Literal converted arguments are anonymous owners too. Ordinary
        # generic/native constructor inputs remain borrowed for the call.
        owned=[self.literal(a.role('value') if a.kind=='FieldValue' else a) is not None or
               self.e.ownership.may_own(a.role('value') if a.kind=='FieldValue' else a) for a in args]
        if not any(owned): return callee+'('+', '.join(rendered)+')'
        types=[self.shape(a.role('value') if a.kind=='FieldValue' else a) for a in args]
        count=2 if any(t.kind=='string' for t in types) else 1
        def body(_):
            lines=[]; actual=[]
            for i,(arg,code,own) in enumerate(zip(args,rendered,owned)):
                name=self.s.prefix+f'big_arg_{node.start}_{i}'
                value=arg.role('value') if arg.kind=='FieldValue' else arg
                if arg.kind=='FieldValue': code=code.split('=',1)[1]
                lines.append(f'{name} := {code}')
                if own: lines.append(f'defer {self.module()}.compiler_destroy_value({name})')
                actual.append((arg.role('field').text+'=' if arg.kind=='FieldValue' else '')+name)
            lines.append('return '+callee+'('+', '.join(actual)+')')
            return '\n'.join(lines)
        target=self.s.resolver.type_node(node.role('callee'))
        result=self.module()+'.'+KINDS[target.kind]
        name,inputs=self.e.closed(node,body,result='('+result+', bool)' if count==2 else result,flavor='big_constructor')
        return name+'('+', '.join(inputs)+')'

    def argument_hints(self,node):
        from .compiler import Hint
        signature=self.s.signature(node)
        params=self.s.fields(signature.role('params')) if signature else []
        hints=[]
        for i,arg in enumerate(node.group('args')):
            index=i
            if arg.kind=='FieldValue': index=next((j for j,(name,_) in enumerate(params) if name==arg.role('field').text),i)
            annotation=params[index][1] if index<len(params) else None
            hint=self.s.resolver.call_hint(node,arg,i)
            if annotation and not hint:
                self.s.needs_intrinsics=True
                self.busy.add(node)
                self.e.ownership.querying+=1
                try: call=self.s.render(node)
                finally:
                    self.e.ownership.querying-=1
                    self.busy.remove(node)
                query=f'#type {self.s.prefix}meta.type_proc_parameter_type(type_of({self.s.prefix}meta.procedure_of({call})),{index})'
                hint=Hint(annotation.source,annotation,query)
            hints.append(hint)
        return hints

    def append(self,node):
        from .compiler import Hint
        args=node.group('args')
        if len(args)<2 or node.role('ellipsis'):return None
        typ=self.shape(args[0])
        if typ.kind!='pointer' or not typ.elem or typ.elem.kind!='dynamic' or not typ.elem.elem or typ.elem.elem.kind not in KINDS:
            return None
        def body(_):
            pointer=self.s.prefix+f'big_append_{node.start}'
            lines=[f'{pointer} := {self.s.render(args[0])}']
            values=[]
            hint=Hint(self.s,args[0],f'type_of({pointer}^[0])')
            # The element shape is distinct from the pointer argument's shape.
            annotation=typ.elem.node.role('elem')
            if annotation:hint=Hint(annotation.source,annotation,hint.expression)
            for i,value in enumerate(args[1:]):
                name=pointer+f'_value_{i}'
                lines.append(f'{name}: {self.e.type_code(hint)} = {self.owned(value,hint)}')
                values.append(name)
            lines.append('return append('+', '.join([pointer,*values])+')')
            return '\n'.join(lines)
        name,inputs=self.e.closed(node,body,result='int',flavor='big_append')
        return name+'('+', '.join(inputs)+')'

    def consume_regular_call(self,node):
        if self.e.ownership.querying or self.querying: return None
        callee=node.role('callee')
        if callee.kind=='Ident' and callee.text in {'delete','type_of','size_of','align_of','offset_of','type_info_of','raw_data','new','make','append','copy'} and self.s.resolver.symbol(callee) is None:
            return None
        signature=self.s.signature(node)
        if signature is None: return None
        args=node.group('args')
        hints=self.argument_hints(node)
        owns=[]
        for arg,hint in zip(args,hints):
            value=arg.role('value') if arg.kind=='FieldValue' else arg
            expected=self.expected(hint)
            owns.append(self.e.ownership.may_own(value) and self.shape(value).kind in {*KINDS,'generic','unknown'}
                        or self.literal(value) is not None and expected and expected.kind in KINDS)
        if not any(owns): return None
        count=len(self.s.fields(signature.role('results')))
        def body(_):
            lines=[]
            replacements={}
            params=self.s.fields(signature.role('params'))
            for i,(arg,hint,own) in enumerate(zip(args,hints,owns)):
                value=arg.role('value') if arg.kind=='FieldValue' else arg
                if self.s.denotes_type(value) or i<len(params) and params[i][0].startswith('$'): continue
                name=self.s.prefix+f'big_input_{node.start}_{i}'
                code=self.s.render(value,hint)
                annotation=self.e.type_code(hint) if hint else None
                lines.append(f'{name}: {annotation} = {code}' if annotation else f'{name} := {code}')
                if own: lines.append(f'defer {self.module()}.compiler_destroy_value({name})')
                replacements[arg]=(arg.role('field').text+'=' if arg.kind=='FieldValue' else '')+name
            self.busy.add(node)
            self.replacements.update(replacements)
            try: call=self.s.render(node)
            finally:
                self.busy.remove(node)
                for arg in replacements: self.replacements.pop(arg)
            lines.append(('return ' if count else '')+call)
            return '\n'.join(lines)
        if count==0:
            name,args=self.e.closed(node,body,result=False,flavor='big_call')
        elif count==1:
            name,args=self.e.closed(node,body,result=None,flavor='big_call')
            self.querying+=1
            try: args=[*args,self.e.ownership.native_type(node)]
            finally: self.querying-=1
        else:
            name,args=self.e.closed(node,body,result=(None,)*count,flavor='big_call')
            self.s.needs_intrinsics=True
            self.querying+=1
            try: call=self.s.render(node)
            finally: self.querying-=1
            args=[*args,*(f'{self.s.prefix}meta.type_proc_return_type(type_of({self.s.prefix}meta.procedure_of({call})),{i})' for i in range(count))]
        return name+'('+', '.join(args)+')'

    def optional(self,node):
        """Own the selected result, while return/break stay in the caller."""
        kind=self.shape(node)
        if kind.kind not in {*KINDS,'generic'}:return None
        primary=node.role('x') if node.kind=='OrElse' else node.role('expr')
        primary=unwrap(primary)
        if primary.kind=='Call':types=self.result_types(primary,2)
        else:types=(self.e.ownership.native_type(primary),'bool')
        value=self.s.prefix+f'big_optional_{node.start}'
        status=value+'_ok'
        is_fresh=self.fresh(primary)
        parent=node.parent
        discarded=parent and (parent.kind=='ExpressionStatement' or parent.kind in {'Assignment','Declaration'} and
            len(parent.group('lhs' if parent.kind=='Assignment' else 'names'))==1 and
            parent.group('lhs' if parent.kind=='Assignment' else 'names')[0].text=='_')
        def body(hints):
            lines=[f'{value},{status} := {self.s.render(primary)}']
            # Odin accepts optional bools and error values; their success senses
            # differ. Resolve that distinction at native specialization time.
            success=value+'_success'
            lines.append(f'{success} := {self.module()}.compiler_optional_ok({status})')
            if node.kind=='OrElse':
                own=f'{self.module()}.compiler_own({value},{str(is_fresh).lower()})'
                lines.append(f'if {success} {{return {own}}}')
                if is_fresh:lines.append(f'{self.module()}.compiler_destroy(&{value})')
                fallback=node.role('y')
                lines.append(self.s.render(fallback) if self.s.resolver.is_panic_call(fallback) else 'return '+self.owned(fallback,hints))
            else:
                if discarded:
                    if is_fresh:lines.append(f'{self.module()}.compiler_destroy(&{value})')
                    else:lines.append(f'{value}=type_of({value}){{}}')
                elif is_fresh:lines.append(f'if !{success} {{{self.module()}.compiler_destroy(&{value})}}')
                else:lines.append(f'if {success} {{{value}={self.module()}.compiler_clone({value})}}')
                lines.append(f'return {value},{status}')
            return '\n'.join(lines)
        if node.kind=='OrElse':
            name,args=self.e.closed(node,body,result=None,flavor='big_optional_else')
            return name+'('+', '.join([*args,types[0]])+')'
        name,args=self.e.closed(node,body,result=(None,None),flavor='big_optional_branch')
        replacement=name+'('+', '.join([*args,*types])+')'
        return self.s.splice(node,[(primary.start,primary.end,replacement)])

    def numeric_results(self,node,count):
        return any(self.s.resolver.result_type(node,i).kind in {*KINDS,'generic'} for i in range(count))

    def capture_targets(self,lhs,lines,node):
        targets=[]
        for i,target in enumerate(lhs):
            if target.text=='_': targets.append('_'); continue
            name=self.s.prefix+f'big_target_{node.start}_{i}'
            n=unwrap(target)
            if n.kind=='Index' and self.shape(n.role('expr')).kind=='map':
                base,key=n.role('expr'),n.role('index')
                lines.append(f'{name} := &({self.e.address(base)})')
                keyname=name+'_key'
                lines.append(f'{keyname}: type_of({self.e.module("sets")}.key_witness({name}^)) = {self.s.render(key)}')
                targets.append(f'{name}^[{keyname}]')
            else:
                lines.append(f'{name} := &({self.e.address(target)})')
                targets.append(name+'^')
        return targets

    def commit(self,targets,values,lines):
        for target,value in zip(targets,values):
            if target=='_': lines.append(f'{self.module()}.compiler_destroy_value({value})')
            elif target.endswith(']'):
                old=value+'_old'
                lines.extend([f'{old} := {target}',f'{self.module()}.compiler_destroy(&{old})',f'{target} = {value}'])
            else:
                lines.extend([f'{self.module()}.compiler_destroy(&({target}))',f'{target} = {value}'])

    def assignment(self,node):
        from .compiler import Hint
        if node.data['op']!='=': return None
        lhs,rhs=node.group('lhs'),node.group('rhs')
        if len(rhs)==1 and unwrap(rhs[0]).kind in {'SwitchExpr','CompileSwitchExpr'}:
            lowered=self.e.switches.statement(node,False)
            if lowered is not None:return lowered
        if len(rhs)==1 and len(lhs)>1 and self.numeric_results(rhs[0],len(lhs)):
            return self.multi_assignment(node)
        if not any(self.shape(x).kind in {*KINDS,'generic','unknown'} for x in lhs if x.text!='_'):
            return None
        if not any(self.shape(x).kind in KINDS for x in [*lhs,*rhs]) and not any(self.shape(x).kind=='generic' for x in lhs):
            return None
        if len(lhs)!=len(rhs): return self.multi_assignment(node)
        def body(_):
            lines=[]; targets=[]
            targets=self.capture_targets(lhs,lines,node)
            values=[]
            for i,(value,target) in enumerate(zip(rhs,targets)):
                name=self.s.prefix+f'big_rhs_{node.start}_{i}'
                hint=Hint(self.s,lhs[i],f'type_of({target})') if target!='_' else None
                code=self.owned(value,hint)
                lines.append(self.mapped(value,f'{name}: type_of({target}) = {code}' if target!='_' else f'{name} := {code}'))
                values.append(name)
            self.commit(targets,values,lines)
            return '\n'.join(lines)
        return self.statement(node,body)

    def result_types(self,value,count):
        self.s.needs_intrinsics=True
        self.e.ownership.querying+=1
        try: code=self.s.render(value)
        finally: self.e.ownership.querying-=1
        return tuple(f'{self.s.prefix}meta.type_proc_return_type(type_of({self.s.prefix}meta.procedure_of({code})),{i})'
                     for i in range(count))

    def multi_assignment(self,node):
        lhs,rhs=node.group('lhs'),node.group('rhs')
        if len(rhs)!=1: return None
        def body(_):
            lines=[]; targets=[]
            targets=self.capture_targets(lhs,lines,node)
            names=[self.s.prefix+f'big_multi_value_{node.start}_{i}' for i in range(len(lhs))]
            lines.append(', '.join(names)+' := '+self.s.render(rhs[0]))
            self.commit(targets,names,lines)
            return '\n'.join(lines)
        return self.statement(node,body)

    def multi_declaration(self,node):
        names,values=node.group('names'),node.group('values')
        if len(values)!=1 or len(names)<2 or not self.numeric_results(values[0],len(names)): return None
        global_binding=not any(a.kind=='Procedure' for a in node.ancestors())
        if not global_binding and not any(n.text=='_' for n in names): return None
        value=values[0]
        types=self.result_types(value,len(names))
        def body(result):
            locals=[self.s.prefix+f'big_multi_result_{node.start}_{i}' for i in range(len(names))]
            lines=[', '.join(locals)+' := '+self.s.render(value)]
            for name,local in zip(names,locals):
                if name.text=='_': lines.append(f'{self.module()}.compiler_destroy(&{local})')
            lines.append('return '+(', '.join(locals) if not global_binding else self.e.type_code(result)+'{'+', '.join(locals)+'}'))
            if global_binding:
                runtime=self.e.module('big_context','base:runtime')
                lines.insert(0,f'context = {runtime}.default_context()')
            return '\n'.join(lines)
        if global_binding:
            record='struct{'+','.join(f'value_{i}: {t}' for i,t in enumerate(types))+'}'
            self.e.switches.global_context+=1
            try: helper,args=self.e.closed(node,body,result=None,flavor='big_global_results')
            finally:self.e.switches.global_context-=1
            storage=self.s.prefix+f'big_globals_{node.start}'
            key=self.e.helper_scope(node),storage
            self.e.helpers[key]=self.s.mapping.wrap(f'\n@(private="file")\n{storage} := {helper}('+', '.join([*args,record])+')\n',
                                                  self.s.path,node.start,node.end,'expression-helper')
            replacement=', '.join(f'{storage}.value_{i}' for i in range(len(names)))
        else:
            helper,args=self.e.closed(node,body,result=(None,)*len(names),flavor='big_discard_results')
            replacement=helper+'('+', '.join([*args,*types])+')'
        self.busy.add(node)
        self.replacements[value]=replacement
        try:return self.s._render(node)
        finally:
            self.replacements.pop(value)
            self.busy.remove(node)

    def statement(self,node,builder):
        header=node.parent and node.parent.kind in {'For','If','Switch','Range'}
        if header:
            post=node.parent.role('post') is node
            name,args=self.e.closed(node,lambda _:builder(None)+('\nreturn true' if post else ''),result='bool' if post else False,flavor='big_assign')
            return ('_ = ' if post else '')+name+'('+', '.join(args)+')'
        return '{\n'+builder(None)+'\n}'

    def render(self,node,hint,constant):
        if node in self.replacements: return self.replacements[node]
        if not self.enabled or node in self.busy: return None
        if node.kind in {'OrReturn','OrBranch'}:
            optional=self.optional(node)
            if optional is not None:return optional
        if node.kind=='Call' and node.parent and node.parent.kind=='ExpressionStatement' and node not in self.e.ownership.discarding:
            signature=self.s.signature(node)
            count=len(self.s.fields(signature.role('results'))) if signature else 0
            if self.constructor(node) and any(self.shape(a.role('value') if a.kind=='FieldValue' else a).kind=='string' for a in node.group('args')):count=2
            if count>1 and self.numeric_results(node,count):
                def body(_):
                    names=[self.s.prefix+f'big_unused_{node.start}_{i}' for i in range(count)]
                    return ', '.join(names)+' := '+self.s.render(node)+'\n'+'\n'.join(f'{self.module()}.compiler_destroy(&{n})' for n in names)
                self.e.ownership.discarding.add(node)
                try:name,args=self.e.closed(node,body,result=False,flavor='big_discard_call')
                finally:self.e.ownership.discarding.remove(node)
                return name+'('+', '.join(args)+')'
        discard=self.e.ownership.discard(node)
        if discard is not None:return discard
        if node.kind=='OrElse':
            optional=self.optional(node)
            if optional is not None:return optional
        if node.kind=='Declaration' and node.data.get('mutable'):
            multi=self.multi_declaration(node)
            if multi is not None:return multi
        if node.kind=='DistinctType' and self.s.resolver.type_node(node.role('type')).kind in KINDS:
            return self.error(node,'Distinct Integer/Rational types are not supported yet; use a type alias.')
        if node.kind in {'MapType','SetType'}:
            key=node.role('key') if node.kind=='MapType' else node.group('args')[0]
            if self.s.resolver.type_node(key).kind in KINDS:
                return self.error(key,'Integer/Rational map keys and set elements are not supported yet.')
        if node.kind=='Ident' and not self.binding(node) and self.s.resolver.symbol(node) is None and node.text in {'Integer','Rational'}:
            return self.module()+'.'+node.text
        expected=self.expected(hint)
        if expected and expected.kind in KINDS and self.literal(node) is not None:
            return self.render_literal(node,expected.kind,constant)
        if expected and expected.kind=='generic' and self.literal(node) is not None:
            return self.generic_literal(node,hint,constant)
        target=self.constructor(node)
        if target: return self.render_constructor(node,target,constant)
        if node.kind=='Selector':
            prop=self.property(node)
            if prop:
                base=node.role('expr')
                if self.e.ownership.may_own(base):
                    def body(_):
                        lines=[]
                        value=self.e.ownership.evaluate(base,lines)
                        lines.append(f'defer {self.module()}.compiler_destroy_value({value.value})')
                        lines.append(f'return {self.module()}.{prop}({value.value})')
                        return '\n'.join(lines)
                    name,args=self.e.closed(node,body,result=self.module()+'.Integer',flavor='big_property')
                    return name+'('+', '.join(args)+')'
                base_code=self.s.render(base)
                if self.shape(base).kind=='pointer': base_code='('+base_code+')^'
                return f'{self.module()}.{prop}({base_code})'
            receiver=self.shape(node.role('expr'))
            while receiver.kind=='pointer' and receiver.elem:receiver=receiver.elem
            if receiver.kind in KINDS and node.role('field').text.startswith('_'):
                return self.error(node,'Integer/Rational backing storage is private; use their constructors and read-only properties.')
        if node.kind in {'Assignment','IncDec','Unary'}:
            targets=node.group('lhs') if node.kind=='Assignment' else [node.role('expr')] if node.kind in {'Unary','IncDec'} else []
            for value in targets:
                if value is None: continue
                from .semantics import descendants
                for child in descendants(value):
                    if self.property(child) and (node.kind!='Unary' or node.data['op']=='&'):
                        return self.error(child,'Rational numerator and denominator properties are read-only; use big.set_fraction to change both together.')
        if node.kind=='Assignment': return self.assignment(node)
        if node.kind=='Call':
            callee=node.role('callee'); args=node.group('args')
            if callee.kind=='Ident' and callee.text=='append' and self.s.resolver.symbol(callee) is None:
                appended=self.append(node)
                if appended is not None:return appended
            if callee.kind=='Ident' and callee.text=='delete' and self.s.resolver.symbol(callee) is None and len(args)==1 and self.shape(args[0]).kind in {*KINDS,'generic','unknown'}:
                arg=args[0]
                root=unwrap(arg)
                while root.kind in {'Selector','Index','Paren'}:root=root.role('expr')
                symbol=self.s.resolver.symbol(root)
                borrowed=symbol and symbol.declaration.kind in {'Field','Range','TypeSwitch'}
                if borrowed and self.e.addressable(arg) is not True:
                    error=self.error(arg,'This numeric value is borrowed; clone it before taking ownership or deleting it.')
                    if self.shape(arg).kind in KINDS:return error
                    def body(_):
                        value=self.s.render(arg)
                        return f'when type_of({value}) == {self.module()}.Integer || type_of({value}) == {self.module()}.Rational {{ _ = {error} }} else {{ delete({value}) }}'
                    name,inputs=self.e.closed(node,body,result=False,flavor='big_borrowed_delete')
                    return name+'('+', '.join(inputs)+')'
                n=unwrap(arg)
                if n.kind=='Index' and self.shape(n.role('expr')).kind=='map':
                    def body(_):
                        lines=[]
                        target=self.capture_targets([arg],lines,node)[0]
                        zero=self.s.prefix+f'big_deleted_{node.start}'
                        lines.append(f'{zero}: type_of({target})')
                        self.commit([target],[zero],lines)
                        return '\n'.join(lines)
                    name,inputs=self.e.closed(node,body,result=False,flavor='big_delete_map')
                    return name+'('+', '.join(inputs)+')'
                if self.e.addressable(arg) is True:
                    return f'{self.module()}.compiler_delete(&({self.e.address(arg)}))'
                return f'{self.module()}.compiler_destroy_value({self.s.render(arg)})'
            consumed=self.consume_regular_call(node)
            if consumed is not None: return consumed
        if node.kind=='Compound' and self.shape(node).kind in KINDS:
            if node.group('elems'): return self.error(node,'Construct Integer/Rational values with parentheses; backing fields are private.')
        return None

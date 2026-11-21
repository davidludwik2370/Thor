"""One-clause collection comprehensions with native scoped loop lowering."""
from contextlib import contextmanager

from .compiler import Hint
from .semantics import Type, binding_name, descendants


class Comprehensions:
    def __init__(self,source):
        self.s=source
        self.querying=0

    def module(self):return self.s.expressions.module('comprehension')

    def kind(self,node):
        annotation=node.role('type')
        return self.s.resolver.type_node(annotation).kind if annotation else 'slice'

    @staticmethod
    def interval(node):
        while node.kind=='Paren':node=node.role('expr')
        return node if node.kind=='Binary' and node.data['op'] in {'..','..<','..='} else None

    def bindings(self,node):
        from .expressions import Capture
        s=self.s;e=s.expressions;loop=node.role('loop');source=node.role('expr')
        interval=self.interval(source)
        if interval:
            annotation=loop.role('value_type')
            first=s.render(annotation) if annotation else f'type_of({s.prefix}default_value(true ? ({s.render(interval.role("left"))}) : ({s.render(interval.role("right"))})))'
            types=[first,'int']
        elif s.denotes_type(source):types=[s.render(source),'int']
        else:
            value=s.render(source)
            s.needs_intrinsics=True
            second=f'{self.module()}.second_enum({value}) when {s.prefix}meta.type_is_enumerated_array(type_of({value})) else {self.module()}.second({value})'
            types=[f'type_of({self.module()}.first({value}))',f'type_of({second})']
        shape=e.shape(source)
        while shape.kind=='pointer':shape=shape.elem
        if shape.kind=='array' and shape.node and shape.node.kind=='ArrayType':
            index=shape.node.role('len')
            if index and s.resolver.type_node(index).kind=='enum':types[1]=s.render(index)
        aliases={}
        for i,name in enumerate(loop.group('names')):
            symbol=s.resolver.by_decl.get((loop,binding_name(name)))
            if not symbol:continue
            annotation=loop.role('index_type' if i else 'value_type')
            typ=s.render(annotation) if annotation else types[i]
            value=f'({typ}){{}}'
            aliases[e.symbol_key(symbol)]=Capture(value)
        return aliases

    @contextmanager
    def query(self,node):
        s=self.s;s.needs_intrinsics=s.needs_default=True
        self.querying+=1
        try:
            aliases=self.bindings(node)
            s.expressions.captures.append(aliases)
            try:yield
            finally:s.expressions.captures.pop()
        finally:self.querying-=1

    def type_code(self,node,expected=None):
        s=self.s;annotation=node.role('type')
        if annotation:return s.render(annotation,constant=True)
        if expected and not expected.element_only:
            return s.expressions.type_code(expected)
        declaration=next((a for a in node.ancestors() if a.kind in {'Declaration','Procedure'}),None)
        if declaration and declaration.kind=='Declaration' and declaration.role('type'):
            container=s.resolver.type_node(declaration.role('type'))
            if container.kind in {'array','slice'}:
                s.needs_intrinsics=True
                return f'[]{s.prefix}meta.type_elem_type({s.render(declaration.role("type"))})'
        output=node.role('output')
        shape=s.resolver.type_of(output)
        if node.data.get('spread'):shape=shape.elem or Type()
        if output.kind in {'Literal','Interpolation'} and shape.kind in {'scalar','string'} and shape.nominal is None and shape.name:
            # A concrete primitive needs no lowered arithmetic/call witness.
            # This also keeps package-level constants independent of context.
            return '[]'+shape.name
        with self.query(node):
            # Native type_of still checks calling conventions. procedure_of
            # specializes a call without requiring a runtime context, including
            # calls nested in arithmetic in a package-level constant.
            replaced={}
            try:
                for call in reversed(list(descendants(node.role('output')))):
                    if call in s.run_values:continue
                    if call.kind=='Interpolation':
                        replaced[call]=None;s.run_values[call]='string{}'
                    elif call.kind=='Call' and s.resolver.resolve_call(call):
                        code=s.render(call)
                        replaced[call]=None
                        signature=s.signature(call)
                        operand,parent=call,call.parent
                        while parent and parent.kind=='Paren':operand,parent=parent,parent.parent
                        optional=(parent and parent.kind in {'OrElse','OrPanic','OrReturn','OrBranch'}
                                  and parent.role('x' if parent.kind=='OrElse' else 'expr') is operand)
                        count=len(s.fields(signature.role('results'))) if signature else 0
                        def result(index):
                            return f'({s.prefix}meta.type_proc_return_type(type_of({s.prefix}meta.procedure_of({code})),{index})){{}}'
                        s.run_values[call]=(f'{self.module()}.optional_pair({result(0)},{result(1)})'
                                            if optional and count==2 else result(0))
                output=s.render(node.role('output'))
            finally:
                for call in replaced:s.run_values.pop(call,None)
            if node.data.get('spread'):
                element=f'type_of({self.module()}.first({output}))'
            else:element=f'type_of({s.prefix}default_value({output}))'
        return '[]'+element

    def result_type(self,node,seen=None):
        annotation=node.role('type')
        if annotation:return self.s.resolver.type_node(annotation)
        value=self.s.resolver.type_of(node.role('output'),seen)
        return Type('array' if 'compile_length' in node.data else 'slice',node=node,
            elem=value.elem if node.data.get('spread') else value,length=node.data.get('compile_length'),witness=node)

    def render(self,node,expected=None,constant=False):
        s=self.s;e=s.expressions;loop=node.role('loop');source=node.role('expr')
        s.needs_intrinsics=s.needs_default=True
        kind=self.kind(node)
        if source.kind=='Call':
            signature=s.signature(source)
            if signature and len(s.fields(signature.role('results')))>1:
                return e.error(source,'A comprehension source must be a collection, enum or range; iterator procedures with multiple results are not supported.')
        global_scope=not(s.purity and s.purity.eval_mode) and not any(a.kind=='Procedure' for a in node.ancestors())
        if kind not in {'slice','dynamic','set','map'}:
            return e.error(node,'Comprehensions construct slices, dynamic arrays, sets or maps; omit the type for a compile-time fixed array.')
        key=node.role('key');spread=node.data.get('spread')
        if kind=='map' and not(key or spread):return e.error(node,'A map comprehension requires key = value or ..map.')
        if key and (kind!='map' or spread):return e.error(key,'Only map comprehensions use key = value; unpacking contributes complete entries.')
        target=self.type_code(node,expected)
        builder=s.prefix+f'comprehension_{node.start}'
        meta=s.prefix+'meta'
        module=self.module()
        names=loop.group('names')
        value_type,index_type=loop.role('value_type'),loop.role('index_type')
        interval=self.interval(source)
        if value_type and not interval:return e.error(value_type,'A typed value binding is supported only for numeric ranges; collection element types are inferred.')
        if index_type and interval:return e.error(index_type,'A typed index binding is supported only for arrays and slices, not numeric ranges.')
        if len(names)==2 and binding_name(names[0])==binding_name(names[1])!='_':return e.error(names[1],'Duplicate comprehension binding: '+binding_name(names[1]))
        def body(hint):
            result=e.type_code(hint)
            element=f'{meta}.type_elem_type({result})'
            code=[f'{builder}: [dynamic]{element}'] if kind in {'slice','dynamic'} else [f'{builder}: {result}']
            if global_scope:code.insert(0,f'context = {e.module("comprehension_runtime","base:runtime")}.default_context()')
            output=node.role('output')
            if kind in {'slice','dynamic'}:
                output_hint=Hint(s,output,element)
            elif kind=='set':output_hint=Hint(s,output,f'type_of({e.module("sets")}.element_witness({builder}))')
            else:output_hint=None
            def insert(value,key_code=None):
                if kind in {'slice','dynamic'}:return f'{module}.append_one(&{builder},{value})'
                if kind=='set':return f'{module}.set_insert(&{builder},{value})'
                return f'{module}.map_insert(&{builder},{key_code},{value})'
            if spread:
                part=builder+'_part';item=builder+'_item';index=builder+'_key'
                inner=[f'{part} := {s.render(output)}']
                sequence=f'({meta}.type_is_array(type_of({part})) || {meta}.type_is_slice(type_of({part})) || {meta}.type_is_dynamic_array(type_of({part})) || {meta}.type_is_fixed_capacity_dynamic_array(type_of({part})))'
                if kind=='map':
                    allowed=f'{meta}.type_is_map(type_of({part}))'
                    message='Map comprehension unpacking requires a map.'
                    iteration=f'for {index},{item} in {part} {{ {insert(item,index)} }}'
                elif kind=='set':
                    allowed=f'({sequence} || {e.set_trait(part)})'
                    message='Set comprehension unpacking requires an array, slice, dynamic array or set.'
                    iteration=f'for {item} in {e.module("sets")}.compiler_iterate({part}) {{ {insert(item)} }}'
                else:
                    allowed=sequence
                    message='Sequence comprehension unpacking requires an array, slice or dynamic array.'
                    iteration=f'for {item} in {part} {{ {insert(item)} }}'
                token=e.error(output,message)
                inner.append('{ when !'+allowed+' {_ = '+token+'} else {'+iteration+'} }')
                emitted='\n'.join(inner)
            elif kind=='map':
                # Bind key before value, preserving evaluation order even when
                # the key is a duplicate. Let native parameter types coerce literals.
                k=builder+'_key';v=builder+'_value'
                kt=f'type_of({module}.first({builder}))';vt=f'type_of({module}.second({builder}))'
                emitted=f'{k}: {kt} = {s.render(key,Hint(s,key,kt))}\n{v}: {vt} = {s.big.owned(output,Hint(s,output,vt))}\n{insert(v,k)}'
            else:emitted=insert(s.big.owned(output,output_hint))
            if node.role('cond'):
                emitted=f'if {s.render(node.role("cond"))} {{\n{emitted}\n}}'
            iterable=s.render(source)
            interval=self.interval(source)
            if interval:
                low=builder+'_low';high=builder+'_high'
                typ=loop.role('value_type')
                if typ:
                    code.extend([f'{low}: {s.render(typ)} = {s.render(interval.role("left"))}',f'{high}: {s.render(typ)} = {s.render(interval.role("right"))}'])
                else:
                    lo,hi=(s.render(interval.role(side)) for side in ['left','right'])
                    common=f'type_of({s.prefix}default_value(true ? ({lo}) : ({hi})))'
                    code.extend([f'{low}: {common} = {lo}',f'{high}: {common} = {hi}'])
                iterable=low+interval.data['op']+high
            elif not s.denotes_type(source) and not any(name.kind=='Unary' for name in names):
                input_name=builder+'_source'
                code.append(f'{input_name} := {iterable}')
                iterable=input_name
                if e.shape(source).kind=='set':iterable=f'{e.module("sets")}.entries({iterable})'
                elif e.shape(source).kind in {'unknown','generic'}:iterable=f'{e.module("sets")}.compiler_iterate({iterable})'
            if index_type:
                sequence=e.module('sequence');native=builder+'_index'
                code.append(f'{sequence}.validate_loop_index(type_of({iterable}),{s.render(index_type)})')
                emitted=f'{s.render(names[1])} := {sequence}.checked_loop_index({native},{s.render(index_type)})\n'+emitted
            bindings=', '.join(builder+'_index' if i==1 and index_type else s.copy(n.start,n.end) for i,n in enumerate(names))
            code.append(s.mapping.wrap(f'for {bindings} in {iterable} {{\n{emitted}\n}}',s.path,node.start,node.end,'comprehension-loop'))
            code.append(f'return {result}({module}.finish_slice(&{builder}))' if kind=='slice'
                        else f'return {result}({builder})' if kind=='dynamic' else f'return {builder}')
            return '\n'.join(code)
        name,args=e.closed(node,body,result=None,flavor='comprehension',contextless=global_scope)
        return name+'('+', '.join([*args,target])+')'

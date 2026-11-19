"""Same-type arithmetic for self-contained, fixed-size records."""
from .compiler import Hint
import json


BINARY = {'+','-','*','/','&','|','~','&~','<<','>>','**'}

def display_op(op):return '~' if op=='~unary' else op

def scalar(a,b,op,power):
    if op=='~unary':return f'(~({a}))'
    if op=='**':return f'{power}.power({a},{b})'
    return f'(({a}) {op} ({b}))'

def struct_error(source, node, message):
    token=source.expressions.error(node,message)
    if not hasattr(source,'expression_error_sites'):source.expression_error_sites={}
    source.expression_error_sites[token]=node
    return token


def dispatch_condition(source,typ):
    e=source.expressions;meta=source.prefix+'meta'
    condition=f'{meta}.type_is_struct({typ})'
    if source.big.enabled:
        big=e.module('big');condition+=f' && {typ}!={big}.Integer && {typ}!={big}.Rational'
    # Preserve map-backed Set dispatch without importing otherwise unused code.
    condition+=f' && !({meta}.type_is_map({meta}.type_field_type({typ},"table")) when {meta}.type_has_field({typ},"table") else false)'
    return condition


def generic_helper(source, op):
    expressions=source.expressions
    suffix={'+':'add','-':'sub','*':'mul','/':'div','&':'and','|':'or','~':'xor','&~':'and_not','<<':'shl','>>':'shr','**':'power','~unary':'complement'}[op]
    name=source.prefix+'struct_generic_'+suffix
    key=source.root,name
    if key in expressions.helpers:return name
    names=sorted({name.text for s in source.resolver.sources for record in s.nodes if record.kind=='StructType'
                  for field in (record.role('fields') or record).children if field.kind=='Field'
                  for name in field.group('names') if name.text!='_'})
    source.needs_intrinsics=True
    meta=source.prefix+'meta'
    def direct(field):
        return f'({meta}.type_has_shared_fields(T,struct{{{field}:{meta}.type_field_type(T,{json.dumps(field)})}}) when {meta}.type_has_field(T,{json.dumps(field)}) else false)'
    def count(items):
        if not items:return '0'
        if len(items)==1:return f'(1 when {direct(items[0])} else 0)'
        mid=len(items)//2
        return '('+count(items[:mid])+'+'+count(items[mid:])+')'
    kinds={'**':['integer','float'],'<<':['integer'],'>>':['integer'],'&~':['integer','enum','bit_set','simd_vector'],'~unary':['integer','boolean','bit_set','simd_vector'],'&':['integer','boolean','enum','bit_set','simd_vector'],'|':['integer','boolean','enum','bit_set','simd_vector'],'~':['integer','boolean','enum','bit_set','simd_vector']}.get(op,['integer','float','complex','quaternion','enum','matrix','simd_vector','bit_set'])
    numeric=lambda t:'||'.join(f'{meta}.type_is_{kind}({t})' for kind in kinds)
    raw_layouts=sorted({tuple(name.text for field in (record.role('fields') or record).children
                             if field.kind=='Field' for name in field.group('names'))
                        for s in source.resolver.sources for record in s.nodes
                        if record.kind=='StructType' and record.data.get('op')=='raw_union'})
    raw_checks=[]
    for fields in raw_layouts:
        if '_' in fields:continue
        predicate=' && '.join(direct(field) for field in fields)
        schema=','.join(f'{field}:{meta}.type_field_type(T,{json.dumps(field)})' for field in fields)
        check=f'when {meta}.type_base_type(T)==struct #raw_union #align(align_of(T)){{{schema}}}{{#panic(Site)}}\n'
        raw_checks.append(f'when {predicate}{{{check}}}\n' if predicate else check)
    raw_checks=''.join(raw_checks)
    from . import recordpadding
    builtin=expressions.module('builtin','base:builtin')
    padding_code,padding_count,_=recordpadding.count(source.resolver.sources,meta,builtin,name,cache=source)
    unary=op=='~unary'
    power=expressions.module('numeric') if op=='**' else ''
    args='a:$T,$Site:string' if unary else 'a:$T,b:$U,$Site:string'
    check='' if unary else 'when T!=U {#panic(Site)} else '
    operands=lambda a,b: f'{a},Site' if unary else f'{a},{b},Site'
    array_native='' if op in {'**','<<','>>'} else f'when {numeric(meta+".type_elem_type(T)")} {{return {scalar("a","b",op,power)}}} else '
    fields=[]
    for field in names:
        call=f'result.{field}={name}({operands("a."+field,"b."+field)})'
        # A standalone bool parameter normalizes its byte; retain the native
        # field complement rather than passing the field through that boundary.
        operation=f'when {meta}.type_is_boolean({builtin}.type_of(a.{field})) {{result.{field}={scalar("a."+field,"b."+field,op,power)}}} else {{{call}}}' if op in {'~unary','&','|','~'} else call
        fields.append(f'when {direct(field)} {{{operation}}}\n')
    fields=''.join(fields)
    code=f'''\n{name}::#force_inline proc "contextless"({args})->T {{
{check}when {meta}.type_is_struct(T) {{
    {raw_checks}
    when {meta}.type_struct_field_count(T)!=({count(names)}) {{
        {padding_code}
        when {meta}.type_struct_field_count(T)!=({count(names)})+{padding_count} {{#panic(Site)}}
    }}
    {{
    result:T
    {fields}
    return result
    }}
}} else when {meta}.type_is_array(T)||{meta}.type_is_enumerated_array(T) {{
    {array_native}{{result:T;when {builtin}.len(T{{}})==0 {{return result}};for _,i in result {{result[i]={name}({operands('a[i]','b[i]')})}};return result}}
}} else when {numeric('T')} {{return {scalar('a','b',op,power)}}}
else {{#panic(Site)}}
}}\n'''
    expressions.helpers[key]=source.mapping.wrap(code,source.path,0,0,'expression-helper')
    return name


def generic_render(source,node,a,b,compound,expected):
    e=source.expressions
    op='~unary' if node.kind=='Unary' else node.data['op'].rstrip('=')
    helper=generic_helper(source,op)
    token=struct_error(source,node,f"Cannot apply '{display_op(op)}' to struct: operands must have the same type and every named field must be a self-contained fixed-size value supporting '{display_op(op)}'; pointers, slices, strings, dynamic containers, Integer and Rational are excluded.")
    typ=e.ownership.native_type(a)
    condition=dispatch_condition(source,typ)
    if compound:
        def body(_):
            typ=e.ownership.native_type(a)
            condition=dispatch_condition(source,typ)
            source.struct_math_busy.add(node)
            try:native=source.render(node,expected,False)
            finally:source.struct_math_busy.remove(node)
            lines=[]
            target=source.big.capture_targets([a],lines,node)[0]
            right=source.prefix+f'struct_rhs_{node.start}'
            hint=Hint(source,a,f'type_of({target})',source.resolver.type_of(a))
            expr=source.render(b,hint)
            inner=b
            while inner.kind=='Paren':inner=inner.role('expr')
            if inner.kind=='Compound' and not inner.role('type') and not source.is_array(inner):expr=f'type_of({target})'+source.render(inner,hint)
            lines.extend([f'{right}:='+expr,f'{target}={helper}({target},{right},{json.dumps(token)})'])
            return f'when {condition} {{\n'+ '\n'.join(lines)+f'\n}} else {{\n{native}\n}}'
        return source.big.statement(node,body)
    source.struct_math_busy.add(node)
    try:native=source.render(node,expected,False)
    finally:source.struct_math_busy.remove(node)
    hint=Hint(source,a,typ,source.resolver.type_of(a))
    right='' if op=='~unary' else ','+source.render(b,hint)
    inner=b
    while inner.kind=='Paren':inner=inner.role('expr')
    if op!='~unary' and inner.kind=='Compound' and not inner.role('type') and not source.is_array(inner):right=','+typ+source.render(inner,hint)
    return f'({helper}({source.render(a)}{right},{json.dumps(token)}) when {condition} else {native})'


def shape(resolver, typ, source, site, op, path='', depth=0):
    if depth > 64:
        raise ValueError(f"field '{path}' exceeds the supported nesting depth")
    if typ.kind == 'array' and isinstance(typ.length, int):
        element = shape(resolver, typ.elem, source, site, op, path+'[]', depth+1)
        return None if element is None and op not in {'**','<<','>>'} else ('array', typ.length, element)
    if typ.kind=='scalar' and typ.name=='bool' and op in {'&','|','~','~unary'}:return None
    if typ.kind=='scalar' and (op in {'&','|','~','&~','<<','>>','~unary'} and typ.name.startswith('f') or op=='**' and typ.name.startswith(('complex','quaternion'))):
        raise ValueError(f"field '{path}' does not support '{display_op(op)}'")
    if typ.kind in {'enum','bitset'} and (op=='**' or typ.kind=='enum' and op=='~unary'):
        raise ValueError(f"field '{path}' does not support '{display_op(op)}'")
    if typ.kind in {'enum','bitset'}:
        return None
    if typ.kind in {'generic','unknown'} or typ.kind=='array' and typ.length is None:
        helper=generic_helper(source,op)
        token=struct_error(source,site,f"Cannot apply '{display_op(op)}' to struct: field '{path}' must be a self-contained fixed-size value supporting '{display_op(op)}'; reference-containing values, Integer and Rational are excluded.")
        return ('deferred',helper,json.dumps(token))
    if typ.kind == 'scalar' and (typ.name in {'int','uint','uintptr','rune'}
            or typ.name.startswith(('i','u','f','complex','quaternion')) and any(c.isdigit() for c in typ.name)):
        return None
    if typ.kind == 'struct' and not typ.soa and typ.node and typ.node.kind == 'StructType' and typ.node.data.get('op') != 'raw_union':
        result = []
        for field in (typ.node.role('fields') or typ.node).children:
            if field.kind != 'Field': continue
            field_type = resolver.type_node(field.role('type'), typ.substitutions)
            for name in field.group('names'):
                name = name.text
                if name=='_':continue
                member = shape(resolver, field_type, source, site, op, path+'.'+name if path else name, depth+1)
                result.append((name, member))
        return ('record', result)
    raise ValueError(f"field '{path}' is not a self-contained fixed-size arithmetic value; pointers, slices, strings, dynamic containers, Integer and Rational are excluded")


def value(layout, a, b, op, constant, statements, serial, power):
    if layout is None: return scalar(a,b,op,power)
    if layout[0]=='deferred':return f'{layout[1]}({a},'+('' if op=='~unary' else b+',')+f'{layout[2]})'
    if layout[0] == 'record':
        fields = ','.join(name+'='+value(child, f'({a}).{name}', f'({b}).{name}', op, constant, statements, serial, power)
                          for name, child in layout[1])
        return f'type_of({a}){{{fields}}}'
    _, length, child = layout
    if constant:
        fields = ','.join(value(child, f'({a})[{i}]', f'({b})[{i}]', op, True, statements, serial, power) for i in range(length))
        return f'type_of({a}){{{fields}}}'
    name = f'struct_array_{serial[0]}'
    serial[0] += 1
    index = name+'_i'
    body = []
    result = value(child, f'({a})[{index}]', f'({b})[{index}]', op, False, body, serial, power)
    statements.append(f'{name}:type_of({a})\nfor {index} in 0..<{length} {{\n'+''.join(body)+f'{name}[{index}]={result}\n}}\n')
    return name


def value_annotation(node):
    parent=node.parent
    while parent and parent.kind=='Paren':parent=parent.parent
    if parent and parent.kind in {'Declaration','Field'}:return parent.role('type')
    if parent and parent.kind=='Return':
        procedure=next((node for node in parent.ancestors() if node.kind=='Procedure'),None)
        results=procedure.role('type').role('results') if procedure else None
        fields=[field for field in results.children if field.kind=='Field'] if results else []
        if len(fields)==1 and len(fields[0].group('names'))<=1:return fields[0].role('type')
    return None


def hint_type(hint):
    if hint.semantic and hint.semantic.kind not in {'unknown','generic'}:return hint.semantic
    resolver=hint.source.resolver
    typ=resolver.type_node(hint.node)
    return resolver.type_of(hint.node) if typ.kind in {'unknown','generic'} else typ


def render(source, node, expected, constant):
    if not source.thor or not source.resolver: return None
    if not hasattr(source,'struct_math_busy'):source.struct_math_busy=set()
    if node in source.struct_math_busy:return None
    compound = node.kind == 'Assignment' and node.data['op'].endswith('=') and node.data['op'][:-1] in BINARY
    unary=node.kind=='Unary' and node.data['op']=='~'
    if not compound and not unary and not (node.kind == 'Binary' and node.data['op'] in BINARY): return None
    if unary:a=b=node.role('expr')
    elif compound:
        lhs, rhs = node.group('lhs'), node.group('rhs')
        if len(lhs) != 1 or len(rhs) != 1: return None
        a, b = lhs[0], rhs[0]
    else: a, b = node.role('left'), node.role('right')
    resolver, expressions = source.resolver, source.expressions
    if expected is None and node.data['op']=='**':
        annotation=value_annotation(node)
        if annotation and resolver.type_node(annotation).kind=='struct':expected=Hint(source,annotation,source.render(annotation,constant=True),resolver.type_node(annotation))
    if node.data['op']=='**' and node.parent and (node.parent.kind=='Call' or node.parent.kind=='FieldValue' and node.parent.parent.kind=='Call') and resolver.type_of(a).kind in {'unknown','generic'} and resolver.type_of(b).kind in {'unknown','generic'}:
        argument=node.parent if node.parent.kind=='FieldValue' else node
        call=argument.parent if argument.kind=='FieldValue' else node.parent
        plan=resolver.resolve_call(call)
        if plan:
            _,_,annotation,_=source.big.parameter(call,argument,call.group('args').index(argument))
            if annotation:
                typ=resolver.type_node(annotation,plan.substitutions)
                if typ.kind=='struct':expected=Hint(annotation.source,annotation,semantic=typ)
    at, bt = resolver.type_of(a), resolver.type_of(b)
    anchor = a if at.kind == 'struct' else b if not compound and bt.kind == 'struct' else None
    if anchor:
        typ = resolver.type_of(anchor)
        type_code = f'type_of({source.render(anchor, constant=constant)})'
    elif expected and hint_type(expected).kind == 'struct':
        typ = hint_type(expected)
        type_code = expressions.type_code(expected)
    else:
        if not constant and at.kind in {'unknown','generic'} and not source.denotes_type(a):
            return generic_render(source,node,a,b,compound,expected)
        return None
    op = '~unary' if unary else node.data['op'].rstrip('=')
    power=expressions.module('numeric') if op=='**' else ''
    try: layout = shape(resolver, typ, source, node, op)
    except ValueError as error: return expressions.error(node, f"Cannot apply '{display_op(op)}' to struct: {error}.")
    hint = Hint(source, anchor or a, type_code, typ, constant_literal=constant)
    scope = expressions.helper_scope(node)
    name = source.prefix+f'struct_math_{node.start}_{node.end}'

    def helper():
        statements = []
        result = value(layout, 'a', 'b', op, False, statements, [0], power)
        mismatch=struct_error(source,node,'Struct arithmetic requires operands of the same type.')
        args='a:$T' if unary else 'a:$T,b:$U'
        check='' if unary else f'when T!=U {{#panic({json.dumps(mismatch)})}} else '
        expressions.helpers[scope,name] = source.mapping.wrap(
            f'\n{name} :: #force_inline proc "contextless"({args})->T {{\n{check}{{\n'+''.join(statements)+f'return {result}\n}}\n}}\n',
            source.path, node.start, node.end, 'expression-helper')
    if compound:
        helper()
        def body(_):
            lines = []
            target = source.big.capture_targets([a], lines, node)[0]
            right = source.prefix+f'struct_rhs_{node.start}'
            expr=source.render(b,hint)
            inner=b
            while inner.kind=='Paren':inner=inner.role('expr')
            if inner.kind=='Compound' and not inner.role('type') and not source.is_array(inner):expr=f'type_of({target})'+source.render(inner,hint)
            lines.extend([f'{right}:='+expr, f'{target}={name}({target},{right})'])
            return '\n'.join(lines)
        return source.big.statement(node, body)

    def operand(expr):
        inner = expr
        while inner.kind == 'Paren': inner = inner.role('expr')
        literal = inner.kind == 'Compound' and not inner.role('type') and not source.is_array(inner)
        code = source.render(inner if literal else expr, hint, constant)
        return type_code+code if literal else code
    left, right = operand(a), '' if unary else operand(b)
    if constant:
        name = source.prefix+f'struct_constant_{node.start}_{node.end}'
        result = value(layout, name+'_a', name+'_b', op, True, [], [0], power)
        mismatch = expressions.error(b, 'Struct arithmetic requires operands of the same type.')
        if not unary:result = f'({result} when type_of({name}_a)==type_of({name}_b) else {mismatch})'
        right_alias='' if unary else f'{name}_b :: {right}\n'
        expressions.helpers[scope,name] = source.mapping.wrap(
            f'\n{name}_a :: {left}\n{right_alias}{name} :: {result}\n',
            source.path, node.start, node.end, 'expression-helper')
        return name
    helper()
    return f'{name}({left}'+('' if unary else ','+right)+')'


def power_assignment(source,node):
    lhs,rhs=node.group('lhs'),node.group('rhs')
    if len(lhs)!=1 or len(rhs)!=1:return source.expressions.error(node,'Power assignment requires one destination and one exponent.')
    def body(_):
        lines=[]
        target=source.big.capture_targets(lhs,lines,node)[0]
        right=source.prefix+f'power_rhs_{node.start}'
        hint=Hint(source,lhs[0],f'type_of({target})',source.resolver.type_of(lhs[0]))
        result=source.render(rhs[0],hint)
        module=source.expressions.module('numeric')
        lines.extend([f'{right}:type_of({target})={result}',f'{target}={module}.power({target},{right})'])
        return '\n'.join(lines)
    return source.big.statement(node,body)

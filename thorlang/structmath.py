"""Same-type arithmetic for self-contained, fixed-size records."""
from .compiler import Hint
import json


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
    suffix={'+':'add','-':'sub','*':'mul','/':'div'}[op]
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
    numeric=lambda t:'||'.join(f'{meta}.type_is_{kind}({t})' for kind in ['integer','float','complex','quaternion','enum','matrix','simd_vector','bit_set'])
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
    fields=''.join(f'when {direct(field)} {{result.{field}={name}(a.{field},b.{field},Site)}}\n' for field in names)
    code=f'''\n{name}::#force_inline proc "contextless"(a:$T,b:$U,$Site:string)->T {{
when T!=U {{#panic(Site)}} else when {meta}.type_is_struct(T) {{
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
    when {numeric(meta+'.type_elem_type(T)')} {{return a {op} b}}
    else {{result:T;for i in 0..<len(result){{result[i]={name}(a[i],b[i],Site)}};return result}}
}} else when {numeric('T')} {{return a {op} b}}
else {{#panic(Site)}}
}}\n'''
    expressions.helpers[key]=source.mapping.wrap(code,source.path,0,0,'expression-helper')
    return name


def generic_render(source,node,a,b,compound,expected):
    e=source.expressions
    op=node.data['op'].rstrip('=')
    helper=generic_helper(source,op)
    token=struct_error(source,node,f"Cannot apply '{op}' to struct: operands must have the same type and every named field must be a self-contained fixed-size value supporting '{op}'; pointers, slices, strings, dynamic containers, Integer and Rational are excluded.")
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
    right=source.render(b,hint)
    inner=b
    while inner.kind=='Paren':inner=inner.role('expr')
    if inner.kind=='Compound' and not inner.role('type') and not source.is_array(inner):right=typ+source.render(inner,hint)
    return f'({helper}({source.render(a)},{right},{json.dumps(token)}) when {condition} else {native})'


def shape(resolver, typ, source, site, op, path='', depth=0):
    if depth > 64:
        raise ValueError(f"field '{path}' exceeds the supported nesting depth")
    if typ.kind == 'array' and isinstance(typ.length, int):
        element = shape(resolver, typ.elem, source, site, op, path+'[]', depth+1)
        return None if element is None else ('array', typ.length, element)
    if typ.kind in {'enum','bitset'}:
        return None
    if typ.kind in {'generic','unknown'} or typ.kind=='array' and typ.length is None:
        helper=generic_helper(source,op)
        token=struct_error(source,site,f"Cannot apply '{op}' to struct: field '{path}' must be a self-contained fixed-size value supporting '{op}'; reference-containing values, Integer and Rational are excluded.")
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


def value(layout, a, b, op, constant, statements, serial):
    if layout is None: return f'(({a}) {op} ({b}))'
    if layout[0]=='deferred':return f'{layout[1]}({a},{b},{layout[2]})'
    if layout[0] == 'record':
        fields = ','.join(name+'='+value(child, f'({a}).{name}', f'({b}).{name}', op, constant, statements, serial)
                          for name, child in layout[1])
        return f'type_of({a}){{{fields}}}'
    _, length, child = layout
    if constant:
        fields = ','.join(value(child, f'({a})[{i}]', f'({b})[{i}]', op, True, statements, serial) for i in range(length))
        return f'type_of({a}){{{fields}}}'
    name = f'struct_array_{serial[0]}'
    serial[0] += 1
    index = name+'_i'
    body = []
    result = value(child, f'({a})[{index}]', f'({b})[{index}]', op, False, body, serial)
    statements.append(f'{name}:type_of({a})\nfor {index} in 0..<{length} {{\n'+''.join(body)+f'{name}[{index}]={result}\n}}\n')
    return name


def render(source, node, expected, constant):
    if not source.thor or not source.resolver: return None
    if not hasattr(source,'struct_math_busy'):source.struct_math_busy=set()
    if node in source.struct_math_busy:return None
    compound = node.kind == 'Assignment' and node.data['op'] in {'+=','-=','*=','/='}
    if not compound and not (node.kind == 'Binary' and node.data['op'] in {'+','-','*','/'}): return None
    if compound:
        lhs, rhs = node.group('lhs'), node.group('rhs')
        if len(lhs) != 1 or len(rhs) != 1: return None
        a, b = lhs[0], rhs[0]
    else: a, b = node.role('left'), node.role('right')
    resolver, expressions = source.resolver, source.expressions
    at, bt = resolver.type_of(a), resolver.type_of(b)
    anchor = a if at.kind == 'struct' else b if not compound and bt.kind == 'struct' else None
    if anchor:
        typ = resolver.type_of(anchor)
        type_code = f'type_of({source.render(anchor, constant=constant)})'
    elif expected and (expected.semantic or resolver.type_node(expected.node)).kind == 'struct':
        typ = expected.semantic or resolver.type_node(expected.node)
        type_code = expressions.type_code(expected)
    else:
        if not constant and at.kind in {'unknown','generic'} and not source.denotes_type(a):
            return generic_render(source,node,a,b,compound,expected)
        return None
    op = node.data['op'].rstrip('=')
    try: layout = shape(resolver, typ, source, node, op)
    except ValueError as error: return expressions.error(node, f"Cannot apply '{op}' to struct: {error}.")
    hint = Hint(source, anchor or a, type_code, typ, constant_literal=constant)
    scope = expressions.helper_scope(node)
    name = source.prefix+f'struct_math_{node.start}_{node.end}'

    def helper():
        statements = []
        result = value(layout, 'a', 'b', op, False, statements, [0])
        mismatch=struct_error(source,node,'Struct arithmetic requires operands of the same type.')
        expressions.helpers[scope,name] = source.mapping.wrap(
            f'\n{name} :: #force_inline proc "contextless"(a:$T,b:$U)->T {{\nwhen T!=U {{#panic({json.dumps(mismatch)})}} else {{\n'+''.join(statements)+f'return {result}\n}}\n}}\n',
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
    left, right = operand(a), operand(b)
    if constant:
        name = source.prefix+f'struct_constant_{node.start}_{node.end}'
        result = value(layout, name+'_a', name+'_b', op, True, [], [0])
        mismatch = expressions.error(b, 'Struct arithmetic requires operands of the same type.')
        result = f'({result} when type_of({name}_a)==type_of({name}_b) else {mismatch})'
        expressions.helpers[scope,name] = source.mapping.wrap(
            f'\n{name}_a :: {left}\n{name}_b :: {right}\n{name} :: {result}\n',
            source.path, node.start, node.end, 'expression-helper')
        return name
    helper()
    return f'{name}({left},{right})'

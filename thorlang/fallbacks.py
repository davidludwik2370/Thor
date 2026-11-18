"""Typed multi-value or_else defaults, without runtime tuples or closures."""
from .compiler import Hint


def unwrap(node):
    while node and node.kind=='Paren':node=node.role('expr')
    return node


def discarded(node):
    node=unwrap(node)
    return bool(node and node.kind=='Ident' and node.text=='_')


def result_types(source,value,count):
    source.needs_intrinsics=True
    source.expressions.fstrings.querying+=1
    try:code=source.render(value)
    finally:source.expressions.fstrings.querying-=1
    name=source.prefix+f'result_shape_{value.start}_{count}'
    params=','.join(f'_:$T{i}' for i in range(count))
    fields=','.join(f'v{i}:T{i}' for i in range(count))
    helper=f'\n{name}::proc "contextless"({params})->struct{{{fields}}}{{return {{}}}}\n'
    source.expressions.helpers[source.expressions.helper_scope(value),name]=source.mapping.wrap(helper,source.path,value.start,value.end,'result-type')
    return tuple(f'{source.prefix}meta.type_field_type(type_of({name}({code})),"v{i}")' for i in range(count))


def optional_count(resolver,node):
    node=unwrap(node)
    return 2 if node.kind in {'Index','TypeAssertion'} else count(resolver,node)


def count(resolver,node):
    node=unwrap(node)
    if node.kind in {'FallbackList','SwitchResults'}:return len(node.group('args'))
    if node.kind in {'OrElse','OrPanic','OrReturn','OrBranch'}:
        n=optional_count(resolver,node.role('x' if node.kind=='OrElse' else 'expr'))
        return max(0,n-1) if n is not None else None
    if node.kind=='Call':
        plan=resolver.resolve_call(node)
        return len(resolver.fields(plan.signature.role('results'))) if plan else None
    if node.kind in {'SwitchExpr','CompileSwitchExpr'}:
        counts=[count(resolver,node.source.at(case['result'],node)) for case in node.data['cases']]
        return max((n for n in counts if n is not None),default=1)
    if node.kind=='Unary' and node.data.get('op')=='**':
        typ=resolver.type_of(node.role('expr'))
        if typ.kind=='array':return typ.length if isinstance(typ.length,int) else None
        return len(typ.members) if typ.members else len(resolver.fields(typ.node.role('fields'))) if typ.node else None
    return 1


def targets(resolver,node,n):
    empty=[None]*n;offset=0
    while node.parent:
        parent=node.parent
        if parent.kind in {'Paren','OrElse','OrPanic','OrReturn','OrBranch'}:node=parent;continue
        if parent.kind in {'Ternary','WhenExpr'} and node in (parent.role('x'),parent.role('y')):node=parent;continue
        if parent.kind in {'SwitchExpr','CompileSwitchExpr'}:
            if not any(parent.source.at(case['result'],parent) is node for case in parent.data['cases']):return empty
            node=parent;continue
        if parent.kind in {'Declaration','Assignment','SwitchResults'}:
            values=parent.group({'Declaration':'values','Assignment':'rhs','SwitchResults':'args'}[parent.kind])
            if node not in values:return empty
            for value in values[:values.index(node)]:
                size=count(resolver,value)
                if size is None:return empty
                offset+=size
            if parent.kind=='SwitchResults':node=parent;continue
            assigned=parent.group('names' if parent.kind=='Declaration' else 'lhs')
            return [assigned[offset+i] if offset+i<len(assigned) else None for i in range(n)]
        return empty
    return empty


def render(source,node,constant):
    values=node.group('args')
    if len(values)<2:source.error('An or_else fallback list requires at least two values.',node.start,node.end)
    child=node
    while child.parent and child.parent.kind=='Paren':child=child.parent
    owner=child.parent
    if not owner or owner.kind!='OrElse' or owner.role('y') is not child:
        source.error('A fallback list must be the right-hand operand of or_else.',node.start,node.end)
    left=owner.role('x')
    size=optional_count(source.resolver,left)
    if size is not None and size-1!=len(values):
        source.error(f'or_else fallback list has {len(values)} values; the operation has {max(0,size-1)} successful results (excluding its status).',node.start,node.end)
    assigned=targets(source.resolver,owner,len(values))
    for i,(value,target) in enumerate(zip(values,assigned)):
        if discarded(value)!=discarded(target):
            message=(f"Fallback position {i+1} uses '_', but its result has no matching discarded assignment target." if discarded(value)
                else f"Fallback position {i+1} is discarded; use '_' to match its assignment target.")
            source.error(message,value.start,value.end)
    name=source.prefix+'fallback_shape_'+str(node.start)
    params=','.join([*(f'_:$T{i}' for i in range(len(values))),'_:$Status'])
    fields=','.join(f'v{i}:T{i}' for i in range(len(values)))
    helper=f'\n{name}::proc "contextless"({params})->struct{{{fields}}}{{return {{}}}}\n'
    source.expressions.helpers[source.expressions.helper_scope(node),name]=source.mapping.wrap(helper,source.path,node.start,node.end,'fallback-type')
    source.expressions.fstrings.querying+=1
    try:witness=source.render(left)
    finally:source.expressions.fstrings.querying-=1
    typ=f'type_of({name}({witness}))'
    initializers=[]
    for i,value in enumerate(values):
        if discarded(value):continue
        source.needs_intrinsics=True
        hint=Hint(source,value,f'{source.prefix}meta.type_field_type({typ},"v{i}")',semantic=source.resolver.result_type(left,i))
        initializers.append(f'v{i}='+source.render(value,hint,constant))
    return 'expand_values('+typ+'{'+','.join(initializers)+'})'


def render_call(source,node,expected,constant):
    right=unwrap(node.role('y'))
    if right.kind!='Call' or source.resolver.is_diverging_call(right) or (count(source.resolver,right) or 0)<2:return None
    signature=source.signature(right)
    if signature and any(signature.data.get(flag) for flag in ('optional_ok','optional_allocator_error')):return None
    left=source.render(node.role('x'),expected,constant)
    fallback=source.render(node.role('y'),expected,constant)
    return '('+left+' or_else expand_values(compress_values('+fallback+')))'

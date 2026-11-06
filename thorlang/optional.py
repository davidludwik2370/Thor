"""Single-evaluation optional unwrapping with original-source panic locations."""
import json


def render(e, node):
    s = e.source
    primary = node.role('expr')
    while primary.kind == 'Paren':
        primary = primary.role('expr')
    module = e.module('optional')
    count = 1
    if primary.kind == 'Call':
        # Arity belongs to the called signature, including SDK procedures with
        # more than one successful result. Inspect source facts on demand;
        # native SDK files themselves remain ordinary Odin build inputs.
        enabled = s.resolver.native_enabled
        s.resolver.native_enabled = True
        try:
            signature = s.signature(primary)
        finally:
            s.resolver.native_enabled = enabled
        if signature:
            count = max(0, len(s.fields(signature.role('results'))) - 1)
        if e.extrema.plan(primary):
            count = 1
        if s.big.constructor(primary) and any(e.shape(a).kind == 'string' for a in primary.group('args')):
            count = 1
    # The native or_else query preserves optional promotion, overload selection,
    # and generic result inference without executing the operand a second time.
    e.ownership.querying += 1
    try:
        query = s.render(primary)
    finally:
        e.ownership.querying -= 1
    types = ([f'type_of({query} or_else {module}.failure_contextless(false,"",{{}}))'] if count == 1
             else list(s.big.result_types(primary, count)) if count else [])
    # Store source bytes, not generated caller_expression/caller_location values.
    line = s.raw[:node.start].count(b'\n') + 1
    column = len(s.raw[:node.start].rsplit(b'\n', 1)[-1].decode('utf-8')) + 1
    owner = next((a for a in node.ancestors() if a.kind == 'Procedure'), None)
    declaration = owner.parent if owner else None
    names = declaration.group('names') if declaration and declaration.kind == 'Declaration' else []
    procedure = names[0].text if names else '<anonymous>' if owner else '<global>'
    summary = 'unexpected failure\n  expression: ' + primary.text + '\n  procedure: ' + procedure
    location = '{' + ','.join([json.dumps(str(s.path), ensure_ascii=False), str(line), str(column), json.dumps(procedure, ensure_ascii=False)]) + '}'
    if primary.kind == 'Call' and primary.role('callee').text == '#load':
        # Odin exposes #load's optional status only through or_else, not tuple
        # assignment. Preserve its native constant-folding and missing-file
        # behavior rather than introducing a runtime wrapper.
        fail = 'failure' if owner is not None and e.has_context(node) else 'failure_contextless'
        extra = ', false' if fail == 'failure' else ''
        return f'({query} or_else {module}.{fail}(false, {json.dumps(summary, ensure_ascii=False)}, {location}{extra}))'
    prefix = s.prefix + f'optional_{node.start}_'
    values = [prefix + str(i) for i in range(count)]
    status = prefix + 'status'

    def body(_):
        code = [', '.join([*values, status]) + ' := ' + s.render(primary)]
        fail = 'failure' if e.has_context(node) else 'failure_contextless'
        extra = ', false' if fail == 'failure' else ''
        code.append(f'if !{module}.succeeded({status}) {{ {module}.{fail}({status}, {json.dumps(summary, ensure_ascii=False)}, {location}{extra}) }}')
        if s.big.enabled:
            # Direct logical-number copies own their storage; aggregates retain
            # the ordinary shallow-copy contract. A call transfers its results.
            fresh = s.big.fresh(primary)
            values_out = [f'{s.big.module()}.compiler_own({value},{str(fresh).lower()})' for value in values]
        else:
            values_out = values
        if values_out:
            code.append('return ' + ', '.join(values_out))
        return '\n'.join(code)

    result = False if not count else None if count == 1 else (None,) * count
    name, args = e.closed(node, body, result=result, flavor='or_panic')
    return s.mapping.wrap(name + '(' + ', '.join([*args, *types]) + ')',
                          s.path, node.start, node.end, 'or-panic')

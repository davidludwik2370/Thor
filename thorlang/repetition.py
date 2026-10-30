"""Compile-time string repetition using native constant concatenation.

Keep decoding, constant eligibility, generic instantiation and byte semantics in
Odin. A small doubling chain avoids expanding the source once per repetition.
"""
from dataclasses import replace


LIMIT = 16 * 1024 * 1024  # Match the existing compile-time value budget.


def empty_value(source, value):
    # A constant string slice retains native typed/untyped literal behavior.
    # Slicing a distinct string drops its named type, so restore that type only
    # for named strings. Ask Odin about the actual type, including conditional
    # operands and concatenations whose right operand determines their type.
    typ = f'type_of({source.prefix}default_value({value}))'
    return f'(({value})[:0] when {typ} == string else {typ}(""))'


def operands(node):
    if not node.source.thor or node.kind != 'Binary' or node.data['op'] != '*':
        return None
    resolver = node.source.resolver
    if resolver is None:
        return None
    left, right = node.role('left'), node.role('right')
    if resolver.type_of(left).kind == 'string':
        return left, right
    if resolver.type_of(right).kind == 'string':
        return right, left
    return None


def result_type(node, seen):
    """Also infer integer * string as a string, retaining named string types."""
    resolver = node.source.resolver
    for side in ('left', 'right'):
        typ = resolver.type_of(node.role(side), seen.copy())
        if typ.kind == 'string':
            return replace(typ, witness=node)
    return None


def runtime_operand(node, seen=None):
    """Reject actual runtime bindings before hoisting constant declarations.

    Odin still verifies unresolved names and constant-only calls. Type operands
    and fields of constant aggregates are not runtime bindings.
    """
    if node is None or node in node.source.run_values:
        return False
    seen = set() if seen is None else seen
    if node in seen: return False
    seen.add(node)
    source, resolver = node.source, node.source.resolver
    if node.kind in {'Run','Literal'} or node.kind.endswith('Type'): return False
    if node.kind == 'Selector': return runtime_operand(node.role('expr'), seen)
    symbol = resolver.symbol(node)
    if symbol:
        declaration = symbol.declaration
        if declaration.kind in {'Range','TypeSwitch'}: return True
        if declaration.kind == 'Field':
            return not any(name.kind == 'PolyType' for name in declaration.group('names'))
        if declaration.kind == 'Declaration' and (declaration.data.get('mutable',True) or declaration.data.get('immutable',False)):
            return True
    if node.kind == 'Call':
        if source.purity and not (source.purity.builtin(node) or source.denotes_type(node.role('callee'))):
            return True
        return any(runtime_operand(arg, seen.copy()) for arg in node.group('args'))
    return any(runtime_operand(child, seen.copy()) for child in node.children)


def render(expressions, node):
    pair = operands(node)
    if pair is None:
        return None
    source = node.source
    text, count = pair
    if runtime_operand(text) or runtime_operand(count):
        return expressions.error(node, 'String repetition requires compile-time constant operands, like string concatenation (+).')
    scope = expressions.helper_scope(node)
    if scope is source.root:
        from .semantics import descendants
        generic = any((symbol := source.resolver.symbol(child)) and symbol.declaration.kind == 'Field'
                      and any(name.kind == 'PolyType' for name in symbol.declaration.group('names'))
                      for operand in pair for child in descendants(operand))
        if generic:
            known = source.resolver.constant_int(count)
            if known is None or not 0 <= known <= 128:
                return expressions.error(node, 'String repetition in a generic signature or type expression requires a fixed nonnegative integer count through 128; place a variable-count repetition in the procedure body.')
            source.needs_intrinsics = source.needs_default = True
            value = f'({source.render(text, constant=True)} + "")'
            empty = empty_value(source, value)
            def concatenate(n):
                if n == 0: return empty
                if n == 1: return value
                return f'({concatenate(n//2)} + {concatenate(n-n//2)})'
            amount = source.render(count, constant=True)
            integer = f'{source.prefix}meta.type_is_integer(type_of({source.prefix}default_value({amount})))'
            allowed = f'{integer} && len({value}) <= {LIMIT//known}' if known else f'{integer} && len({value}) >= 0'
            error = expressions.error(node, 'String repetition requires an integer count and a result within the 16 MiB compile-time limit.')
            return f'({concatenate(known)} when ({allowed}) else {error})'
    name = source.prefix + f'repeat_{node.start}_{node.end}'
    key = scope, name
    if key in expressions.helpers:
        return name
    expressions.helpers[key] = ''
    source.needs_intrinsics = True
    source.needs_default = True
    base, amount = name + '_base', name + '_count'
    wide = name + '_wide'
    # The + "" is deliberate: even zero/one repetitions require a constant
    # string, exactly like ordinary Odin concatenation. No runtime call is made.
    lines = [f'{base} :: ({source.render(text, constant=True)}) + ""',
             f'{amount} :: ({source.render(count, constant=True)})']
    def check(condition, suffix, message):
        token = name + '_error_' + suffix
        source.error_messages[token] = message
        return f'#assert({condition}, "{token}")'
    integer = f'{source.prefix}meta.type_is_integer(type_of({source.prefix}default_value({amount})))'
    lines += [check(integer, 'integer', 'String repetition requires an integer compile-time count.'),
              f'when {integer} {{',
              check(f'{amount} >= 0', 'negative', 'String repetition requires a nonnegative count.'),
              f'when {amount} >= 0 {{',
              f'{wide} :: u128({amount})']
    allowed = f'len({base}) == 0 || {wide} <= u128({LIMIT}) / u128(max(len({base}), 1))'
    lines += [check(allowed, 'size', 'String repetition result exceeds the 16 MiB compile-time limit.'),
              f'when {allowed} {{']
    known = source.resolver.constant_int(count)
    bits = max(1, known.bit_length()) if known is not None and 0 <= known < 1 << 128 else 128
    empty = empty_value(source, base)
    previous_chunk, previous_result = base, empty
    for bit in range(bits):
        chunk, result = f'{name}_chunk_{bit}', f'{name}_result_{bit}'
        if bit:
            lines.append(f'{chunk} :: ({previous_chunk} + {previous_chunk}) when ({wide} >> {bit}) != 0 else {empty}')
        else:
            lines.append(f'{chunk} :: {base}')
        lines.append(f'{result} :: ({previous_result} + {chunk}) when ({wide} & (u128(1) << {bit})) != 0 else {previous_result}')
        previous_chunk, previous_result = chunk, result
    lines += [f'{name} :: {previous_result}']
    for _ in range(3):
        lines += ['} else {', f'{name} :: {empty}', '}']
    child = node
    for parent in node.ancestors():
        if parent.kind == 'WhenExpr' and child in {parent.role('x'), parent.role('y')}:
            condition = source.render(parent.role('cond'), constant=True)
            if child is parent.role('y'): condition = f'!({condition})'
            lines = [f'when {condition} {{', *lines, '} else {', f'{name} :: ""', '}']
        elif parent.kind == 'CompileSwitchExpr':
            cases = parent.data['cases']
            selected = next((case for case in cases if case['result'][0] <= node.start and node.end <= case['result'][1]), None)
            if selected is not None:
                nondefault = [case for case in cases if case['matches']]
                before = nondefault[:nondefault.index(selected)] if selected['matches'] else nondefault
                condition = expressions.switch_condition(parent, selected)
                for earlier in reversed(before):
                    previous = expressions.switch_condition(parent, earlier)
                    condition = f'(false when ({previous}) else ({condition}))'
                lines = [f'when {condition} {{', *lines, '} else {', f'{name} :: ""', '}']
        child = parent
    privacy = '@(private="file")\n' if scope is source.root else ''
    # Attributes attach only to declarations, not the generated when blocks.
    code = '\n' + '\n'.join(privacy + line if line.startswith(name) else line for line in lines) + '\n'
    # Nested repetitions must precede their local consumers. The placeholder
    # above breaks recursion, but its initial insertion order is not emission order.
    del expressions.helpers[key]
    expressions.helpers[key] = source.mapping.wrap(code, source.path, node.start, node.end, 'string-repetition')
    return name

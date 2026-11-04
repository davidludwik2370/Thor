"""Constant sequence concatenation; native Odin owns values and exact types."""
from dataclasses import replace

from .compiler import Hint
from .semantics import Type


def unparen(node):
    while node.kind == 'Paren':
        node = node.role('expr')
    return node


def operands(node):
    """Flatten constant chains into one intrinsic, including grouped chains."""
    inner = unparen(node)
    if inner.kind == 'Binary' and inner.data['op'] == '++':
        return operands(inner.role('left')) + operands(inner.role('right'))
    return [node]


def result_type(node, seen):
    resolver = node.source.resolver
    values = operands(node)
    types = [resolver.type_of(value, seen.copy()) for value in values]
    known = next((typ for value,typ in zip(values,types) if not node.source.is_array(unparen(value))
                  and typ.kind in {'array','slice'}), None)
    known = known or next((typ for typ in types if typ.kind in {'array', 'slice'} and typ.elem and typ.elem.kind != 'unknown'), Type())
    element = known.elem
    if known.kind == 'array':
        length = sum(typ.length for typ in types) if all(typ.kind == 'array' and typ.length is not None for typ in types) else None
        return Type('array', node=node, elem=element, length=length, witness=node)
    return replace(known, node=node, elem=element, witness=node)


def render(expressions, node, expected):
    from .repetition import runtime_operand
    source = node.source
    values = operands(node)
    def error(value, message):
        return source.mapping.wrap(expressions.error(value, message), source.path,
                                   value.start, value.end, 'concatenation-operand')
    container = None
    for value in values:
        if runtime_operand(value):
            return error(value, "Array concatenation ('++') requires compile-time constant operands; runtime variables and immutable bindings do not qualify.")
        kind = source.resolver.type_of(value).kind
        if kind not in {'array', 'slice', 'generic', 'unknown'}:
            return error(value, "Array concatenation ('++') requires fixed arrays or constant slices; use + for constant strings.")
        if kind in {'array', 'slice'}:
            if container is not None and kind != container:
                return error(value, "Array concatenation ('++') cannot mix fixed arrays and slices; convert the operands to the same container kind explicitly.")
            container = kind

    source.needs_intrinsics = True
    meta = source.prefix + 'meta'
    element = None
    if expected:
        shape = expected.semantic or source.resolver.type_node(expected.node)
        if shape.kind in {'array', 'slice'}:
            element = f'{meta}.type_elem_type({expressions.type_code(expected)})'
    # Established operands supply context to new bracket literals, without
    # converting an existing array or changing an operand's container kind.
    inline = lambda value: source.is_array(unparen(value))
    anchor = next((value for value in values if not inline(value)
                   and source.resolver.type_of(value).kind in {'array', 'slice'}), None)
    if anchor is None:
        anchor = next((value for value in values if inline(value) and unparen(value).group('elems')), None)
    rendered = {}
    if element is None and anchor is not None:
        rendered[anchor] = source.render(anchor, constant=True)
        if inline(anchor):
            # Query the first element, not the complete array literal once per
            # operand. This keeps large literal chains' emitted source linear.
            first = unparen(anchor).group('elems')[0]
            source.needs_default = True
            element = f'type_of({source.prefix}default_value({source.render(first, constant=True)}))'
        else:
            element = f'{meta}.type_elem_type(type_of({rendered[anchor]}))'
    element_hint = Hint(source, anchor or values[0], element, element_only=True) if element else None
    arguments = []
    for value in values:
        code = rendered.get(value)
        if code is None:
            code = source.render(value, element_hint if inline(value) else None, constant=True)
        arguments.append(source.mapping.wrap(code, source.path, value.start, value.end, 'concatenation-operand'))
    comments = source.comments(node.start, node.end, [(value.start, value.end) for value in values])
    return source.mapping.wrap(f'{meta}.concatenate({comments}{", ".join(arguments)})',
                               source.path, node.start, node.end, 'concatenation')

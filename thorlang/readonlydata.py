"""Promote independently constructed constant slice literals to read-only data.

This deliberately does not promote views, calls that allocate, or unresolved
layouts. Native Odin remains responsible for exact constant/type checking.
"""


def attributes(source, declaration):
    if (not source.thor or not declaration.data.get('immutable') or not source.resolver
            or declaration.group('attributes')):
        return None
    parent = declaration.parent
    if (not parent or parent.kind not in {'File', 'Block', 'Case'}
            or parent.kind == 'Block' and not parent.text.startswith('{')):
        return None
    values = declaration.group('values')
    if not values or len(values) != len(declaration.group('names')):
        return None
    proof = Constants(source.resolver, max(1024, len(source.nodes) * 16))
    annotation = declaration.role('type')
    for value in values:
        while value.kind == 'Paren':
            value = value.role('expr')
        typ = source.resolver.type_node(annotation) if annotation else source.resolver.type_of(value)
        if value.kind != 'Compound' or typ.kind != 'slice' or not proof.constant(value, typ):
            return None
    return ('@(static, rodata)\n' if any(n.kind == 'Procedure' for n in declaration.ancestors())
            else '@(rodata)\n')


class Constants:
    def __init__(self, resolver, budget):
        self.resolver = resolver
        self.budget = budget

    @staticmethod
    def atom(typ):
        return typ.kind in {'scalar', 'string', 'enum'}

    def fields(self, typ):
        if typ.kind != 'struct':
            return []
        if typ.members:
            return [(name, value, None) for name, value in typ.members.items()]
        node = typ.node.role('fields') if typ.node else None
        return [(name.text, self.resolver.type_node(field.role('type'), typ.substitutions), field.role('default'))
                for field in node.children if field.kind == 'Field' for name in field.group('names')] if node else []

    def shape(self, typ, inline=False, depth=0):
        if depth > 64 or self.budget <= 0:
            return False
        self.budget -= 1
        if typ.kind == 'scalar':
            return typ.name not in {'any', 'rawptr', 'typeid'}
        if typ.kind in {'string', 'enum'}:
            return True
        if typ.kind == 'array':
            return typ.length is not None and self.shape(typ.elem, inline, depth + 1)
        if typ.kind == 'slice':
            return not inline and self.shape(typ.elem, False, depth + 1)
        if typ.kind == 'struct':
            if typ.node and typ.node.data.get('op') == 'raw_union':
                return False
            fields = self.fields(typ)
            return (all(self.shape(value, inline, depth + 1) for _, value, _ in fields)
                    and all(default is None or self.constant(default, value) for _, value, default in fields))
        return False

    def constant(self, node, expected, seen=None):
        seen = set() if seen is None else seen
        if self.budget <= 0 or node is None or node in seen or len(seen) > 128:
            return False
        self.budget -= 1
        seen.add(node)
        if node.kind == 'Paren':
            return self.constant(node.role('expr'), expected, seen)
        if not self.shape(expected):
            return False
        if node in node.source.run_values:
            # Preserve ownership of runtime-materialized strings/aggregates.
            return expected.kind in {'scalar', 'enum'}
        kind = node.kind
        if kind == 'Literal':
            typ = self.resolver.type_of(node)
            return (self.atom(expected) and self.atom(typ)
                    and (expected.kind == 'string') == (typ.kind == 'string'))
        if kind == 'ImplicitSelector':
            return expected.kind == 'enum'
        if kind == 'Compound':
            fields = self.fields(expected)
            for index, elem in enumerate(node.group('elems')):
                value = elem.role('value') or elem
                field = elem.role('field')
                typ = (expected.elem if expected.kind in {'array', 'slice'} else
                       next((typ for name, typ, _ in fields if field and name == field.text), None)
                       or (fields[index][1] if index < len(fields) else None))
                if typ is None or not self.constant(value, typ, seen.copy()):
                    return False
            return True
        if kind == 'Unary' and node.data['op'] in {'+', '-', '~', '!'}:
            return self.atom(expected) and self.constant(node.role('expr'), expected, seen)
        if kind == 'Binary' and node.data['op'] in {
                '+', '-', '*', '/', '%', '%%', '<<', '>>', '&', '|', '~', '&~',
                '==', '!=', '<', '>', '<=', '>=', '&&', '||'}:
            return all(self.atom(self.resolver.type_of(node.role(role)))
                       and self.constant(node.role(role), self.resolver.type_of(node.role(role)), seen.copy())
                       for role in ['left', 'right'])
        if kind in {'Ternary', 'WhenExpr'}:
            return all(self.constant(node.role(role), self.resolver.type_of(node.role(role))
                                    if role == 'cond' else expected, seen.copy()) for role in ['cond', 'x', 'y'])
        if kind == 'Call':
            callee = node.role('callee')
            cast = self.resolver.known_type_operand(callee)
            builtins = {'len', 'size_of', 'align_of', 'offset_of', 'min', 'max', 'abs', 'clamp',
                        'complex', 'quaternion', 'real', 'imag', 'jmag', 'kmag', 'conj'}
            if (not self.atom(expected) or not self.shape(expected, inline=True)
                    or not (cast
                            or any(self.resolver.native_builtin(callee, name) for name in builtins))):
                return False
            return all(self.shape(self.resolver.type_of(value), inline=True)
                       and self.constant(value, self.resolver.type_of(value), seen.copy())
                       for arg in node.group('args') for value in [arg.role('value') or arg])
        if kind in {'Ident', 'Selector', 'Index'}:
            # A constant header can still alias writable storage. Follow only
            # constants whose values contain no mutable backing references.
            if not self.shape(expected, inline=True):
                return False
            symbol = self.resolver.symbol(node)
            if symbol:
                return (symbol.declaration.kind == 'Declaration' and not symbol.declaration.data['mutable']
                        and not symbol.immutable and self.constant(symbol.value, expected, seen))
            if kind == 'Selector' and self.resolver.known_type_operand(node.role('expr')):
                return expected.kind == 'enum'
            if kind in {'Selector', 'Index'}:
                if kind == 'Selector' and not self.atom(expected):
                    return False
                value = node.role('expr')
                typ = self.resolver.type_of(value)
                return (self.shape(typ, inline=True) and self.constant(value, typ, seen.copy())
                        and (kind != 'Index' or self.constant(node.role('index'),
                             self.resolver.type_of(node.role('index')), seen.copy())))
            return node.text in {'true', 'false', 'nil'}
        return False

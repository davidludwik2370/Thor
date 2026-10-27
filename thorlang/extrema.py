"""Builtin min/max expansion with original argument order and optional slice status."""
from __future__ import annotations


class Extrema:
    def __init__(self, expressions):
        self.e = expressions
        self.s = expressions.source

    def builtin(self, node, seen=None):
        """Resolve genuine builtins, including immutable aliases and core:math."""
        seen = set() if seen is None else seen
        if node is None or node in seen:
            return None
        seen.add(node)
        if node.kind == 'Paren':
            return self.builtin(node.role('expr'), seen)
        s, r = node.source, node.source.resolver
        # Installed namespaces other than math/builtin are ordinary procedures,
        # not Thor's builtin extrema spellings. Do not load fmt/reflect/etc just
        # to classify a normal variadic call while scanning runtime code.
        namespace=None
        if node.kind=='Selector':
            base=node.role('expr')
            if base.kind=='Ident' and r.lookup(base.text,base) is None:namespace=base.text
        elif node.kind=='Ident' and node.text in s.selected and r.lookup(node.text,node) is None:
            namespace=s.selected[node.text].split('.',1)[0]
        path=s.imports.get(namespace)
        if path and path not in {'core:math','base:builtin'} and s.import_dir(path) is None:return None
        symbol = r.symbol(node)
        if symbol:
            if symbol.declaration.data.get('mutable') or not symbol.value:
                return None
            return self.builtin(symbol.value, seen)
        if node.kind == 'Ident':
            if node.text in s.selected:
                namespace, member = s.selected[node.text].split('.', 1)
                if s.imports.get(namespace) == 'core:math' and s.import_dir('core:math') is None:
                    return member if member in {'min', 'max'} else None
                return None
            return node.text if node.text in {'min', 'max'} and node.text not in s.imports else None
        if node.kind == 'Selector':
            base, field = node.role('expr'), node.role('field')
            if (base.kind == 'Ident' and r.lookup(base.text, base) is None
                    and s.imports.get(base.text) in {'core:math','base:builtin'} and s.import_dir(s.imports[base.text]) is None):
                return field.text if field.text in {'min', 'max'} else None
        return None

    def plan(self, call):
        if not self.s.thor or call.kind != 'Call':
            return None
        ellipsis = call.data.get('roles', {}).get('ellipsis')
        parts = []
        for arg in call.group('args'):
            value = arg
            while value.kind == 'Paren':
                value = value.role('expr')
            if ellipsis and arg.start >= ellipsis[1]:
                parts.append(('slice', arg))
            elif value.kind == 'Unary' and value.data['op'] == '**':
                parts.append(('array', value.role('expr')))
            else:
                parts.append(('scalar', arg))
        # Ordinary calls do not need min/max resolution. Resolving them here
        # needlessly discovers SDK packages while preparing evaluation helpers.
        if not any(k != 'scalar' for k, _ in parts):
            return None
        name = self.builtin(call.role('callee'))
        return (name, parts, bool(ellipsis)) if name else None

    def element(self, call):
        plan = self.plan(call)
        if plan:
            return next(value for kind, value in plan[1] if kind != 'scalar')

    def result_type(self, call):
        s = self.s
        value = self.element(call)
        return f'type_of({self.e.module("sequence")}.element({s.render(value)}))'

    def render(self, call, constant=False):
        plan = self.plan(call)
        if not plan:
            return None
        name, parts, optional = plan
        s, e = self.s, self.e
        def error(value, message):
            return s.mapping.wrap(e.error(value, message), s.path, value.start, value.end, 'extrema-argument')
        for kind, value in parts:
            if value.kind == 'FieldValue':
                return error(value, 'Spread min/max calls take positional values, without named arguments.')
            typ = s.resolver.type_of(value)
            if kind == 'slice' and typ.kind not in {'slice', 'unknown', 'generic'}:
                return error(value, "A '..' argument to min/max must be a slice; use values[:] for an array.")
            if kind == 'array' and typ.kind not in {'array', 'unknown', 'generic'}:
                return error(value, "A '**' argument to min/max must be a fixed array.")
            if kind == 'array' and typ.length == 0:
                return error(value, 'An empty fixed array cannot be expanded into min/max.')
        module = e.module('sequence')
        if len(parts) == 1:
            kind, value = parts[0]
            return f'{module}.{name}_{kind}({s.render(value)})'

        # Bind values before reducing, in the original source order. Array
        # expansion snapshots its elements; slice expansion borrows its storage.
        # Type queries do not execute the source container. Explicit scalar
        # annotations preserve literal coercion to the sequence element type.
        def body(hint):
            prefix = s.prefix + f'extrema_{call.start}_'
            result, ok = prefix+'result', prefix+'ok'
            code = []
            for i, (kind, value) in enumerate(parts):
                binding = f'{prefix}arg_{i}'
                code.append(f'{binding}: {hint.expression} = {s.render(value)}' if kind == 'scalar'
                            else f'{binding} := {s.render(value)}')
            code += [f'{result}: {hint.expression}', f'{ok} := false']
            for i, (kind, value) in enumerate(parts):
                binding = f'{prefix}arg_{i}'
                if kind == 'scalar':
                    statement = f'if {ok} {{ {result} = {module}.{name}_value({result}, {binding}) }} else {{ {result} = {binding}; {ok} = true }}'
                elif kind == 'array':
                    statement = f'{result} = {module}.{name}_array_seeded({binding}, {result}, {ok}); {ok} = true'
                else:
                    statement = f'{result}, {ok} = {module}.{name}_slice_seeded({binding}, {result}, {ok})'
                code.append(s.mapping.wrap(statement, s.path, value.start, value.end, 'extrema-argument'))
            code.append(f'return {result}, {ok}' if optional else f'return {result}')
            return '\n'.join(code)
        helper, args = e.closed(call, body, result=None, flavor='extrema', optional_ok=optional)
        args.append(self.result_type(call))
        return f'{helper}({", ".join(args)})'

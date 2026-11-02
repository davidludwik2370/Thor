"""Lift generic inline defaults without exposing generated names in Thor source.

Odin can specialize a named generic procedure against an explicit callback type.
Keep helpers beside their owning declaration so imports, enclosing compile-time
bindings and conditional compilation retain their original lexical scope.
"""
from dataclasses import dataclass
import hashlib
import os

from .semantics import descendants


@dataclass
class DefaultCallback:
    procedure: object
    field: object
    declaration: object
    name: str
    replacements: dict


class DefaultCallbacks:
    def __init__(self, source):
        self.source = source
        self.plans = {}
        self.fields = {}
        self.declarations = {}
        self.errors = {}
        self.replacements = {}
        if not source.thor:
            return
        for procedure in source.nodes:
            if procedure.kind != 'Procedure' or not source.resolver.generic_default_error(procedure):
                continue
            self.prepare(procedure)

    def prepare(self, procedure):
        source, resolver = self.source, self.source.resolver
        value = procedure
        while value.parent.kind == 'Paren':
            value = value.parent
        field = value.parent
        signature = procedure.role('type')
        host_signature = field.parent.parent
        host = host_signature.parent
        declaration = host
        while declaration.parent and declaration.parent.kind == 'Paren':
            declaration = declaration.parent
        declaration = declaration.parent
        if host.kind != 'Procedure' or not declaration or declaration.kind != 'Declaration':
            return  # The existing mapped guard explains unsupported contexts.
        if not field.role('type') and any(n.kind == 'PolyType' for n in descendants(signature)):
            self.errors[procedure] = (signature,
                'An explicitly generic inline default needs an explicit callback parameter type. '
                'Use the enclosing element type in the inline signature, or annotate the callback parameter.')
            return

        # Only introduce generics at direct structural parameter-type positions.
        # Computed types/lengths require Odin's evaluator and are left untouched.
        replacements, inferred = {}, set()

        def bind(node):
            if node is None or node.kind in {'Call', 'Selector', 'WhenExpr', 'Ternary', 'Binary', 'ProcedureType'}:
                return
            symbol = resolver.symbol(node) if node.kind == 'Ident' else None
            if symbol and symbol.node.kind == 'PolyType' and symbol.scope is host and symbol.name not in inferred:
                inferred.add(symbol.name)
                replacements[node] = '$' + node.text
            for child in node.children:
                bind(child)

        for parameter in signature.role('params').children:
            bind(parameter.role('type'))

        for node in descendants(procedure):
            if node.kind != 'Ident':
                continue
            symbol = resolver.symbol(node)
            if not symbol or symbol.scope is not host:
                continue
            if symbol.node.kind == 'PolyType':
                if symbol.name in inferred:
                    continue
                message = (f"Inline default callback depends on generic '{symbol.name}', which cannot be inferred "
                           'from its parameter types. Use a named generic default or pass an explicit callback.')
            else:
                message = (f"Inline default callbacks cannot capture enclosing parameter '{symbol.name}'. "
                           'Pass the required value as a callback argument instead.')
            self.errors[procedure] = (signature if symbol.node.kind == 'PolyType' else node, message)
            return

        # Alternate when declarations share a native scope. Give the same
        # procedure/parameter the same helper name in every branch and file,
        # letting Odin select the active helper rather than guessing a branch.
        scope = source.scope(declaration)
        key = (tuple(n.text for n in declaration.group('names')),
               tuple(n.text for n in field.group('names')),
               None if scope.kind == 'File' else (str(source.path), scope.start))
        prefix = min(s.prefix for s in resolver.sources if s.path.parent == source.path.parent)
        name = prefix + 'default_' + hashlib.sha256(repr(key).encode()).hexdigest()[:16]
        while any(name.encode() in s.raw for s in resolver.sources):
            name += '_'
        plan = DefaultCallback(procedure, field, declaration, name, replacements)
        self.plans[procedure] = self.fields[field] = plan
        self.declarations.setdefault(declaration, []).append(plan)

    def render(self, node):
        if node in self.replacements:
            return self.replacements[node]
        plan = self.fields.get(node)
        if plan is None:
            return None
        source = self.source
        value, annotation = node.role('default'), node.role('type')
        if annotation:
            return (source.copy(node.start, annotation.start) + source.render(annotation) +
                    source.comments(annotation.end, value.start))
        names = node.group('names')
        comments = source.comments(names[-1].end, value.start)
        return (source.copy(node.start, names[-1].end) + ': ' + comments +
                source.render(plan.procedure.role('type')))

    def helpers(self, declaration):
        source = self.source
        code = []
        for plan in self.declarations.get(declaration, []):
            previous = self.replacements
            self.replacements = {**previous, **plan.replacements}
            try:
                body = source.render(plan.procedure)
            finally:
                self.replacements = previous
            code.append(source.mapping.wrap(plan.name + ' :: ' + body + '\n', source.path,
                                            plan.procedure.start, plan.procedure.end, 'default-callback'))
        return ''.join(code)

    def arguments(self, call):
        """Supply omitted defaults explicitly at their original call sites.

        This avoids Odin's caller-scope lookup and eager specialization of an
        overridden generic default. Actual callback types, procedure-value ABI,
        nil/explicit overrides and runtime argument evaluation stay intact.
        """
        source, resolver = self.source, self.source.resolver
        candidate = resolver.resolve_call(call)
        signature = candidate.signature if candidate else None
        procedure = signature.parent if signature else None
        if not procedure or procedure.kind != 'Procedure':
            return []
        defaults = procedure.source.defaults
        if not defaults:
            return []
        fields = signature.role('params').children
        names = [name.text.lstrip('$') for f in fields for name in f.group('names')]
        receiver = call.role('callee').kind == 'Selector' and call.role('callee').data.get('op') == '->'
        supplied = set(names[:1] if receiver else [])
        index = int(receiver)
        for argument in call.group('args'):
            if argument.kind == 'FieldValue':
                supplied.add(argument.role('field').text)
            else:
                if index < len(names):
                    supplied.add(names[index])
                index += 1
        added = []
        for field in fields:
            plan = defaults.fields.get(field)
            if plan is None:
                continue
            for name in field.group('names'):
                if name.text.lstrip('$') in supplied:
                    continue
                access = resolver.access(source, procedure.source, plan.name)
                if access == plan.name and procedure.source.path.parent != source.path.parent:
                    # A package-level alias can expose a procedure without its
                    # import being present in this caller's individual file.
                    namespace = source.prefix + f'default_import_{len(source.hidden_namespaces)}'
                    source.imports[namespace] = os.path.relpath(procedure.source.path.parent,
                                                              source.path.parent).replace(os.sep, '/')
                    access = source.namespace_code(namespace) + '.' + plan.name
                added.append(source.mapping.wrap(name.text.lstrip('$') + '=' + access,
                    procedure.source.path, plan.procedure.start, plan.procedure.end, 'default-callback'))
        if not added:
            return []
        arguments = call.group('args')
        position = arguments[-1].end if arguments else call.end - 1
        return [(position, (', ' if arguments else '') + ', '.join(added))]

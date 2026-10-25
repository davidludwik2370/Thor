"""Lexical bindings and the type/procedure facts needed by Thor additions.

This is not an Odin type checker. Shapes guide Thor checks and context lowering;
generated type witnesses ask Odin to resolve exact types, generic constraints,
assignability and overload validity in the defining scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
from pathlib import Path


@dataclass(eq=False)
class Type:
    kind: str = "unknown"
    name: str = ""
    node: object = None
    elem: Type | None = None
    length: int | None = None
    substitutions: dict = field(default_factory=dict)
    witness: object = None
    untyped: bool = False
    nominal: object = None


@dataclass(eq=False)
class Symbol:
    name: str
    node: object
    declaration: object
    scope: object
    annotation: object = None
    value: object = None
    index: int = 0
    projection: object = None
    candidates: list = field(default_factory=list)

    @property
    def immutable(self):
        return self.declaration.data.get("immutable", False)


@dataclass
class Candidate:
    symbol: Symbol
    signature: object
    substitutions: dict
    score: int = 0


@dataclass
class Protection:
    symbol: Symbol
    predicates: list[str] = field(default_factory=list)


def descendants(n):
    yield n
    for child in n.children:
        yield from descendants(child)


def binding_name(n):
    if n.kind in {"Unary", "PolyType"}:
        return n.data.get("name") or n.text.lstrip("&$")
    return n.text


class Resolver:
    def __init__(self, sources):
        self.sources = sources
        self.scopes = {}
        self.by_decl = {}
        self.using = {}
        self.active = set()
        self.call_plans = {}
        self.wrappers = {}
        self.markers = {}
        for source in sources:
            source.resolver = self
        for source in sources:
            for n in source.nodes:
                names, scope, annotation, values = [], None, None, []
                if n.kind == "Declaration":
                    names, scope, annotation, values = n.group("names"), source.scope(n), n.role("type"), n.group("values")
                    if n.data.get("using_field"):
                        self.using.setdefault(self.scope_key(scope), []).append(n)
                elif n.kind == "Field":
                    parent = next((a for a in n.ancestors() if a.kind in {"ProcedureType", "StructType"}), None)
                    if parent and parent.kind == "ProcedureType":
                        scope = parent.parent if parent.parent and parent.parent.kind == "Procedure" else parent
                        names, annotation = n.group("names"), n.role("type")
                elif n.kind == "Range":
                    names, scope = n.group("names"), n
                elif n.kind == "TypeSwitch":
                    names, scope = n.group("names"), n
                elif n.kind == "Using":
                    self.using.setdefault(self.scope_key(source.scope(n)), []).append(n)
                elif n.kind == "PolyType":
                    names, scope = [n], source.scope(n)
                    prototype = next((a for a in n.ancestors() if a.kind == "ProcedureType"), None)
                    if prototype:
                        scope = prototype.parent if prototype.parent and prototype.parent.kind == "Procedure" else prototype
                if scope:
                    for i, name in enumerate(names):
                        symbol = Symbol(binding_name(name), name, n, scope, annotation,
                                        values[i] if i < len(values) else values[0] if len(values) == 1 else None, i)
                        if symbol.name == "_":
                            continue
                        self.scopes.setdefault(self.scope_key(scope), {}).setdefault(symbol.name, []).append(symbol)
                        self.by_decl[(n, symbol.name)] = symbol
        for table in self.scopes.values():
            for symbols in table.values():
                for symbol in symbols:
                    symbol.candidates = symbols
                if any(s.immutable for s in symbols) and any(any(a.kind in {"When", "CompileSwitch"} for a in s.declaration.ancestors()) for s in symbols):
                    marker = symbols[0].node.source.prefix + "immutable_" + hashlib.sha256(
                        (symbols[0].name + str(symbols[0].scope.start)).encode()).hexdigest()[:12]
                    for symbol in symbols:
                        self.markers[symbol.declaration, symbol.name] = marker

    @staticmethod
    def scope_key(scope):
        return (scope.source.path.parent, "package") if scope.kind == "File" else scope

    def lexical_scopes(self, context):
        result = [a for a in context.ancestors() if a.source.is_scope(a) or a.kind == "ProcedureType"]
        if context.source.is_scope(context):
            result.insert(0, context)
        if context.source.root not in result:
            result.append(context.source.root)
        return result

    def lookup(self, name, context, *, projections=True):
        key = (name, context)
        if key in self.active:
            return None
        self.active.add(key)
        try:
            for scope in self.lexical_scopes(context):
                symbols = self.scopes.get(self.scope_key(scope), {}).get(name, [])
                if scope.kind == "File" and (name in context.source.selected or name in context.source.imports):
                    symbols = [s for s in symbols if s.node.source is context.source and
                               s.declaration not in context.source.conditional_conflicts]
                if symbols:
                    # File-private selective aliases do not become declarations
                    # here. When branches share a native declaration scope.
                    return next((s for s in symbols if s.immutable), symbols[0])
                if projections:
                    for using in self.using.get(self.scope_key(scope), []):
                        owners = using.group("values") if using.kind == "Using" else using.group("names")
                        for owner in owners:
                            owner_type = self.type_of(owner)
                            field = self.field(owner_type, name)
                            if field:
                                original = self.symbol(owner)
                                if original:
                                    return Symbol(name, field[1], original.declaration, scope,
                                                  projection=(owner, name), candidates=original.candidates)
            return None
        finally:
            self.active.remove(key)

    def package_symbol(self, directory, name):
        symbols = self.scopes.get((directory, "package"), {}).get(name, [])
        return next((s for s in symbols if s.immutable), symbols[0] if symbols else None)

    def symbol(self, n, seen=None):
        if n is None:
            return None
        seen = set() if seen is None else seen
        if n in seen:
            return None
        seen.add(n)
        if n.kind == "Paren":
            return self.symbol(n.role("expr"), seen)
        if n.kind == "Ident":
            local = self.lookup(n.text, n)
            if local:
                return local
            if n.text in n.source.selected:
                namespace, member = n.source.selected[n.text].split(".", 1)
                return self.package_symbol(n.source.import_dir(n.source.imports[namespace]), member)
        if n.kind == "Selector":
            base, member = n.role("expr"), n.role("field")
            if base and base.kind == "Ident" and not self.lookup(base.text, base):
                imported = n.source.imports.get(base.text)
                if imported:
                    return self.package_symbol(n.source.import_dir(imported), member.text)
        return None

    def declaration_for(self, n):
        symbol = self.symbol(n)
        return symbol.declaration if symbol else None

    def type_node(self, n, substitutions=None, seen=None):
        if n is None:
            return Type()
        substitutions = substitutions or {}
        seen = set() if seen is None else seen
        key = (n, "type")
        if key in seen:
            return Type(node=n)
        seen.add(key)
        kind = n.kind
        if kind == 'SetType':
            return Type('set',node=n,elem=self.type_node(n.group('args')[0],substitutions,seen))
        if kind == "Paren":
            return self.type_node(n.role("expr"), substitutions, seen)
        if kind == "PolyType":
            name = n.data["name"]
            if name in substitutions and isinstance(substitutions[name], Type):
                return substitutions[name]
            specialization = n.role("specialization")
            if specialization:
                return self.type_node(specialization, substitutions, seen)
            return Type("generic", name, n)
        if kind == "ArrayType":
            length = n.role("len")
            if length is None:
                return Type("slice", node=n, elem=self.type_node(n.role("elem"), substitutions, seen))
            count = substitutions.get(binding_name(length))
            if not isinstance(count, int):
                count = n.source.constant_int(length)
            return Type("array", node=n, elem=self.type_node(n.role("elem"), substitutions, seen), length=count)
        if kind == "DynamicArrayType":
            return Type("dynamic", node=n, elem=self.type_node(n.role("elem"), substitutions, seen))
        if kind in {"PointerType", "MultiPointerType"}:
            return Type("pointer", node=n, elem=self.type_node(n.role("elem"), substitutions, seen))
        if kind == "MapType":
            return Type("map", node=n, elem=self.type_node(n.role("value"), substitutions, seen))
        if kind == "DistinctType":
            return replace(self.type_node(n.role("type"), substitutions, seen), nominal=n)
        if kind in {"StructType", "UnionType", "EnumType", "BitSetType", "ProcedureType"}:
            return Type({"StructType": "struct", "UnionType": "union", "EnumType": "enum",
                         "BitSetType": "bitset", "ProcedureType": "procedure"}[kind], node=n, substitutions=substitutions)
        if kind == "Ident":
            if n.text in substitutions and isinstance(substitutions[n.text], Type):
                return substitutions[n.text]
            if n.text in {"string", "cstring"}:
                return Type("string", n.text, n)
            if n.text in {"bool", "int", "uint", "uintptr", "rune", "byte", "rawptr", "typeid", "any"} or n.text in {
                f"{p}{bits}{endian}" for p in ["i", "u", "f", "b", "complex", "quaternion"]
                for bits in [8, 16, 32, 64, 128, 256] for endian in ["", "le", "be"]
            }:
                return Type("scalar", n.text, n)
        symbol = self.symbol(n)
        if symbol:
            if symbol.declaration.kind == "PolyType":
                return Type("generic", symbol.name, n)
            if symbol.annotation and (symbol.annotation.kind == "TypeidType" or symbol.annotation.text == "typeid"):
                return Type("generic", symbol.name, n)
            if symbol.value:
                return self.type_node(symbol.value, substitutions, seen)
        if kind == "Call":
            declaration = self.declaration_for(n.role("callee"))
            values = declaration.group("values") if declaration else []
            if len(values) == 1 and values[0].kind == "StructType":
                record = values[0]
                parameters = self.fields(record.role("params"))
                mapping = dict(substitutions)
                for (name, _), arg in zip(parameters, n.group("args")):
                    mapping[name.lstrip("$")] = self.type_node(arg)
                if (record.source.path == Path(__file__).resolve().parents[1]/'runtime'/'sets'/'sets.odin'
                        and any(name.text=='Set' for name in declaration.group('names'))):
                    return Type('set',node=n,elem=mapping.get('T',Type()))
                return Type("struct", node=record, substitutions=mapping)
        return Type(node=n)

    def type_of(self, n, seen=None):
        if n is None:
            return Type()
        seen = set() if seen is None else seen
        if n in seen:
            return Type(node=n)
        seen.add(n)
        kind = n.kind
        if kind == 'Interpolation':
            return Type('string','string',n,witness=n)
        if kind=='CompileSwitchExpr':
            # Only the selected arm determines the type. Let a native witness
            # select it rather than borrowing an inactive arm's shape.
            return Type(node=n,witness=n)
        if kind=='SwitchExpr':
            cases=n.data['cases']
            return self.type_of(n.source.at(cases[0]['result'],n),seen) if cases else Type(node=n)
        if kind in {"Paren", "FieldValue"}:
            return self.type_of(n.role("expr") if kind == "Paren" else n.role("value"), seen)
        if kind == "Literal":
            name = {"integer": "int", "float": "f64", "string": "string", "rune": "rune"}.get(n.data["op"], "")
            return Type("string" if name == "string" else "scalar", name, n, witness=n, untyped=True)
        if kind == "Ident" and n.text in {"true", "false"}:
            return Type("scalar", "bool", n, witness=n, untyped=True)
        symbol = self.symbol(n)
        if symbol:
            if symbol.projection:
                owner, member = symbol.projection
                result = self.field(self.type_of(owner, seen), member)
                return result[0] if result else Type(node=n)
            if len(symbol.candidates) > 1 and any(any(a.kind in {"When", "CompileSwitch"} for a in s.declaration.ancestors()) for s in symbol.candidates):
                return Type(node=n, witness=n)
            if symbol.annotation:
                return replace(self.type_node(symbol.annotation), witness=n)
            if symbol.value:
                if symbol.value.kind == "Procedure":
                    return self.type_node(symbol.value.role("type"))
                if symbol.value.kind == "Call":
                    plan = self.resolve_call(symbol.value)
                    results = self.fields(plan.signature.role("results")) if plan else []
                    index = symbol.index if len(symbol.declaration.group("values")) == 1 else 0
                    if index < len(results):
                        return replace(self.type_node(results[index][1], plan.substitutions), witness=n)
                result = self.type_of(symbol.value, seen)
                result.witness = n
                result.untyped = not symbol.declaration.data.get("mutable", True) and result.untyped
                return result
            if symbol.declaration.kind == "Range":
                container = self.type_of(symbol.declaration.role("expr"), seen)
                return container.elem or Type(node=n)
        if kind == "Compound":
            if n.role("type"):
                return self.type_node(n.role("type"))
            if n.source.is_array(n):
                elements = n.group("elems")
                return Type("array", node=n, elem=self.type_of(elements[0], seen) if elements else Type(), length=len(elements), witness=n)
        if kind == "Call":
            callee = n.role("callee")
            if callee.kind=='Ident' and self.symbol(callee) is None and n.group('args'):
                if callee.text=='make': return self.type_node(n.group('args')[0])
                if callee.text=='new': return Type('pointer',node=n,elem=self.type_node(n.group('args')[0]))
            if callee.kind=='Deref' and self.type_of(callee.role('expr'),seen).kind=='set':
                return self.type_of(callee.role('expr'))
            # A named type conversion is checked by Odin; its shape is known.
            target = self.type_node(callee)
            if target.kind not in {"unknown", "procedure", "generic"}:
                target.witness = n
                target.untyped = False
                return target
            plan = self.resolve_call(n)
            if plan:
                results = self.fields(plan.signature.role("results"))
                if results:
                    return self.type_node(results[0][1], plan.substitutions)
        if kind == "Cast":
            return self.type_node(n.role("type"))
        if kind == "Deref":
            return self.type_of(n.role("expr"), seen).elem or Type(node=n)
        if kind == "Selector":
            base_type = self.type_node(n.role("expr"))
            if base_type.kind == "enum":
                return replace(base_type, witness=n)
            result = self.field(self.type_of(n.role("expr"), seen), n.role("field").text)
            return replace(result[0], witness=n) if result else Type(node=n)
        if kind == "Index":
            return self.type_of(n.role("expr"), seen).elem or Type(node=n)
        if kind == "Slice":
            container = self.type_of(n.role("expr"), seen)
            return container if container.kind == "string" else Type("slice", node=n, elem=container.elem)
        if kind in {"Ternary", "WhenExpr"}:
            return self.type_of(n.role("x"), seen)
        if kind == "Binary":
            if n.data["op"] in {"in", "not_in", "==", "!=", "<", ">", "<=", ">=", "&&", "||"}:
                return Type("scalar", "bool", n)
            return self.type_of(n.role("left"), seen)
        if kind == "Unary":
            inner = self.type_of(n.role("expr"), seen)
            return Type("pointer", node=n, elem=inner) if n.data["op"] == "&" else inner
        return Type(node=n)

    @staticmethod
    def fields(node):
        return [(binding_name(name) if name else "", f.role("type")) for f in node.children if f.kind == "Field"
                for name in (f.group("names") or [None])] if node else []

    def field(self, typ, name, seen=None):
        seen = set() if seen is None else seen
        if typ.kind == "pointer":
            found = self.field(typ.elem, name, seen)
            return (*found[:2], True) if found else None
        if typ.node is None or typ.node in seen:
            return None
        seen.add(typ.node)
        if typ.kind == "struct":
            fields = typ.node.role("fields")
            for f in fields.children if fields else []:
                if f.kind != "Field":
                    continue
                field_type = self.type_node(f.role("type"), typ.substitutions)
                if any(binding_name(n) == name for n in f.group("names")):
                    return field_type, f, False
                if f.data.get("using_field"):
                    found = self.field(field_type, name, seen)
                    if found:
                        return found
        return None

    def procedure_candidates(self, n, seen=None):
        seen = set() if seen is None else seen
        symbol = self.symbol(n)
        if not symbol and n.kind == "Selector":
            member = self.field(self.type_of(n.role("expr")), n.role("field").text)
            if member and member[0].kind == "procedure":
                symbol = Symbol(n.role("field").text, n.role("field"), member[1], member[1].source.scope(member[1]),
                                annotation=member[0].node)
        if not symbol or symbol in seen:
            return []
        seen.add(symbol)
        if symbol.annotation and symbol.annotation.kind == "ProcedureType":
            return [(symbol, symbol.annotation)]
        value = symbol.value
        if value:
            if value.kind == "Procedure":
                return [(symbol, value.role("type"))]
            if value.kind == "ProcedureGroup":
                return [candidate for arg in value.group("args") for candidate in self.procedure_candidates(arg, seen.copy())]
            if value.kind in {"Ident", "Selector"}:
                return self.procedure_candidates(value, seen)
        return []

    def unify(self, formal, actual, substitutions):
        if formal is None:
            return 0
        if formal.kind == "PolyType":
            name = formal.data["name"]
            if actual.kind != "unknown":
                substitutions.setdefault(name, actual)
            specialization = formal.role("specialization")
            return self.unify(specialization, actual, substitutions) if specialization else 1
        if formal.kind == "Ident" and formal.text not in substitutions:
            generic = self.type_node(formal)
            if generic.kind == "generic" and actual.kind != "unknown":
                substitutions[formal.text] = actual
                return 1
        if formal.kind == "Ident" and formal.text in substitutions:
            expected = substitutions[formal.text]
        else:
            expected = self.type_node(formal, substitutions)
        if formal.kind == "ArrayType" and actual.kind in {"array", "slice"}:
            length = formal.role("len")
            if length and length.kind == "PolyType":
                substitutions[length.data["name"]] = actual.length
            elif length and actual.length is not None:
                count = formal.source.constant_int(length)
                if count is not None and count != actual.length:
                    return -1000
            score = self.unify(formal.role("elem"), actual.elem or Type(), substitutions)
            return score + 3 if score >= 0 else score
        if actual.kind == "unknown" or expected.kind in {"unknown", "generic"}:
            return 0
        if expected.kind != actual.kind:
            return -1000
        if expected.kind in {"enum", "struct", "union"} and expected.node is not actual.node:
            return -1000
        if expected.nominal is not actual.nominal and not actual.untyped:
            return -1000
        if expected.name and actual.name and expected.name != actual.name:
            if not actual.untyped:
                return -1000
            return 0
        return 0 if actual.untyped else 5

    def resolve_call(self, call):
        if call in self.call_plans:
            return self.call_plans[call]
        key = (call, "call")
        if key in self.active:
            return None
        self.active.add(key)
        try:
            candidates = []
            for symbol, signature in self.procedure_candidates(call.role("callee")):
                fields = self.fields(signature.role("params"))
                substitutions, score, valid = {}, 0, True
                args = call.group("args")
                generics = self.generic_parameters(signature)
                for index, arg in enumerate(args):
                    if call.role("callee").kind == "Selector" and call.role("callee").data.get("op") == "->":
                        index += 1
                    if index >= len(fields):
                        break  # Odin validates defaults, variadics, and arity.
                    if arg.kind == "FieldValue":
                        param = next((f for f in fields if f[0] == arg.role("field").text), None)
                        arg = arg.role("value")
                    else:
                        param = fields[index]
                    if param:
                        if param[0] in generics:
                            if generics[param[0]] == "typeid":
                                substitutions[param[0]] = replace(self.type_node(arg), witness=("type", arg))
                            else:
                                value = arg.source.constant_int(arg)
                                if value is not None:
                                    substitutions[param[0]] = value
                        amount = self.unify(param[1], self.type_of(arg), substitutions)
                        score += amount
                        valid &= amount >= 0
                if valid:
                    candidates.append(Candidate(symbol, signature, substitutions, score))
            if not candidates:
                return None
            candidates.sort(key=lambda c: c.score, reverse=True)
            if len(candidates) > 1 and candidates[0].score == candidates[1].score:
                return None  # Preserve native ambiguity instead of choosing one.
            return candidates[0]
        finally:
            self.active.remove(key)

    def protection(self, n):
        if n is None:
            return None
        if n.kind == "Paren":
            return self.protection(n.role("expr"))
        symbol = self.symbol(n)
        if symbol:
            if symbol.projection:
                owner, member = symbol.projection
                field = self.field(self.type_of(owner), member)
                return None if field and field[2] else self.protection(owner)
            if any(s.immutable for s in symbol.candidates or [symbol]):
                marker = self.markers.get((symbol.declaration, symbol.name))
                if marker:
                    marker = self.access(n.source, symbol.node.source, marker)
                return Protection(symbol, [marker] if marker else [])
            return None
        if n.kind == "Deref":
            return None  # Data reached through an existing pointer is shallow.
        if n.kind in {"Selector", "Index"}:
            base = n.role("expr")
            protected = self.protection(base)
            if not protected:
                return None
            typ = self.type_of(base)
            if n.kind == "Selector":
                if typ.kind == "pointer":
                    return None
                field = self.field(typ, n.role("field").text)
                if field and field[2]:
                    return None
                if typ.kind in {"unknown", "generic"}:
                    protected.predicates.append(f"!{{meta}}.type_is_pointer(type_of({base.source.render(base)}))")
            else:
                if typ.kind in {"slice", "dynamic", "pointer", "string"}:
                    return None
                if typ.kind in {"unknown", "generic"}:
                    code = base.source.render(base)
                    protected.predicates.append(f"({{meta}}.type_is_array(type_of({code})) || {{meta}}.type_is_enumerated_array(type_of({code})) || {{meta}}.type_is_map(type_of({code})))")
            return protected
        return None

    def access(self, caller, owner, name):
        if caller.path.parent == owner.path.parent:
            return name
        for namespace, imported in caller.imports.items():
            if caller.import_dir(imported) == owner.path.parent:
                return caller.namespace_code(namespace) + "." + name
        return name

    @staticmethod
    def generic_parameters(signature):
        result = {}
        params = signature.role("params")
        for f in params.children if params else []:
            if f.kind == "Field":
                for name in f.group("names"):
                    if name.kind == "PolyType":
                        annotation = f.role("type")
                        result[name.data["name"]] = "typeid" if annotation.kind == "TypeidType" else annotation.text
        for n in descendants(signature):
            if n.kind == "PolyType":
                length = n.parent and n.parent.kind == "ArrayType" and n.parent.role("len") is n
                result.setdefault(n.data["name"], "int" if length else "typeid")
        return result

    def template(self, n):
        if n.kind == "PolyType":
            specialization = n.role("specialization")
            return self.template(specialization) if specialization else n.data["name"]
        return n.source.splice(n, [(c.start, c.end, self.template(c)) for c in n.children])

    def witness(self, typ, caller):
        if not isinstance(typ, Type):
            return str(typ)
        if isinstance(typ.witness, tuple):
            return typ.witness[1].source.render(typ.witness[1])
        if typ.witness:
            code = typ.witness.source.render(typ.witness)
            if typ.untyped:
                caller.needs_default = True
                code = f"{caller.prefix}default_value({code})"
            return f"type_of({code})"
        return None

    def call_hint(self, call, arg, index):
        from .compiler import Hint
        if not any(n.source.is_array(n) for n in descendants(arg)):
            return None
        plan = self.call_plans.get(call)
        if not plan:
            return None
        original = self.declaration_for(call.role("callee"))
        group = original and any(v.kind == "ProcedureGroup" for v in original.group("values"))
        if not group and "$" not in plan.signature.role("params").text:
            return None  # A direct type_of(callee) query retains native selection.
        fields = self.fields(plan.signature.role("params"))
        if arg.kind == "FieldValue":
            index = next((i for i, f in enumerate(fields) if f[0] == arg.role("field").text), -1)
        if not 0 <= index < len(fields):
            return None
        annotation = fields[index][1]
        if not annotation:
            return None
        parameters = self.generic_parameters(plan.signature)
        # Specialization binders ($C/[$N]$E) supply the constrained shape, not C.
        identifiers = {n.text for n in descendants(annotation) if n.kind == "Ident" and (not n.parent or n.parent.kind != "PolyType")}
        identifiers |= {n.data["name"] for n in descendants(annotation) if n.kind == "PolyType" and not n.role("specialization")}
        used = [(name, kind) for name, kind in parameters.items() if name in identifiers]
        values = []
        for name, _ in used:
            value = plan.substitutions.get(name)
            code = self.witness(value, call.source) if value is not None else None
            if code is None:
                return None
            values.append(code)
        key = (plan.symbol.declaration, index)
        if key not in self.wrappers:
            name = annotation.source.prefix + f"parameter_{plan.symbol.declaration.start}_{index}"
            generic = ", ".join(f"${name}: {kind}" for name, kind in used)
            generic = f"({generic})" if generic else ""
            code = f"\n{name} :: struct{generic} {{ value: {self.template(annotation)} }}\n"
            self.wrappers[key] = (name, code, annotation)
            # A native when chooses the declaration and its type witness
            # together. Emitting only the first branch's witness would make
            # contextual literals depend on source order rather than #config.
            for alternative in plan.symbol.candidates:
                if alternative.declaration is plan.symbol.declaration or not any(
                    a.kind in {"When", "CompileSwitch"} for a in alternative.declaration.ancestors()
                ):
                    continue
                value = alternative.value
                signature = value.role("type") if value and value.kind == "Procedure" else alternative.annotation
                other_fields = self.fields(signature.role("params")) if signature and signature.kind == "ProcedureType" else []
                if index >= len(other_fields):
                    continue
                other = other_fields[index][1]
                other_parameters = self.generic_parameters(signature)
                other_identifiers = {n.text for n in descendants(other) if n.kind == "Ident" and (not n.parent or n.parent.kind != "PolyType")}
                other_identifiers |= {n.data["name"] for n in descendants(other) if n.kind == "PolyType" and not n.role("specialization")}
                other_used = [(p, k) for p, k in other_parameters.items() if p in other_identifiers]
                if [kind for _, kind in used] != [kind for _, kind in other_used]:
                    continue
                other_generic = ", ".join(f"${p}: {k}" for p, k in other_used)
                other_generic = f"({other_generic})" if other_generic else ""
                other_code = f"\n{name} :: struct{other_generic} {{ value: {self.template(other)} }}\n"
                self.wrappers[alternative.declaration, index] = (name, other_code, other)
        name = self.access(call.source, annotation.source, self.wrappers[key][0])
        if values:
            name += "(" + ", ".join(values) + ")"
        call.source.needs_intrinsics = True
        query = f'#type {call.source.prefix}meta.type_field_type({name}, "value")'
        return Hint(annotation.source, annotation, query)

    def prepare_contexts(self):
        for source in self.sources:
            for call in source.nodes:
                if call.kind == "Call":
                    self.call_plans[call] = self.resolve_call(call)
        for source in self.sources:
            for call in source.nodes:
                if call.kind == "Call":
                    for index, arg in enumerate(call.group("args")):
                        self.call_hint(call, arg, index)

    def declaration_support(self, n):
        source = n.source
        parts = []
        for name in n.group("names"):
            marker = self.markers.get((n, binding_name(name)))
            if marker:
                parts.append(source.mapping.wrap(f"\n{marker} :: {'true' if n.data.get('immutable') else 'false'}\n",
                                                  source.path, name.start, name.end, "immutable"))
        for (declaration, _), (_, code, origin) in self.wrappers.items():
            if declaration is n:
                parts.append(source.mapping.wrap(code, source.path, origin.start, origin.end, "type-context"))
        return "".join(parts)

    def prepare_storage_checks(self):
        for source in self.sources:
            for n in source.nodes:
                targets = []
                if n.kind == "Assignment" and n.data.get("op") != "in":
                    targets = [(lhs, lhs, "write") for lhs in n.group("lhs")]
                elif n.kind == "Unary" and n.data["op"] == "&":
                    targets = [(n, n.role("expr"), "take a mutable address of")]
                elif n.kind == "Slice":
                    targets = [(n, n.role("expr"), "create a mutable slice of")]
                elif n.kind == "Range" and any(name.kind == "Unary" and name.data["op"] == "&" for name in n.group("names")):
                    targets = [(n.role("expr"), n.role("expr"), "iterate by mutable reference over")]
                elif n.kind == "Call" and n.role("callee").text in {"raw_data", "soa_unzip"} and n.group("args"):
                    targets = [(n, n.group("args")[0], "obtain a mutable data pointer to")]
                elif n.kind=='Call' and n.role('callee').text=='clear' and self.symbol(n.role('callee')) is None and n.group('args') and self.type_of(n.group('args')[0]).kind in {'set','unknown','generic'}:
                    targets=[(n,n.group('args')[0],'write')]
                elif n.kind == "Call" and n.role("callee").kind == "Selector" and n.role("callee").data.get("op") == "->":
                    callee = n.role("callee")
                    receiver = callee.role("expr")
                    typ = self.type_of(receiver)
                    method = self.field(typ, callee.role("field").text)
                    if typ.kind != "pointer" and method and method[0].kind == "procedure":
                        params = self.fields(method[0].node.role("params"))
                        if params and self.type_node(params[0][1]).kind == "pointer":
                            targets = [(n, receiver, "take an implicit mutable address of")]
                    elif typ.kind in {"unknown", "generic"}:
                        targets = [(n, receiver, "take an implicit mutable address of")]
                elif n.kind == "Cast" and self.type_node(n.role("type")).kind == "slice":
                    targets = [(n, n.role("expr"), "create a mutable slice of")]
                for origin, target, action in targets:
                    protected = self.protection(target)
                    if not protected:
                        continue
                    if n.kind=='Call' and n.role('callee').text=='clear' and self.type_of(target).kind in {'unknown','generic'}:
                        protected.predicates.append(source.expressions.set_trait(source.render(target)))
                    if action == "take an implicit mutable address of" and self.type_of(target).kind in {"unknown", "generic"}:
                        code = source.render(target)
                        field = n.role("callee").role("field").text
                        protected.predicates.append(
                            f"(!{{meta}}.type_is_pointer(type_of({code})) && {{meta}}.type_is_pointer({{meta}}.type_proc_parameter_type(type_of(({code}).{field}), 0)))")
                    if action in {"create a mutable slice of", "iterate by mutable reference over", "obtain a mutable data pointer to"}:
                        typ = self.type_of(target)
                        if typ.kind in {"slice", "dynamic", "map", "pointer", "string"}:
                            continue
                        if typ.kind in {"unknown", "generic"}:
                            code = source.render(target)
                            protected.predicates.append(f"({{meta}}.type_is_array(type_of({code})) || {{meta}}.type_is_enumerated_array(type_of({code})))")
                    message = f"Cannot {action} immutable storage '{protected.symbol.name}'"
                    if not protected.predicates:
                        token = source.prefix + "immutable_error_" + str(len(source.blocked))
                        source.blocked[origin] = (token, message)
                        source.error_messages[token] = message
                    else:
                        statement = next((p for p in [n, *n.ancestors()] if p.kind in {
                            "Assignment", "Declaration", "Return", "ExpressionStatement", "Range", "If", "For", "Switch", "When"
                        }), n)
                        source.needs_intrinsics = True
                        condition = " && ".join(f"({p})" for p in protected.predicates).replace("{meta}", source.prefix+"meta")
                        parent = statement.parent
                        control = parent if parent and parent.kind in {"If", "For"} else statement if statement.kind in {"If", "For"} else None
                        converted_block = next((p for p in statement.ancestors() if p.kind == "Block"), None)
                        if control:
                            source.guard_block(control.role("body"), condition, message, origin)
                        elif converted_block and source.raw[converted_block.start:converted_block.start+1] != b"{":
                            source.guard_block(converted_block, condition, message, origin)
                        else:
                            source.guards.setdefault(statement, []).append((condition, message, origin))
                        source.guard_messages[origin.start, origin.end] = message

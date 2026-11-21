"""Lexical bindings and the type/procedure facts needed by Thor additions.

This is not an Odin type checker. Shapes guide Thor checks and context lowering;
generated type witnesses ask Odin to resolve exact types, generic constraints,
assignability and overload validity in the defining scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from contextlib import contextmanager
import hashlib
from pathlib import Path

BIG_TYPES_SOURCE = Path(__file__).resolve().parents[1]/'runtime/big/big.odin'


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
    lexical: dict = field(default_factory=dict)
    members: dict = field(default_factory=dict)
    soa: bool = False


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
        if self.declaration.kind=='Range' and self.index==1 and self.declaration.role('index_type'):
            return True
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
    if n.kind=='Ident' and n.data.get('name')=='_':return '_'
    if n.kind == 'Unary' and (operand := n.role('expr')) and operand.kind == 'Ident':
        return operand.data.get('name') or operand.text
    if n.kind in {"Unary", "PolyType"}:
        return n.data.get("name") or n.text.lstrip("&$")
    return n.text


class Resolver:
    def __init__(self, sources):
        self.sources = []
        self.native = None
        self.native_enabled = False
        self.scopes = {}
        self.by_decl = {}
        self.using = {}
        self.active = set()
        self.call_plans = {}
        self.bound_types = {}
        self.bound_constants = {}
        self.integer_active = set()
        self.wrappers = {}
        self.markers = {}
        self.lexical_scope_cache = {}
        self.query_caches = None
        self.add_sources(sources)

    def add_sources(self, sources):
        sources = [source for source in sources if source not in self.sources]
        if sources and self.query_caches is not None:
            for cache in self.query_caches.values():cache.clear()
        self.sources.extend(sources)
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
                    parent = next((a for a in n.ancestors() if a.kind in {"ProcedureType", "StructType", "BitFieldType"}), None)
                    if parent and parent.kind == "ProcedureType":
                        scope = parent.parent if parent.parent and parent.parent.kind == "Procedure" else parent
                        names, annotation = n.group("names"), n.role("type")
                        if n.role('default'):values=[n.role('default')]
                        if n.data.get('using_field'):
                            self.using.setdefault(self.scope_key(scope), []).append(n)
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
                        binding_annotation=n.role('value_type' if i==0 else 'index_type') if n.kind=='Range' else annotation
                        symbol = Symbol(binding_name(name), name, n, scope, binding_annotation,
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
        if context in self.lexical_scope_cache:return self.lexical_scope_cache[context]
        result = [a for a in context.ancestors() if a.source.is_scope(a) or a.kind == "ProcedureType"]
        if context.source.is_scope(context):
            result.insert(0, context)
        if context.source.root not in result:
            result.append(context.source.root)
        self.lexical_scope_cache[context]=result
        return result

    def lookup(self, name, context, *, projections=True):
        key = (name, context)
        if key in self.active:
            return None
        self.active.add(key)
        try:
            own_field = next((field for field in context.ancestors() if field.kind=='Field'
                              and any(part and (part is context or part in context.ancestors())
                                      for part in [field.role('type'),field.role('default')])),None)
            for scope in self.lexical_scopes(context):
                if scope.kind=='Range' and 'comprehension' in scope.data:
                    source=scope.role('expr')
                    if context is source or source in context.ancestors():continue
                symbols = self.scopes.get(self.scope_key(scope), {}).get(name, [])
                if own_field:
                    symbols = [symbol for symbol in symbols if symbol.node not in own_field.group('names')]
                if scope.kind != 'File':
                    # Runtime locals enter scope after their initializer. In
                    # `x := x`, the RHS still names the enclosing binding.
                    # Constants/procedures retain Odin's forward visibility.
                    symbols = [s for s in symbols if s.declaration.kind != 'Declaration'
                               or not s.declaration.data.get('mutable', True)
                               or context.start >= s.declaration.end
                               or context in s.declaration.group('names')]
                if scope.kind=='File':
                    from .imports import file_private
                    visible=[s for s in symbols if s.node.source is context.source or not file_private(s.declaration)]
                    if visible:symbols=visible
                if scope.kind == "File" and (name in context.source.selected or name in context.source.imports):
                    symbols = [s for s in symbols if s.node.source is context.source and
                               s.declaration not in context.source.conditional_conflicts]
                if symbols:
                    # File-private selective aliases do not become declarations
                    # here. When branches share a native declaration scope.
                    return next((s for s in symbols if s.immutable), symbols[0])
                if projections:
                    for using in self.using.get(self.scope_key(scope), []):
                        if (scope.kind != 'File' and (using.kind == 'Using'
                                or using.kind == 'Declaration' and using.data.get('mutable', True))
                                and using.source is context.source and using.end > context.start):
                            continue
                        owners = using.group("values") if using.kind == "Using" else using.group("names")
                        for owner in owners:
                            if using.kind=='Using' and owner.kind=='Ident' and not self.lookup(owner.text,owner):
                                imported=owner.source.imports.get(owner.text)
                                if imported:
                                    member=self.package_symbol(self.import_directory(owner.source,imported),name)
                                    if member:return member
                                    continue
                            owner_type = self._projection_owner_type(owner)
                            field = self.field(owner_type, name)
                            if field:
                                original = self.symbol(owner)
                                if original:
                                    return Symbol(name, field[1], original.declaration, scope,
                                                  projection=(owner, name), candidates=original.candidates)
            return None
        finally:
            self.active.remove(key)

    def _projection_owner_type(self, owner, seen=None):
        symbol = self.symbol(owner)
        if symbol and symbol.declaration.kind == 'TypeSwitch':
            # Do not replace ordinary alias expressions' native type witnesses.
            # The promoted field only needs the type at its `using` site.
            case = next((case for case in owner.ancestors() if case.kind == 'Case'
                         and case.parent and case.parent.parent is symbol.declaration), None)
            types = case.group('types') if case else []
            if len(types) == 1 and types[0].text != 'nil':
                narrowed = self.type_node(types[0])
                if symbol.node.kind == 'Unary' and symbol.node.data.get('op') == '&':
                    return Type('pointer', node=owner, elem=narrowed, witness=owner)
                return replace(narrowed, witness=owner)
        return self.type_of(owner, seen)

    def package_symbol(self, directory, name):
        symbols = self.scopes.get((directory, "package"), {}).get(name, [])
        if not symbols and self.native_enabled and self.native and self.native.effect_discovery:
            self.native.ensure_symbol(directory,name)
            symbols=self.scopes.get((directory,"package"),{}).get(name,[])
        return next((s for s in symbols if s.immutable), symbols[0] if symbols else None)

    def import_directory(self, source, path):
        directory = source.import_dir(path)
        if self.native_enabled and self.native and (directory is None or getattr(source, 'native_inspected', False)):
            directory = self.native.load(source, path) or directory
        elif directory is None and self.native:
            inspected=self.native.directory(source,path)
            if inspected in self.native.packages:directory=inspected
        return directory

    @contextmanager
    def cached_queries(self):
        """Reuse resolved source facts during one fixed source-analysis phase.

        Native package discovery invalidates these facts. Generic bindings and
        recursive/partially resolved queries retain their normal uncached path.
        Permission graphs are not cached here.
        """
        previous=self.query_caches
        source_count=len(self.sources)
        self.query_caches={'symbol':{},'type_node':{},'type_of':{}}
        try:yield
        finally:
            if previous is not None and len(self.sources)!=source_count:
                for cache in previous.values():cache.clear()
            self.query_caches=previous

    def symbol(self, n, seen=None):
        cache=self.query_caches
        use=bool(cache is not None and n is not None and seen is None and
                 not self.bound_types and not self.bound_constants and (n.text,n) not in self.active)
        key=n,self.native_enabled
        if use and key in cache['symbol']:return cache['symbol'][key]
        result=self._symbol(n,seen)
        if use and result is not None and not result.projection:cache['symbol'][key]=result
        return result

    def _symbol(self, n, seen=None):
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
                return self.package_symbol(self.import_directory(n.source, n.source.imports[namespace]), member)
            if self.native_enabled and self.native and self.native.effect_discovery and getattr(n.source,'native_inspected',False):
                return self.package_symbol(n.source.path.parent,n.text)
        if n.kind == "Selector":
            base, member = n.role("expr"), n.role("field")
            if base and base.kind == "Ident" and not self.lookup(base.text, base):
                imported = n.source.imports.get(base.text)
                if imported:
                    return self.package_symbol(self.import_directory(n.source, imported), member.text)
        return None

    def declaration_for(self, n):
        symbol = self.symbol(n)
        return symbol.declaration if symbol else None

    def native_builtin(self, callee, name):
        if callee.source.purity:
            return callee.source.purity.builtin_name(callee) == name
        return (callee.kind=='Ident' and callee.text==name and self.symbol(callee) is None
                and callee.text not in callee.source.imports and callee.text not in callee.source.selected)

    def native_builtin_alias(self, callee, name):
        seen=set()
        while callee is not None and callee not in seen:
            seen.add(callee)
            if callee.kind=='Paren':
                callee=callee.role('expr')
                continue
            symbol=self.symbol(callee)
            if symbol is None:
                imported=callee.source.purity.imported(callee) if callee.source.purity else None
                if imported:
                    return imported==('base:builtin',name) and callee.source.import_dir(imported[0]) is None
                return (callee.kind=='Ident' and callee.text==name
                        and name not in callee.source.imports and name not in callee.source.selected)
            if symbol.declaration.data.get('mutable') or symbol.immutable:return False
            callee=symbol.value
            if callee is not None and callee.kind not in {'Ident','Selector','Paren'}:return False
        return False

    def known_type_operand(self, node, seen=None):
        """Conclusive type expressions only, for value-use diagnostics.

        Approximate value shapes, runtime typeid variables, polymorphic formals
        and competing conditional declarations are left to Odin. In particular,
        a user procedure named type_of must not be mistaken for the builtin.
        """
        seen = set() if seen is None else seen
        if node is None or node in seen: return False
        seen.add(node)
        if node.kind == 'Paren': return self.known_type_operand(node.role('expr'), seen)
        if node.kind.endswith('Type'): return node.kind != 'PolyType'
        symbol = self.symbol(node)
        if symbol:
            if len(symbol.candidates) > 1: return False
            declaration = symbol.declaration
            return (declaration.kind == 'Declaration' and not declaration.data.get('mutable', True)
                    and symbol.value is not None and self.known_type_operand(symbol.value, seen))
        if node.kind == 'Call':
            callee = node.role('callee')
            return self.native_builtin(callee, 'type_of')
        return (node.kind == 'Ident' and node.text not in node.source.imports
                and node.text not in node.source.selected and node.source.denotes_type(node))

    def generic_default_error(self, procedure):
        """Recognize anonymous default signatures Odin cannot specialize.

        Only generic bindings owned by this parameter list (or by the default
        procedure itself) are still unbound here. Types from an enclosing
        procedure body can already be concrete when that body is instantiated.
        Leave inferred default-parameter types and other uncertain cases to Odin.
        """
        value = procedure
        while value.parent and value.parent.kind == 'Paren' and value.parent.role('expr') is value:
            value = value.parent
        field = value.parent
        if not field or field.kind != 'Field' or field.role('default') is not value:
            return None
        host_signature = next((n for n in field.ancestors() if n.kind == 'ProcedureType'), None)
        if host_signature is None or field.parent is not host_signature.role('params'):
            return None
        host = host_signature.parent if host_signature.parent and host_signature.parent.kind == 'Procedure' else host_signature
        signature = procedure.role('type')
        dependencies = set()

        def visit(node):
            if node is None:
                return
            # A computed type/length can be independent of a generic mentioned
            # inside it (e.g. [N when false else 2]int). Without Odin's type
            # evaluator, those expressions cannot prove polymorphism.
            if node.kind in {'Call', 'Selector', 'WhenExpr', 'Ternary', 'Binary'}:
                return
            if node.kind == 'Field':
                for name in node.group('names'):
                    if name.kind == 'PolyType':
                        dependencies.add(name.data['name'])
                visit(node.role('type'))
                return  # Defaults are values, not necessarily part of the signature's type.
            if node.kind == 'PolyType':
                dependencies.add(node.data['name'])
            elif node.kind == 'Ident':
                parent = node.parent
                if parent and parent.kind in {'Selector', 'FieldValue'} and parent.role('field') is node:
                    return
                symbol = self.symbol(node)
                if symbol and symbol.node.kind == 'PolyType' and symbol.scope in {host, procedure}:
                    dependencies.add(symbol.name)
            for child in node.children:
                visit(child)

        visit(signature)
        if not dependencies:
            return None
        names = ', '.join(n.text for n in field.group('names'))
        generics = ', '.join(sorted(dependencies))
        message = (f"Anonymous default procedure for '{names}' has an unspecialized generic signature ({generics}). "
                   "Use a named generic procedure as the default, or require an explicit callback.")
        return signature, message

    def frozen_slice_storage(self, typ):
        """Value storage that freezing can expose as a read-only slice."""
        pending=[typ];seen=set()
        while pending:
            typ=pending.pop()
            key=(typ.kind,typ.node,tuple((name,id(value) if isinstance(value,Type) else repr(value))
                                       for name,value in sorted(typ.substitutions.items())))
            if key in seen:continue
            seen.add(key)
            if typ.soa or typ.kind in {'slice','array','fixed','unknown','generic'}:return True
            if typ.kind=='struct':
                if typ.members:pending.extend(typ.members.values())
                elif typ.node:
                    pending.extend(self.type_node(field.role('type'),typ.substitutions)
                        for field in (typ.node.role('fields') or typ.node).children if field.kind=='Field')
            if typ.kind=='union' and typ.node:
                pending.extend(self.type_node(variant,typ.substitutions) for variant in typ.node.group('variants'))
        return False

    def type_node(self, n, substitutions=None, seen=None):
        cache=self.query_caches
        use=cache is not None and n is not None and seen is None and not self.bound_types and not self.bound_constants
        if use:
            key=n,self.native_enabled,tuple(sorted((substitutions or {}).items()))
            if key in cache['type_node']:return cache['type_node'][key]
        result=self._type_node(n,substitutions,seen)
        if use and result.kind not in {'unknown','generic'}:cache['type_node'][key]=result
        return result

    def _type_node(self, n, substitutions=None, seen=None):
        if n is None:
            return Type()
        substitutions = substitutions or {}
        from . import resulttypes
        if resulttypes.is_query(self,n):
            call=resulttypes.expression(n)
            result=resulttypes.resolve(self,call,substitutions) if call else None
            return resulttypes.packed(result) if result else Type()
        seen = set() if seen is None else seen
        key = (n, "type")
        if key in seen:
            return Type(node=n)
        seen.add(key)
        kind = n.kind
        # The runtime namespace is intentionally not loaded as ordinary SDK
        # source. Its allocator status is nevertheless a known enum, rather
        # than an unknown value which might contain slice references.
        imported=None
        if kind=='Selector' and n.role('expr').kind=='Ident' and self.lookup(n.role('expr').text,n.role('expr')) is None:
            imported=(n.source.imports.get(n.role('expr').text),n.role('field').text)
        elif kind=='Ident' and n.text in n.source.selected and self.lookup(n.text,n) is None:
            namespace,name=n.source.selected[n.text].split('.',1)
            imported=(n.source.imports.get(namespace),name)
        if imported and imported[0]=='base:builtin':
            directory=n.source.import_dir(imported[0]);root=n.source.info.get('odin_root')
            if directory is None or root and directory==(Path(root)/'base/builtin').resolve():
                primitive=self.primitive_type(imported[1],n)
                if primitive is not None:return primitive
        if imported==('base:runtime','Allocator_Error'):
            directory=n.source.import_dir(imported[0]);root=n.source.info.get('odin_root')
            if directory is None or root and directory==(Path(root)/'base/runtime').resolve():
                return Type('enum','Allocator_Error',witness=('type',n))
        if kind=='Comprehension':return n.source.comprehensions.result_type(n,seen)
        if kind=='Call' and self.native_builtin(n.role('callee'),'type_of') and len(n.group('args'))==1:
            return self.type_of(n.group('args')[0],seen)
        symbol=self.symbol(n) if kind=='Ident' else None
        if kind=='Ident' and n.source.thor and n.text in {'Integer','Rational'} and symbol is None:
            return Type('big_integer' if n.text=='Integer' else 'big_rational',n.text,n)
        bound=self.bound_types.get((n,n.data.get('name'))) if kind=='PolyType' else self.bound_types.get((symbol.declaration,symbol.name)) if symbol else None
        if bound is not None:return bound
        if kind in {'Run','Runtime'}:return self.type_of(n.role('expr'),seen)
        if kind == 'SetType':
            return Type('set',node=n,elem=self.type_node(n.group('args')[0],substitutions,seen))
        if kind in {"Paren", "HelperType"}:
            return self.type_node(n.role("expr"), substitutions, seen)
        if kind=='WhenExpr':
            left=self.type_node(n.role('x'),substitutions,seen.copy())
            right=self.type_node(n.role('y'),substitutions,seen.copy())
            # A target-dependent numeric alias still always has value storage.
            # Keep its exact width deferred to Odin rather than inventing one.
            if left.kind==right.kind=='scalar' and left.name not in {'rawptr','any','typeid'} and right.name not in {'rawptr','any','typeid'}:
                return Type('scalar',left.name if left.name==right.name else '',n,witness=('type',n))
        if kind == "PolyType":
            name = n.data["name"]
            if name in substitutions and isinstance(substitutions[name], Type):
                return substitutions[name]
            specialization = n.role("specialization")
            if specialization:
                return self.type_node(specialization, substitutions, seen)
            return Type("generic", name, n)
        if kind=="Ellipsis":return Type("slice",node=n,elem=self.type_node(n.role("expr"),substitutions,seen))
        if kind == "ArrayType":
            length = n.role("len")
            if length is None:
                return Type("slice", node=n, elem=self.type_node(n.role("elem"), substitutions, seen),soa=n.data.get('op')=='soa')
            count = substitutions.get(binding_name(length))
            if not isinstance(count, int):
                count = self.constant_int(length)
            return Type("array", node=n, elem=self.type_node(n.role("elem"), substitutions, seen), length=count,soa=n.data.get('op')=='soa')
        if kind == "DynamicArrayType":
            return Type("dynamic", node=n, elem=self.type_node(n.role("elem"), substitutions, seen),soa=n.data.get('op')=='soa')
        if kind in {"PointerType", "MultiPointerType"}:
            return Type("pointer", node=n, elem=self.type_node(n.role("elem"), substitutions, seen))
        if kind == "MapType":
            return Type("map", node=n, elem=self.type_node(n.role("value"), substitutions, seen))
        if kind == "DistinctType":
            return replace(self.type_node(n.role("type"), substitutions, seen), nominal=n)
        if kind in {"StructType", "UnionType", "EnumType", "BitSetType", "BitFieldType", "ProcedureType"}:
            substitutions=dict(substitutions)
            lexical={}
            owners=set(n.ancestors())
            for (declaration,name),bound in self.bound_types.items():
                if any(owner in owners for owner in declaration.ancestors() if owner.kind=='Procedure'):
                    substitutions.setdefault(name,bound)
                    lexical[declaration,name]=(bound,None)
            for (declaration,name),bound in self.bound_constants.items():
                if any(owner in owners for owner in declaration.ancestors() if owner.kind=='Procedure'):
                    if bound['kind']=='integer':substitutions.setdefault(name,int(bound['value']))
                    symbol=self.by_decl.get((declaration,name))
                    lexical[declaration,name]=(self.type_node(symbol.annotation) if symbol and symbol.annotation else Type('scalar','int'),bound)
            return Type({"StructType": "struct", "UnionType": "union", "EnumType": "enum",
                         "BitSetType": "bitset", "BitFieldType": "struct", "ProcedureType": "procedure"}[kind], node=n, substitutions=substitutions,lexical=lexical)
        if kind == "Ident":
            if n.text in substitutions and isinstance(substitutions[n.text], Type):
                return substitutions[n.text]
            primitive=self.primitive_type(n.text,n)
            if primitive is not None:return primitive
        symbol = self.symbol(n)
        if symbol is None and kind == 'Ident' and n.text == 'Maybe':
            symbol = self.package_symbol(self.import_directory(n.source, 'base:runtime'), 'Maybe')
        if symbol:
            if (symbol.declaration.source.path == BIG_TYPES_SOURCE
                    and symbol.name in {'Integer','Rational'}):
                return Type('big_integer' if symbol.name=='Integer' else 'big_rational',symbol.name,n)
            if symbol.declaration.kind == "PolyType":
                return Type("generic", symbol.name, n)
            if symbol.annotation and (symbol.annotation.kind == "TypeidType" or symbol.annotation.text == "typeid"):
                return Type("generic", symbol.name, n)
            if symbol.value:
                return self.type_node(symbol.value, substitutions, seen)
        if kind == "Call":
            declaration = self.declaration_for(n.role("callee"))
            values = declaration.group("values") if declaration else []
            if not values:
                target = self.type_node(n.role('callee'), substitutions, seen.copy())
                if target.kind in {'struct', 'union'} and target.node:
                    values = [target.node]
            if len(values) == 1 and values[0].kind in {"StructType", "UnionType"}:
                record = values[0]
                parameters = self.fields(record.role("params"))
                # A record's identity contains its own arguments and lexical
                # captures, rather than unrelated type variables of its caller.
                mapping = dict(self.type_node(record).substitutions)
                for owner in record.ancestors():
                    signature=owner.role('type') if owner.kind=='Procedure' else owner if owner.kind=='StructType' else None
                    if signature:
                        for captured in self.generic_parameters(signature):
                            if captured in substitutions:mapping.setdefault(captured,substitutions[captured])
                arguments = n.group("args")
                for index, (name, annotation) in enumerate(parameters):
                    key = name.lstrip("$")
                    arg = next((a.role('value') for a in arguments if a.kind=='FieldValue'
                                and a.role('field').text==key),
                               arguments[index] if index<len(arguments) else None)
                    if arg is None:continue
                    if annotation and annotation.kind=='TypeidType':
                        mapping[key] = self.type_node(arg, substitutions)
                    else:
                        mapping[key] = self.record_constant(arg, substitutions, expected=self.type_node(annotation, substitutions) if annotation else None)
                if (record.source.path == Path(__file__).resolve().parents[1]/'runtime'/'sets'/'sets.odin'
                        and any(name.text=='Set' for name in declaration.group('names'))):
                    return Type('set',node=n,elem=mapping.get('T',Type()))
                return Type("union" if record.kind == "UnionType" else "struct", node=record, substitutions=mapping)
        return Type(node=n)

    def record_constant(self, node, substitutions, seen=None, expected=None):
        """Preserve integer/boolean/text record arguments as values."""
        seen=set() if seen is None else seen
        if node is None or node in seen:return Type()
        seen.add(node)
        key=binding_name(node)
        if key in substitutions:
            value=substitutions[key]
            if isinstance(value,(int,bool,str)) or isinstance(value,tuple) and value and value[0]=='enum':return value
        if node.kind=='Paren':return self.record_constant(node.role('expr'),substitutions,seen,expected)
        if node.text in {'true','false'}:return node.text=='true'
        value=self.constant_int(node)
        if value is not None:return value
        symbol=self.symbol(node)
        if symbol and not symbol.declaration.data.get('mutable',True) and not symbol.immutable and symbol.value:
            return self.record_constant(symbol.value,substitutions,seen,expected)
        if expected and expected.kind=='enum' and node.kind in {'ImplicitSelector','Selector'}:
            if node.kind=='Selector' and not node.source.denotes_type(node.role('expr')):return Type(node=node)
            field=node.role('field').text if node.role('field') else node.text.lstrip('.')
            previous={};next_value=0
            for member in expected.node.group('members'):
                name=member.role('field').text if member.kind=='FieldValue' else member.text
                value=self.record_constant(member.role('value'),{**substitutions,**previous},seen.copy()) if member.kind=='FieldValue' else next_value
                if name==field:return ('enum',expected.node,value) if type(value) is int else Type(node=node)
                if type(value) is int:previous[name]=value;next_value=value+1
                else:next_value=None
            return Type(node=node)
        if node.kind=='Literal' and node.text.startswith(('"','`')):
            from .compiler import unquote
            return unquote(node.text)
        return Type(node=node)

    @staticmethod
    def primitive_type(name, node):
        if name in {'string','cstring'}:return Type('string',name,node)
        if name in {'bool','int','uint','uintptr','rune','byte','rawptr','typeid','any'} or name in {
            f'{p}{bits}{endian}' for p in ['i','u','f','b','complex','quaternion']
            for bits in [8,16,32,64,128,256] for endian in ['', 'le','be']
        }:return Type('scalar',name,node)
        return None

    def result_type(self, n, index, seen=None):
        if n.kind in {'Paren','OrPanic','OrReturn','OrBranch','AutoCast'}: return self.result_type(n.role('expr'), index, seen)
        if n.kind=='OrElse':return self.result_type(n.role('x'),index,seen)
        if index==1 and (n.kind=='TypeAssertion' or n.kind=='Index' and self.type_of(n.role('expr')).kind in {'map','generic','unknown'}):
            return Type('scalar','bool',n)
        if n.kind in {'SwitchResults','FallbackList'}:
            args = n.group('args')
            return self.type_of(args[index], seen) if index < len(args) else Type(node=n)
        if n.kind == 'SwitchExpr':
            arms = [n.source.at(c['result'], n) for c in n.data['cases']]
            value = next((a for a in arms if a.kind != 'SwitchBreak' and not self.is_diverging_call(a)), None)
            return self.result_type(value, index, seen) if value else Type(node=n)
        if n.kind == 'CompileSwitchExpr':
            data=n.source.big.constants.payloads.get(n,{})
            if data.get('kind') in {'big_integer','big_rational'}:return Type(data['kind'],'Integer' if data['kind']=='big_integer' else 'Rational',n,witness=n)
            return Type(node=n)
        if n.kind == 'Call':
            from .builtin_types import result
            builtin=result(self,n,index,seen)
            if builtin is not None:return builtin
            target=self.type_node(n.role('callee'))
            if target.kind in {'big_integer','big_rational'}:
                return Type('scalar','bool',n) if index else target
            extrema=n.source.expressions.extrema.plan(n)
            if index==1 and extrema and extrema[2]:return Type('scalar','bool',n)
            if index==1 and self.native_builtin(n.role('callee'),'make'):return Type('enum','Allocator_Error')
            plan = self.resolve_call(n)
            results = self.fields(plan.signature.role('results')) if plan else []
            if index < len(results):
                typ=self.type_node(results[index][1], plan.substitutions)
                return replace(typ,witness=n) if len(results)==1 else typ
        return self.type_of(n, seen) if index == 0 else Type(node=n)

    def type_of(self, n, seen=None):
        cache=self.query_caches
        use=cache is not None and n is not None and seen is None and not self.bound_types and not self.bound_constants
        key=n,self.native_enabled
        if use and key in cache['type_of']:return cache['type_of'][key]
        result=self._type_of(n,seen)
        if use and result.kind not in {'unknown','generic'}:cache['type_of'][key]=result
        return result

    def _type_of(self, n, seen=None):
        if n is None:
            return Type()
        from . import resulttypes
        if resulttypes.is_query(self,n):
            call=resulttypes.expression(n)
            result=resulttypes.resolve(self,call) if call else None
            return resulttypes.packed(result) if result else Type()
        seen = set() if seen is None else seen
        if n in seen:
            return Type(node=n)
        seen.add(n)
        kind = n.kind
        if kind=='Implicit' and n.text=='context':
            symbol=self.package_symbol(self.import_directory(n.source,'base:runtime'),'Context')
            if symbol is None and self.native_enabled and self.native:symbol=self.native.runtime_type(n.source,'Context')
            return self.type_node(symbol.value) if symbol and symbol.value else Type()
        if kind=='Comprehension':return n.source.comprehensions.result_type(n,seen)
        data=n.source.big.constants.payloads.get(n,{})
        if data.get('kind') in {'big_integer','big_rational'}:return Type(data['kind'],'Integer' if data['kind']=='big_integer' else 'Rational',n,witness=n)
        if kind in {'Run','Runtime'}:return self.type_of(n.role('expr'),seen)
        if kind == 'Interpolation':
            return Type('string','string',n,witness=n)
        if kind=='CompileSwitchExpr':
            # Only the selected arm determines the type. Let a native witness
            # select it rather than borrowing an inactive arm's shape.
            return Type(node=n,witness=n)
        if kind=='SwitchExpr':
            return self.result_type(n, 0, seen)
        if kind=='Procedure':
            return self.type_node(n.role('type'))
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
                result = self.field(self._projection_owner_type(owner, seen), member)
                return result[0] if result else Type(node=n)
            if len(symbol.candidates) > 1 and any(any(a.kind in {"When", "CompileSwitch"} for a in s.declaration.ancestors()) for s in symbol.candidates):
                return Type(node=n, witness=n)
            if symbol.annotation:
                return replace(self.type_node(symbol.annotation), witness=n)
            if symbol.declaration.kind=='TypeSwitch':
                case=next((a for a in n.ancestors() if a.kind=='Case' and a.parent and a.parent.parent is symbol.declaration),None)
                types=case.group('types') if case else []
                if len(types)==1 and types[0].text!='nil':
                    narrowed=self.type_node(types[0])
                    return narrowed
                return self.type_of(symbol.declaration.role('expr'),seen)
            if symbol.value:
                from .switches import unwrap
                if symbol.declaration.kind=='Declaration' and symbol.index and len(symbol.declaration.group('values'))==1:
                    return replace(self.result_type(symbol.value,symbol.index,seen),witness=n)
                if unwrap(symbol.value).kind in {'SwitchExpr','CompileSwitchExpr'}:
                    index = symbol.index if len(symbol.declaration.group('values')) == 1 else 0
                    return replace(self.result_type(symbol.value,index,seen),witness=n)
                if symbol.value.kind == "Procedure":
                    return self.type_node(symbol.value.role("type"))
                if symbol.value.kind == "Call":
                    target=self.type_node(symbol.value.role('callee'))
                    if target.kind in {'big_integer','big_rational'}:
                        return Type('scalar','bool',n) if symbol.index and len(symbol.declaration.group('values'))==1 else replace(target,witness=n)
                    extrema = symbol.value.source.expressions.extrema.plan(symbol.value)
                    if extrema and extrema[2] and symbol.index == 1 and len(symbol.declaration.group('values')) == 1:
                        return Type('scalar', 'bool', n)
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
                source=symbol.declaration.role('expr')
                if 'comprehension' in symbol.declaration.data:
                    interval=source.source.comprehensions.interval(source)
                    if interval:
                        if symbol.index:return Type('scalar','int',n)
                        left,right=(self.type_of(interval.role(side),seen.copy()) for side in ['left','right'])
                        return right if left.untyped and not right.untyped else left
                if source.source.denotes_type(source):return self.type_node(source) if symbol.index==0 else Type('scalar','int',n)
                container=self.type_of(source,seen)
                while container.kind=='pointer' and container.elem:container=container.elem
                if container.kind=='map':
                    return self.type_node(container.node.role('key')) if symbol.index==0 and container.node else container.elem or Type(node=n)
                if symbol.index==1 and container.kind=='array' and container.node and container.node.kind=='ArrayType':
                    index=container.node.role('len')
                    if index and self.type_node(index).kind=='enum':return self.type_node(index)
                if symbol.index==1:return Type('scalar','int',n)
                if container.kind=='string':return Type('scalar','rune',n)
                if source.kind=='Binary' and source.data.get('op') in {'..','..<','..='}:return container
                return container.elem or Type(node=n)
        if kind == "Compound":
            if n.role("type"):
                return self.type_node(n.role("type"))
            if n.source.is_array(n):
                elements = n.group("elems")
                return Type("array", node=n, elem=self.type_of(elements[0], seen) if elements else Type(), length=len(elements), witness=n)
        if kind == "Call":
            callee = n.role("callee")
            if any(self.native_builtin(callee,name) for name in {'size_of','align_of','offset_of','len','cap'}):
                return Type('scalar','uintptr' if self.native_builtin(callee,'offset_of') else 'int',n,witness=n,untyped=any(self.native_builtin(callee,name) for name in {'size_of','align_of'}))
            if n.source.big.enabled:
                numeric=n.source.big.integration.result(n)
                if numeric is not None:return replace(numeric,witness=n)
            from .builtin_types import result
            builtin=result(self,n,seen=seen)
            if builtin is not None:return replace(builtin,witness=n)
            operand = n.source.expressions.extrema.element(n)
            if operand is not None:
                return self.type_of(operand, seen).elem or Type(node=n)
            if callee.text=='#config' and len(n.group('args'))==2:
                return self.type_of(n.group('args')[1],seen)
            if callee.kind=='Ident' and self.symbol(callee) is None and n.group('args'):
                if callee.text=='make': return self.type_node(n.group('args')[0])
                if callee.text=='new': return Type('pointer',node=n,elem=self.type_node(n.group('args')[0]))
            # A named type conversion is checked by Odin; its shape is known.
            target = self.type_node(callee)
            if target.kind not in {"unknown", "procedure", "generic"}:
                target.witness = n
                target.untyped = False
                return target
            if target.kind=='unknown' and self.native_builtin_alias(callee,'soa_zip'):
                members={}
                for index,argument in enumerate(n.group('args')):
                    value=argument.role('value') if argument.kind=='FieldValue' else argument
                    column=self.type_of(value,seen.copy())
                    if column.kind!='slice' or column.soa:return Type(node=n,witness=n)
                    name=argument.role('field').text if argument.kind=='FieldValue' else f'_{index}'
                    members[name]=column.elem or Type()
                if members:return Type('slice',elem=Type('struct',members=members),soa=True,witness=n)
            plan = self.resolve_call(n)
            if plan:
                results = self.fields(plan.signature.role("results"))
                if results:
                    typ=self.type_node(results[0][1], plan.substitutions)
                    return replace(typ,witness=n) if len(results)==1 else typ
        if kind in {"Cast","TypeAssertion"}:
            return self.type_node(n.role("type"))
        if kind in {'AutoCast','OrReturn','OrBranch','OrPanic'}:return self.type_of(n.role('expr'),seen)
        if kind=='OrElse':return self.type_of(n.role('x'),seen)
        if kind == "Deref":
            return self.type_of(n.role("expr"), seen).elem or Type(node=n)
        if kind == "Selector":
            base_type = self.type_node(n.role("expr"))
            if base_type.kind == "enum":
                return replace(base_type, witness=n)
            owner_type=self.type_of(n.role('expr'),seen)
            while owner_type.kind=='pointer': owner_type=owner_type.elem or Type()
            if owner_type.kind=='big_rational' and n.role('field').text in {'numerator','denominator'}:
                return Type('big_integer','Integer',n,witness=n)
            selection = self.array_selector_type(owner_type, n.role('field').text)
            if selection is not None:return replace(selection,witness=n)
            result = self.field(owner_type, n.role("field").text)
            return replace(result[0], witness=n) if result else Type(node=n)
        if kind == "Index":
            container=self.type_of(n.role("expr"),seen)
            if container.kind=='pointer':
                multi=self.multipointer(n.role('expr'))
                if multi is None:return Type(node=n)
                if multi:return container.elem or Type(node=n)
                container=container.elem or Type()
            return Type('scalar','u8') if container.kind=='string' else container.elem or Type(node=n)
        if kind == "Slice":
            container = self.type_of(n.role("expr"), seen)
            if container.kind=='pointer':
                multi=self.multipointer(n.role('expr'))
                if multi is None:return Type(node=n)
                if multi:return Type('slice',elem=container.elem)
                container=container.elem or Type()
            return container if container.kind == "string" else Type("slice", node=n, elem=container.elem)
        if kind in {"Ternary", "WhenExpr"}:
            return self.type_of(n.role("x"), seen)
        if kind == "Binary":
            if n.source.thor and n.data['op'] == '++':
                from .concatenation import result_type
                return result_type(n, seen)
            if n.data["op"] in {"in", "not_in", "==", "!=", "<", ">", "<=", ">=", "&&", "||"}:
                return Type("scalar", "bool", n)
            operands=[self.type_of(n.role(side),seen.copy()) for side in ['left','right']]
            for operand in operands:
                if operand.kind in {'big_integer','big_rational'}:return replace(operand,witness=n)
            if n.data['op']=='**':
                left,right=operands
                if left.kind in {'unknown','generic'} and right.kind=='struct':return replace(right,witness=n)
                if left.untyped and (not right.untyped or right.name.startswith('f')):return replace(right,witness=n)
                return replace(left,witness=n,untyped=left.untyped and right.untyped)
            if n.source.thor and n.data['op']=='*':
                from .repetition import result_type
                repeated = result_type(n, seen)
                if repeated is not None: return repeated
            if n.source.thor and operands[0].kind in {'unknown','generic'} and operands[1].kind=='struct':
                return replace(operands[1],witness=n)
            return self.type_of(n.role("left"), seen)
        if kind == "Unary":
            inner = self.type_of(n.role("expr"), seen)
            return Type("pointer", node=n, elem=inner) if n.data["op"] == "&" else inner
        return Type(node=n)

    def multipointer(self,node,depth=0):
        if node is None or depth>32:return None
        if node.kind=='PointerType':return False
        if node.kind=='MultiPointerType':return True
        if node.kind=='Unary' and node.data.get('op')=='&':return False
        if node.kind in {'Paren','HelperType'}:return self.multipointer(node.role('expr'),depth+1)
        if node.kind=='Cast':return self.multipointer(node.role('type'),depth+1)
        if node.kind=='Binary' and node.data.get('op')=='/':return self.multipointer(node.role('right'),depth+1)
        if node.kind in {'Ident','Selector'}:
            symbol=self.symbol(node)
            if symbol:return self.multipointer(symbol.annotation or symbol.value,depth+1)
        if node.kind=='Call':
            callee=node.role('callee')
            if self.native_builtin_alias(callee,'raw_data'):return True
            if node.source.denotes_type(callee):return self.multipointer(callee,depth+1)
            signature=self.type_of(callee)
            fields=self.fields(signature.node.role('results')) if signature.kind=='procedure' else []
            if fields:return self.multipointer(fields[0][1],depth+1)
        return None

    def membership_untyped(self, node, seen=None):
        """Whether a needle lacks a fixed type for inline-list inference.

        Shapes are deliberately approximate; arithmetic must consider both
        operands here (except shifts), not just the left operand's shape.
        Odin still checks actual types and representability in the emitted code.
        """
        if node is None: return True
        seen = set() if seen is None else seen
        if node in seen: return False
        seen.add(node)
        if node.kind == 'Paren': return self.membership_untyped(node.role('expr'), seen)
        if node.kind == 'ImplicitSelector' or node.text == 'nil': return True
        symbol = self.symbol(node)
        if symbol is None and self.native and node.source.purity:
            imported = node.source.purity.imported(node)
            if imported:
                # Inspect an opaque SDK constant only when it is actually used
                # as a needle; do not default an untyped math.PI to f64 first.
                directory = self.native.load(node.source, imported[0])
                if directory: symbol = self.package_symbol(directory, imported[1])
        if symbol and symbol.declaration.kind == 'Declaration':
            if symbol.annotation or symbol.declaration.data.get('mutable', True): return False
            return self.membership_untyped(symbol.value, seen)
        if node.kind == 'Binary' and node.data['op'] in {'==','!=','<','>','<=','>='}:
            return True # Native comparisons produce an untyped boolean, even at runtime.
        if node.kind == 'Binary' and node.data['op'] not in {'in','not_in'}:
            sides = ['left'] if node.data['op'] in {'<<','>>'} else ['left','right']
            return all(self.membership_untyped(node.role(side), seen.copy()) for side in sides)
        if node.kind == 'Unary' and node.data['op'] in {'+','-','~','!'}:
            return self.membership_untyped(node.role('expr'), seen)
        if node.kind in {'Ternary','WhenExpr'}:
            return all(self.membership_untyped(node.role(side), seen.copy()) for side in ['x','y'])
        if node.kind == 'Call' and node.source.purity:
            builtin = node.source.purity.builtin(node)
            args = node.group('args')
            if builtin in {'size_of','align_of','offset_of'}: return True
            if builtin in {'min','max','abs','clamp','complex','quaternion','real','imag','jmag','kmag','conj'}:
                return all(self.membership_untyped(arg, seen.copy()) for arg in args)
            if builtin in {'len','cap'} and args:
                typ = self.type_of(args[0])
                if typ.kind == 'array': return True
                if typ.kind == 'string':
                    from .repetition import runtime_operand
                    return not runtime_operand(args[0])
        return self.type_of(node).untyped

    def conditional_callable(self, node, seen=None):
        """A native when may select different procedure signatures."""
        seen=set() if seen is None else seen
        if node is None or node in seen:return False
        seen.add(node)
        if node.kind=='Paren':return self.conditional_callable(node.role('expr'),seen)
        symbol=self.symbol(node)
        if not symbol:return False
        if len(symbol.candidates)>1 and any(any(parent.kind in {'When','CompileSwitch'} for parent in candidate.declaration.ancestors())
                                            for candidate in symbol.candidates):return True
        return bool(symbol.value and symbol.value.kind in {'Ident','Selector','Paren'} and self.conditional_callable(symbol.value,seen))

    def is_diverging_call(self, node):
        """A -> ! call terminates an arm without supplying any result types.

        Use parsed procedure signatures, including aliases and procedure values.
        Ordinary void calls remain value errors; names alone never grant a user
        declaration permission to terminate a value-producing arm.
        """
        while node and node.kind == 'Paren': node = node.role('expr')
        if node is None or node.kind != 'Call': return False
        if self.conditional_callable(node.role('callee')):return False
        key=(node,'diverging')
        if key in self.active:return False
        self.active.add(key)
        try:
            signature=node.source.signature(node)
            if signature:return signature.data.get('diverging',False)
            # Builtins have no project AST. Follow aliases but respect lexical
            # shadowing and selected imports, just as for normal call resolution.
            def builtin(callee,seen):
                if callee is None or callee in seen:return False
                seen.add(callee)
                if callee.kind=='Paren':return builtin(callee.role('expr'),seen)
                symbol=self.symbol(callee)
                if symbol:
                    return not symbol.annotation and builtin(symbol.value,seen)
                names={'panic','panic_contextless','unreachable'}
                imported=callee.source.purity.imported(callee) if callee.source.purity else None
                if imported:return (imported[0]=='base:builtin' and imported[1] in names
                                    or imported[0]=='base:runtime' and imported[1] in {'panic','panic_contextless'})
                return (callee.kind=='Ident' and callee.text in names
                        and callee.text not in callee.source.selected and callee.text not in callee.source.imports)
            if builtin(node.role('callee'),set()):return True
            # Standard-library procedures are not emitted into project mirrors.
            # Inspect their source signature on demand, without treating their
            # effects or implementation as eligible for pure evaluation.
            if self.native:
                enabled=self.native_enabled;self.native_enabled=True
                try:
                    self.call_plans.pop(node,None)
                    signature=node.source.signature(node)
                finally:self.native_enabled=enabled
                if signature:return signature.data.get('diverging',False)
            return False
        finally:self.active.remove(key)

    @staticmethod
    def fields(node):
        return [(binding_name(name) if name else "", f.role("type")) for f in node.children if f.kind == "Field"
                for name in (f.group("names") or [None])] if node else []

    @staticmethod
    def array_selector_type(typ, name):
        """Odin array components and swizzles have the element's real shape.

        Native checking still owns selector validity and exact nominal types.
        In particular, these are element projections, not unknown record fields.
        """
        if typ.kind!='array' or not 1<=len(name)<=4:return None
        alphabet=next((letters for letters in ('xyzw','rgba') if all(c in letters for c in name)),None)
        if alphabet is None:return None
        if isinstance(typ.length,int):
            if len(name)==1 and typ.length>4:return None
            if any(alphabet.index(c)>=typ.length for c in name):return None
        if len(name)==1:return typ.elem or Type()
        return typ if len(name)==typ.length else Type('array',elem=typ.elem,length=len(name))

    def field(self, typ, name, seen=None):
        seen = set() if seen is None else seen
        if typ.kind=='struct' and name in typ.members:return typ.members[name],typ.node,False
        if typ.kind == "pointer":
            found = self.field(typ.elem, name, seen)
            return (*found[:2], True) if found else None
        if typ.node is None or typ.node in seen:
            return None
        seen.add(typ.node)
        if typ.kind == "struct":
            fields = typ.node.role("fields")
            for f in fields.children if fields else typ.node.group("fields"):
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

    def field_path(self, typ, name, seen=None):
        """Resolve promoted fields through the storage that actually contains them."""
        seen=set() if seen is None else seen
        if typ.kind=='pointer':return self.field_path(typ.elem,name,seen)
        if typ.members:return (name,) if name in typ.members else None
        if typ.kind!='struct' or typ.node is None or typ.node in seen:return None
        seen.add(typ.node)
        fields=typ.node.role('fields')
        fields=[field for field in (fields.children if fields else typ.node.group('fields')) if field.kind=='Field']
        if any(any(binding_name(n)==name for n in field.group('names')) for field in fields):return (name,)
        found=[]
        for field in fields:
            if not field.data.get('using_field'):continue
            path=self.field_path(self.type_node(field.role('type'),typ.substitutions),name,seen.copy())
            if path:
                found.extend((binding_name(n),*path) for n in field.group('names'))
        return found[0] if len(found)==1 else None

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
        if formal.kind == "Ellipsis":
            return self.unify(formal.role("expr"), actual.elem, substitutions) if actual.kind == "slice" and actual.elem else -1000
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
            if actual.kind!=('array' if length else 'slice'):return -1000
            if length and length.kind == "PolyType":
                substitutions[length.data["name"]] = actual.length
            elif length and actual.length is not None:
                count = formal.source.constant_int(length)
                if count is not None and count != actual.length:
                    return -1000
            score = self.unify(formal.role("elem"), actual.elem or Type(), substitutions)
            return score + 3 if score >= 0 else score
        if formal.kind=='PointerType' and actual.kind=='pointer':
            score=self.unify(formal.role('elem'),actual.elem or Type(),substitutions)
            return score+3 if score>=0 else score
        if formal.kind=='MapType' and actual.kind=='map':
            key=self.type_node(actual.node.role('key'),actual.substitutions) if actual.node else Type()
            score=self.unify(formal.role('key'),key,substitutions)
            if score<0:return score
            value=self.unify(formal.role('value'),actual.elem or Type(),substitutions)
            return score+value+3 if value>=0 else value
        if formal.kind=='Call' and actual.kind=='struct' and actual.node is expected.node:
            parameters=self.fields(actual.node.role('params'))
            arguments=formal.group('args');score=0
            for index,(name,annotation) in enumerate(parameters):
                argument=next((arg.role('value') for arg in arguments if arg.kind=='FieldValue' and arg.role('field').text==name),
                              arguments[index] if index<len(arguments) else None)
                if argument is None:continue
                value=actual.substitutions.get(name)
                if value is None:continue
                if argument.kind=='PolyType':
                    substitutions.setdefault(argument.data['name'],value)
                    score+=1
                elif isinstance(value,Type):
                    amount=self.unify(argument,value,substitutions)
                    if amount<0:return amount
                    score+=amount
                else:
                    wanted=self.record_constant(argument,substitutions,expected=self.type_node(annotation,substitutions) if annotation else None)
                    if not isinstance(wanted,Type) and wanted!=value:return -1000
            return score+3
        if actual.kind == "unknown" or expected.kind in {"unknown", "generic"}:
            return 0
        if expected.kind=='scalar' and expected.name=='rawptr' and actual.kind=='pointer':return 1
        if expected.kind=='union' and expected.node and (actual.kind!='union' or actual.node is not expected.node):
            scores=[self.unify(v,actual,dict(substitutions)) for v in expected.node.group('variants')]
            return max(scores,default=-1000)
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
        return 2 if actual.untyped and expected.name and expected.name==actual.name else 0 if actual.untyped else 5

    def resolve_call(self, call, targets=None):
        if targets is None and not getattr(self,'evaluation_active',False) and not self.bound_types and not self.bound_constants and call in self.call_plans:
            return self.call_plans[call]
        key = (call, "call")
        if key in self.active:
            return None
        self.active.add(key)
        try:
            candidates = []
            arranged=None
            if targets is not None:
                declared=self.type_of(call.role("callee"))
                if declared.kind!='procedure':return None
                from .caller_metadata import arrange
                arranged=arrange(declared.node,call.group("args"))
                if arranged is None:return None
            for symbol, signature in ([(None,target.role("type")) for target in targets] if targets is not None else self.procedure_candidates(call.role("callee"))):
                fields = self.fields(signature.role("params"))
                substitutions, score, valid = {}, 0, True
                args = call.group("args")
                generics = self.generic_parameters(signature)
                indexed=enumerate(args) if arranged is None else ((i,arg) for i,group in enumerate(arranged) for arg in group)
                for index, arg in indexed:
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
                                value = self.constant_int(arg)
                                if value is not None:
                                    substitutions[param[0]] = value
                        actual=arg
                        while actual.kind=='Paren':actual=actual.role('expr')
                        formal=param[1]
                        if formal and formal.kind=='Ellipsis':
                            ellipsis=call.data.get('roles',{}).get('ellipsis')
                            packed=bool(ellipsis and ellipsis[0]>=0 and arg.source is call.source and arg.start>=ellipsis[1]) or any(
                                item.kind=='FieldValue' and item.role('value') is arg for item in args)
                            if not packed:formal=formal.role('expr')
                        amount = self.unify(formal, self.type_of(arg), substitutions)
                        if actual.kind=='AutoCast' and amount<0:amount=0
                        score += amount
                        valid &= amount >= 0
                if valid:
                    from . import resulttypes
                    for default in resulttypes.defaults(self,signature):
                        bound=resulttypes.default_bindings(self,signature,call,default)
                        if bound and bound[2].get(default.name) is default.query:
                            result=resulttypes.resolve_default(self,call,default.call,bound[0],bound[1])[0]
                            if result:
                                if not result[1]:valid=False;break
                                substitutions[default.name]=resulttypes.packed(result)
                                score+=1
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

    def constant_int(self,n):
        symbol=self.symbol(n)
        key=(symbol.declaration,symbol.name) if symbol else (n,n.data.get('name')) if n else None
        if key in self.bound_constants:
            value=self.bound_constants[key]
            if value['kind']=='integer':return int(value['value'])
        value=n.source.constant_int(n) if n else None
        if value is None and n and hasattr(self,'evaluated_integer') and n not in self.integer_active:
            self.integer_active.add(n)
            try:return self.evaluated_integer(n)
            finally:self.integer_active.remove(n)
        return value

    def protection(self, n):
        if n is None:
            return None
        if n.kind == "Paren":
            return self.protection(n.role("expr"))
        symbol = self.symbol(n)
        if symbol:
            if symbol.projection:
                owner, member = symbol.projection
                field = self.field(self._projection_owner_type(owner), member)
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
            if self.import_directory(caller, imported) == owner.path.parent:
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
        if n.kind=='Ident' and n.source.thor and n.text in {'Integer','Rational'} and self.symbol(n) is None:
            return n.source.big.module()+'.'+n.text
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
        if annotation.kind == 'PolyType' and not annotation.role('specialization'):
            # A bare $T is inferred from this argument. It cannot also supply
            # the contextual element type used to construct that argument.
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

    @staticmethod
    def value_iteration_slice(node):
        """A slice consumed directly by a value loop cannot expose its header.

        Odin's value iteration bindings are readonly and non-addressable, so
        this use cannot make the backing immutable array writable. Keep named,
        passed, returned and reference-iterated slices under the normal checks.
        """
        if node.kind != 'Slice': return False
        value = node
        while value.parent and value.parent.kind == 'Paren': value = value.parent
        loop = value.parent
        return bool(loop and loop.kind == 'Range' and loop.role('expr') is value
                    and not any(name.kind == 'Unary' and name.data.get('op') == '&'
                                for name in loop.group('names')))

    def prepare_storage_checks(self):
        for source in self.sources:
            for n in source.nodes:
                targets = []
                if n.kind == "Assignment" and n.data.get("op") != "in":
                    targets = [(lhs, lhs, "write") for lhs in n.group("lhs")]
                elif n.kind == "Unary" and n.data["op"] == "&":
                    targets = [(n, n.role("expr"), "take a mutable address of")]
                elif n.kind == "Slice":
                    if not source.thor and not self.value_iteration_slice(n):
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
                    if not source.thor:targets = [(n, n.role("expr"), "create a mutable slice of")]
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

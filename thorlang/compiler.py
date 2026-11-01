"""Lower T01-T23 using ASTs from the adapted Odin tooling parser.

Offsets are UTF-8 byte offsets, as in Odin. Unchanged source is copied verbatim;
all transformations are attached to parsed nodes, never textual pattern matches.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import subprocess

from .sourcemap import MappingBuilder


class ThorError(Exception):
    def __init__(self, message, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics


def unquote(text: str) -> str:
    if text.startswith("`"):
        return text[1:-1]
    # Import/resource paths use Odin's quoted-string spelling. These common
    # escapes have the same spelling in JSON; reject unsupported spellings.
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ThorError(f"unsupported path literal: {text}") from exc


@dataclass(eq=False)
class Node:
    source: Source
    index: int
    data: dict
    children: list[Node] = field(default_factory=list)
    parent: Node | None = None

    @property
    def kind(self):
        return self.data["kind"]

    @property
    def start(self):
        return self.data["start"]

    @property
    def end(self):
        return self.data["end"]

    @property
    def text(self):
        return self.source.text(self.start, self.end)

    def role(self, name):
        return self.source.at(self.data.get("roles", {}).get(name), self)

    def group(self, name):
        return [self.source.at(s, self) for s in self.data.get("lists", {}).get(name, [])]

    def ancestors(self):
        current = self.parent
        while current:
            yield current
            current = current.parent


@dataclass
class Hint:
    source: Source
    node: Node
    expression: str | None = None
    semantic: object = None


class Source:
    def __init__(self, path: Path, frontend: Path, *, thor=True, package=False, test=False, project_name="", mapping=None, raw=None, target=(), cache=True, cache_dir=None):
        self.path = path.resolve()
        self.thor = thor
        self.raw = path.read_bytes() if raw is None else raw
        self.mapping = mapping or MappingBuilder()
        self.cache_dir=cache_dir
        args = [str(frontend), str(path)] + ([] if thor else ["--odin"])
        if package:
            args.append("--package")
        if test:
            args.append("--test")
        args.append(f"--project-name={project_name}")
        if target:
            args.extend([f"--target-os={target[0]}", f"--target-arch={target[1]}"])
        args.append("--stdin")
        try:
            from . import parsecache
            result = parsecache.run(args,self.raw,enabled=cache,directory=cache_dir)
        except subprocess.TimeoutExpired as exc:
            raise ThorError(f"parser timed out: {path}") from exc
        if result.returncode:
            try:
                errors = json.loads(result.stdout)["errors"]
            except (ValueError, KeyError):
                errors = None
            raise ThorError(result.stderr.decode(errors="replace").strip() or f"parser failed: {path}", diagnostics=errors)
        try:
            self.info = json.loads(result.stdout)
        except ValueError as exc:
            raise ThorError(f"invalid frontend output for {path}") from exc
        self.nodes = [Node(self, i, d) for i, d in enumerate(self.info["nodes"])]
        for feature in self.info.get("features") or []:
            node = next((n for n in self.nodes if n.start == feature["start"] and n.end == feature["end"] and n.kind in {"Call", "Switch", "Procedure"}), None)
            if node:
                if feature['kind']=='PureFunction':
                    node.data['pure']=True
                    continue
                node.data.update(feature)
                if feature['kind']=='Run': node.data.setdefault('roles',{})['expr']=node.data['lists']['args'][0]
                if feature["kind"] in {"SwitchExpr", "CompileSwitchExpr", "CompileSwitch"}:
                    node.data.setdefault("roles", {})["selector"] = feature["selector"]
        self.excluded = self.info.get("excluded", False)
        self.diagnostics = self.info.get("diagnostics") or []
        declaration_separators = {span[0]:span[1:] for span in self.info.get("declaration_separators") or []}
        immutable_starts = {span[0] for span in self.info.get("immutables") or []}
        for n in self.nodes:
            if n.kind == "Declaration":
                n.data["immutable"] = n.start in immutable_starts
                first_name=(n.data.get('lists',{}).get('names') or [[n.start]])[0][0]
                if first_name in declaration_separators:n.data['roles']['separator']=declaration_separators[first_name]
        if not self.nodes:
            self.nodes = [Node(self, 0, {"kind": "File", "start": 0, "end": len(self.raw), "parent": -1})]
        self.by_span: dict[tuple[int, int], list[Node]] = {}
        for n in self.nodes:
            self.by_span.setdefault((n.start, n.end), []).append(n)
            parent = n.data["parent"]
            if parent >= 0:
                n.parent = self.nodes[parent]
                n.parent.children.append(n)
        self.root = self.nodes[0]
        digest = hashlib.sha256(str(self.path).encode()).hexdigest()[:12]
        self.prefix = f"__thor_{digest}_"
        while self.prefix.encode() in self.raw:
            self.prefix += "x_"
        self.edits = [(e["start"], e["end"], e["text"]) for e in self.info.get("edits", [])]
        self.imports = {}
        self.import_names = {}
        self.evaluation_import_paths = {}
        self.blank_selections = {}
        for node in self.nodes:
            if node.kind != 'Import': continue
            path = unquote(node.data['op'])
            namespace = node.data['name'] or path.rsplit('/', 1)[-1].rsplit(':', 1)[-1]
            selection = next((item for item in self.info.get('selections') or []
                              if item['package_name']=='_' and item['path']==node.data['op'] and item['start']>=node.end
                              and not any(n.kind=='Import' and node.end<n.start<item['start'] for n in self.nodes)), None)
            if namespace == '_' and selection:
                namespace = self.prefix+f'import_{node.start}'
                self.blank_selections[selection['start']] = namespace
                a,b = node.data['roles']['alias']
                self.edits.append((a,b,namespace))
            self.imports[namespace] = path
            self.import_names[node] = namespace
        self.project: dict[Path, list[Source]] = {}
        self.collections: dict[str, Path] = {}
        self.needs_membership = False
        self.needs_default = False
        self.needs_intrinsics = False
        self.error_messages = {}
        self.hidden_namespaces = {}
        self.asset_collections = {}
        self.blocked = {}
        self.guards = {}
        self.block_guards = {}
        self.converted_do_blocks = set()
        self.guard_messages = {}
        self.resolver = None
        self._fallback_symbols = None
        self.defaults = None
        self.purity = None
        self.run_values = {}
        self.evaluation_keep = None
        self.evaluation_query_checks = {}
        from .expressions import Expressions
        self.expressions = Expressions(self)
        from .typedloops import TypedLoops
        self.typed_loops = TypedLoops(self)
        from .bignumbers import BigNumbers
        self.big = BigNumbers(self)
        self.selected = {}
        self.selected_origins = {}
        self.wildcards = []
        self.wildcard_names = set()
        self.conditional_conflicts = set()
        for selection in self.info.get("selections", []) or []:
            if selection.get('wildcard'):
                self.wildcards.append(selection)
                self.edits.append((selection['start'], selection['end'], ''))
                continue
            names = selection["names"]
            if len(names) != len(set(names)):
                self.error("duplicate name in selective import", selection["start"], selection["end"])
            package = selection["package_name"] or unquote(selection["path"]).rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            if package == "_":
                package = self.blank_selections[selection['start']]
            comments = self.comments(selection["start"], selection["end"])
            for i, name in enumerate(names):
                start = selection["positions"][i]["offset"]
                if name in self.selected or name in self.imports:
                    self.error(f"conflicting selective import: {name}", start, start + len(name.encode()))
                self.selected[name] = f"{package}.{name}"
                self.selected_origins[name] = (start, start + len(name.encode()))
            self.edits.append((selection["start"], selection["end"], "\n" + comments))
        for n in self.nodes:
            if n.kind == "Declaration" and self.scope(n).kind == "File":
                for name in n.group("names"):
                    if name.text in self.selected:
                        if any(a.kind == "When" for a in n.ancestors()):
                            self.conditional_conflicts.add(n)
                        else:
                            self.error(f"declaration conflicts with selective import: {name.text}", name.start, name.end)
        # Discover support packages before emission so their dependencies and
        # source maps participate in the same project graph.
        spreading = self.thor and any(n.kind=='Call' and (n.data.get('roles',{}).get('ellipsis') or
            any(a.kind=='Unary' and a.data['op']=='**' for a in n.group('args'))) for n in self.nodes)
        for module,needed in [('format',any(n.kind=='Interpolation' for n in self.nodes)),
                              ('temporaries',self.thor and any(n.kind=='Interpolation' for n in self.nodes)),
                              ('pure',spreading or any(n.data.get('pure') or n.kind=='Run' or self.thor and n.kind=='Binary' and n.data['op']=='**' for n in self.nodes)),
                              ('sets',self.thor and any(n.kind in {'SetType','Binary','Call','Range'} for n in self.nodes)),
                              ('sequence',spreading or self.thor and any(n.kind in {'Index','Slice'} or n.kind=='Range' and n.role('index_type') for n in self.nodes)),
                              ('numeric',self.thor and any(n.kind=='Binary' and n.data['op']=='**' for n in self.nodes)),
                              ('big',self.thor and any(n.kind=='Ident' and n.text in {'Integer','Rational'} for n in self.nodes))]:
            if needed:
                self.imports[self.prefix+module]='thor:'+module

    def error(self, message, start, end):
        raise ThorError(message, diagnostics=[{"type": "error", "stage": "thor", "pos": {
            "file": str(self.path), "offset": start, "end_offset": end}, "msgs": [message]}])

    def copy(self, start, end):
        return self.mapping.wrap(self.text(start, end), self.path, start, end, "source", exact=True)

    def text(self, start, end):
        return self.raw[start:end].decode("utf-8")

    def at(self, span, relative=None):
        if not span or span[0] < 0:
            return None
        candidates = self.by_span.get(tuple(span), [])
        if relative:
            for candidate in candidates:
                if candidate is not relative and relative in candidate.ancestors():
                    return candidate
        return next((n for n in candidates if n is not relative), None)

    def comments(self, start, end, exclude=()):
        comments = []
        for a, b in self.info.get("comments", []) or []:
            if start <= a and b <= end and not any(x <= a and b <= y for x, y in exclude):
                comments.append(self.text(a, b) + "\n")
        return "".join(comments)

    @staticmethod
    def is_scope(n):
        if n.kind == "Block" and n.parent and n.parent.kind in {"When","CompileSwitch","ForeignBlock"}:
            return False  # Conditional declarations and foreign blocks share the enclosing scope.
        if n.kind == 'Case' and n.parent and n.parent.parent and n.parent.parent.kind=='CompileSwitch':
            return False
        return n.kind in {"Block", "Procedure", "File", "StructType", "For", "If", "Range", "Switch", "TypeSwitch", "Case"}

    def scope(self, n):
        return next((a for a in n.ancestors() if self.is_scope(a)), self.root)

    def lookup(self, name, context):
        if self.resolver:
            symbol = self.resolver.lookup(name, context)
            return symbol.declaration if symbol else None
        scopes = [a for a in context.ancestors() if self.is_scope(a)]
        if context.kind == "File":
            scopes = [context]
        if self.root not in scopes:
            scopes.append(self.root)
        for scope in scopes:
            for n in self.fallback_symbols().get((scope,name),[]):
                if n.kind=='Declaration' and name in self.selected and scope.kind=='File' and any(a.kind=='When' for a in n.ancestors()):
                    continue
                return n
        # Other source files in the same package share top-level declarations.
        for other in self.project.get(self.path.parent, []):
            if other is self:
                continue
            for n in other.fallback_symbols().get((other.root,name),[]):
                if n.kind=='Declaration':return n
        return None

    def fallback_symbols(self):
        if self._fallback_symbols is None:
            result={}
            for n in self.nodes:
                if n.kind not in {'Declaration','Range','PolyType','Field'}:continue
                scope=n if n.kind=='Range' else self.scope(n)
                if n.kind=='Field' and scope.kind not in {'Procedure','StructType'}:continue
                names=[n.data['name']] if n.kind=='PolyType' else [x.text.lstrip('$') if n.kind=='Field' else x.text for x in n.group('names')]
                for name in names:result.setdefault((scope,name),[]).append(n)
            self._fallback_symbols=result
        return self._fallback_symbols

    def declaration_for(self, expression):
        if expression is None:
            return None
        if self.resolver:
            return self.resolver.declaration_for(expression)
        if expression.kind == "Ident":
            local = expression.source.lookup(expression.text, expression)
            if local or expression.text not in self.selected:
                return local
            namespace, member_name = self.selected[expression.text].split(".", 1)
            directory = self.import_dir(self.imports[namespace])
            for other in self.project.get(directory, []):
                found = other.lookup(member_name, other.root)
                if found:
                    return found
        if expression.kind == "Selector":
            base, member = expression.role("expr"), expression.role("field")
            if base and base.kind == "Ident" and base.text in self.imports:
                directory = self.import_dir(self.imports[base.text])
                for other in self.project.get(directory, []):
                    found = other.lookup(member.text, other.root)
                    if found:
                        return found
        return None

    def import_dir(self, path):
        if ":" in path:
            collection, suffix = path.split(":", 1)
            base = self.collections.get(collection)
            return (base / suffix).resolve() if base else None
        return (self.path.parent / path).resolve()

    def type_hint(self, expression, seen=None):
        if expression is None:
            return None
        if self.resolver and expression.kind in {"Ident", "Selector", "Index", "Call", "Deref", "Paren"}:
            symbol = self.resolver.symbol(expression)
            if symbol and symbol.projection:
                # A promoted field shares its owner's declaration for storage
                # protection, but the owner annotation is not its value type.
                return Hint(expression.source, expression, f"type_of({expression.source.render(expression)})")
            shape = self.resolver.type_of(expression)
            if shape.kind in {"array", "slice", "dynamic", "struct", "map", "pointer"}:
                return Hint(expression.source, expression, f"type_of({expression.source.render(expression)})")
        seen = set() if seen is None else seen
        if expression in seen:
            return None
        seen.add(expression)
        s = expression.source
        if expression.kind == "Paren":
            return self.type_hint(expression.role("expr"), seen)
        if expression.kind in {"ArrayType", "StructType", "DynamicArrayType", "MapType"}:
            return Hint(s, expression)
        if expression.kind == "Compound" and expression.role("type"):
            return self.type_hint(expression.role("type"), seen)
        declaration = s.declaration_for(expression)
        if declaration:
            annotation = declaration.role("type")
            if annotation:
                return Hint(annotation.source, annotation)
            values = declaration.group("values")
            if len(values) == 1:
                value = values[0]
                if value.kind == "Compound":
                    return self.type_hint(value.role("type"), seen) or Hint(value.source, value)
                if value.kind == "Call":
                    return self.type_hint(value.role("callee"), seen)
                resolved = self.type_hint(value, seen)
                if resolved and not declaration.data.get("mutable", True):
                    return Hint(resolved.source, resolved.node, s.render(expression))
                return resolved
        if expression.kind == "Selector":
            base = self.type_hint(expression.role("expr"), seen)
            if base:
                fields = self.struct_fields(base)
                return fields.get(expression.role("field").text)
        return None

    def denotes_type(self, expression, seen=None):
        """Recognize statically named types; Odin verifies the actual identity."""
        seen = set() if seen is None else seen
        if expression is None or expression in seen:
            return False
        seen.add(expression)
        if expression.kind == 'Paren':
            return self.denotes_type(expression.role('expr'), seen)
        if expression.kind.endswith("Type"):
            return True
        if expression.kind == "Call" and expression.role("callee").text == "type_of":
            return True
        builtins = {"bool", "b8", "b16", "b32", "b64", "int", "uint", "uintptr", "rune",
                    "i8", "i16", "i32", "i64", "i128", "u8", "u16", "u32", "u64", "u128",
                    "f16", "f32", "f64", "string", "cstring", "rawptr", "typeid", "any"}
        builtins.update(f'{prefix}{bits}{endian}' for prefix in ['i','u','f']
                        for bits in ([16,32,64] if prefix=='f' else [8,16,32,64,128])
                        for endian in ['', 'le', 'be'])
        builtins.update({'complex32','complex64','complex128','quaternion64','quaternion128','quaternion256'})
        declaration = self.declaration_for(expression)
        if declaration is None:
            if expression.source.thor and expression.kind=='Ident' and expression.text in {'Integer','Rational'}:
                return True
            return expression.kind == "Ident" and expression.text in builtins
        if declaration.kind == "PolyType":
            return True
        if declaration.kind == "Field":
            annotation = declaration.role("type")
            return annotation is not None and (annotation.kind == "TypeidType" or annotation.text == "typeid")
        values = declaration.group("values")
        return not declaration.data.get("mutable", True) and len(values) == 1 and self.denotes_type(values[0], seen)

    def constant_int(self, expression, seen=None):
        seen = set() if seen is None else seen
        if expression is None or expression in seen:
            return None
        seen.add(expression)
        if expression.kind=='Unary' and expression.data['op'] in {'+','-'}:
            value=self.constant_int(expression.role('expr'),seen)
            return (-value if expression.data['op']=='-' else value) if value is not None else None
        if expression.kind == "Literal":
            try:
                text = expression.text.replace("_", "")
                return int(text, 0) if text.startswith(("0x", "0b", "0o")) else int(text)
            except ValueError:
                return None
        declaration = self.declaration_for(expression)
        if declaration and not declaration.data.get("mutable", True):
            values = declaration.group("values")
            return self.constant_int(values[0], seen) if len(values) == 1 else None
        if expression.kind == "Binary":
            left = self.constant_int(expression.role("left"), set(seen))
            right = self.constant_int(expression.role("right"), set(seen))
            operations = {"+": lambda: left + right, "-": lambda: left - right, "*": lambda: left * right}
            if left is not None and right is not None and expression.data["op"] in operations:
                return operations[expression.data["op"]]()
        return None

    def constant_array_length(self, expression, seen=None):
        seen = set() if seen is None else seen
        if expression is None or expression in seen:
            return None
        seen.add(expression)
        if expression.kind == "Paren":
            return self.constant_array_length(expression.role("expr"), seen)
        if self.is_array(expression):
            return len(expression.group("elems"))
        if expression.kind == "Compound":
            hint = self.type_hint(expression.role("type"))
            if hint and hint.node.kind == "ArrayType":
                length = hint.node.role("len")
                if length and length.text == "?":
                    return len(expression.group("elems"))
                return self.constant_int(length)
        declaration = self.declaration_for(expression)
        if declaration and not declaration.data.get("mutable", True):
            values = declaration.group("values")
            return self.constant_array_length(values[0], seen) if len(values) == 1 else None
        return None

    def array_hint(self, hint):
        if hint is None:
            return None
        n = hint.node
        if n.kind == "ArrayType" and n.role("len") is not None:
            elem = n.role("elem")
            if "$" not in elem.text:
                return elem
        if n.kind == "Compound" and n.source.is_array(n):
            elements = n.group("elems")
            if elements:
                # A reassignment can reuse the inferred type of the existing array.
                return (f"type_of(({n.source.render(n)})[0])", n.source)
        return None

    def struct_fields(self, hint):
        n = hint.node
        if self.resolver:
            shape = self.resolver.type_of(n)
            if shape.kind == "struct":
                fields = shape.node.role("fields")
                return {name.text: Hint(f.source, f.role("type")) for f in fields.children if f.kind == "Field"
                        for name in f.group("names")}
        if n.kind != "StructType":
            resolved = n.source.type_hint(n)
            if not resolved or resolved.node is n:
                return {}
            return self.struct_fields(resolved)
        fields = n.role("fields")
        result = {}
        for f in fields.children if fields else []:
            if f.kind == "Field" and f.role("type"):
                for name in f.group("names"):
                    result[name.text] = Hint(f.source, f.role("type"))
        return result

    def signature(self, call):
        if self.resolver:
            plan = self.resolver.call_plans.get(call) or self.resolver.resolve_call(call)
            if plan:
                return plan.signature
            target=self.resolver.type_of(call.role('callee'))
            if target.kind=='procedure' and target.node:return target.node
        callee = call.role("callee")
        declaration = self.declaration_for(callee)
        if declaration:
            annotation = declaration.role("type")
            if annotation and annotation.kind == "ProcedureType":
                return annotation
            values = declaration.group("values")
            if len(values) == 1 and values[0].kind == "Procedure":
                return values[0].role("type")
        return None

    @staticmethod
    def fields(node):
        result = []
        for f in node.children if node else []:
            if f.kind == "Field":
                names = f.group("names")
                for name in names or [None]:
                    result.append((name.text if name else "", f.role("type")))
        return result

    def is_array(self, node):
        opened = node.data.get("roles", {}).get("open", [-1])[0]
        return node.kind == "Compound" and opened >= 0 and self.raw[opened:opened+1] == b"["

    def splice(self, n, replacements, inserts=()):
        # AST children can include documentation outside their parent's code span.
        chosen = []
        for a, b, text in sorted(replacements, key=lambda x: (x[0], -x[1])):
            if n.start <= a < b <= n.end and not any(x <= a and b <= y for x, y, _ in chosen):
                chosen.append((a, b, text))
        for a, b, text in self.edits:
            if n.start <= a < b <= n.end and not any(x <= a and b <= y for x, y, _ in chosen):
                chosen.append((a, b, self.mapping.wrap(text, self.path, a, b, "syntax")))
        for offset, text in inserts:
            if n.start <= offset <= n.end and not any(a < offset < b for a, b, _ in chosen):
                chosen.append((offset, offset, text))
        output, cursor = [], n.start
        for a, b, text in sorted(chosen):
            if a < cursor:
                raise ThorError(f"overlapping source transformations in {self.path}")
            output.extend([self.copy(cursor, a), text])
            cursor = b
        output.append(self.copy(cursor, n.end))
        return "".join(output)

    def render(self, n, expected=None, constant=False):
        if n is None:
            return ""
        if n.source is not self:
            return n.source.render(n, expected, constant)
        if self.evaluation_keep is not None and n is not self.root and n.kind!='Package':
            if not any(k is n or k in n.ancestors() or n in k.ancestors() for k in self.evaluation_keep):return ''
        if n in self.blocked:
            token, message = self.blocked[n]
            self.error_messages[token] = message
            code = token
        else:
            # User procedure literals establish their own invocation lifetime;
            # they must never capture a surrounding call's temporary scope.
            lifetimes=self.expressions.fstrings
            saved=lifetimes.frames
            if n.kind=='Procedure':lifetimes.frames=[]
            try:code = self._render(n, expected, constant)
            finally:lifetimes.frames=saved
        if n in self.guards:
            code = self.guard_code(self.guards[n]) + code
        if n in self.evaluation_query_checks:
            code=''.join('_ = '+self.render(query)+'\n' for query in self.evaluation_query_checks[n])+code
        return self.mapping.wrap(code, self.path, n.start, n.end, n.kind)

    def guard_code(self, guards):
        return "".join(self.mapping.wrap(f'#assert(!({condition}), {json.dumps(message)})\n', self.path,
                                          origin.start, origin.end, "immutable")
                       for condition, message, origin in guards)

    def guard_block(self, block, condition, message, origin):
        self.block_guards.setdefault(block, []).append((condition, message, origin))
        if self.raw[block.start:block.start+1] != b"{" and block not in self.converted_do_blocks:
            # The owned lexer identifies the actual `do` keyword, excluding
            # comments/strings and nested expression syntax in the header.
            tokens = [span for span in self.info.get("do_tokens") or []
                      if block.parent.start <= span[0] < block.start]
            if tokens:
                a, b = tokens[-1]
                self.edits.append((a, b, ""))
                self.converted_do_blocks.add(block)

    def namespace_code(self, namespace):
        if namespace not in self.hidden_namespaces:
            self.hidden_namespaces[namespace] = self.prefix + "import_" + str(len(self.hidden_namespaces))
        return self.hidden_namespaces[namespace]

    def import_code(self, namespace):
        return self.output_import(self.evaluation_import_paths.get(namespace, self.imports[namespace]))

    def output_import(self,path):
        mapper=getattr(self,'backend_import',None)
        return mapper(self,path) if mapper else path

    def _render(self, n, expected=None, constant=False):
        overrides: dict[Node, tuple[Hint | None, bool]] = {}
        owning = set()
        kind = n.kind
        if kind == 'Import':
            namespace = self.import_names[n]
            path = self.import_code(namespace)
            if path != unquote(n.data['op']):
                a,b = n.data['roles']['path']
                return self.splice(n, [(a,b,self.mapping.wrap(json.dumps(path),self.path,a,b,'import'))])
        if self.defaults:
            code = self.defaults.render(n)
            if code is not None:
                return code
        if n in self.run_values:
            value=self.run_values[n]
            if callable(value):return value(expected,constant)
            return value['constant' if constant else 'runtime'] if isinstance(value,dict) else value
        lifetime_code=self.expressions.fstrings.render(n,expected,constant)
        if lifetime_code is not None:return lifetime_code
        if kind == 'Procedure' and self.resolver:
            invalid_default = (self.defaults.errors.get(n) if self.defaults else None)
            if not invalid_default and not (self.defaults and n in self.defaults.plans):
                invalid_default = self.resolver.generic_default_error(n)
            if invalid_default:
                signature, message = invalid_default
                token = self.prefix + f'generic_default_{n.start}_{n.end}'
                self.error_messages[token] = message
                # Keep the assertion at the original default-expression site.
                # Odin then honors inactive when arms and uninstantiated outer
                # bodies, while its diagnostic retains a real source span.
                return self.mapping.wrap(f'#assert(false, {json.dumps(token)})', self.path,
                                         signature.start, signature.end, 'generic-default')
        if self.purity:
            pure_code=self.purity.render(n,expected,constant)
            if pure_code is not None:return pure_code
        if kind=='Range':
            typed_loop=self.typed_loops.render(n)
            if typed_loop is not None:return typed_loop
        big_code = self.big.render(n, expected, constant)
        if big_code is not None: return big_code
        if kind=='Ternary' and constant:
            return '('+self.render(n.role('x'),expected,True)+' when ('+self.render(n.role('cond'),constant=True)+') else '+self.render(n.role('y'),expected,True)+')'
        extra = self.expressions.render(n, expected, constant)
        if extra is not None:
            return extra
        if kind == "Ident" and n.text in self.selected:
            parent = n.parent
            binding = parent and (
                parent.kind in {"Declaration", "Field", "Range"} and n in parent.group("names")
                or parent.kind in {"Selector", "FieldValue"} and n is parent.role("field")
                or parent.kind in {"ImplicitSelector", "EnumType", "PolyType"}
            )
            if not binding and self.lookup(n.text, n) is None:
                namespace, member = self.selected[n.text].split(".", 1)
                return self.namespace_code(namespace) + "." + member
        if kind == "Declaration":
            annotation = n.role("type")
            hint = Hint(self, annotation) if annotation else None
            for value in n.group("values"):
                overrides[value] = (hint, not n.data["mutable"])
                if n.data['mutable'] and (len(n.group('values'))==len(n.group('names'))): owning.add(value)
            if len(n.group('values')) == 1 and len(n.group('names')) > 1:
                from .switches import unwrap
                if unwrap(n.group('values')[0]).kind in {'SwitchExpr','CompileSwitchExpr'}:
                    overrides[n.group('values')[0]] = (self.expressions.switches.hints(n), not n.data['mutable'])
        elif kind == "Assignment":
            for lhs, rhs in zip(n.group("lhs"), n.group("rhs")):
                if lhs.kind == "Ident" and lhs.text == "_":
                    overrides[rhs] = (None, constant)
                    continue
                # The checker knows the exact type even for indexed fields,
                # dereferences, and procedure results. type_of never evaluates it.
                hint = self.type_hint(lhs) or Hint(self, lhs, f"type_of({self.render(lhs)})")
                overrides[rhs] = (hint, constant)
            if len(n.group('rhs')) == 1 and len(n.group('lhs')) > 1:
                from .switches import unwrap
                if unwrap(n.group('rhs')[0]).kind in {'SwitchExpr','CompileSwitchExpr'}:
                    overrides[n.group('rhs')[0]] = (self.expressions.switches.hints(n), constant)
        elif kind == "Field" and n.role("type") and n.role("default"):
            overrides[n.role("default")] = (Hint(self, n.role("type")), constant)
        elif kind == "Procedure":
            # A procedure is usually declared with ::, but its body is runtime code.
            for child in n.children:
                overrides[child] = (None, False)
            for clause in n.group("where"):
                overrides[clause] = (None, True)
        elif kind in {"StructType", "UnionType"}:
            for clause in n.group("where"):
                overrides[clause] = (None, True)
        elif kind == "When":
            overrides[n.role("cond")] = (None, True)
        elif kind == "ArrayType":
            if n.role("len"):
                overrides[n.role("len")] = (None, True)
        elif kind == "WhenExpr":
            overrides[n.role("cond")] = (None, True)
            for name in ("x", "y"):
                overrides[n.role(name)] = (expected, constant)
        elif kind == "Paren":
            overrides[n.role("expr")] = (expected, constant)
        elif kind == "FieldValue":
            overrides[n.role("value")] = (expected, constant)
        elif kind == "Return":
            procedure = next((a for a in n.ancestors() if a.kind == "Procedure"), None)
            fields = self.fields(procedure.role("type").role("results")) if procedure else []
            for value, (_, annotation) in zip(n.group("values"), fields):
                numeric = self.big.enabled and self.resolver.type_node(annotation).kind in {'big_integer','big_rational','generic'}
                overrides[value] = (Hint(annotation.source,annotation) if numeric else self.type_hint(annotation) or Hint(self, annotation), constant)
                if len(n.group('values'))==len(fields): owning.add(value)
            if len(n.group('values')) == 1 and len(fields) > 1:
                from .switches import unwrap
                if unwrap(n.group('values')[0]).kind in {'SwitchExpr','CompileSwitchExpr'}:
                    overrides[n.group('values')[0]] = (self.expressions.switches.hints(n), constant)
        elif kind == "Call":
            signature = self.signature(n)
            params = self.fields(signature.role("params")) if signature else []
            generic = signature and "$" in signature.role("params").text
            method = n.role("callee").kind == "Selector" and n.role("callee").data.get("op") == "->"
            for index, arg in enumerate(n.group("args")):
                param = None
                param_index = index + int(method)
                if arg.kind == "FieldValue":
                    for j, (name, annotation) in enumerate(params):
                        if name == arg.role("field").text:
                            param, param_index = annotation, j
                            break
                elif param_index < len(params):
                    param = params[param_index][1]
                hint = None
                if param and not generic:
                    # Query Odin for the actual formal type instead of copying
                    # callee-local/private type names into the caller's scope.
                    callee_text = self.render(n.role("callee"))
                    if method:
                        selector = n.role("callee")
                        callee_text = f'({self.render(selector.role("expr"))}).{self.render(selector.role("field"))}'
                    query = f"#type {self.prefix}meta.type_proc_parameter_type(type_of({callee_text}), {param_index})"
                    hint = Hint(param.source, param, query)
                if self.resolver:
                    hint = self.resolver.call_hint(n, arg, index) or hint
                if self.big.enabled:
                    hint=self.big.call_hint(n,arg,index) or hint
                overrides[arg] = (hint, constant or n.role("callee").text in {"#assert", "#config"})
            callee = n.role("callee")
            args = n.group("args")
            if len(args) == 1 and len(params) > 1:
                from .switches import unwrap
                switch = unwrap(args[0])
                if switch.kind in {'SwitchExpr','CompileSwitchExpr'} and self.expressions.switches.special(switch):
                    if not generic and not method:
                        self.needs_intrinsics = True
                        callee_text = self.render(callee)
                        count = self.expressions.switches.count(switch)
                        overrides[args[0]] = (tuple(Hint(self,args[0],f'#type {self.prefix}meta.type_proc_parameter_type(type_of({callee_text}), {i})')
                                                   if i < len(params) else None for i in range(count)),constant)
            if callee and callee.text in {"#load", "#load_hash", "#load_directory", "#exists"} and args and args[0].kind == "Literal":
                path = unquote(args[0].text)
                resource = None
                if ":" in path and not Path(path).is_absolute():
                    name, relative = path.split(":", 1)
                    if name in self.collections:
                        resource = (self.collections[name] / relative).resolve()
                elif not Path(path).is_absolute():
                    resource = (self.path.parent / path).resolve()
                if resource is not None:
                    replacement = json.dumps(str(resource), ensure_ascii=False)
                    children = [(c.start, c.end, replacement if c is args[0] else self.render(c, *overrides.get(c, (None, constant)))) for c in n.children]
                    return self.splice(n, children)
        if kind == "ForeignImport":
            replacements = []
            for literal in n.group("paths"):
                path = unquote(literal.text)
                base = self.path.parent
                if ":" in path:
                    collection, relative = path.split(":", 1)
                    if collection not in self.collections:
                        continue  # e.g. system: and framework: retain native meaning.
                    base, path = self.collections[collection], relative
                if not Path(path).is_absolute():
                    alias = next((name for name, directory in self.asset_collections.items() if directory == base), None)
                    if alias is None:
                        alias = self.prefix + "assets_" + str(len(self.asset_collections))
                        self.asset_collections[alias] = base
                    replacement = json.dumps(alias + ":" + path, ensure_ascii=False)
                    replacements.append((literal.start, literal.end, self.mapping.wrap(replacement, self.path, literal.start, literal.end, "resource")))
            return self.splice(n, replacements)
        if kind == "Compound" and not self.is_array(n):
            hint = self.type_hint(n.role("type")) or expected
            fields = self.struct_fields(hint) if hint else {}
            ordered = list(fields.items())
            compound_type = self.render(n.role("type")) if n.role("type") else expected.expression if expected else None
            elem = self.array_hint(hint)
            for i, value in enumerate(n.group("elems")):
                field_hint = None
                field_name = None
                if value.kind == "FieldValue":
                    field_name = value.role("field").text
                    field_hint = fields.get(field_name)
                elif i < len(ordered):
                    field_name, field_hint = ordered[i]
                if field_name and compound_type:
                    query = f"#type {self.prefix}meta.type_field_type({compound_type}, {json.dumps(field_name)})"
                    field_hint = Hint(field_hint.source, field_hint.node, query) if field_hint else Hint(self, n, query)
                if isinstance(elem, Node):
                    field_hint = Hint(elem.source, elem)
                shape = self.big.expected(hint) if self.big.enabled else None
                if shape and shape.kind == 'map' and shape.node:
                    annotation = shape.node.role('value')
                    field_hint = Hint(annotation.source, annotation)
                overrides[value] = (field_hint, constant)
                owning.add(value)
        if self.thor and kind == "Ternary" and n.data["op"] == "?":
            cond, x, y = [n.role(k) for k in ("cond", "x", "y")]
            if "\n" in self.text(cond.end, y.start):
                comments = self.comments(n.start, n.end, [(c.start, c.end) for c in (cond, x, y)])
                return f"(({self.render(cond, constant=constant)}) ?\n{comments}({self.render(x, expected, constant)}) : ({self.render(y, expected, constant)}))"
            overrides[x] = overrides[y] = (expected, constant)
        if self.thor and kind == "Binary" and n.data["op"] in {"in", "not_in"}:
            left, right = n.role("left"), n.role("right")
            sequence = right
            while sequence.kind == "Paren":
                sequence = sequence.role("expr")
            type_membership = self.denotes_type(left) and self.is_array(sequence)
            length = self.constant_array_length(sequence) if constant else None
            if (constant or type_membership) and (self.is_array(sequence) or length is not None):
                elements = sequence.group("elems") if self.is_array(sequence) else []
                equality = "==" if n.data["op"] == "in" else "!="
                joiner = " || " if equality == "==" else " && "
                lhs = self.render(left, constant=True)
                comments = self.comments(n.start, n.end, [(c.start, c.end) for c in [left, *elements]])
                if self.is_array(sequence) and not (type_membership or self.denotes_type(left)):
                    # Retain first-element/contextual typing instead of letting
                    # separate untyped comparisons accept a heterogeneous list.
                    rhs = self.render_array(sequence, needle=left, constant=True)
                    values = [f"({rhs})[{i}]" for i in range(len(elements))]
                else:
                    values = [self.render(e, constant=True) for e in elements]
                if not self.is_array(sequence):
                    rhs = self.render(sequence, constant=True)
                    values = [f"({rhs})[{i}]" for i in range(length)]
                result = joiner.join(f"(({lhs}) {equality} ({value}))" for value in values)
                if not result:
                    # Still resolve/type-check the operand of an empty list.
                    result = f"(({lhs}) == ({lhs}))" + (" && false" if equality == "==" else " || true")
                return "(" + comments + result + ")"
            if constant:
                # Native bit-set membership already is a constant expression.
                # Preserve it instead of introducing a procedure invocation.
                return self.splice(n, [(c.start, c.end, self.render(c, constant=True)) for c in n.children])
            self.needs_membership = True
            lhs = self.render(left, constant=constant)
            # Named arguments preserve source evaluation order while the first
            # formal parameter (container) supplies the needle's expected type.
            rhs = self.render_array(sequence, needle=left, constant=constant) if self.is_array(sequence) else self.render(right, constant=constant)
            value = f"{self.prefix}contains(value=({lhs}), values=({rhs}))"
            return f"(!{value})" if n.data["op"] == "not_in" else value
        if self.is_array(n):
            return self.render_array(n, expected=expected, constant=constant)
        replacements = []
        for child in n.children:
            hint, const = overrides.get(child, (None, constant))
            code = self.big.owned(child, hint, const) if child in owning else self.render(child, hint, const)
            replacements.append((child.start, child.end, code))
        inserts = []
        if n is self.root:
            inserts.extend(self.using_feature_insert())
        if self.defaults and kind == 'Call':
            inserts.extend(self.defaults.arguments(n))
        if self.purity and kind=='Call':
            inserts.extend(self.purity.calls.arguments(n))
        guards = self.block_guards.get(n)
        real_block = kind == "Block" and self.raw[n.start:n.start+1] == b"{"
        if guards and real_block:
            inserts = [(n.start+1, self.guard_code(guards))]
        if real_block and self.purity:
            prologue=self.purity.prologue(n)
            if prologue:inserts.append((n.start+1,prologue))
        if real_block and n in getattr(self, 'memory_prologues', ()):
            inserts.append((n.start+1, self.mapping.wrap(self.memory_prologues[n], self.path,
                                                       n.start, n.start+1, 'memory-debug')))
        result = self.splice(n, replacements, inserts)
        if guards and not real_block:
            result = "{" + self.guard_code(guards) + result + "}"
        if self.resolver and kind == "Declaration":
            result += self.resolver.declaration_support(n)
        if self.defaults and kind == 'Declaration':
            result = self.defaults.helpers(n) + result
        if self.purity and kind=='Declaration':
            result += self.purity.calls.helpers(n)
        if n in self.conditional_conflicts:
            flavor = 'wildcard' if any(name.text in self.wildcard_names for name in n.group('names')) else 'selective'
            result = f'#panic("declaration conflicts with {flavor} import")\n' + result
        if kind=='Case':
            declarations=self.expressions.declarations(n)
            if declarations:
                result=self.splice(n,replacements,[(n.data['roles']['colon'][1],declarations)])
        if kind == "Block":
            declarations = self.expressions.declarations(n)
            if declarations:
                if real_block:
                    result = self.splice(n, replacements, [*inserts, (n.start+1,declarations)])
                else:
                    result = "{" + declarations + result + "}"
                    self.expressions.convert_do(n)
            prologue=self.typed_loops.prologue(n)
            if prologue:
                if not real_block:
                    self.expressions.convert_do(n)
                    result='{'+result+'}'
                result='{'+prologue+result+'}'
        return result

    def render_array(self, n, expected=None, needle=None, constant=False):
        return self.mapping.wrap(self._render_array(n, expected, needle, constant), self.path, n.start, n.end, "array")

    @staticmethod
    def membership_needle(n):
        container = n
        while container.parent and container.parent.kind == 'Paren': container = container.parent
        parent = container.parent
        if parent and parent.kind == 'Binary' and parent.data['op'] in {'in','not_in'} and parent.role('right') is container:
            return parent.role('left')
        return None

    def _render_array(self, n, expected=None, needle=None, constant=False):
        needle = needle or self.membership_needle(n)
        elements = n.group("elems")
        target_type = None
        first_rendered = None
        if expected:
            if expected.expression:
                target_type = expected.expression
            elif expected.node.kind == "Compound":
                target_type = f"type_of({expected.source.render(expected.node)})"
            else:
                target_type = expected.source.render(expected.node)
            self.needs_intrinsics = True
            element_type = f"{self.prefix}meta.type_elem_type({target_type})"
            elem_node = self.array_hint(expected)
            node = elem_node if isinstance(elem_node, Node) else expected.node
            nested_hint = Hint(node.source, node, element_type)
        elif elements:
            first = elements[0]
            if needle and (first.kind == "ImplicitSelector" or first.text == "nil"
                           or not self.resolver.membership_untyped(needle)):
                self.needs_default = True
                element_type = f"type_of({self.prefix}default_value({self.render(needle)}))"
            else:
                first_rendered = self.render(first, constant=constant)
                self.needs_default = True
                element_type = f"type_of({self.prefix}default_value({first_rendered}))"
            # Later elements receive the inferred element type as their context,
            # including inner array literals and elided struct literal types.
            nested_hint = Hint(self, first, element_type)
        elif needle:
            self.needs_default = True
            element_type, nested_hint = f"type_of({self.prefix}default_value({self.render(needle)}))", None
        else:
            # Let Odin reject this only if the containing branch is active.
            element_type, nested_hint = self.prefix + "array_literal_needs_element_type", None
        values = ", ".join(self.big.owned(e, nested_hint, constant) if self.big.enabled else
                           first_rendered if i == 0 and first_rendered is not None else self.render(e, nested_hint, constant)
                           for i, e in enumerate(elements))
        comments = self.comments(n.start, n.end, [(e.start, e.end) for e in elements])
        literal = self.mapping.wrap(f"[{len(elements)}]{element_type}{{{comments}{values}}}",
                                    self.path, n.start, n.end, "array")
        # A named distinct array target needs an explicit same-length conversion.
        # Ordinary arrays must stay compound literals: wrapping them in even an
        # identity cast makes them invalid as Odin default parameter constants.
        resolved = expected.source.type_hint(expected.node) if expected else None
        plain_array = expected and (expected.node.kind == "ArrayType" or resolved and resolved.node.kind == "ArrayType")
        if target_type and not plain_array:
            literal = f"cast({target_type})({literal})"
        return f"({literal})"

    def using_feature_origin(self):
        """Gate only the AST forms Odin restricts, leaving explicit flags alone."""
        if not self.thor: return None
        for tag in self.info.get('file_tags') or []:
            words = tag.removeprefix('#+').split()
            if words and words[0] == 'feature' and any(word in {'using-stmt','!using-stmt'} for word in words[1:]):
                return None
        for node in self.nodes:
            if node.kind == 'Using': return node
            if node.kind == 'Field' and node.data.get('using_field'):
                fields = node.parent
                signature = fields.parent if fields else None
                if signature and signature.kind == 'ProcedureType' and signature.role('params') is fields:
                    return node
        return None

    def using_feature_insert(self):
        origin = self.using_feature_origin()
        if origin is None: return []
        newline = '\r\n' if b'\r\n' in self.raw else '\n'
        offset = 3 if self.raw.startswith(b'\xef\xbb\xbf') else 0
        return [(offset, self.mapping.wrap('#+feature using-stmt'+newline, self.path,
                                          origin.start, origin.end, 'support'))]

    def transpile(self, *, preserve_native=False):
        if self.excluded:
            code, self.source_map = self.mapping.finish(self.copy(0, len(self.raw)))
            return code
        if preserve_native:
            # Installed Odin already has valid native syntax. Only checked
            # variants need lowering; leave the rest of this file verbatim.
            inserts=self.using_feature_insert()+[(declaration.end,self.purity.calls.helpers(declaration))
                     for declaration in self.purity.calls.declarations if declaration.source is self]
            code=self.splice(self.root,[],inserts)
        else:
            code = self.render(self.root)
        code += self.expressions.declarations(self.root)
        arrays = [n for n in self.nodes if self.is_array(n)]
        array_span = (arrays[0].start, arrays[0].end) if arrays else (0, 0)
        if self.needs_intrinsics:
            code += self.mapping.wrap(f'\nimport {self.prefix}meta "base:intrinsics"\n', self.path, *array_span, "support")
        explicit = {name:target for name,target in self.selected.items() if name not in self.wildcard_names}
        if explicit:
            # Odin does not allow privacy attributes on every kind of alias.
            # Resolve file-local names in the AST and validate even unused imports
            # through local constant aliases in an otherwise empty private init.
            checks = "\n".join(self.mapping.wrap(f"    checked_{i} :: {self.namespace_code(target.split('.')[0])}.{name}", self.path,
                                *self.selected_origins[name], "import") for i, (name, target) in enumerate(explicit.items()))
            code += self.mapping.wrap(f'\n@(private="file", init)\n{self.prefix}validate_imports :: proc "contextless" () {{\n{checks}\n}}\n', self.path, *next(iter(self.selected_origins.values())), "support")
        if self.needs_default:
            code += self.mapping.wrap(f'\n@(private="file")\n{self.prefix}default_value :: proc "contextless" (value: $T) -> T {{ return value }}\n', self.path, *array_span, "support")
        if self.needs_membership:
            operation = next(n for n in self.nodes if n.kind == "Binary" and n.data["op"] in {"in", "not_in"})
            code += self.mapping.wrap("\n" + membership_helpers(self.prefix,self.expressions.module('sets'),self.big.module() if self.big.enabled else None), self.path, operation.start, operation.end, "membership-helper")
        for namespace, alias in self.hidden_namespaces.items():
            code += self.mapping.wrap(f'\nimport {alias} {json.dumps(self.import_code(namespace))}\n',
                                      self.path, 0, 0, "support")
        code += self.expressions.imports()
        if getattr(self, 'memory_import', None):
            code += self.mapping.wrap(self.memory_import, self.path, 0, 0, 'memory-debug')
        code, self.source_map = self.mapping.finish(code)
        return code


def membership_helpers(prefix,sets,big=None):
    signatures = {
        "array": "$C/[$N]$E",
        "slice": "$C/[]$E",
        "dynamic": "$C/[dynamic]$E",
        "fixed": "$C/[dynamic; $N]$E",
    }
    parts = []
    for name, signature in signatures.items():
        parts.append(f'''@(private="file")
{prefix}{name} :: proc "contextless" (values: {signature}, value: E) -> bool {{
    for element in values {{ if {big+'.compiler_equal(element,value)' if big else 'element == value'} {{ return true }} }}
    return false
}}
''')
    for name, signature, element in (("map", "$C/map[$K]$V", "K"), ("bits", "$C/bit_set[$E]", "E")):
        parts.append(f'''@(private="file")
{prefix}{name} :: proc "contextless" (values: {signature}, value: {element}) -> bool {{
    return value in values
}}
''')
    parts.append(f'@(private="file")\n{prefix}set :: proc "contextless" (values: {sets}.Set($E), value: E) -> bool {{ return value in values.table }}\n')
    parts.append(f'@(private="file")\n{prefix}contains :: proc{{' + ", ".join(prefix+n for n in [*signatures, "map", "bits", "set"]) + "}\n")
    return "\n".join(parts)

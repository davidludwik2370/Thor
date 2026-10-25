"""Lower T01-T17 using ASTs from the adapted Odin tooling parser.

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


class Source:
    def __init__(self, path: Path, frontend: Path, *, thor=True, package=False, test=False, project_name="", mapping=None, raw=None, target=()):
        self.path = path.resolve()
        self.thor = thor
        self.raw = path.read_bytes() if raw is None else raw
        self.mapping = mapping or MappingBuilder()
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
            result = subprocess.run(args, input=self.raw, capture_output=True, timeout=30)
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
            node = next((n for n in self.nodes if n.start == feature["start"] and n.end == feature["end"] and n.kind in {"Call", "Switch"}), None)
            if node:
                node.data.update(feature)
                if feature["kind"] in {"SwitchExpr", "CompileSwitchExpr", "CompileSwitch"}:
                    node.data.setdefault("roles", {})["selector"] = feature["selector"]
        self.excluded = self.info.get("excluded", False)
        self.diagnostics = self.info.get("diagnostics") or []
        immutable_starts = {span[0] for span in self.info.get("immutables") or []}
        for n in self.nodes:
            if n.kind == "Declaration":
                n.data["immutable"] = n.start in immutable_starts
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
        self.imports = {
            n.data["name"] or unquote(n.data["op"]).rsplit("/", 1)[-1].rsplit(":", 1)[-1]:
            unquote(n.data["op"]) for n in self.nodes if n.kind == "Import"
        }
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
        from .expressions import Expressions
        self.expressions = Expressions(self)
        self.selected = {}
        self.selected_origins = {}
        self.conditional_conflicts = set()
        digest = hashlib.sha256(str(self.path).encode()).hexdigest()[:12]
        self.prefix = f"__thor_{digest}_"
        while self.prefix.encode() in self.raw:
            self.prefix += "x_"
        self.edits = [(e["start"], e["end"], e["text"]) for e in self.info.get("edits", [])]
        for selection in self.info.get("selections", []) or []:
            names = selection["names"]
            if len(names) != len(set(names)):
                self.error("duplicate name in selective import", selection["start"], selection["end"])
            package = selection["package_name"] or unquote(selection["path"]).rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            if package == "_":
                self.error("selective import requires a package namespace", selection["start"], selection["end"])
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
        for module,needed in [('format',any(n.kind=='Interpolation' for n in self.nodes)),
                              ('sets',self.thor and any(n.kind in {'SetType','Binary','Call'} for n in self.nodes)),
                              ('sequence',self.thor and any(n.kind in {'Index','Slice'} for n in self.nodes))]:
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
        if n.kind == "Block" and n.parent and n.parent.kind in {"When","CompileSwitch"}:
            return False  # Odin when branches do not introduce a scope.
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
            for n in self.nodes:
                if n.kind == "Declaration" and self.scope(n) is scope:
                    if any(x and x.text == name for x in n.group("names")):
                        if name in self.selected and scope.kind == "File" and any(a.kind == "When" for a in n.ancestors()):
                            continue
                        return n
                if n.kind == "Range" and scope is n:
                    if any(x.text == name for x in n.group("names")):
                        return n
                if n.kind == "PolyType" and self.scope(n) is scope and n.data["name"] == name:
                    return n
                if n.kind == "Field" and scope.kind in {"Procedure", "StructType"} and self.scope(n) is scope:
                    if any(x and x.text.lstrip("$") == name for x in n.group("names")):
                        return n
        # Other source files in the same package share top-level declarations.
        for other in self.project.get(self.path.parent, []):
            if other is self:
                continue
            for n in other.nodes:
                if n.kind == "Declaration" and other.scope(n).kind == "File":
                    if any(x and x.text == name for x in n.group("names")):
                        return n
        return None

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
        if expression.kind in {"ArrayType", "StructType", "DynamicArrayType"}:
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
        if expression.kind.endswith("Type"):
            return True
        if expression.kind == "Call" and expression.role("callee").text == "type_of":
            return True
        builtins = {"bool", "b8", "b16", "b32", "b64", "int", "uint", "uintptr", "rune",
                    "i8", "i16", "i32", "i64", "i128", "u8", "u16", "u32", "u64", "u128",
                    "f16", "f32", "f64", "string", "cstring", "rawptr", "typeid", "any"}
        declaration = self.declaration_for(expression)
        if declaration is None:
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
        if n in self.blocked:
            token, message = self.blocked[n]
            self.error_messages[token] = message
            code = token
        else:
            code = self._render(n, expected, constant)
        if n in self.guards:
            code = self.guard_code(self.guards[n]) + code
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

    def _render(self, n, expected=None, constant=False):
        overrides: dict[Node, tuple[Hint | None, bool]] = {}
        kind = n.kind
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
        elif kind == "Assignment":
            for lhs, rhs in zip(n.group("lhs"), n.group("rhs")):
                if lhs.kind == "Ident" and lhs.text == "_":
                    overrides[rhs] = (None, constant)
                    continue
                # The checker knows the exact type even for indexed fields,
                # dereferences, and procedure results. type_of never evaluates it.
                hint = self.type_hint(lhs) or Hint(self, lhs, f"type_of({self.render(lhs)})")
                overrides[rhs] = (hint, constant)
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
                overrides[value] = (self.type_hint(annotation) or Hint(self, annotation), constant)
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
                overrides[arg] = (hint, constant or n.role("callee").text in {"#assert", "#config"})
            callee = n.role("callee")
            args = n.group("args")
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
                overrides[value] = (field_hint, constant)
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
            replacements.append((child.start, child.end, self.render(child, hint, const)))
        inserts = []
        guards = self.block_guards.get(n)
        real_block = kind == "Block" and self.raw[n.start:n.start+1] == b"{"
        if guards and real_block:
            inserts = [(n.start+1, self.guard_code(guards))]
        result = self.splice(n, replacements, inserts)
        if guards and not real_block:
            result = "{" + self.guard_code(guards) + result + "}"
        if self.resolver and kind == "Declaration":
            result += self.resolver.declaration_support(n)
        if n in self.conditional_conflicts:
            result = '#panic("declaration conflicts with selective import")\n' + result
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
        return result

    def render_array(self, n, expected=None, needle=None, constant=False):
        return self.mapping.wrap(self._render_array(n, expected, needle, constant), self.path, n.start, n.end, "array")

    def _render_array(self, n, expected=None, needle=None, constant=False):
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
            first_rendered = self.render(first, constant=constant)
            if needle and (first.kind == "ImplicitSelector" or first.text == "nil"):
                self.needs_default = True
                element_type = f"type_of({self.prefix}default_value({self.render(needle)}))"
            else:
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
        values = ", ".join(first_rendered if i == 0 and first_rendered is not None else self.render(e, nested_hint, constant)
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

    def transpile(self):
        if self.excluded:
            code, self.source_map = self.mapping.finish(self.copy(0, len(self.raw)))
            return code
        code = self.render(self.root)
        code += self.expressions.declarations(self.root)
        arrays = [n for n in self.nodes if self.is_array(n)]
        array_span = (arrays[0].start, arrays[0].end) if arrays else (0, 0)
        if self.needs_intrinsics:
            code += self.mapping.wrap(f'\nimport {self.prefix}meta "base:intrinsics"\n', self.path, *array_span, "support")
        if self.selected:
            # Odin does not allow privacy attributes on every kind of alias.
            # Resolve file-local names in the AST and validate even unused imports
            # through local constant aliases in an otherwise empty private init.
            checks = "\n".join(self.mapping.wrap(f"    checked_{i} :: {self.namespace_code(target.split('.')[0])}.{name}", self.path,
                                *self.selected_origins[name], "import") for i, (name, target) in enumerate(self.selected.items()))
            code += self.mapping.wrap(f'\n@(private="file", init)\n{self.prefix}validate_imports :: proc "contextless" () {{\n{checks}\n}}\n', self.path, *next(iter(self.selected_origins.values())), "support")
        if self.needs_default:
            code += self.mapping.wrap(f'\n@(private="file")\n{self.prefix}default_value :: proc "contextless" (value: $T) -> T {{ return value }}\n', self.path, *array_span, "support")
        if self.needs_membership:
            operation = next(n for n in self.nodes if n.kind == "Binary" and n.data["op"] in {"in", "not_in"})
            code += self.mapping.wrap("\n" + membership_helpers(self.prefix,self.expressions.module('sets')), self.path, operation.start, operation.end, "membership-helper")
        for namespace, alias in self.hidden_namespaces.items():
            code += self.mapping.wrap(f'\nimport {alias} {json.dumps(self.imports[namespace])}\n',
                                      self.path, 0, 0, "support")
        code += self.expressions.imports()
        code, self.source_map = self.mapping.finish(code)
        return code


def membership_helpers(prefix,sets):
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
    for element in values {{ if element == value {{ return true }} }}
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

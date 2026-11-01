"""Translate structured backend diagnostics and render one Thor diagnostic format."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .sourcemap import Origin, position


class Diagnostics:
    def __init__(self, sources=None, maps=None, generated=None, overlays=None, *, path_aliases=None):
        self.sources = {str(p): s for p, s in (sources or {}).items()}
        self.raw = {str(p): s.raw for p, s in (sources or {}).items()}
        self.prefixes = [s.prefix for s in (sources or {}).values()]
        self.error_messages = {name: message for s in (sources or {}).values() for name, message in s.error_messages.items()}
        self.namespace_aliases = {alias: name for s in (sources or {}).values() for name, alias in s.hidden_namespaces.items()}
        self.guard_messages = {(str(path), start, end): message for path, s in (sources or {}).items()
                               for (start, end), message in s.guard_messages.items()}
        self.type_operand_errors = {str(path): s.type_operand_errors for path, s in (sources or {}).items()}
        self.hidden_parameters={calls.marker for s in (sources or {}).values()
            if (calls:=getattr(getattr(s,'purity',None),'calls',None)) and calls.names}
        self.raw.update({str(p): b for p, b in (overlays or {}).items()})
        self.maps = maps or {}
        self.generated = generated or {}
        self.path_aliases=path_aliases or {}
        self.reference_patterns = [(file, re.compile(re.escape(file) + r"(?:\((\d+):(\d+)\)|:(\d+):(\d+):)"))
                                   for file in self.maps]

    def read(self, file):
        if file not in self.raw:
            try:
                self.raw[file] = Path(file).read_bytes()
            except OSError:
                self.raw[file] = b""
        return self.raw[file]

    def locate(self, origin):
        raw = self.read(origin.file)
        line, column = position(raw, origin.start)
        end_line, end_column = position(raw, max(origin.start, origin.end))
        return {"file": origin.file, "offset": origin.start, "end_offset": origin.end,
                "line": line, "column": column, "end_line": end_line, "end_column": end_column}

    def lookup(self, file, offset, length=1):
        file = str(Path(file).resolve()) if file else ""
        if file in self.maps:
            return self.maps[file].lookup(offset, length)
        return Origin(file, offset, offset + length, "source", True)

    def reference_origin(self, file, line, column):
        raw = self.generated[file]
        lines = raw.splitlines(keepends=True)
        # Odin compiler columns count Unicode code points, offsets count bytes.
        offset = sum(map(len, lines[:line - 1]))
        if 0 < line <= len(lines):
            offset += len(lines[line - 1].decode("utf-8")[:max(column - 1, 0)].encode())
        return self.lookup(file, offset)

    def reference(self, file, line, column, unix=False):
        origin = self.reference_origin(file, line, column)
        pos = self.locate(origin)
        if unix:
            return f'{pos["file"]}:{pos["line"]}:{pos["column"]}:'
        return f'{pos["file"]}({pos["line"]}:{pos["column"]})'

    def translate(self, errors, *, backend=True):
        result, seen = [], set()
        for error in errors:
            msgs = list(error.get("msgs") or [])
            generated_result_failure = False
            type_operand = None
            related = []
            for name in self.hidden_parameters:
                msgs=[re.sub(r'(?:,\s*)?\$?'+re.escape(name)+r'\s*:\s*\$?(?:bool|'+re.escape(name)+r'_Type)(?:\s*=\s*(?:true|false))?', '',m) for m in msgs]
            for token, message in self.error_messages.items():
                if any(token in m for m in msgs):
                    msgs = [message]
                    break
            pos = error.get("pos") or {}
            file = pos.get("file", "")
            origin = None
            if file:
                offset = pos.get("offset", 0)
                if backend:
                    raw = self.generated.get(file, self.read(file))
                    # Native JSON stores end_column, but no end offset.
                    count = max(pos.get("end_column", 0) - pos.get("column", 0), 1)
                    length = len(raw[offset:].decode("utf-8", errors="replace")[:count].encode())
                    origin = self.lookup(file, offset, length)
                else:
                    raw = self.read(file)
                    token = re.match(r"\w+|.", raw[offset:].decode("utf-8", errors="replace"))
                    end = pos.get("end_offset", offset + (len(token[0].encode()) if token else 0))
                    origin = Origin(file, offset, end)
            if origin and origin.kind == "immutable":
                message = self.guard_messages.get((origin.file, origin.start, origin.end))
                if message:
                    msgs = [message, *[m for m in msgs if "instantiated as" in m or "Called within" in m]]
            if origin and origin.kind=='range-type':
                msgs=[m.replace('ternary expression','range membership') for m in msgs]
            if origin and any('is not an expression but a type' in message for message in msgs):
                for operation, base in self.type_operand_errors.get(origin.file, {}).items():
                    if base.start <= origin.start < base.end:
                        verb = 'index' if operation.kind == 'Index' else 'slice'
                        shape = base.source.resolver.type_node(base)
                        value = 'an array value' if shape.kind == 'array' else 'a value'
                        msgs = [f"Cannot {verb} '{base.text}': it names a type, not {value}."]
                        symbol = base.source.resolver.symbol(base)
                        if symbol:
                            declaration = symbol.declaration
                            related = [{'pos': self.locate(Origin(str(symbol.node.source.path), symbol.node.start, symbol.node.end)),
                                        'message': f"'{symbol.name}' is declared as a type here."}]
                            if symbol.value and symbol.value.kind == 'ArrayType' and len(declaration.group('names')) == 1:
                                msgs.append(f"To create a zero-initialized array variable, use '{symbol.name}: {symbol.value.text}'.")
                        origin = Origin(origin.file, base.start, base.end)
                        type_operand = operation
                        break
            if origin and origin.kind=='power-type':
                mismatch=next((re.search(r"Mismatched types in (?:binary expression .* : |ternary if expression, )(.+)",m) for m in msgs if 'Mismatched types in ' in m),None)
                if mismatch:
                    msgs=["Power ('**') requires matching operand types: "+mismatch[1]+'.',
                          'Convert an operand explicitly; only untyped numeric constants are coerced.']
                elif any('String concatenation' in m or 'not defined' in m or 'not supported' in m for m in msgs):
                    msgs=["Power ('**') requires integer or floating-point operands."]
            # These are public Thor support contracts, rather than an error in
            # the library implementation. Native generic notes identify the
            # actual use (including a call in an imported/unsaved file).
            runtime = Path(__file__).resolve().parent.parent / "runtime"
            contract = None
            if origin and Path(origin.file) == runtime / "format" / "format.odin" and any(
                "type_is_integer(T)" in m for m in msgs
            ):
                contract = "Integer grouping requires an integer value."
            elif origin and Path(origin.file) == runtime / "sets" / "sets.odin" and any(
                "type_is_valid_map_key(T)" in m or "Invalid type of a key for a map" in m for m in msgs
            ):
                contract = "Set elements must be valid Odin map keys."
            elif origin and Path(origin.file) == runtime / 'numeric' / 'numeric.odin':
                contract = "Power ('**') requires integer or floating-point operands of matching types."
            elif origin and Path(origin.file) == runtime / 'sequence' / 'sequence.odin' and any(
                'type_is_ordered(T)' in m for m in msgs
            ):
                contract = 'Spread min/max requires ordered numeric, enum, or string elements.'
            elif origin and Path(origin.file) == runtime / 'sequence' / 'sequence.odin':
                contract=next((text for text in [
                    'Typed loop indices require an integer type.',
                    'Typed index bindings are supported only for arrays and slices.',
                    "The array's indices do not fit in the declared loop index type.",
                ] if any(text in message for message in msgs)),None)
            if contract:
                caller = None
                for message in msgs:
                    for reference_file, pattern in self.reference_patterns:
                        for match in pattern.finditer(message):
                            candidate = self.reference_origin(reference_file, *reference_coordinates(match)[:2])
                            if not Path(candidate.file).is_relative_to(runtime):
                                caller = candidate
                                break
                        if caller:
                            break
                    if caller:
                        break
                if caller:
                    source = self.sources.get(caller.file)
                    calls = [n for n in source.nodes if (n.kind == "Call" or n.kind=='Binary' and n.data['op']=='**') and n.start <= caller.start < n.end] if source else []
                    if calls:
                        call = min(calls, key=lambda n: n.end - n.start)
                        caller = Origin(caller.file, call.start, call.end)
                    origin, msgs = caller, [contract]
            # An error in a generic support body belongs to its instantiation.
            if origin and origin.kind == "membership-helper":
                for message in msgs:
                    for reference_file, pattern in self.reference_patterns:
                        for m in pattern.finditer(message):
                            if message[m.end():].lstrip().startswith("instantiated as"):
                                line, column, _ = reference_coordinates(m)
                                origin = self.reference_origin(reference_file, line, column)
                                break
                        if origin.kind != "membership-helper":
                            break
                    if origin.kind != "membership-helper":
                        break
                msgs = ["Membership requires comparable elements. " + msgs[0]]
            compiler_support=origin and (Path(origin.file) in {runtime/'sequence'/'sequence.odin',runtime/'sets'/'compiler.odin',runtime/'pure'/'pure.odin',runtime/'eval'/'eval.odin'} or origin.kind=='pure-type-validator')
            if compiler_support:
                # Generic dispatch is part of language lowering. Its checker
                # failures belong to the user's operation, not the support file.
                caller=None
                for message in msgs:
                    for reference_file,pattern in self.reference_patterns:
                        for match in pattern.finditer(message):
                            candidate=self.reference_origin(reference_file,*reference_coordinates(match)[:2])
                            if not Path(candidate.file).is_relative_to(runtime) and candidate.kind!='pure-type-validator':
                                caller=candidate
                                break
                        if caller: break
                    if caller: break
                if caller: origin=caller
            msgs=[m for m in msgs if not ('instantiated as' in m and any(prefix in m for prefix in self.prefixes))]
            if compiler_support:
                msgs=[m for m in msgs if not re.search(r"instantiated as '(?:compiler_[a-z_]+|normalize_signed|normalize_unsigned|validate|validate_format|write|emit)\(",m)]
            if any(prefix + "array_literal_needs_element_type" in m for m in msgs for prefix in self.prefixes):
                msgs = ["An empty array literal needs an element type from its context."]
            elif msgs and any(f"procedure group '{prefix}contains'" in msgs[0] for prefix in self.prefixes):
                types = [m.replace(" • value =", " • element =").replace(" • values =", " • container =")
                         for m in msgs[1:] if "Given argument types" in m or " • " in m]
                msgs = ["Invalid operands for membership ('in' / 'not_in'): no matching operation.", *types]
            elif msgs:
                operations={'union':'|','intersection':'&','difference':'-','symmetric_difference':'~','equal':'== / !=',
                            'add_assign':'+=','sub_assign':'-=','union_assign':'|=',
                            'intersection_assign':'&=','xor_assign':'~='}
                for name,operator in operations.items():
                    if any(f"procedure group '{prefix}sets.compiler_{name}'" in msgs[0] for prefix in self.prefixes):
                        types=[m for m in msgs[1:] if 'Given argument types' in m or ' • ' in m]
                        msgs=[f"Invalid operands for '{operator}': no matching operation.",*types]
                        break
            if origin:
                original = self.read(origin.file)[origin.start:origin.end].decode("utf-8", errors="replace")
                if msgs and "'where' clause evaluated to false" in msgs[0] and origin.kind == "Binary":
                    if len(msgs) > 1:
                        expanded = msgs[1].strip()
                        if expanded:
                            msgs = [m.replace(expanded, original) for m in msgs]
                # Native messages also quote whole lowered expressions. Replace
                # those using recorded emission boundaries, never source guesses.
                for generated_file, mapping in self.maps.items():
                    for a, b, source_file, start, end in sorted(mapping.expansions, key=lambda s: s[1] - s[0], reverse=True):
                        generated_text = self.generated[generated_file][a:b].decode("utf-8")
                        original_text = self.read(source_file)[start:end].decode("utf-8")
                        if generated_text and generated_text != original_text:
                            msgs = [m.replace(generated_text, original_text) for m in msgs]
                # An unresolved generated result parameter is not a real return
                # type. For example, a power mismatch can leave the enclosing
                # numeric-dispatch helper returning '$__thor_..._Result'. Keep
                # that failure only if no underlying error covers its source
                # expression. Actual typed returns (and user generic parameters)
                # must retain their ordinary diagnostics.
                generated_result_failure = not origin.exact and any(
                    message.startswith('Cannot assign value ') and 'in return statement' in message
                    and re.search(r"of type '\$"+re.escape(prefix)+r"Result(?:_\d+)?'",message)
                    for message in msgs for prefix in self.prefixes
                )
                # Generated implementation names never belong in user diagnostics.
                for name in self.hidden_parameters:
                    msgs=[re.sub(r'(?:,\s*)?'+re.escape(name)+r'\s*=\s*true\b','',m) for m in msgs]
                for alias, name in self.namespace_aliases.items():
                    if name == 'big':
                        for logical in ('Integer','Rational'):
                            msgs = [m.replace(alias+'.'+logical, logical) for m in msgs]
                    msgs = [m.replace(alias, name) for m in msgs]
                for prefix in self.prefixes:
                    msgs = [re.sub(re.escape(prefix) + r"[A-Za-z0-9_]+", "Thor expression", m) for m in msgs]
            # Rewrite locations embedded in overloads, declarations and generic notes.
            for generated_file, pattern in self.reference_patterns:
                msgs = [pattern.sub(lambda m: self.reference(generated_file, *reference_coordinates(m)), text) for text in msgs]
            for generated_prefix,original_prefix in self.path_aliases.items():
                msgs=[text.replace(generated_prefix,original_prefix) for text in msgs]
            diagnostic = {"type": error.get("type", "error"), "stage": error.get("stage", "checker" if backend else "thor"),
                          "pos": self.locate(origin) if origin else None, "msgs": msgs}
            if related: diagnostic['related'] = related
            diagnostic['_type_operand'] = type_operand
            diagnostic['_type_query_cascade'] = bool(origin and not origin.exact and any(
                message == 'Expected a type to assign to the type parameter'
                or "'type_of' of polymorphic type cannot be determined" in message
                or "Cannot determine polymorphic type from parameter: 'invalid type'" in message
                for message in msgs
            ))
            diagnostic["_cascade"] = bool(origin and not origin.exact and (
                generated_result_failure or origin.kind in {'range-type','power-type'}
                or origin.kind=='typed-loop-check' and msgs and msgs[0].startswith('A typed numeric range requires ')
                or origin.kind in {"array", "Compound"} and any("type_of" in m for m in msgs)))
            if msgs and msgs[0].startswith("Power ('**') requires"):
                diagnostic['_cascade']=False
            if origin and origin.kind=='power-type' and msgs and (msgs[0].startswith('Cannot convert numeric value') or 'truncated to' in msgs[0]):
                diagnostic['_cascade']=False
            key = json.dumps([diagnostic["type"], diagnostic["pos"], msgs], sort_keys=True)
            if key not in seen:
                result.append(diagnostic)
                seen.add(key)
        # Inference duplicates user expressions inside type queries. Keep the
        # underlying error; discard only query failures covering that same error.
        filtered = []
        for diagnostic in result:
            pos = diagnostic["pos"]
            if pos and any(self.type_operand_cascade(diagnostic, other['_type_operand'])
                           for other in result if other['_type_operand'] is not None):
                continue
            if diagnostic["_cascade"] and any(
                not other["_cascade"] and other["type"] == "error" and other["pos"]
                and other["pos"]["file"] == pos["file"]
                and pos["offset"] <= other["pos"]["offset"] < pos["end_offset"]
                for other in result
            ):
                continue
            filtered.append({k: v for k, v in diagnostic.items() if not k.startswith('_')})
        return filtered

    @staticmethod
    def type_operand_cascade(diagnostic, operation):
        """Only discard contextual compound failures from this invalid target.

        Odin itself may infer the array type as the destination for an element
        assignment after rejecting that destination. Independent name/type
        errors, explicit compound types and other statements remain visible.
        """
        pos = diagnostic['pos']
        if (diagnostic['_type_query_cascade'] and pos['file'] == str(operation.source.path)
                and pos['offset'] <= operation.start < pos['end_offset']): return True
        current = operation
        while current.parent and current.parent.kind in {'Paren', 'Selector', 'Index', 'Deref'}:
            current = current.parent
        assignment = current.parent
        if not assignment or assignment.kind != 'Assignment': return False
        lhs, rhs = assignment.group('lhs'), assignment.group('rhs')
        if current not in lhs or len(lhs) != len(rhs): return False
        value = rhs[lhs.index(current)]
        if value.kind != 'Compound' or value.role('type') or value.source.is_array(value): return False
        if pos['file'] != str(value.source.path) or not value.start <= pos['offset'] < value.end: return False
        if pos['offset'] == value.start and any(
            re.match(r'Expected \d+ values for this (?:array|struct) literal', message)
            or message.startswith('Illegal compound literal,') for message in diagnostic['msgs']
        ): return True
        # Nested calls and explicitly typed compounds supply their own contexts;
        # a conversion failure inside those is independently useful.
        elements = [element.role('value') if element.kind == 'FieldValue' else element
                    for element in value.group('elems')]
        return (any(element.start == pos['offset'] and not (element.kind == 'Compound' and element.role('type'))
                    for element in elements)
                and any(message.startswith('Cannot convert untyped value ') for message in diagnostic['msgs']))

    def text(self, diagnostic, *, terse=False, unix=False):
        pos = diagnostic.get("pos")
        location = "thor"
        if pos:
            location = (f'{pos["file"]}:{pos["line"]}:{pos["column"]}:' if unix
                        else f'{pos["file"]}({pos["line"]}:{pos["column"]}):')
        severity = diagnostic["type"].capitalize()
        output = [f'{location} {severity}: ' + "\n".join(diagnostic["msgs"])]
        if pos and not terse:
            raw = self.read(pos["file"])
            lines = raw.decode("utf-8", errors="replace").splitlines()
            if 0 < pos["line"] <= len(lines):
                line = lines[pos["line"] - 1]
                prefix = raw[:pos["offset"]].rsplit(b"\n", 1)[-1].decode("utf-8", errors="replace")
                width = max(1, len(raw[pos["offset"]:pos["end_offset"]].split(b"\n", 1)[0].decode("utf-8", errors="replace").expandtabs(4)))
                output.extend(["    " + line.expandtabs(4), "    " + " " * len(prefix.expandtabs(4)) + "^" * width])
        for related in diagnostic.get('related', []):
            output.append(self.text({'type': 'note', 'pos': related['pos'], 'msgs': [related['message']]}, terse=terse, unix=unix))
        return "\n".join(output)


def reference_coordinates(match):
    return int(match[1] or match[3]), int(match[2] or match[4]), match[3] is not None


def payload(diagnostics):
    return {"error_count": sum(d["type"] == "error" for d in diagnostics), "errors": diagnostics}

"""Translate structured backend diagnostics and render one Thor diagnostic format."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .sourcemap import Origin, position


def backend_error_flags(flags):
    """Leave room for native helper cascades before source-level deduplication.

    Odin counts repeated errors before merging them. Its small default budget
    can otherwise stop checking before an independent source error is visited.
    Explicit user limits retain their ordinary native meaning.
    """
    return [] if any(flag.startswith('-max-error-count:') for flag in flags) else ['-max-error-count:1000']


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
        self.sequence_bounds = {str(path): s.sequence_bounds for path, s in (sources or {}).items()}
        self.hidden_parameters={name for s in (sources or {}).values()
            if (calls:=getattr(getattr(s,'purity',None),'calls',None))
            for name in (calls.marker,calls.ownership)}
        self.checked_names={name:proc.parent.group('names')[0].text for s in (sources or {}).values()
            if (calls:=getattr(getattr(s,'purity',None),'calls',None)) for proc,name in calls.names.items()}
        self.checked_callable_markers={calls.ownership_type for s in (sources or {}).values()
            if (calls:=getattr(getattr(s,'purity',None),'calls',None))}
        self.pure_type_checks={(str(path),start,end):description for path,s in (sources or {}).items()
                               for (start,end),description in getattr(s,'pure_type_checks',{}).items()}
        self.pure_type_tokens={token:Origin(str(path),start,end,'pure-type-check') for path,s in (sources or {}).items()
                               for token,(start,end) in getattr(s,'pure_type_tokens',{}).items()}
        self.purity_messages={message for s in (sources or {}).values() if (purity:=getattr(s,'purity',None))
                              for message in [purity.storage_message,*purity.problems.values(),*(message for _,message in purity.contracts.values()),
                                              *(message for conditions in purity.conditions.values() for _,message in conditions)]}
        self.native_pure_origins={proc:site for s in (sources or {}).values()
                                  if (calls:=getattr(getattr(s,'purity',None),'calls',None))
                                  for proc,site in calls.origins.items() if getattr(proc.source,'native_inspected',False)}
        self.raw.update({str(p): b for p, b in (overlays or {}).items()})
        self.maps = maps or {}
        self.generated = generated or {}
        self.path_aliases=path_aliases or {}
        self.expression_patterns = None
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
            if msgs and msgs[0].startswith('Invalid use of a non-specialized polymorphic type') and any(name in msgs[0] for name in self.checked_callable_markers):
                msgs=['Pure code cannot store or indirectly call procedure values.']
            generated_result_failure = False
            generated_type_failure = False
            type_operand = None
            index_dependencies = []
            related = list(error.get('related') or []) if not backend else []
            for name in self.hidden_parameters:
                msgs=[re.sub(r'(?:,\s*)?\$?'+re.escape(name)+r'\s*:\s*\$?(?:bool|'+re.escape(name)+r'_Type)(?:\s*=\s*(?:true|false|struct\s*\{\s*\}\s*\{\s*\}))?', '',m) for m in msgs]
                msgs=[re.sub(r'^[ \t]*•[ \t]*'+re.escape(name)+r'[ \t]*=.*(?:\n|$)','',m,flags=re.MULTILINE) for m in msgs]
            msgs=[message for message in msgs if message.strip()]
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
            if origin:
                bound_info=self.sequence_bounds.get(origin.file,{}).get((origin.start,origin.end))
                normalization=msgs and any(f"procedure group '{prefix}sequence.normalize'" in msgs[0] for prefix in self.prefixes)
                query_failure=any(
                    message.startswith("Expected a type for 'type_is_")
                    or "'type_of' of polymorphic type cannot be determined" in message
                    or message == 'Expected a type to assign to the type parameter'
                    or "Cannot determine polymorphic type from parameter: 'invalid type'" in message
                    or any(f"procedure group '{prefix}sequence.element'" in message for prefix in self.prefixes)
                    for message in msgs
                )
                implicit_bound_cast=(bound_info and bound_info[1].kind in {'Literal','Ident','Unary','Paren'}
                                     and msgs and msgs[0].startswith('Cannot cast '))
                if bound_info and (origin.kind in {'sequence-index','sequence-lower','sequence-upper'} or implicit_bound_cast):
                    operation,bound,role=bound_info
                    types=[message.strip().removeprefix('• ').strip() for message in msgs if message.strip().startswith('• ')]
                    actual=types[-1] if normalization and types else None
                    if not actual and not normalization and any('Cannot convert' in message or 'Cannot cast' in message or 'truncated to' in message for message in msgs):
                        shape=bound.source.resolver.type_of(bound)
                        if shape.name and (shape.kind=='string' or shape.kind=='scalar' and shape.name.startswith(('f','complex','quaternion','b'))):
                            actual=('untyped string' if shape.kind=='string' else 'untyped float' if shape.name.startswith('f') else 'untyped boolean') if shape.untyped else shape.name
                    if actual and actual != 'invalid type':
                        subject='Sequence indices' if role=='index' else f'The {role} slice bound'
                        notes=[message for message in msgs if 'instantiated as' in message]
                        msgs=[f"{subject} must have an integer type; '{bound.text}' has type '{actual}'.",*notes]
                    elif normalization or query_failure:
                        index_dependencies=[operation]
                if not origin.exact and query_failure and not index_dependencies:
                    source=self.sources.get(origin.file)
                    if source:
                        index_dependencies=[node for node in source.nodes if node.kind in {'Index','Slice'}
                            and origin.start <= node.start and node.end <= origin.end]
                context=self.membership_context(origin,msgs)
                if context:
                    explanation,note=context
                    msgs=[*msgs,explanation]
                    related.append(note)
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
                "type_is_integer(T)" in m and "Grouping requires" not in m for m in msgs
            ):
                contract = "Integer grouping requires an integer value."
            elif origin and Path(origin.file).parent == runtime / 'format':
                contract=next((text for text in [
                    'Grouping requires a decimal numeric format.',
                    'Interpolation width must have an integer type.',
                    'Rational g/G formatting requires explicit precision, e.g. :.6g; omit the verb for an exact fraction.',
                    'Rational formatting does not support this verb.',
                    'Integer formatting does not support this verb.',
                    'Boolean formatting requires v, w, T, or t.',
                    'Precision is not supported for boolean values.',
                    'String formatting requires v, w, T, s, q, x, or X.',
                    'Float formatting requires v, w, T, f/F, e/E, g/G, h/H, or %.',
                    'Complex/quaternion formatting requires v, w, T, f/F, e/E, g/G, or h/H.',
                    'Precision is supported for scalar numbers and strings, not aggregate values.',
                    'Decimal formatting requires a numeric value.',
                    'Byte precision requires s, q, w, x, or X.',
                ] if any(text in m for m in msgs)),None)
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
                    if calls and caller.kind!='interpolation-width':
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
            pure_format=bool(origin and Path(origin.file)==runtime/'format'/'format.odin' and msgs
                             and msgs[0].removeprefix('Compile time panic: ').startswith('Pure formatting supports'))
            compiler_support=origin and (Path(origin.file) in {runtime/'sequence'/'sequence.odin',runtime/'sets'/'compiler.odin',runtime/'pure'/'pure.odin',runtime/'eval'/'eval.odin'} or origin.kind=='pure-type-validator' or pure_format)
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
            pure_type=self.pure_type_checks.get((origin.file,origin.start,origin.end)) if origin else None
            for token,site in self.pure_type_tokens.items():
                if msgs and token in msgs[0]:
                    origin=site
                    pure_type=self.pure_type_checks[site.file,site.start,site.end]
                    msgs=[message.replace(token,'') for message in msgs]
                    break
            if pure_type and msgs:
                # Keep the rejected type, without compiler validator frames.
                instantiated=next((match[1] for message in msgs if (match:=re.search(r"\$T\s*=\s*(.+?),\s*\$Result:",message))),None)
                msgs=[msgs[0].removeprefix('Compile time panic: '),f'While checking {pure_type}.'+
                      (f' Rejected type: {instantiated}.' if instantiated else '')]
            elif msgs and msgs[0].removeprefix('Compile time panic: ') in self.purity_messages:
                msgs=[msgs[0].removeprefix('Compile time panic: ')]
            elif pure_format:
                msgs=[msgs[0].removeprefix('Compile time panic: ')]
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
                msgs = self.restore_expressions(msgs)
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
                # A copied literal can be rejected against an unresolved compiler
                # result type inferred from another operand. Its exact source map
                # does not make that generated type conversion a primary error.
                generated_type_failure = any(
                    re.search(r"(?:to|of type) '\$"+re.escape(prefix)+r"[A-Za-z0-9_]+'", message)
                    for message in msgs for prefix in self.prefixes
                )
                generated_type_failure |= bool(not origin.exact and (
                    msgs and msgs[0].startswith('Invalid operands for ') and any('invalid type' in message for message in msgs)
                    or msgs and msgs[0] in {'Expected a constant boolean condition in ternary when expression',
                                           "Non-constant condition in 'when' statement"}
                    or any(message.startswith("Expected a type for 'type_is_") and any(prefix in message for prefix in self.prefixes)
                           for message in msgs)
                    or origin.kind in {'expression-helper','interpolation-field','debug-capture'}
                    and any(prefix in message and ('is not an expression but a type' in message
                                or message.startswith("'type_of(") and message.endswith("' is not a type"))
                            for message in msgs for prefix in self.prefixes)
                ))
                # Generated implementation names never belong in user diagnostics.
                for name in self.hidden_parameters:
                    msgs=[re.sub(r'(?:,\s*)?'+re.escape(name)+r'\s*=\s*true\b','',m) for m in msgs]
                for name,original in self.checked_names.items():
                    msgs=[re.sub(r'\b'+re.escape(name)+r'\b',lambda _:original,m) for m in msgs]
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
            if origin:
                proc=min((proc for proc in self.native_pure_origins if str(proc.source.path)==origin.file
                          and proc.start<=origin.start<proc.end),key=lambda proc:proc.end-proc.start,default=None)
                if proc:
                    note=self.locate(origin);site=self.native_pure_origins[proc].role('callee')
                    related.append({'pos':note,'message':'Purity violation in standard-library implementation.'})
                    msgs.append(f"In standard-library source: {note['file']}({note['line']}:{note['column']})")
                    origin=Origin(str(site.source.path),site.start,site.end,'pure-native-call')
            diagnostic = {"type": error.get("type", "error"), "stage": error.get("stage", "checker" if backend else "thor"),
                          "pos": self.locate(origin) if origin else None, "msgs": msgs}
            if related: diagnostic['related'] = related
            diagnostic['_type_operand'] = type_operand
            diagnostic['_index_dependencies'] = index_dependencies
            diagnostic['_type_query_cascade'] = bool(origin and not origin.exact and self.inference_query_failure(msgs))
            diagnostic['_pure_type_context']=(origin.file,origin.start,origin.end) if pure_type and origin else None
            diagnostic['_inference_failure'] = generated_type_failure or diagnostic['_type_query_cascade']
            diagnostic['_inference_contexts'] = self.inference_contexts(origin) if diagnostic['_inference_failure'] else ()
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
            if diagnostic['_pure_type_context'] and self.inference_query_failure(diagnostic['msgs']) and any(
                    other is not diagnostic and other['_pure_type_context']==diagnostic['_pure_type_context']
                    and any(message.startswith('Undeclared name:') or 'is not a type' in message for message in other['msgs'])
                    for other in result):continue
            contexts=diagnostic['_inference_contexts']
            if contexts and any(
                other is not diagnostic and other['type']=='error' and other['pos']
                and not other['_inference_failure'] and not other['_cascade']
                and other['pos']['file']==str(context.source.path)
                and context.start <= other['pos']['offset'] < other['pos']['end_offset'] <= context.end
                for context in contexts for other in result
            ): continue
            if diagnostic['_index_dependencies'] and any(
                other['type']=='error' and other['pos'] and other['msgs']
                and (other['msgs'][0].startswith('Undeclared name:')
                     or re.search(r"of type '.+' has no field '",other['msgs'][0]))
                and any(other['pos']['file']==str(operation.source.path)
                        and operation.start <= other['pos']['offset'] < operation.end
                        for operation in diagnostic['_index_dependencies'])
                for other in result
            ): continue
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
    def inference_query_failure(messages):
        return any(
            message == 'Expected a type to assign to the type parameter'
            or "'type_of' of polymorphic type cannot be determined" in message
            or "Cannot determine polymorphic type from parameter: 'invalid type'" in message
            for message in messages
        )

    def inference_contexts(self, origin):
        """Value operations on which a generated type query/conversion depends.

        Both sides of arithmetic and the arms of a ternary share inference;
        parentheses/unary operations propagate it. Call arguments, separate
        interpolation fields and statements are boundaries, not dependencies
        merely because they share a line or an enclosing string.
        """
        source=self.sources.get(origin.file) if origin else None
        if source is None:return ()
        propagate={'Binary','Unary','Paren','Ternary','Index','Slice','Selector',
                   'Deref','Compound','SwitchExpr','CompileSwitchExpr'}
        operations=propagate|{'Call','Interpolation','Run'}
        atoms={'Ident','Literal','ImplicitSelector'}
        candidates=[node for node in source.nodes if node.kind in operations|atoms
                    and node.start <= origin.start and origin.end <= node.end]
        if not candidates:return ()
        original=min(candidates,key=lambda n:n.end-n.start)
        node=original.parent if original.kind in atoms else original
        if node and node.kind=='Call' and original.kind in atoms:return (node,)
        contexts=[]
        while node and node.kind in operations:
            contexts.append(node)
            parent=node.parent
            if parent and parent.kind=='Call':
                contexts.append(parent)
                break
            if not parent or parent.kind not in propagate:break
            node=parent
        return contexts

    def restore_expressions(self, messages):
        if self.expression_patterns is None:
            from .hygiene import tokens
            self.expression_patterns=[]
            for generated_file,mapping in self.maps.items():
                for a,b,source_file,start,end in sorted(mapping.expansions,key=lambda span:span[1]-span[0],reverse=True):
                    emitted=self.generated[generated_file][a:b]
                    original=self.read(source_file)[start:end].decode('utf-8')
                    text=emitted.decode('utf-8')
                    # Ordinary folded literals/type names need no cleanup.
                    # Replacing e.g. emitted '8' globally would corrupt 'i8'.
                    if not text or text==original or not any(prefix in text for prefix in self.prefixes):continue
                    # Odin pretty-prints expressions with its own whitespace.
                    # Match complete recorded token sequences, keeping literal
                    # contents and identifier boundaries intact.
                    pattern=None
                    if any(prefix in text for prefix in self.prefixes):
                        parts=[re.escape(token.decode('utf-8')) for _,_,token in tokens(emitted)]
                        if parts:pattern=re.compile(r'(?<!\w)'+r'\s*'.join(parts)+r'(?!\w)')
                    self.expression_patterns.append((text,pattern,original))
        for text,pattern,original in self.expression_patterns:
            messages=[message.replace(text,original) if text in message else
                      pattern.sub(lambda _:original,message) if pattern else message for message in messages]
        return messages

    def membership_context(self, origin, messages):
        if not messages or not messages[0].startswith(('Cannot convert ', 'Cannot assign ')): return None
        source=self.sources.get(origin.file)
        if source is None:return None
        for node in source.nodes:
            if node.kind!='Compound' or not source.is_array(node):continue
            needle=source.membership_needle(node)
            if needle is None:continue
            # Only conversions of list elements themselves use membership's
            # context. Errors inside calls/explicit typed values have their own.
            if not any(element.start==origin.start for element in node.group('elems')):continue
            target=re.search(r" to '(.+?)' from ",messages[0])
            typed=f" (type '{target[1]}')" if target and not source.resolver.membership_untyped(needle) else ''
            message=f"In this membership test, each list element must be comparable with '{needle.text}'{typed}."
            note={'pos':self.locate(Origin(str(source.path),needle.start,needle.end)),
                  'message':'The value being tested for membership is here.'}
            return message,note
        return None

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

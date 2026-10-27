"""Translate structured backend diagnostics and render one Thor diagnostic format."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .sourcemap import Origin, position


class Diagnostics:
    def __init__(self, sources=None, maps=None, generated=None, overlays=None):
        self.sources = {str(p): s for p, s in (sources or {}).items()}
        self.raw = {str(p): s.raw for p, s in (sources or {}).items()}
        self.prefixes = [s.prefix for s in (sources or {}).values()]
        self.error_messages = {name: message for s in (sources or {}).values() for name, message in s.error_messages.items()}
        self.namespace_aliases = {alias: name for s in (sources or {}).values() for name, alias in s.hidden_namespaces.items()}
        self.guard_messages = {(str(path), start, end): message for path, s in (sources or {}).items()
                               for (start, end), message in s.guard_messages.items()}
        self.raw.update({str(p): b for p, b in (overlays or {}).items()})
        self.maps = maps or {}
        self.generated = generated or {}
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
            if origin and origin.kind=='power-type':
                mismatch=next((re.search(r"Mismatched types in (?:binary expression .* : |ternary if expression, )(.+)",m) for m in msgs if 'Mismatched types in ' in m),None)
                if mismatch:
                    msgs=["Power ('**') requires matching operand types: "+mismatch[1]+'. Untyped numeric constants may be coerced.']
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
                # Generated implementation names never belong in user diagnostics.
                for alias, name in self.namespace_aliases.items():
                    msgs = [m.replace(alias, name) for m in msgs]
                for prefix in self.prefixes:
                    msgs = [re.sub(re.escape(prefix) + r"[A-Za-z0-9_]+", "Thor expression", m) for m in msgs]
            # Rewrite locations embedded in overloads, declarations and generic notes.
            for generated_file, pattern in self.reference_patterns:
                msgs = [pattern.sub(lambda m: self.reference(generated_file, *reference_coordinates(m)), text) for text in msgs]
            diagnostic = {"type": error.get("type", "error"), "stage": error.get("stage", "checker" if backend else "thor"),
                          "pos": self.locate(origin) if origin else None, "msgs": msgs}
            diagnostic["_cascade"] = bool(origin and not origin.exact and (
                origin.kind in {'range-type','power-type'} or origin.kind in {"array", "Compound"} and any("type_of" in m for m in msgs)))
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
            if diagnostic["_cascade"] and any(
                not other["_cascade"] and other["type"] == "error" and other["pos"]
                and other["pos"]["file"] == pos["file"]
                and pos["offset"] <= other["pos"]["offset"] < pos["end_offset"]
                for other in result
            ):
                continue
            filtered.append({k: v for k, v in diagnostic.items() if k != "_cascade"})
        return filtered

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
        return "\n".join(output)


def reference_coordinates(match):
    return int(match[1] or match[3]), int(match[2] or match[4]), match[3] is not None


def payload(diagnostics):
    return {"error_count": sum(d["type"] == "error" for d in diagnostics), "errors": diagnostics}

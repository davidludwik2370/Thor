"""Opt-in debug instrumentation, installed only after compile-time evaluation."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys

from .compiler import ThorError, unquote
from .memorymap import compact, encode
from .sourcemap import SourceMap
from .symbolizer import find_llvm


def attributes(declaration):
    return [element for attribute in declaration.group('attributes') for element in attribute.group('elems')]


def has_asan_options(project):
    for source in project.sources.values():
        if source.excluded:
            continue
        for node in source.nodes:
            if node.kind != 'Declaration':
                continue
            if any(name.text == '__asan_default_options' for name in node.group('names')):
                return True
            for element in attributes(node):
                field, value = element.role('field'), element.role('value')
                if field and field.text == 'link_name' and value and value.text.startswith(('"', '`')) \
                        and unquote(value.text) == '__asan_default_options':
                    return True
    return False


@dataclass
class MemoryDebug:
    tracking: str
    address: bool
    extra_flags: list

    @classmethod
    def select(cls, command, flags, tracking="auto", no_sanitize=False, *, platform=None):
        platform = sys.platform if platform is None else platform
        target = next((f.split(":", 1)[1] for f in flags if f.startswith("-target:")), "")
        native = platform == "darwin" and (not target or target.startswith("darwin_"))
        debug = "-debug" in flags
        test = command == "test" or "-build-mode:test" in flags
        mode = next((f.split(":", 1)[1] for f in flags if f.startswith("-build-mode:")), "exe")
        executable = command in {"build", "run", "test"} and mode in {"exe", "test"} and "-no-entry-point" not in flags
        if tracking == "auto":
            tracking = "basic" if native and debug and executable and not test else "off"
        if tracking != "off" and (not native or not executable or test):
            raise ThorError("Automatic allocation tracking currently supports native macOS executables. "
                            "Tests use Odin's per-test tracker; use --track-allocations=off here.")
        if tracking == "trace" and not debug:
            raise ThorError("--track-allocations=trace requires -debug for allocation call stacks")
        extra = []
        explicit_sanitizer = any(f.startswith("-sanitize:") for f in flags)
        automatic = native and debug and executable and not no_sanitize and not explicit_sanitizer
        if automatic:
            extra.append("-sanitize:address")
        address = native and executable and (automatic or "-sanitize:address" in flags)
        if address and not any(f in flags for f in ("-use-single-module", "-use-separate-modules")):
            extra.append("-use-single-module")
        return cls(tracking, address, extra)

    @property
    def enabled(self):
        return self.tracking != "off" or self.address

    def prepare(self, project, root):
        self.root = root
        self.source = None
        self.mains = []
        entries = [s for p, s in project.sources.items() if not s.excluded and
                   (p == project.entry if project.file_entry else p.parent == project.entry)]
        if not entries:
            return
        self.source = entries[0]
        for source in entries:
            for node in source.nodes:
                if node.kind != "Procedure" or not node.parent or node.parent.kind != "Declaration":
                    continue
                names = node.parent.group("names")
                if len(names) != 1 or names[0].text != "main" or any(p.kind == "Procedure" for p in node.ancestors()):
                    continue
                if any(element.text == 'naked' for element in attributes(node.parent)):
                    continue
                body = node.role("body")
                if body and source.raw[body.start:body.start + 1] == b"{":
                    self.mains.append((source, body))
        if self.tracking != "off" and not self.mains:
            # Let Odin diagnose a missing/ineligible entry point as usual.
            self.tracking = "off"
        collection = "__thor_memory"
        while collection in project.collections:
            collection += "_"
        self.collection = collection
        project.memory_flags = [*self.extra_flags, f"-collection:{collection}={root / 'runtime'}"]
        prefix = self.source.prefix + "memory_"
        self.prefix = prefix
        if self.tracking != "off":
            # One stable package state; callbacks remain valid through Odin fini.
            for source, body in self.mains:
                alias = source.prefix + "memory_start"
                package = "debug_memory_trace" if self.tracking == "trace" else "debug_memory"
                source.memory_import = f'\nimport {alias} "{collection}:{package}"\n'
                prologues = getattr(source, 'memory_prologues', {})
                prologues[body] = f"\ncontext.allocator = {alias}.begin({prefix}map)\n"
                source.memory_prologues = prologues
        if self.address:
            for source in project.sources.values():
                if source.excluded or source.path.is_relative_to(root / 'runtime') or getattr(source, 'native_inspected', False):
                    continue
                source.needs_intrinsics = True
                prologues = getattr(source, 'memory_prologues', {})
                for node in source.nodes:
                    if node.kind != 'Procedure':
                        continue
                    if node.parent and node.parent.kind == 'Declaration' and any(
                            element.text in {'no_sanitize_address', 'naked'} for element in attributes(node.parent)):
                        continue
                    body = node.role('body')
                    if body and source.raw[body.start:body.start+1] == b'{':
                        # Odin/LLVM can attribute ASan's entry instructions to
                        # the first named stack local, before parameters have
                        # valid debugger homes. A named marker gets that setup
                        # a synthetic location; its own block keeps it out of
                        # the user's scope. Anonymous temporaries do not fix it.
                        prologues[body] = prologues.get(body, '') + (
                            '\n{\n_debug_entry: u64 = 0\n'
                            f'_ = {source.prefix}meta.volatile_load(&_debug_entry)\n}}\n')
                source.memory_prologues = prologues

    def finish(self, project):
        if self.source is None:
            return
        source, prefix = self.source, self.prefix
        symbolizer = find_llvm() if self.address else ""
        data = compact(project, symbolizer or "")
        memory, runtime = prefix + "support", prefix + "runtime"
        footer = [f'\nimport {memory} "{self.collection}:debug_memory"\n',
                  f'import {runtime} "base:runtime"\n',
                  f'@(private="file")\n{prefix}files := []{memory}.File{{\n']
        for unit in data["units"]:
            footer.append("{" + json.dumps(unit["path"]) + f", []{memory}.Span{{\n")
            for line, column, end, file, row, col, exact, procedure in unit["rows"]:
                footer.append("{%d,%d,%d,%s,%d,%d,%s,%s},\n" %
                    (line, column, end, json.dumps(data["files"][file]), row, col,
                     str(exact).lower(), json.dumps(procedure)))
            footer.append("}},\n")
        footer.append("}\n")
        aliases = {str(output): str(path) for path, output in project.outputs.items()
                   if path.suffix == ".odin" and str(output) != str(path)}
        footer.append(f'@(private="file")\n{prefix}aliases := []{memory}.Alias{{' +
            ",".join("{" + json.dumps(a) + "," + json.dumps(b) + "}" for a, b in sorted(aliases.items())) + "}\n")
        footer.append(f'@(private="package")\n{prefix}map :: proc "contextless" (loc: {runtime}.Source_Code_Location) -> {runtime}.Source_Code_Location {{\n'
                      f'    return {memory}.map_location(loc, {prefix}files, {prefix}aliases)\n}}\n')
        if self.address:
            blob = encode(data)
            footer.append('@(export, link_section="__DATA,__thor_map")\n' +
                          f'{prefix}blob: [{len(blob)}]u8 = {{' + ",".join(map(str, blob)) + "}\n")
            custom_options = has_asan_options(project)
            if not custom_options and symbolizer:
                libc = prefix + "libc"
                options = 'external_symbolizer_path="' + str(self.root / "llvm-symbolizer-thor") + '"'
                footer.append(f'import {libc} "core:c/libc"\n'
                    '@(export, no_sanitize_address, link_name="__asan_default_options")\n'
                    f'{prefix}asan_options :: proc "c" () -> cstring {{\n'
                    f'    if {libc}.getenv("ASAN_SYMBOLIZER_PATH") != nil {{ return "" }}\n'
                    f'    return {json.dumps(options)}\n}}\n')
        path = str(project.outputs[source.path])
        before = project.generated[path]
        code = "".join(footer).encode()
        original = self.mains[0][1].start if self.mains else 0
        old = project.maps[path]
        project.maps[path] = SourceMap([*old.segments,
            [len(before), len(before) + len(code), str(source.path), original, original, "memory-debug", False]],
            old.expansions, old.regions)
        project.generated[path] = before + code
        relative = Path(path).relative_to(project.destination).as_posix()
        project._planned[relative] = before + code
        if project.persist_maps:
            metadata = project.maps[path].json(path, project.sources)
            metadata["generated_sha256"] = hashlib.sha256(before + code).hexdigest()
            project._planned[relative + ".map.json"] = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n"
        project.manifest["memory_debug"] = {"tracking": self.tracking, "address_sanitizer": self.address,
            "source_map_version": 1, "llvm_symbolizer": symbolizer or ""}

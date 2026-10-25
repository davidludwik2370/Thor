"""Build the Odin frontend, mirror source packages, and invoke Odin."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .compiler import Source, ThorError, unquote
from .diagnostics import Diagnostics, payload
from .sourcemap import MappingBuilder
from .semantics import Resolver

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.4.0"


def frontend_binary(odin):
    executable = shutil.which(odin)
    if executable is None:
        raise ThorError(f"Odin compiler not found: {odin}; set THOR_ODIN or use --odin")
    version = subprocess.run([executable, "version"], capture_output=True, text=True, check=True).stdout
    digest = hashlib.sha256((str(Path(executable).resolve()) + version).encode())
    for path in sorted((ROOT / "frontend").rglob("*.odin")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    directory = ROOT / ".thor-cache"
    directory.mkdir(exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    binary = directory / ("frontend-" + digest.hexdigest()[:20] + suffix)
    if not binary.exists():
        # A unique intermediate permits concurrent test/build processes.
        with tempfile.TemporaryDirectory(prefix="build-", dir=directory) as temporary:
            intermediate = Path(temporary) / ("frontend" + suffix)
            result = subprocess.run([executable, "build", str(ROOT / "frontend"), "-o:speed", f"-out:{intermediate}"])
            if result.returncode:
                raise ThorError("could not build Thor's Odin frontend")
            os.replace(intermediate, binary)
    return binary, executable


class Project:
    def __init__(self, entry, frontend, flags, *, test=False, overlays=None):
        self.entry = Path(entry).resolve()
        self.overlays = overlays or {}
        self.file_entry = self.entry.is_file() or self.entry in self.overlays
        self.mapping = MappingBuilder()
        if not self.entry.exists() and self.entry not in self.overlays:
            raise ThorError(f"source does not exist: {self.entry}")
        if self.file_entry and self.entry.suffix not in {".thor", ".odin"}:
            raise ThorError("a source file must have a .thor or .odin extension")
        self.frontend = frontend
        self.flags = flags
        self.test = test
        target = next((f[len("-target:"):] for f in flags if f.startswith("-target:")), "")
        self.target = tuple(target.split("_")[:2]) if "_" in target else ()
        self.default_collections = set()
        self.collections = {}
        for flag in flags:
            if flag.startswith("-collection:"):
                name, separator, value = flag[len("-collection:"):].partition("=")
                if not separator:
                    raise ThorError(f"invalid collection flag: {flag}")
                self.collections[name] = Path(value).resolve()
        self.sources = {}
        self.packages = {}
        self._visited = set()
        self.discover(self.entry)
        if not self.sources:
            raise ThorError(f"no .thor or .odin sources found: {self.entry}")
        roots = [p.parent for p in self.sources] + list(self.collections.values())
        self.common = Path(os.path.commonpath(roots))
        for source in self.sources.values():
            source.project = self.packages
            source.collections = self.collections
        self.resolver = Resolver(list(self.sources.values()))
        self.resolver.prepare_contexts()
        self.resolver.prepare_storage_checks()

    def discover(self, entry):
        entry = entry.resolve()
        if any((directory / "thor-manifest.json").is_file() for directory in [entry, *entry.parents] if directory.is_dir()):
            raise ThorError(f"generated Odin is not a Thor input; use the original source: {entry}")
        key = (entry, entry.is_file())
        if key in self._visited:
            return
        self._visited.add(key)
        paths = [entry] if entry.is_file() or entry in self.overlays else sorted(
            {p for p in entry.iterdir() if p.is_file() and p.suffix in {".thor", ".odin"}} |
            {p for p in self.overlays if p.parent == entry and p.suffix in {".thor", ".odin"}})
        for path in paths:
            if path in self.sources:
                continue
            source = Source(path, self.frontend, thor=path.suffix == ".thor", package=entry.is_dir(),
                            test=self.test, project_name=self.entry.stem, mapping=self.mapping,
                            raw=self.overlays.get(path), target=self.target)
            self.sources[path] = source
            self.packages.setdefault(path.parent, []).append(source)
        for path in paths:
            source = self.sources[path]
            for imported in source.imports.values():
                if ":" in imported:
                    name, relative = imported.split(":", 1)
                    if name in {"core", "base", "vendor"} and name not in self.collections:
                        continue
                    if name == "thor" and name not in self.collections:
                        self.collections[name] = ROOT / "runtime"
                        self.default_collections.add(name)
                    base = self.collections.get(name)
                    if base is None:
                        source.error(f"unknown library collection {name!r}", 0, 0)
                    dependency = (base / relative).resolve()
                else:
                    dependency = (path.parent / imported).resolve()
                if not dependency.is_dir():
                    node = next(n for n in source.nodes if n.kind == "Import" and imported == unquote(n.data["op"]))
                    source.error(f"imported package does not exist: {dependency}", node.start, node.end)
                self.discover(dependency)

    def emit(self, destination):
        destination = Path(destination).resolve()
        if any(destination == path.parent or destination in path.parents for path in self.sources):
            raise ThorError("output directory must not contain or replace source files")
        manifest_path = destination / "thor-manifest.json"
        old_manifest = {}
        if manifest_path.exists():
            try:
                old_manifest = json.loads(manifest_path.read_text())
            except ValueError as exc:
                raise ThorError(f"invalid generated-output manifest: {manifest_path}") from exc
        elif destination.exists() and any(destination.iterdir()):
            raise ThorError(f"output directory is not empty and is not managed by Thor: {destination}")
        generated = {}
        self.outputs = {}
        self.maps = {}
        self.generated = {}
        for path, source in self.sources.items():
            relative = path.relative_to(self.common).with_suffix(".odin")
            if relative.as_posix() in generated:
                raise ThorError(f"generated filename collision: {relative}")
            generated[relative.as_posix()] = source.transpile()
            self.outputs[path] = destination / relative
            self.maps[str(destination / relative)] = source.source_map
            self.generated[str(destination / relative)] = generated[relative.as_posix()].encode()
            # Each sidecar is self-contained, including original source snapshots.
            generated[relative.as_posix() + ".map.json"] = json.dumps(
                source.source_map.json(destination / relative, self.sources), ensure_ascii=False, separators=(",", ":")) + "\n"
        # Do all parsing/lowering before updating any generated source.
        destination.mkdir(parents=True, exist_ok=True)
        for collection in self.collections.values():
            (destination / collection.relative_to(self.common)).mkdir(parents=True, exist_ok=True)
        for relative, code in generated.items():
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(code.encode("utf-8"))
        # Delete only files explicitly owned by the previous manifest.
        for relative in set(old_manifest.get("generated", [])) - set(generated):
            obsolete = (destination / relative).resolve()
            if destination in obsolete.parents and obsolete.is_file():
                obsolete.unlink()
        self.destination = destination
        manifest = {"thor_version": VERSION, "entry": str(self.entry), "source_map_version": 1,
                    "generated": sorted(generated), "odin_flags": self.odin_flags()}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        return self.outputs[self.entry] if self.file_entry else destination / self.entry.relative_to(self.common)

    def odin_flags(self):
        result = []
        for flag in self.flags:
            if flag.startswith("-collection:"):
                name = flag[len("-collection:"):].split("=", 1)[0]
                mapped = self.destination / self.collections[name].relative_to(self.common)
                result.append(f"-collection:{name}={mapped}")
            else:
                result.append(flag)
        for name in self.default_collections:
            result.append(f"-collection:{name}={self.destination / self.collections[name].relative_to(self.common)}")
        for source in self.sources.values():
            for name, directory in source.asset_collections.items():
                result.append(f"-collection:{name}={directory}")
        return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    runtime_args = []
    if "--" in argv:
        boundary = argv.index("--")
        runtime_args, argv = argv[boundary + 1:], argv[:boundary]
    parser = argparse.ArgumentParser(prog="thor", description="Thor T01-T17: compile additive Odin syntax to Odin.")
    parser.add_argument("--version", action="version", version=f"Thor {VERSION}")
    parser.add_argument("command", choices=["transpile", "check", "build", "run", "test"])
    parser.add_argument("source", help="a .thor/.odin file or package directory")
    parser.add_argument("--output-dir", type=Path, help="directory for generated Odin (default: .thor-build/<source>)")
    parser.add_argument("--odin", default=os.environ.get("THOR_ODIN", "odin"), help="Odin compiler executable")
    parser.add_argument("--diagnostic-format", choices=["text", "json"], default="text")
    parser.add_argument("--overlay", type=Path, help="editor overlay JSON: {\"Replace\": {original: replacement_file}}")
    parser.add_argument("--raw-diagnostics", action="store_true", help="show unmodified Odin diagnostics for transpiler debugging")
    options, flags = parser.parse_known_args(argv)
    if any(not flag.startswith("-") for flag in flags):
        parser.error("unexpected argument; runtime arguments must follow --")
    if runtime_args and options.command not in {"run", "test"}:
        parser.error("arguments after -- are only supported by run and test")
    diagnostics = Diagnostics()
    json_output = options.diagnostic_format == "json" or "-json-errors" in flags
    flags = [f for f in flags if f != "-json-errors"]
    reported_errors = False

    def report(errors):
        nonlocal reported_errors
        normalized, seen = [], set()
        for error in errors:
            if error["type"] == "warning" and "-ignore-warnings" in flags:
                continue
            error = {**error, "msgs": [m.removeprefix("Syntax Warning: ") for m in error["msgs"]]}
            if error["type"] == "warning" and "-warnings-as-errors" in flags:
                error["type"] = "error"
            pos = error.get("pos") or {}
            key = (error["type"], pos.get("file"), pos.get("offset"), tuple(error["msgs"]))
            if key not in seen:
                normalized.append(error)
                seen.add(key)
        errors = normalized
        reported_errors |= any(d["type"] == "error" for d in errors)
        if json_output:
            print(json.dumps(payload(errors), ensure_ascii=False), file=sys.stderr)
        else:
            for error in errors:
                print(diagnostics.text(error, terse="-terse-errors" in flags,
                                       unix="-error-pos-style:unix" in flags), file=sys.stderr)
    try:
        overlays = {}
        if options.overlay:
            for original, replacement in json.loads(options.overlay.read_text())["Replace"].items():
                overlays[Path(original).resolve()] = Path(replacement).read_bytes()
        diagnostics = Diagnostics(overlays=overlays)
        frontend, odin = frontend_binary(options.odin)
        test_mode = options.command == "test" or "-build-mode:test" in flags
        project = Project(options.source, frontend, flags, test=test_mode, overlays=overlays)
        destination = options.output_dir or Path.cwd() / ".thor-build" / project.entry.stem
        target = project.emit(destination)
        diagnostics = Diagnostics(project.sources, project.maps, project.generated)
        frontend_diagnostics = diagnostics.translate([d for s in project.sources.values() for d in s.diagnostics], backend=False)
        if options.command == "transpile":
            if frontend_diagnostics or json_output:
                report(frontend_diagnostics)
            print(target)
            return 1 if reported_errors else 0
        # Keep the running program's streams interactive and entirely separate
        # from structured compiler output (including test runner output).
        native_command = "build" if options.command in {"run", "test"} else options.command
        command = [odin, native_command, str(target)]
        if project.file_entry:
            command.append("-file")
        command.extend(project.odin_flags())
        executable = Path(next((f[len("-out:"):] for f in flags if f.startswith("-out:")),
                               str(project.destination / ('program.exe' if os.name == 'nt' else 'program')))).resolve()
        if options.command in {"build", "run", "test"} and not any(f.startswith("-out:") for f in flags):
            command.append(f"-out:{executable}")
        if options.command == "test":
            command.append("-build-mode:test")
        if options.raw_diagnostics:
            if frontend_diagnostics:
                report(frontend_diagnostics)
            result = subprocess.run(command)
        else:
            command.append("-json-errors")
            result = subprocess.run(command, stderr=subprocess.PIPE)
            if result.stderr:
                try:
                    native = json.loads(result.stderr)
                except ValueError:
                    # Linker/toolchain/invalid-option failures can be plain text.
                    report([*frontend_diagnostics, {"type": "error" if result.returncode else "warning", "stage": "toolchain",
                             "pos": None, "msgs": [result.stderr.decode(errors="replace").strip()]}])
                else:
                    report([*frontend_diagnostics, *diagnostics.translate(native.get("errors", []))])
            elif json_output or frontend_diagnostics:
                report(frontend_diagnostics)
        if result.returncode or reported_errors or options.command not in {"run", "test"}:
            return result.returncode or (1 if reported_errors else 0)
        try:
            return subprocess.run([str(executable), *runtime_args]).returncode
        finally:
            if "-keep-executable" not in flags:
                executable.unlink(missing_ok=True)
    except (ThorError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, ThorError) and exc.diagnostics:
            report(diagnostics.translate(exc.diagnostics, backend=False))
        else:
            report([{"type": "error", "stage": "thor", "pos": None, "msgs": [str(exc)]}])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

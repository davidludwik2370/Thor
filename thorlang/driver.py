"""Build the Odin frontend, mirror source packages, and invoke Odin."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from .compiler import Source, ThorError, unquote
from .diagnostics import Diagnostics, payload, backend_error_flags
from .sourcemap import MappingBuilder
from .semantics import Resolver

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.41.6"


def backend_failure(status):
    number = -status
    try:
        name = signal.Signals(number).name
    except ValueError:
        name = str(number)
    return {"type": "error", "stage": "toolchain", "pos": None,
            "msgs": [f"Odin compiler terminated by {name} (signal {number}). "
                     "This is a backend compiler failure; no source location was provided."]}


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
    def __init__(self, entry, frontend, flags, *, test=False, overlays=None, cache_frontend=True,
                 cache_directory=None,use_shared=True,debug_maps=None):
        self.entry = Path(entry).resolve()
        self.overlays = overlays or {}
        self.cache_frontend = cache_frontend
        self.cache_directory=Path(cache_directory) if cache_directory is not None else (self.entry.parent if self.entry.is_file() or self.entry in self.overlays else self.entry)/'.thor-cache'
        self.use_shared=use_shared
        self.file_entry = self.entry.is_file() or self.entry in self.overlays
        self.mapping = MappingBuilder(debug=('-debug' in flags if debug_maps is None else debug_maps))
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
        self.support_collection='__thor_runtime'
        while self.support_collection in self.collections:self.support_collection+='_'
        self.collections[self.support_collection]=ROOT/'runtime'
        self.default_collections.add(self.support_collection)
        self.sources = {}
        self.packages = {}
        self._visited = set()
        self.discover(self.entry)
        if not self.sources:
            raise ThorError(f"no .thor or .odin sources found: {self.entry}")
        roots = [self.entry.parent if self.file_entry else self.entry]
        roots += [p.parent for p in self.sources if not (use_shared and p.is_relative_to(ROOT/'runtime'))]
        roots += [p for name,p in self.collections.items() if not(use_shared and name in self.default_collections)]
        self.common = Path(os.path.commonpath(roots))
        for source in self.sources.values():
            source.project = self.packages
            source.collections = dict(self.collections)
            if source.path.is_relative_to(ROOT/'runtime'):source.collections['thor']=ROOT/'runtime'
            source.support_collection=self.support_collection
            source.compiler_flags=self.flags
        from .imports import prepare
        prepare(self)
        self.resolver = Resolver(list(self.sources.values()))
        from .defaults import DefaultCallbacks
        for source in self.sources.values():
            source.defaults = DefaultCallbacks(source)
        with self.resolver.cached_queries():self.resolver.prepare_contexts()
        self.resolver.prepare_storage_checks()
        from .purity import Purity
        self.purity=Purity(self)
        from .sliceaccess import SliceAccess
        self.slice_access=SliceAccess(self)

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
        if not self.test and entry.is_dir():
            paths=[path for path in paths if not path.name.endswith(('_test.thor','_test.odin'))]
        # Compiler support has its own native test command. Importing its test
        # files into a program can add core-library dependencies to bootstrap
        # support used by checked versions of those same libraries.
        if entry.parent==ROOT/'runtime' and entry.name in {'basic','big','pure','eval','format','numeric','sequence','sets','optional'} and entry!=self.entry:
            paths=[path for path in paths if not path.name.endswith('_test.odin')]
        for path in paths:
            if path in self.sources:
                continue
            source = Source(path, self.frontend, thor=path.suffix == ".thor", package=entry.is_dir(),
                            test=self.test, project_name=(self.entry.parent if self.file_entry else self.entry).name, mapping=self.mapping,
                            raw=self.overlays.get(path), target=self.target,cache=self.cache_frontend,cache_dir=self.cache_directory)
            for namespace,imported in list(source.imports.items()):
                if namespace.startswith(source.prefix) and imported.startswith('thor:'):
                    source.imports[namespace]=self.support_collection+imported[len('thor'):]
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
                    base = ROOT/'runtime' if name=='thor' and path.is_relative_to(ROOT/'runtime') else self.collections.get(name)
                    if base is None:
                        source.error(f"unknown library collection {name!r}", 0, 0)
                    dependency = base / relative
                else:
                    dependency = path.parent / imported
                dependency = dependency.resolve()
                if not dependency.is_dir():
                    node = next((n for n in source.nodes if n.kind == "Import" and imported == unquote(n.data["op"])),None)
                    source.error(f"imported package does not exist: {dependency}", node.start if node else 0, node.end if node else 0)
                self.discover(dependency)

    def emit(self, destination, *, persist_maps=None, materialize=True):
        self.slice_access.prepare()
        if self.purity.eval_mode:
            for proc in self.purity.active:self.purity.analyze(proc)
            self.purity.borrowing.finish(self.purity.active)
        self.purity.calls.prepare()
        self.persist_maps='-debug' in self.flags if persist_maps is None else persist_maps
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
        self.shared_maps=[]
        self.backend_collections={name:destination/path.relative_to(self.common) for name,path in self.collections.items()
                                  if name not in self.default_collections}
        self._old_manifest=old_manifest
        self._planned=generated
        # Settle logical numeric constant types before any value-copy lowering,
        # including forward declarations and selected compile-time switch arms.
        for source in self.sources.values():
            if source.big.enabled:
                for declaration in source.nodes:
                    if declaration.kind=='Declaration' and not declaration.data.get('mutable',True):
                        annotation=declaration.role('type')
                        kind=source.resolver.type_node(annotation).kind if annotation else None
                        hint=source.big.type_hint(kind) if kind in {'big_integer','big_rational'} else None
                        for value in declaration.group('values'):source.big.constants.render(value,hint)
        from .shared import Runtime
        runtime=Runtime(self,ROOT) if self.use_shared and not self.purity.eval_mode else None
        context_sources={declaration.source for declaration,_ in self.resolver.wrappers}
        runtime_base=ROOT/'runtime'
        if runtime:
            runtime_base=runtime.prepare(with_basic=any(runtime.eligible(source) and not
                (path==self.entry if self.file_entry else path.parent==self.entry) for path,source in self.sources.items()))
        local_runtime=set()
        adapted={path.parent for path,source in self.sources.items() if runtime and runtime.bundled(source)
                 and not (path==self.entry if self.file_entry else path.parent==self.entry)
                 and (not runtime.native(source) and not runtime.eligible(source) or path in self.overlays
                      or source in context_sources or any(d.source is source for d in self.purity.calls.declarations))}
        regular=[]
        for path, source in self.sources.items():
            entry_source=path==self.entry if self.file_entry else path.parent==self.entry
            checked=any(d.source is source for d in self.purity.calls.declarations)
            if runtime and runtime.bundled(source) and not entry_source:
                if path.parent not in adapted and runtime.eligible(source):
                    runtime.attach(source,self)
                    runtime_base=runtime.directory
                    continue
                if path.parent not in adapted and runtime.native(source) and not checked and path not in self.overlays:
                    self.outputs[path]=runtime_base/path.relative_to(ROOT/'runtime')
                    continue
                local_runtime.add(path.parent.name)
                relative=Path('.thor-runtime')/path.relative_to(ROOT/'runtime')
            else:
                relative = (Path(self.pure_sdk_subdir)/path.relative_to(self.pure_sdk_root)
                        if getattr(source,'native_inspected',False) and getattr(self,'pure_sdk_collections',None)
                        else self.generated_relative(destination,path)).with_suffix(".odin")
            relative=relative.with_suffix('.odin')
            if relative.as_posix() in generated:
                raise ThorError(f"generated filename collision: {relative}")
            self.outputs[path]=destination/relative
            # Reserve names before lowering, including .thor/.odin collisions.
            generated[relative.as_posix()]=''
            regular.append((path,source,relative))
        if 'thor' in self.default_collections:
            if runtime and local_runtime:
                for package in (ROOT/'runtime').iterdir():
                    if package.is_dir() and package.name not in local_runtime:
                        generated['.thor-runtime/'+package.name]=runtime_base/package.name
                runtime_base=destination/'.thor-runtime'
            self.backend_collections['thor']=runtime_base if runtime else destination/self.collections['thor'].relative_to(self.common)
        self.runtime_alias=(runtime.directory,ROOT/'runtime') if runtime and runtime.directory else None
        if not runtime and any(path.is_relative_to(ROOT/'runtime') for path in self.sources):
            runtime_base=destination/self.generated_relative(destination,ROOT/'runtime')
        self.backend_collections['thor']=runtime_base
        self.backend_collections[self.support_collection]=runtime_base
        directories={path.parent:output.parent for path,output in self.outputs.items()}
        def import_path(source,path):
            if path.startswith('thor:') and ('thor' in self.default_collections or source.path.is_relative_to(ROOT/'runtime')):return path
            original=source.import_dir(path)
            target=directories.get(original)
            return os.path.relpath(target,self.outputs[source.path].parent).replace(os.sep,'/') if target else path
        for path,source,relative in regular:
            source.backend_import=import_path
            native_support=(path.suffix=='.odin' and path.is_relative_to(ROOT/'runtime') and not source.edits
                and not source.run_values and not any(self.purity.checked(n) for n in source.nodes if n.kind=='Procedure'))
            if native_support and source not in context_sources:
                from .sourcemap import SourceMap
                code=source.raw.decode()
                source.source_map=SourceMap([[0,len(source.raw),str(path),0,len(source.raw),'source',True]])
            else:code=source.transpile(preserve_native=bool(native_support or
                getattr(source,'native_inspected',False) and not self.purity.eval_mode))
            generated[relative.as_posix()]=code
            self.maps[str(destination / relative)] = source.source_map
            self.generated[str(destination / relative)] = code.encode()
            if self.persist_maps:
                map_data=source.source_map.json(destination / relative, self.sources)
                map_data['generated_sha256']=hashlib.sha256(code.encode()).hexdigest()
                generated[relative.as_posix() + ".map.json"] = json.dumps(
                    map_data, ensure_ascii=False, separators=(",", ":")) + "\n"
        from .sdkmirror import extend
        extend(self,destination,generated)
        self.destination = destination
        manifest = {"thor_version": VERSION, "entry": str(self.entry), "source_map_version": 1,
                    "generated": sorted(generated), "odin_flags": self.odin_flags(),
                    "odin_environment": {'ODIN_ROOT':str(self.sdk_backend_root)} if self.sdk_backend_root else {},
                    "debug_roots": getattr(self,"debug_roots",[]), "debug_map_version": 1,
                    "shared_maps":self.shared_maps if self.persist_maps else []}
        self.manifest = manifest
        if materialize:self.write()
        return self.outputs[self.entry] if self.file_entry else directories[self.entry]

    def generated_relative(self,destination,path):
        relative=path.relative_to(self.common)
        return Path(self.common.name)/relative if destination.name!=self.common.name else relative

    def write(self):
        destination=self.destination
        manifest_path=destination/'thor-manifest.json'
        generated=self._planned
        old_manifest=self._old_manifest
        # Do all parsing/lowering before updating any generated source.
        destination.mkdir(parents=True, exist_ok=True)
        if not manifest_path.exists():
            # Mark the tree before its first source-file event reaches editors.
            # Retain planned ownership if an initial emission is interrupted.
            manifest_path.write_text(json.dumps({'generated':sorted(generated)},indent=2)+'\n')
        for collection in self.backend_collections.values():
            if destination==collection or destination in collection.parents:collection.mkdir(parents=True,exist_ok=True)
        for relative, code in generated.items():
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.is_symlink():output.unlink()
            if isinstance(code,Path):
                output.symlink_to(code,target_is_directory=code.is_dir())
            else:output.write_bytes(code.encode("utf-8") if isinstance(code,str) else code)
        # Delete only files explicitly owned by the previous manifest.
        for relative in set(old_manifest.get("generated", [])) - set(generated):
            if Path(relative).is_absolute() or '..' in Path(relative).parts:continue
            original=destination/relative
            parent=original.parent.resolve()
            if parent!=destination and destination not in parent.parents:continue
            if original.is_symlink():
                original.unlink()
                continue
            obsolete = original.resolve()
            if destination in obsolete.parents and obsolete.is_file():
                obsolete.unlink()
        manifest_path.write_text(json.dumps(self.manifest, indent=2) + "\n")

    def odin_flags(self):
        result = []
        for flag in self.flags:
            if flag.startswith("-collection:"):
                name = flag[len("-collection:"):].split("=", 1)[0]
                mapped = self.backend_collections[name]
                result.append(f"-collection:{name}={mapped}")
            else:
                result.append(flag)
        for name in self.default_collections:
            result.append(f"-collection:{name}={self.backend_collections[name]}")
        if 'thor' not in self.collections:result.append(f"-collection:thor={self.backend_collections['thor']}")
        if self.support_collection not in self.default_collections:result.append(f"-collection:{self.support_collection}={self.backend_collections[self.support_collection]}")
        for source in [*self.sources.values(),*getattr(self,'sdk_sources',{}).values()]:
            for name, directory in source.asset_collections.items():
                result.append(f"-collection:{name}={directory}")
        result.extend(getattr(self, 'memory_flags', ()))
        if any(path.parent == ROOT / 'runtime/app' for path in self.sources):
            if any(f.startswith(('-define:THOR_APP_SOURCE_ROOT=', '-define:THOR_APP_LAYOUT=')) for f in result):
                raise ThorError('THOR_APP_SOURCE_ROOT and THOR_APP_LAYOUT are reserved for thor:app.')
            layout = getattr(self, 'publish_layout', '')
            if layout:
                result.append('-define:THOR_APP_LAYOUT=' + layout)
            else:
                root = self.entry.parent if self.file_entry else self.entry
                result.append('-define:THOR_APP_SOURCE_ROOT=' + str(root))
        from .cli import check_scopes
        check_scopes(self, result)
        return result

    def diagnostic_adapter(self):
        aliases={str(self.sdk_backend_root)+os.sep:str(self.pure_sdk_root)+os.sep} if self.sdk_backend_root else {}
        if self.runtime_alias:aliases[str(self.runtime_alias[0])+os.sep]=str(self.runtime_alias[1])+os.sep
        return Diagnostics({**self.sources,**getattr(self,'sdk_sources',{})}, self.maps, self.generated,path_aliases=aliases)


def _main(argv=None):
    from .vet import CHECKS
    argv = list(sys.argv[1:] if argv is None else argv)
    from . import cli
    early = cli.early(argv, VERSION)
    if early is not None:
        return early
    if argv and argv[0] == 'new':
        from .project_template import main as new_project
        return new_project(argv[1:])
    compiler_arguments = argv[:argv.index('--') if '--' in argv else len(argv)]
    if argv and (argv[0] == 'migrate' or '--odin-compatible' in compiler_arguments
                 or argv[0] == 'vet' and '-build-mode:test' in compiler_arguments):
        # Migration and explicit Odin command compatibility use shared Rust
        # tooling, including test-build vet corrections. Ordinary Python
        # lowering remains an independent reference.
        native = ROOT/'rust/target/release/thor-rust'
        if not native.is_file():
            raise ThorError('Migration and Odin-compatible commands require the native tool executable. Build rust/Cargo.toml with cargo build --release')
        return subprocess.run([str(native), *argv]).returncode
    runtime_args = []
    if "--" in argv:
        boundary = argv.index("--")
        runtime_args, argv = argv[boundary + 1:], argv[:boundary]
    parser = argparse.ArgumentParser(prog="thor", add_help=False, description="Thor T01-T23: compile additive Odin syntax to Odin.",
                                     epilog="Create a project with thor new NAME; see thor help new for creation options.")
    parser.add_argument("--version", action="version", version=f"Thor {VERSION}")
    parser.add_argument("command", choices=[c["name"] for c in cli.CATALOG["commands"] if c["source"]])
    parser.add_argument("source", nargs="?", help="a .thor/.odin file or package directory (default: .)")
    parser.add_argument("--output-dir", type=Path, help="directory for generated Odin (default: .thor-build/<source>)")
    parser.add_argument("--odin", default=os.environ.get("THOR_ODIN", "odin"), help="Odin compiler executable")
    parser.add_argument("--diagnostic-format", choices=["text", "json"], default="text")
    parser.add_argument("--overlay", type=Path, help="editor overlay JSON: {\"Replace\": {original: replacement_file}}")
    parser.add_argument("--source-maps", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--raw-diagnostics", action="store_true", help="show unmodified Odin diagnostics for transpiler debugging")
    parser.add_argument('--editor-info',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--vet', nargs='?', const=','.join(CHECKS), metavar='CHECKS',
                        help='enable Thor convention checks (default: immutable,pure,unused,semicolon)')
    corrections = parser.add_mutually_exclusive_group()
    corrections.add_argument('--apply', action='store_true', help='apply verified corrections with thor vet')
    corrections.add_argument('--ask', action='store_true', help='preview and confirm corrections with thor vet')
    parser.add_argument("--run-timeout",type=float,default=0,help="maximum seconds for each native #run execution; 0 means unlimited (default: 0)")
    parser.add_argument('--cache-dir',type=Path,help='cache root (default: .thor-cache beside the entry package)')
    parser.add_argument('--no-cache',action='store_true',help='bypass compile-time result and per-file parser caching')
    parser.add_argument('--clear-cache',action='store_true',help='clear compile-time result and per-file parser entries before compiling')
    parser.add_argument('--cache-report',action='store_true',help='print compile-time evaluation cache counters as JSON on stdout')
    parser.add_argument('--track-allocations', choices=['auto', 'off', 'basic', 'trace'], default='auto',
                        help='allocation tracker (auto: basic for native macOS debug executables)')
    parser.add_argument('--no-sanitize', action='store_true', help='disable automatic debug AddressSanitizer; explicit Odin sanitizer flags still apply')
    packaging = parser.add_argument_group('publish options (thor publish only; defaults to -o:speed)')
    packaging.add_argument('--app-name', help='application/package name (default: executable name)')
    packaging.add_argument('--app-id', help='reverse-domain application identifier')
    packaging.add_argument('--app-version', help='one to three numeric version components (default: 0.1.0)')
    packaging.add_argument('--app-icon', type=Path, help='project-relative PNG or ICO (default: assets/icon.png or assets/icon.ico)')
    packaging.add_argument('--app-type', choices=['gui', 'console'], help='application launch mode (default: gui)')
    packaging.add_argument('--app-resource', action='append', default=[], help='additional project-relative resource file/directory; repeatable')
    packaging.add_argument('--app-library', action='append', default=[], help='additional native library/plugin; repeatable')
    packaging.add_argument('--publish-dir', type=Path, help='package destination (default: current directory, or parent of -out:)')
    argv = ['--vet='+','.join(CHECKS) if argument == '--vet' else argument for argument in argv]
    options, flags = parser.parse_known_args(argv)
    # argparse can leave an optional positional after intervening flags in the
    # remainder. Recover that explicit source before validating Odin flags.
    if options.source is None:
        source_index = next((index for index, flag in enumerate(flags) if not flag.startswith('-')), None)
        options.source = flags.pop(source_index) if source_index is not None else '.'
    if (options.apply or options.ask) and options.command != 'vet':
        parser.error('--apply and --ask are only supported by thor vet')
    if (options.apply or options.ask) and options.overlay:
        parser.error('vet corrections require saved files without --overlay')
    if options.vet is not None:
        from .vet import CHECKS
        if not options.vet or any(check not in CHECKS for check in options.vet.split(',')):
            parser.error('Thor vet checks must be selected from '+','.join(CHECKS))
        if options.command == 'transpile':
            parser.error('--vet requires native checking. Run thor vet.')
    if options.raw_diagnostics and (options.command == 'vet' or options.vet is not None):
        parser.error('Thor vet requires mapped diagnostics without --raw-diagnostics')
    if options.command != 'publish' and (any(getattr(options, name) is not None for name in
            ('app_name', 'app_id', 'app_version', 'app_icon', 'app_type', 'publish_dir'))
            or options.app_resource or options.app_library):
        parser.error('application packaging options are only supported by thor publish')
    if not math.isfinite(options.run_timeout) or options.run_timeout < 0:
        parser.error('--run-timeout requires a finite, nonnegative number of seconds; 0 means unlimited')
    if any(not flag.startswith("-") for flag in flags):
        parser.error("unexpected argument; runtime arguments must follow --")
    if runtime_args and options.command not in {"run", "test"}:
        parser.error("arguments after -- are only supported by run and test")
    diagnostics = Diagnostics()
    project = None
    json_output = options.diagnostic_format == "json" or "-json-errors" in flags
    flags = [f for f in flags if f != "-json-errors"]
    reported_errors = False
    publish_diagnostics = []
    publish_information = []

    def report(errors, information=""):
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
            if options.command == 'publish':
                publish_diagnostics.extend(errors)
                if information: publish_information.append(information)
            else:
                output=payload(errors)
                if information: output["information"] = information
                if options.editor_info and project is not None:output['slice_access']=project.slice_access.editor_info()
                print(json.dumps(output, ensure_ascii=False), file=sys.stderr)
        else:
            if information: print(information, end="" if information.endswith("\n") else "\n", file=sys.stderr)
            for error in errors:
                print(diagnostics.text(error, terse="-terse-errors" in flags,
                                       unix="-error-pos-style:unix" in flags), file=sys.stderr)
    publish_scratch = None
    publisher = None
    try:
        overlays = {}
        if options.overlay:
            for original, replacement in json.loads(options.overlay.read_text())["Replace"].items():
                overlays[Path(original).resolve()] = Path(replacement).read_bytes()
        diagnostics = Diagnostics(overlays=overlays)
        entry = Path(options.source).resolve()
        try:
            cli.normalize(options, flags, entry, overlays)
        except ValueError as exc:
            parser.error(str(exc))
        odin = shutil.which(options.odin)
        if odin is None:
            raise ThorError(f"Odin compiler not found: {options.odin}; set THOR_ODIN or use --odin")
        listing = cli.query(flags, odin)
        if listing is not None: return listing
        entry=cli.resolve_entry(Path(options.source), flags, odin)
        if options.command == 'publish':
            from .publishing import Publisher
            publisher = Publisher.prepare(entry, flags, options)
            if publisher.warnings:
                report([{'type': 'warning', 'stage': 'publish', 'pos': None, 'msgs': [message]}
                        for message in publisher.warnings])
                if reported_errors:
                    return 1
            publisher.output.parent.mkdir(parents=True, exist_ok=True)
            publish_scratch = tempfile.TemporaryDirectory(prefix='.thor-publish-', dir=publisher.output.parent)
        destination = (options.output_dir or Path.cwd() / '.thor-build' / entry.stem).resolve()
        test_mode = options.command == "test" or "-build-mode:test" in flags
        from .evaluation import Evaluator
        from .cache import EvaluationCache
        cache_root=options.cache_dir or (Path(os.environ['THOR_CACHE_DIR']) if 'THOR_CACHE_DIR' in os.environ else None)
        cache_root=cache_root or (entry.parent if entry.is_file() or entry in overlays else entry)/'.thor-cache'
        cache=EvaluationCache(cache_root,enabled=not options.no_cache)
        if options.clear_cache:
            cache.clear()
        try:
            frontend, odin = frontend_binary(options.odin)
            if options.clear_cache:
                from . import parsecache
                parsecache.clear(frontend,cache_root)
            preparing = time.monotonic()
            project = Project(entry,frontend,flags,test=test_mode,overlays=overlays,
                              cache_frontend=not options.no_cache,cache_directory=cache_root)
            cli.validate_native_checks(project, options.command, flags)
            preparation = time.monotonic()-preparing
            if options.command == 'ast':
                print(json.dumps({'version':1,'sources':[{'path':str(source.path),'frontend':source.info} for source in project.sources.values()]}))
                return 0
            lowering = time.monotonic()
            project.slice_access.enabled|=options.editor_info
            project.slice_access.full_contracts=options.editor_info
            if publisher:
                project.publish_layout = publisher.system
            if '-debug' in flags:
                odin_root=Path(subprocess.check_output([odin,'root'],text=True).strip())
                project.debug_roots=[str(ROOT/'runtime'),*[str(odin_root/name) for name in ['base','core','vendor']]]
            Evaluator(project,odin,options.run_timeout,cache).evaluate_all()
            memory = None
            if '-debug' in flags or options.track_allocations not in {'auto', 'off'}:
                from .memory_debug import MemoryDebug
                memory = MemoryDebug.select('build' if publisher else options.command, flags, options.track_allocations, options.no_sanitize)
                if memory.enabled:
                    memory.prepare(project, ROOT)
            target = project.emit(destination,persist_maps=options.command=='transpile' or options.source_maps or '-debug' in flags,
                                  materialize=not (memory and memory.enabled))
            if memory and memory.enabled:
                memory.finish(project)
                project.write()
            diagnostics = project.diagnostic_adapter()
            frontend_diagnostics = diagnostics.translate([d for s in project.sources.values() for d in s.diagnostics], backend=False)
        finally:
            if options.cache_report:print(json.dumps({'thor_cache':cache.stats,'thor_prepared_hits':cache.prepared_hits}),flush=True)
        lowering = time.monotonic()-lowering
        if options.command == "transpile":
            if frontend_diagnostics or json_output:
                report(frontend_diagnostics)
            print(target)
            return 1 if reported_errors else 0
        # Keep the running program's streams interactive and entirely separate
        # from structured compiler output (including test runner output).
        native_command = ('check' if options.command in {'vet','strip-semicolon'} else
                          "build" if options.command in {"run", "test", "publish"} else options.command)
        command = [odin, native_command, str(target)]
        if project.file_entry:
            command.append("-file")
        native_flags = project.odin_flags()
        if options.command == 'strip-semicolon': native_flags = [f for f in native_flags if f != '-keep-temp-files']
        if publisher:
            native_flags = publisher.native_flags(native_flags, Path(publish_scratch.name))
        command.extend(native_flags)
        binary_directory = Path.cwd() if options.command == "build" else project.destination
        binary_name = (binary_directory.name or 'program') if options.command == "build" else 'program'
        if os.name == 'nt':
            binary_name += '.exe'
        explicit_output = next((f[len("-out:"):] for f in flags if f.startswith("-out:")), None)
        implicit_output = binary_directory if any(flag in {'-build-mode:llvm','-build-mode:llvm-ir'} for flag in flags) else binary_directory / binary_name
        executable = Path(explicit_output if explicit_output is not None else implicit_output).resolve()
        if publisher:
            # Use a simple toolchain output name; the final package can safely
            # contain display/executable names Odin rejects in its -out parser.
            executable = Path(publish_scratch.name) / 'compiled' / ('program.exe' if publisher.system == 'windows' else 'program')
            executable.parent.mkdir()
        launcher=(ROOT/'thor').resolve()
        same_launcher=executable==launcher or executable.exists() and launcher.exists() and os.path.samefile(executable,launcher)
        if options.command == 'build' and explicit_output is None and same_launcher:
            raise ThorError('The default output would overwrite the Thor launcher; choose another name with -out:name.')
        if publisher or options.command in {"build", "run", "test"} and explicit_output is None:
            command.append(f"-out:{executable}")
        if options.command == "test":
            command.append("-build-mode:test")
        native_started = time.monotonic()
        if options.raw_diagnostics:
            if frontend_diagnostics:
                report(frontend_diagnostics)
            result = subprocess.run(command,env={**os.environ,'ODIN_ROOT':str(project.sdk_backend_root)} if project.sdk_backend_root else None)
            if result.returncode < 0:
                report([backend_failure(result.returncode)])
        else:
            command.append("-json-errors")
            command.extend(backend_error_flags(command))
            from .backend import run as run_backend
            result = run_backend(project, command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ,'ODIN_ROOT':str(project.sdk_backend_root)} if project.sdk_backend_root else None)
            from .source_tools import remap_text, documentation, remap_exports, remap_document, strip_semicolons
            information = ''
            if any(f in {'-show-timings','-show-more-timings'} for f in flags):
                timings = {'preparation':preparation, 'lowering':lowering, 'native':time.monotonic()-native_started}
                information += 'Thor compilation timings\n'+''.join(f'  {phase:20} {timings[phase]*1000:10.3f} ms\n' for phase in sorted(timings))+f'  {"total":20} {sum(timings.values())*1000:10.3f} ms\n\n'
            if result.stdout and options.command != 'doc': print(remap_text(project,result.stdout.decode(errors='replace')), end='', flush=True)
            invalid_source=any(d['type']=='error' or d['type']=='warning' and '-warnings-as-errors' in flags for d in frontend_diagnostics)
            if not result.returncode and not invalid_source:
                remap_exports(project, flags)
                if options.command == 'strip-semicolon': information += strip_semicolons(project,flags)
                if options.command == 'doc':
                    text = result.stdout.decode(errors='replace')
                    if '-doc-format' in flags:
                        remap_document(project,text)
                        print(text,end='')
                    else: print(documentation(project,text, '-short' in flags),end='')
            if result.stderr and result.returncode >= 0:
                packet = json.loads(result.stderr)
                information += packet.get('information','')
            if result.returncode == 0 and (options.command == 'vet'  or options.vet is not None):
                from .vet import run as vet
                try:
                    native = json.loads(result.stderr) if result.stderr else None
                except ValueError:
                    native = None
                findings = vet(project, options, flags, native.get('errors', []) if native else None)
                if options.command == 'build':
                    findings = [{**finding, 'type':'error'} for finding in findings]
                frontend_diagnostics.extend(findings)
                diagnostics.raw.update(getattr(project, 'vet_updated_sources', {}))
                if native is not None:
                    result.stderr = json.dumps(native).encode()
            if result.returncode < 0:
                from .backend import decode
                packet=decode(result.stderr,False,False)
                located=any(item.get('type')=='error' and (item.get('pos') or {}).get('file')
                            for item in packet.get('errors',[]))
                error = backend_failure(result.returncode)
                if located:
                    error['msgs'][0]=error['msgs'][0].replace('; no source location was provided','')
                    detail=packet.get('information','').strip()
                    native=diagnostics.translate(packet.get('errors',[]))
                else:
                    detail=result.stderr.decode(errors='replace').strip()
                    native=[]
                if detail:error['msgs'].append(detail)
                report([*frontend_diagnostics, *native, error])
            elif result.stderr:
                try:
                    native = json.loads(result.stderr)
                except ValueError:
                    # Linker/toolchain/invalid-option failures can be plain text.
                    report([*frontend_diagnostics, {"type": "error" if result.returncode else "warning", "stage": "toolchain",
                             "pos": None, "msgs": [result.stderr.decode(errors="replace").strip()]}])
                else:
                    report([*frontend_diagnostics, *diagnostics.translate(native.get("errors", []))], information)
            elif json_output or frontend_diagnostics or information:
                report(frontend_diagnostics, information)
        backend_status = 128-result.returncode if result.returncode < 0 else result.returncode
        if publisher and not result.returncode and not reported_errors:
            output = publisher.package(executable, Path(publish_scratch.name))
            print(output)
            return 0
        if not result.returncode and not reported_errors and options.command in {'build','run','test'} and '-debug' in flags and executable.is_file():
            manifest_path=project.destination/'thor-manifest.json'
            manifest=json.loads(manifest_path.read_text())
            manifest['debug_program']={'path':str(executable),'sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}
            manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
        if result.returncode or reported_errors or options.command not in {"run", "test"}:
            return backend_status or (1 if reported_errors else 0)
        try:
            return subprocess.run([str(executable), *runtime_args]).returncode
        finally:
            if "-keep-executable" not in flags:
                executable.unlink(missing_ok=True)
    except (ThorError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, ThorError) and exc.diagnostics:
            report(diagnostics.translate(exc.diagnostics, backend=False))
        else:
            report([{"type": "error", "stage": "publish" if options.command == 'publish' else "thor", "pos": None, "msgs": [str(exc)]}])
        return 1
    finally:
        if publish_scratch is not None:
            publish_scratch.cleanup()
        if json_output and options.command == 'publish':
            packet=payload(publish_diagnostics)
            if publish_information: packet['information']=''.join(publish_information)
            print(json.dumps(packet, ensure_ascii=False), file=sys.stderr)


class CompilationCancelled(BaseException):pass


def main(argv=None):
    # TLS terminates superseded checks. Unwind subprocess.run and temporary
    # directories so the current native helper is stopped and scratch removed.
    def cancel(signum,frame):raise CompilationCancelled()
    previous=signal.signal(signal.SIGTERM,cancel)
    try:return _main(argv)
    except CompilationCancelled:return 128+signal.SIGTERM
    finally:signal.signal(signal.SIGTERM,previous)


if __name__ == "__main__":
    raise SystemExit(main())

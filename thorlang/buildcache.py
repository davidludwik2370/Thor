"""Reuse complete, validated transpilation artifacts before loading the frontend.

The native compiler still runs on every invocation. Cached state contains data,
never Python objects or executable helpers. Changed or unavailable inputs and
damaged artifacts fall back to the ordinary compiler path.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from types import SimpleNamespace

from .cache import canonical, file_digest

SCHEMA = 1
MAX_RECORD = 64 * 1024 * 1024


def signature(path):
    try:
        stat = Path(path).stat()
    except FileNotFoundError:
        return None
    return [stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns]


def members(path, suffixes):
    with os.scandir(path) as entries:
        return sorted(entry.name for entry in entries
                      if Path(entry.name).suffix in suffixes and entry.is_file())


class Inputs:
    def __init__(self, data=None):
        self.data = data or dict(files={}, directories={}, members={}, bindings={})
        self.usable = True

    def file(self, path, raw=None, *, content=True):
        path = str(Path(path))
        before = signature(path)
        digest = file_digest(Path(path)) if content and before is not None else None
        after = signature(path)
        value = dict(stat=before, sha256=digest)
        if before != after or raw is not None and hashlib.sha256(raw).hexdigest() != digest:
            self.usable = False
        previous = self.data['files'].get(path)
        if previous is not None and previous['stat'] != before:
            self.usable = False
        if previous is None or content:
            self.data['files'][path] = value

    def binding(self, path):
        path = str(Path(path).absolute())
        resolved = str(Path(path).resolve())
        previous = self.data['bindings'].setdefault(path, resolved)
        if previous != resolved:
            self.usable = False

    def package(self, directory, paths, suffixes=('.odin', '.thor')):
        value = dict(suffixes=list(suffixes), names=sorted(Path(p).name for p in paths))
        previous = self.data['members'].setdefault(str(directory), value)
        if previous != value:
            self.usable = False

    def tree(self, root):
        root = Path(root)
        if not root.exists():
            self.file(root, content=False)
            return
        for directory, directories, files in os.walk(root):
            directories[:] = sorted(name for name in directories
                                    if name not in {'__pycache__', '.git', '.thor-cache', '.thor-build'})
            self.data['directories'][directory] = signature(directory)
            for name in files:
                if Path(name).suffix in {'.odin', '.py'}:
                    self.file(Path(directory) / name, content=False)

    def source(self, source):
        from .compiler import unquote
        self.file(source.path, source.raw)
        for node in source.nodes:
            if node.kind != 'Call':
                continue
            callee = node.role('callee')
            if callee is None or callee.text not in {'#load', '#load_hash', '#load_directory', '#exists'}:
                continue
            args = node.group('args')
            if not args or args[0].kind != 'Literal':
                self.usable = False
                continue
            name = unquote(args[0].text)
            path = source.import_dir(name) if ':' in name else source.path.parent / name
            if path is None:
                self.usable = False
                continue
            self.binding(path)
            path = path.resolve()
            if path.is_dir():
                self.data['directories'][str(path)] = signature(path)
                if callee.text == '#load_directory':
                    for child in sorted(path.iterdir()):
                        if child.is_file():
                            self.file(child)
            else:
                self.file(path)

    def valid(self):
        if not self.usable:
            return False
        try:
            for path, value in self.data['files'].items():
                if signature(path) != value['stat']:
                    return False
                if value['sha256'] is not None and file_digest(Path(path)) != value['sha256']:
                    return False
            for path, expected in self.data['directories'].items():
                if signature(path) != expected:
                    return False
            for path, expected in self.data['members'].items():
                if members(path, expected['suffixes']) != expected['names']:
                    return False
            for path, expected in self.data['bindings'].items():
                if str(Path(path).resolve()) != expected:
                    return False
        except (OSError, ValueError, TypeError, KeyError):
            return False
        return True


class LazyDiagnostics:
    def __init__(self, project):
        self.project = project
        self.loaded = None

    def __getattr__(self, name):
        if self.loaded is None:
            from .compiler import Node
            from .diagnostics import Diagnostics
            from .sourcemap import SourceMap
            sources = {}
            for path, data in self.project.record['sources'].items():
                source = SimpleNamespace(raw=data['raw'].encode('utf-8'), prefix=data['prefix'],
                    error_messages=data['errors'], hidden_namespaces=data['namespaces'],
                    guard_messages={(a,b):message for a,b,message in data['guards']})
                source.nodes = [Node(source, i, node) for i,node in enumerate(data['nodes'])]
                sources[Path(path)] = source
            maps, generated = {}, {}
            for path in self.project.record['maps']:
                data = json.loads(Path(path + '.map.json').read_bytes())
                maps[path] = SourceMap(data['segments'], data.get('expansions', []), data.get('regions', []))
                generated[path] = Path(path).read_bytes()
            self.loaded = Diagnostics(sources, maps, generated)
        return getattr(self.loaded, name)


class CachedProject:
    def __init__(self, record):
        self.record = record
        self.entry = Path(record['entry'])
        self.destination = Path(record['destination'])
        self.file_entry = record['file_entry']
        self.frontend_diagnostics = record['frontend_diagnostics']

    def odin_flags(self):
        return self.record['odin_flags']

    def diagnostic_adapter(self):
        return LazyDiagnostics(self)


class TranspilationCache:
    def __init__(self, directory, request, *, enabled=True):
        self.directory = Path(directory).resolve() / f'transpile-v{SCHEMA}'
        self.key = hashlib.sha256(canonical(request)).hexdigest()
        self.path = self.directory / (self.key + '.json')
        self.enabled = enabled
        self.inputs = Inputs()
        self.stats = dict(hits=0, misses=0, writes=0, bypasses=0)

    @staticmethod
    def request(entry, destination, flags, test, odin, timeout):
        # Shell bookkeeping changes between equivalent invocations. The actual
        # cwd is recorded explicitly; retain other environment changes.
        environment = {k:v for k,v in os.environ.items() if k not in {'_', 'SHLVL', 'PWD', 'OLDPWD'}}
        return dict(entry=str(entry), destination=str(destination), flags=flags, test=test,
                    odin=odin, resolved_odin=str(Path(odin).resolve()), cwd=str(Path.cwd()),
                    python=[sys.executable, sys.version], platform=[sys.platform, platform.machine()],
                    environment=hashlib.sha256(canonical(environment)).hexdigest(), timeout=timeout)

    def clear(self):
        try:
            for path in self.directory.glob('*.json'):
                if len(path.stem) == 64 and all(c in '0123456789abcdef' for c in path.stem) and not path.is_symlink():
                    path.unlink(missing_ok=True)
        except OSError:
            pass

    def load(self):
        if not self.enabled:
            self.stats['bypasses'] += 1
            return None
        try:
            if self.path.stat().st_size > MAX_RECORD:
                raise ValueError('oversize transpilation record')
            packet = json.loads(self.path.read_bytes())
            record = packet['record']
            if packet['schema'] != SCHEMA or packet['key'] != self.key:
                raise ValueError('transpilation schema/key')
            if hashlib.sha256(canonical(record)).hexdigest() != packet['checksum']:
                raise ValueError('transpilation checksum')
            if not Inputs(record['inputs']).valid() or not Inputs(record['artifacts']).valid():
                raise ValueError('changed transpilation dependency')
            manifest = json.loads((Path(record['destination'])/'thor-manifest.json').read_bytes())
            manifest.pop('debug_program', None)
            if manifest != record['manifest']:
                raise ValueError('changed generated-output manifest')
            result = CachedProject(record)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            self.stats['misses'] += 1
            return None
        self.stats['hits'] += 1
        return result

    def begin(self, root, odin, sdk):
        if not self.enabled:
            return
        try:
            self.inputs.file(Path(odin).resolve(), content=False)
            self.inputs.file(Path(sys.executable).resolve(), content=False)
            for tree in [root/'thorlang', root/'frontend', root/'runtime',
                         sdk/'base', sdk/'core', sdk/'vendor']:
                self.inputs.tree(tree)
        except OSError:
            self.inputs.usable = False

    def save(self, project, target, frontend_diagnostics):
        if not self.enabled:
            return
        temporary = None
        try:
            if not self.inputs.valid() or any(error['type']=='error' for error in frontend_diagnostics):
                self.stats['bypasses'] += 1
                return
            artifacts = Inputs()
            for name,mapping in project.maps.items():
                artifacts.file(name,project.generated[name])
                raw = Path(name+'.map.json').read_bytes()
                expected = mapping.json(name,project.sources)
                expected['generated_sha256'] = hashlib.sha256(project.generated[name]).hexdigest()
                if json.loads(raw) != expected:
                    artifacts.usable = False
                artifacts.file(name+'.map.json',raw)
            manifest = dict(project.manifest)
            current = json.loads((project.destination/'thor-manifest.json').read_bytes())
            current.pop('debug_program',None)
            if not artifacts.valid() or current != manifest:
                self.stats['bypasses'] += 1
                return
            sources = {str(path):dict(raw=source.raw.decode('utf-8'), prefix=source.prefix,
                errors=source.error_messages, namespaces=source.hidden_namespaces,
                guards=[[a,b,message] for (a,b),message in source.guard_messages.items()],
                nodes=[dict(kind=node.kind,start=node.start,end=node.end,op=node.data.get('op',''))
                       for node in source.nodes if node.kind in {'Call','Binary'}])
                for path,source in project.sources.items()}
            record = dict(inputs=self.inputs.data, artifacts=artifacts.data, manifest=manifest,
                entry=str(project.entry), destination=str(project.destination), target=str(target),
                file_entry=project.file_entry, odin_flags=project.odin_flags(), sources=sources,
                frontend_diagnostics=frontend_diagnostics, maps=list(project.maps))
            packet = canonical(dict(schema=SCHEMA, key=self.key, record=record,
                                    checksum=hashlib.sha256(canonical(record)).hexdigest()))
            if len(packet) > MAX_RECORD:
                self.stats['bypasses'] += 1
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix='.entry-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(packet)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.stats['writes'] += 1
        except (OSError, ValueError, TypeError, KeyError):
            self.stats['bypasses'] += 1
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

"""File, package and resource dependencies for compile-time result validation."""
import hashlib
import os
from pathlib import Path

from .cache import file_digest


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
                      if ('*' in suffixes or Path(entry.name).suffix in suffixes) and entry.is_file())


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

    def source(self, source, *, source_file=True):
        from .compiler import unquote
        if source_file:self.file(source.path, source.raw)
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
                    children = [child for child in sorted(path.iterdir()) if child.is_file()]
                    self.package(path, children, ('*',))
                    for child in children:
                        self.file(child)
            else:
                self.file(path)

    def valid(self):
        if not self.usable:
            return False
        try:
            for path, value in self.data['files'].items():
                # Editors and other compiler invocations can rewrite identical
                # bytes. Content-tracked inputs remain valid then.
                if signature(path) != value['stat'] and value['sha256'] is None:
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

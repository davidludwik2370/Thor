"""Atomic contents-only cache keyed by emitted dependencies and configuration.

Keys are deliberately conservative: equivalent expressions can miss, whereas
changes to transitive code, resources, toolchain or configuration cannot hit.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
from . import values
from .compiler import unquote

SCHEMA = 1
MAX_PACKET = 16 * 1024 * 1024

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()

def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()

class EvaluationCache:
    def __init__(self, directory, *, enabled=True):
        self.directory = Path(directory).resolve() / f'eval-v{SCHEMA}'
        self.enabled = enabled
        self.stats = dict(hits=0, misses=0, writes=0, corrupt=0, bypasses=0)
        self.identities = {}
        self.toolchain_stamp = None
        self.prepared_hits = 0

    def clear(self):
        """Preserve unrelated files, directories and symlinks."""
        try:
            for path in self.directory.iterdir():
                if re.fullmatch(r'[0-9a-f]{64}\.json', path.name) and path.is_file() and not path.is_symlink():
                    path.unlink(missing_ok=True)
        except OSError: pass
        for name in ['prepared-v1', 'identity-v1', 'scalar-v1']:
            try:
                for path in (self.directory.parent/name).glob('*.json'):
                    if re.fullmatch(r'[0-9a-f]{64}\.json',path.name) and not path.is_symlink():path.unlink(missing_ok=True)
            except OSError:pass

    def read_record(self, namespace, key):
        if not self.enabled or key is None:return None
        try:
            path=self.directory.parent/namespace/(key+'.json')
            if path.stat().st_size>MAX_PACKET:return None
            packet=json.loads(path.read_bytes());data=packet['data']
            if packet['checksum']!=hashlib.sha256(canonical(data)).hexdigest():return None
            return data
        except (OSError,ValueError,TypeError,KeyError):return None

    def write_record(self, namespace, key, data):
        if not self.enabled or key is None:return
        temporary=None
        try:
            directory=self.directory.parent/namespace;directory.mkdir(parents=True,exist_ok=True)
            packet=canonical(dict(data=data,checksum=hashlib.sha256(canonical(data)).hexdigest()))
            with tempfile.NamedTemporaryFile(dir=directory,prefix='.entry-',delete=False) as stream:
                temporary=Path(stream.name);stream.write(packet)
            os.replace(temporary,directory/(key+'.json'))
        except (OSError,ValueError,TypeError):pass
        finally:
            if temporary is not None:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass

    def prepared(self, key, skip=None):
        record=self.read_record('prepared-v1',key)
        target=record.get('target') if isinstance(record,dict) else None
        if not isinstance(target,str) or not re.fullmatch('[0-9a-f]{64}',target):return None,None
        if target==skip:return None,target
        value=self.get(target)
        if value is not None:self.prepared_hits+=1
        return value,target

    def remember(self, prepared, target):
        if target is not None:self.write_record('prepared-v1',prepared,dict(target=target))

    def identity(self, odin, root):
        if odin in self.identities: return self.identities[odin]
        if self.toolchain_stamp is not None:
            saved=self.read_record('identity-v1',self.toolchain_stamp)
            if isinstance(saved,str) and re.fullmatch('[0-9a-f]{64}',saved):
                self.identities[odin]=saved;return saved
        digest = hashlib.sha256(canonical(dict(schema=SCHEMA, platform=os.sys.platform, machine=platform.machine())))
        executable = Path(odin).resolve()
        digest.update(canonical([str(executable),file_digest(executable)]))
        for command in ['version', 'root']:
            result = subprocess.run([odin, command], capture_output=True, check=True, timeout=15)
            digest.update(canonical([command,result.stdout.hex()]))
            if command == 'root': native_root = Path(result.stdout.decode().strip())
        # Locally edited stdlib/compiler support invalidates unchanged binaries.
        for tree in [root/'thorlang', root/'frontend', root/'runtime', native_root/'base', native_root/'core', native_root/'vendor']:
            for path in sorted(tree.rglob('*')):
                if path.suffix in {'.odin', '.py'} and path.is_file():
                    digest.update(canonical([str(path),file_digest(path)]))
        self.identities[odin] = digest.hexdigest()
        self.write_record('identity-v1',self.toolchain_stamp,self.identities[odin])
        return self.identities[odin]

    def resources(self, project):
        """Dynamic resource paths bypass caching rather than guessing inputs."""
        digest = hashlib.sha256()
        native=getattr(project,'native_sources',None)
        sources=[*project.sources.values(),*(native.sources.values() if native else [])]
        for source in sources:
            if source.excluded: continue
            for node in source.nodes:
                if node.kind != 'Call': continue
                callee = node.role('callee')
                if not callee or callee.text not in {'#load', '#load_hash', '#load_directory', '#exists'}: continue
                args = node.group('args')
                if not args or args[0].kind != 'Literal': return None
                name = unquote(args[0].text)
                path = source.import_dir(name) if ':' in name else (source.path.parent/name).resolve()
                if path is None: return None
                digest.update(canonical(str(path)))
                if path.is_dir():
                    digest.update(b'directory')
                    # Native #load_directory loads immediate regular files;
                    # subdirectories (including our own cache) are excluded.
                    if callee.text=='#load_directory':
                        for child in sorted(path.iterdir()):
                            if child.is_file():
                                digest.update(canonical([child.name,file_digest(child)]))
                elif path.is_file(): digest.update(file_digest(path).encode())
                else: digest.update(b'missing')
        return digest.hexdigest()

    def key(self, original, helper, target, odin, root):
        if not self.enabled:
            self.stats['bypasses'] += 1;return None
        try:
            resources = self.resources(original)
            if resources is None:
                self.stats['bypasses'] += 1;return None
            normalize = lambda text: str(text).replace(str(helper.destination), '<helper>')
            digest = hashlib.sha256(canonical(dict(identity=self.identity(odin, root), resources=resources,
                flags=[normalize(flag) for flag in helper.odin_flags()], target=normalize(target))))
            for path in sorted(helper.destination.rglob('*.odin')):
                digest.update(canonical([str(path.relative_to(helper.destination)),normalize(path.read_text())]))
            return digest.hexdigest()
        except (OSError, ValueError, subprocess.SubprocessError):
            self.stats['bypasses'] += 1;return None

    def get(self, key):
        if key is None: return None
        path = self.directory/(key+'.json')
        try:
            if path.stat().st_size > MAX_PACKET+4096: raise ValueError('oversize cache entry')
            packet = json.loads(path.read_bytes());value = packet['value']
            if packet['schema'] != SCHEMA or packet['key'] != key: raise ValueError('cache schema/key')
            if packet['checksum'] != hashlib.sha256(canonical(value)).hexdigest(): raise ValueError('cache checksum')
            values.expression(value, 'T', 'meta')
        except FileNotFoundError:
            self.stats['misses'] += 1;return None
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            self.stats['corrupt'] += 1;self.stats['misses'] += 1;return None
        self.stats['hits'] += 1
        return value

    def put(self, key, value):
        if key is None: return
        temporary = None
        try:
            values.expression(value, 'T', 'meta');contents = canonical(value)
            if len(contents) > MAX_PACKET: return
            packet = canonical(dict(schema=SCHEMA, key=key, value=value, checksum=hashlib.sha256(contents).hexdigest()))
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix='.entry-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(packet);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary, self.directory/(key+'.json'));self.stats['writes'] += 1
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            pass # optional storage failure must not change compilation
        finally:
            if temporary is not None:
                try: temporary.unlink(missing_ok=True)
                except OSError: pass

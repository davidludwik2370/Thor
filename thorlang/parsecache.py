"""Content-addressed frontend JSON; each consumer constructs its own AST."""
import hashlib
import json
from pathlib import Path
import os
import subprocess
import tempfile

HEADER = b'thor-ast-v1\n'
MAX_PACKET = 64 * 1024 * 1024


def clear(frontend):
    directory = Path(frontend).parent/'parse-v1'
    try:
        for path in directory.glob('*.ast'):
            if len(path.stem)==64 and all(c in '0123456789abcdef' for c in path.stem) and not path.is_symlink():
                path.unlink(missing_ok=True)
    except OSError:
        pass


def run(args, raw, *, enabled=True):
    path = None
    if enabled:
        try:
            frontend = Path(args[0])
            stat = frontend.stat()
            identity = [str(frontend.resolve()), stat.st_dev, stat.st_ino, stat.st_size,
                        stat.st_mtime_ns, stat.st_ctime_ns, [str(arg) for arg in args]]
            key = hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()+b'\0'+raw).hexdigest()
            path = frontend.parent/'parse-v1'/(key+'.ast')
            if path.stat().st_size <= MAX_PACKET:
                packet = path.read_bytes()
                checksum, separator, output = packet[len(HEADER):].partition(b'\n')
                if packet.startswith(HEADER) and separator and hashlib.sha256(output).hexdigest().encode()==checksum:
                    return subprocess.CompletedProcess(args,0,output,b'')
        except (OSError, ValueError):
            pass
    result = subprocess.run(args,input=raw,capture_output=True,timeout=30)
    temporary = None
    if path is not None and result.returncode==0 and len(result.stdout)<MAX_PACKET:
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            packet = HEADER+hashlib.sha256(result.stdout).hexdigest().encode()+b'\n'+result.stdout
            with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.entry-',delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(packet)
            os.replace(temporary,path)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass
    return result

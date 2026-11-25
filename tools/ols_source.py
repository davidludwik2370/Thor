"""Stage hash-verified, unmodified upstream OLS source for ARM64 Linux releases."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]

def stage_source(destination, source=None, *, metadata=None, archive=None):
    metadata=metadata or json.loads((ROOT/'install/ols-source.json').read_text())
    files=metadata['files']
    for name in files:
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name:
            raise ValueError('Unsafe upstream OLS source path')
    if source is not None:
        payload={name:(Path(source)/name).read_bytes() for name in files}
    else:
        if archive is None:
            url=f"https://codeload.github.com/DanielGavin/ols/tar.gz/{metadata['revision']}"
            with urllib.request.urlopen(url,timeout=60) as response:
                archive=response.read(20*1024*1024+1)
        if len(archive)>20*1024*1024:raise ValueError('Upstream OLS archive exceeds 20 MiB')
        payload={}
        prefix='ols-'+metadata['revision']+'/'
        with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as incoming:
            for member in incoming:
                if not member.name.startswith(prefix):continue
                name=member.name[len(prefix):]
                if name not in files:continue
                if name in payload or not member.isfile() or member.size>4*1024*1024:
                    raise ValueError('Unsupported or duplicate upstream OLS source file')
                payload[name]=incoming.extractfile(member).read()
    for name,expected in files.items():
        if name not in payload or hashlib.sha256(payload[name]).hexdigest()!=expected:
            raise ValueError('Missing or modified pinned upstream OLS source: '+name)
    for name,data in payload.items():
        target=Path(destination)/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    (Path(destination)/'UPSTREAM.json').write_text(json.dumps(metadata,sort_keys=True,indent=2)+'\n')

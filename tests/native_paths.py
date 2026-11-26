"""Native test tools follow the host executable naming rules."""
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
EXE_SUFFIX = '.exe' if os.name == 'nt' else ''


def directory_aliases(pairs):
    """Create real directory aliases under an ordinary Windows account too."""
    pairs=[(Path(link),Path(target)) for link,target in pairs]
    if os.name!='nt':
        for link,target in pairs:link.symlink_to(target,target_is_directory=True)
        return
    import json
    import subprocess
    records=[]
    for link,target in pairs:
        target=(target if target.is_absolute() else link.parent/target).resolve()
        if not target.is_dir():raise ValueError('Directory alias target is not a directory: '+str(target))
        records.append({'link':str(link.absolute()),'target':str(target)})
    # Values travel as JSON on stdin, never as interpolated shell source.
    script="$ErrorActionPreference='Stop'; $thorAliasItems=ConvertFrom-Json ([Console]::In.ReadToEnd()); foreach ($thorAliasItem in $thorAliasItems) {New-Item -ItemType Junction -Path $thorAliasItem.link -Target $thorAliasItem.target | Out-Null}"
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        input=json.dumps(records),capture_output=True,text=True,check=True,timeout=30)


def directory_alias(link,target):
    directory_aliases([(link,target)])


def is_directory_alias(link):
    link=Path(link)
    try:return link.is_symlink() or getattr(link.lstat(),'st_reparse_tag',0)==0xA0000003
    except FileNotFoundError:return False


def remove_directory_alias(link):
    link=Path(link)
    if not is_directory_alias(link):raise ValueError('Refusing to remove an ordinary directory')
    if getattr(link.lstat(),'st_reparse_tag',0)==0xA0000003:link.rmdir()
    else:link.unlink()


def file_reference(link,target):
    """Read-only SDK fixtures need aliasing bytes, not privileged file symlinks."""
    link=Path(link);target=Path(target)
    if os.name!='nt':link.symlink_to(target);return
    import shutil
    if not target.stat().st_mode&0o200:shutil.copyfile(target,link);return
    try:os.link(target,link)
    except OSError:shutil.copyfile(target,link)


def file_uri_path(uri, *, platform=None):
    platform=os.name if platform is None else platform
    parsed=urlparse(uri);value=unquote(parsed.path)
    if platform=='nt' and re.match(r'^/[A-Za-z]:/',value):value=value[1:]
    if platform=='nt' and parsed.netloc:value='//'+parsed.netloc+value
    return Path(value)


def rust_binary(name='thor-rust', *, example=False, platform=None):
    platform = os.name if platform is None else platform
    directory = ROOT / 'rust/target/release'
    if example:
        directory /= 'examples'
    return directory / (name + ('.exe' if platform == 'nt' else ''))


def thor_launcher(*, platform=None):
    platform = os.name if platform is None else platform
    return rust_binary(platform=platform) if platform == 'nt' else ROOT / 'thor'


def normalize_lsp_uris(value, *, platform=None):
    """Compare equivalent Windows drive URI spellings, without changing paths."""
    platform = os.name if platform is None else platform
    if platform != 'nt':
        return value
    if isinstance(value, str):
        return re.sub(r'^(file:///[A-Za-z])%3[aA](?=/)', r'\1:', value)
    if isinstance(value, dict):
        return {normalize_lsp_uris(k, platform=platform): normalize_lsp_uris(v, platform=platform)
                for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_lsp_uris(v, platform=platform) for v in value]
    return value


def file_uri_key(uri, *, platform=None):
    """Windows drive spelling is immaterial; component case and escaping remain."""
    platform=os.name if platform is None else platform
    uri=normalize_lsp_uris(uri,platform=platform)
    if platform=='nt':
        return re.sub(r'^(file:///)([A-Z]):',lambda match:match[1]+match[2].lower()+':',uri)
    return uri


def native_test_compiler(directory, mode, **config):
    """An actual native executable, without script-association assumptions."""
    import json
    import shutil
    import subprocess
    binary = rust_binary('test_compiler_mock', example=True)
    if os.environ.get('THOR_TEST_PREBUILT') != '1':
        subprocess.run(['cargo','build','--release','--locked','--manifest-path',str(ROOT/'rust/Cargo.toml'),
                        '--example','test_compiler_mock','-j','2'],check=True,capture_output=True,timeout=600)
    if not binary.is_file():
        raise RuntimeError('Build the native test_compiler_mock example first.')
    target = directory / ('compiler.exe' if os.name == 'nt' else 'compiler')
    shutil.copy2(binary,target)
    if 'sdk' in config:
        # Odin's Windows ODIN_ROOT validation rejects a trailing directory
        # separator even though its `root` query prints one.
        config['sdk']=str(Path(config['sdk']))
    target.with_suffix('.json').write_text(json.dumps(dict(mode=mode,**config)))
    return target


def process_running(pid):
    """Inspect only a recorded mock PID; never send a Windows pseudo-signal."""
    if os.name == 'nt':
        import csv
        import subprocess
        result=subprocess.run(['tasklist','/FI',f'PID eq {pid}','/FO','CSV','/NH'],capture_output=True,text=True,timeout=10,check=True)
        return any(len(row)>1 and row[1]==str(pid) for row in csv.reader(result.stdout.splitlines()))
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False

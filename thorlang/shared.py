"""Project-independent library output owned by the Thor installation.

Only libraries without Thor evaluation or configuration-dependent syntax may
use this path. Checked variants and editor replacements stay in the project.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .compiler import ThorError
from .sourcemap import SourceMap


class Runtime:
    def __init__(self,project,root):
        self.project=project
        self.root=root
        self.sources=root/'runtime'
        self.directory=None
        self.metadata={}

    def bundled(self,source):
        return source.path.is_relative_to(self.sources) and 'thor' in self.project.default_collections

    def native(self,source):
        return self.bundled(source) and source.path.suffix=='.odin'

    def eligible(self,source):
        if not self.bundled(source) or source.path.suffix!='.thor':return False
        if any(path.is_relative_to(self.sources) for path in self.project.overlays):return False
        if any(d.source is source for d in self.project.purity.calls.declarations):return False
        if source.info.get('selection_tags'):return False
        # Start with syntax whose translation is independent of caller types,
        # defines, platform selection and compile-time execution.
        if any(n.kind in {'Run','CompileSwitch','CompileSwitchExpr','WhenExpr'} or n.data.get('pure')
               or n.kind=='Binary' and n.data.get('op')=='**'
               or n.kind=='Directive' and n.data.get('name') in {'config','load','exists','defined'}
               for n in source.nodes):return False
        members=[unit for unit in self.project.packages.get(source.path.parent,[]) if not unit.excluded]
        return source.path.parent==self.sources/'basic' and len(members)==1 and members[0] is source

    def fingerprint(self):
        digest=hashlib.sha256(str(self.root).encode())
        for directory,suffixes in [(self.root/'thorlang',{'.py'}),(self.root/'frontend',{'.odin'}),(self.sources,{'.thor','.odin'})]:
            for path in sorted(directory.rglob('*')):
                if path.suffix in suffixes and path.is_file():
                    digest.update(str(path.relative_to(self.root)).encode());digest.update(path.read_bytes())
        stat=self.project.frontend.stat()
        digest.update(repr((str(self.project.frontend),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)).encode())
        return digest.hexdigest()[:24]

    def prepare(self, *, with_basic=True):
        if self.directory and self.with_basic==with_basic:return self.directory
        from .driver import Project
        stamp=self.fingerprint()
        key=stamp+('-basic' if with_basic else '-native')
        directory=self.root/'lib'/('runtime-'+key)
        metadata=directory/'basic/basic.odin.map.json'

        def load():
            if not with_basic:
                data=json.loads((directory/'thor-manifest.json').read_text())
                if data['shared_runtime']!=key:raise ValueError('shared runtime version')
                return data,b''
            data=json.loads(metadata.read_text())
            code=(directory/'basic/basic.odin').read_bytes()
            if data['generated_sha256']!=hashlib.sha256(code).hexdigest():raise ValueError('shared library checksum')
            return data,code

        try:data,code=load()
        except (OSError,ValueError,KeyError):
            directory.parent.mkdir(exist_ok=True)
            data={'shared_runtime':key};code=b''
            if with_basic:
                builder=Project(self.sources/'basic',self.project.frontend,[],use_shared=False,
                    cache_directory=self.root/'.thor-cache',debug_maps=True)
                source=builder.sources[self.sources/'basic/basic.thor']
                code=source.transpile().encode()
                data=source.source_map.json(directory/'basic/basic.odin',builder.sources)
                data['generated_sha256']=hashlib.sha256(code).hexdigest()
                data['thor_diagnostics']={'errors':source.error_messages,'aliases':source.hidden_namespaces,
                    'guards':[[a,b,message] for (a,b),message in source.guard_messages.items()]}
            if stamp!=self.fingerprint() or with_basic and source.raw!=source.path.read_bytes():
                raise ThorError('Thor library sources changed during preparation; build again.')
            with tempfile.TemporaryDirectory(prefix='.runtime-build-',dir=directory.parent) as temporary:
                planned=Path(temporary)/'runtime'
                planned.mkdir()
                if with_basic:
                    (planned/'basic').mkdir()
                    (planned/'basic/basic.odin').write_bytes(code)
                    (planned/'basic/basic.odin.map.json').write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n')
                for package in sorted(self.sources.iterdir()):
                    if package.is_dir() and package.name!='basic':
                        (planned/package.name).mkdir()
                        for file in package.iterdir():
                            if not file.name.endswith('_test.odin'):
                                (planned/package.name/file.name).symlink_to(file,target_is_directory=file.is_dir())
                (planned/'thor-manifest.json').write_text(json.dumps({'shared_runtime':key})+'\n')
                try:os.rename(planned,directory)
                except OSError:
                    # Another compiler may have published the same complete
                    # library while this one was preparing it.
                    existing,existing_code=load()
                    if existing_code!=code:raise ThorError('Shared Thor library output is inconsistent.')
                    data=existing
        self.directory=directory
        self.with_basic=with_basic
        self.metadata=data
        return directory

    def attach(self,source,project):
        directory=self.prepare()
        output=directory/'basic/basic.odin'
        data=self.metadata
        project.outputs[source.path]=output
        project.maps[str(output)]=SourceMap(data['segments'],data.get('expansions',()),data.get('regions',()))
        project.generated[str(output)]=output.read_bytes()
        info=data.get('thor_diagnostics',{})
        source.error_messages.update(info.get('errors',{}))
        source.hidden_namespaces.update(info.get('aliases',{}))
        source.guard_messages.update({(a,b):message for a,b,message in info.get('guards',[])})
        project.shared_maps.append(str(output)+'.map.json')

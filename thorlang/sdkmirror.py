"""Local SDK collections augmented with checked procedure versions.

The backend uses one collection root for both ordinary and checked calls, so
nominal and private SDK types retain a single identity. Original SDK files are
never modified. Files without added procedures are copied with identity maps.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .compiler import Source
from .sourcemap import SourceMap


def extend(project,destination,generated):
    project.sdk_sources={}
    project.sdk_backend_root=None
    if not getattr(project,'pure_sdk_collections',None):return
    project.sdk_backend_root=destination/project.pure_sdk_subdir
    # The compiler's collection names are fixed. A local ODIN_ROOT selects the
    # augmented collections uniformly, including imports made by untouched SDK
    # packages. Unchanged collections/toolchain directories remain read-only inputs.
    for name in ['base','core','vendor','shared','bin','lib']:
        original=project.pure_sdk_root/name
        if name not in project.pure_sdk_collections and original.exists():
            generated[project.pure_sdk_subdir+'/'+name]=original
    for name in sorted(getattr(project,'pure_sdk_collections',())):
        root=project.pure_sdk_root/name
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path in project.sources:continue
            relative=project.pure_sdk_subdir+'/'+path.relative_to(project.pure_sdk_root).as_posix()
            raw=project.overlays.get(path,path.read_bytes())
            if path.suffix!='.odin':
                generated[relative]=raw
                continue
            prefix='__thor_sdk_'+hashlib.sha256(str(path).encode()).hexdigest()[:12]+'_'
            source=SimpleNamespace(raw=raw,prefix=prefix,error_messages={},hidden_namespaces={},guard_messages={},nodes=[],asset_collections={})
            code=raw
            mapping=SourceMap([[0,len(raw),str(path),0,len(raw),'source',True]])
            # Preserve static source-relative resources and foreign assets.
            # Only files containing these constructs need frontend parsing.
            if any(word in raw for word in (b'#load',b'#exists',b'foreign import')):
                source=Source(path,project.frontend,thor=False,package=True,test=project.test,
                              project_name=project.entry.stem,mapping=project.mapping,raw=raw,
                              target=project.target,cache=project.cache_frontend)
                source.collections={**{n:project.pure_sdk_root/n for n in ['base','core','vendor']},**project.collections}
                resources=[]
                for node in source.nodes:
                    if node.kind=='ForeignImport' or (node.kind=='Call' and node.role('callee').text in {'#load','#load_hash','#load_directory','#exists'}
                                                       and node.group('args') and node.group('args')[0].kind=='Literal'):
                        if not any(parent in resources for parent in node.ancestors()):resources.append(node)
                annotated=source.splice(source.root,[(n.start,n.end,source.render(n)) for n in resources])
                text,mapping=source.mapping.finish(annotated)
                code=text.encode()
            output=destination/relative
            generated[relative]=code
            project.sdk_sources[path]=source
            project.maps[str(output)]=mapping
            project.generated[str(output)]=code
            data=mapping.json(output,{path:source})
            data['generated_sha256']=hashlib.sha256(code).hexdigest()
            sidecar='.thor-sdk-maps/'+hashlib.sha256(str(path).encode()).hexdigest()+'.odin.map.json'
            generated[sidecar]=json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n'

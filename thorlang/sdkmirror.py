"""A sparse SDK overlay: generated checked files, links to untouched inputs.

One ODIN_ROOT keeps ordinary and checked calls in the same type universe.
Unchanged packages and resources remain native inputs, including their original
relative layout. Only generated files need source-map sidecars.
"""
import hashlib
import json
from types import SimpleNamespace

from .sourcemap import SourceMap


def extend(project,destination,generated):
    project.sdk_sources={}
    project.sdk_backend_root=None
    if not getattr(project,'pure_sdk_collections',None):return
    root=project.pure_sdk_root
    project.sdk_backend_root=destination/project.pure_sdk_subdir
    changed={path for path,source in project.sources.items() if getattr(source,'native_inspected',False)}
    # Unsaved SDK files/resources are inputs too, even when they contain no
    # checked procedure. Copy only these replacements; all other inputs link.
    for path,raw in project.overlays.items():
        if root not in path.parents or path in changed:continue
        changed.add(path)
        output=destination/project.pure_sdk_subdir/path.relative_to(root)
        generated[output.relative_to(destination).as_posix()]=raw
        if path.suffix!='.odin':continue
        source=SimpleNamespace(raw=raw,prefix='__thor_sdk_'+hashlib.sha256(str(path).encode()).hexdigest()[:12]+'_',
            error_messages={},hidden_namespaces={},guard_messages={},nodes=[],asset_collections={})
        mapping=SourceMap([[0,len(raw),str(path),0,len(raw),'source',True]])
        project.sdk_sources[path]=source
        project.maps[str(output)]=mapping
        project.generated[str(output)]=raw
        data=mapping.json(output,{path:source})
        data['generated_sha256']=hashlib.sha256(raw).hexdigest()
        if project.persist_maps:
            generated[output.relative_to(destination).as_posix()+'.map.json']=json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n'
    parents={parent for path in changed for parent in path.parents if parent==root or root in parent.parents}

    def include(path):
        if path in changed:return
        if path in parents:
            for child in sorted(path.iterdir()):include(child)
        else:
            generated[project.pure_sdk_subdir+'/'+path.relative_to(root).as_posix()]=path

    for name in ['base','core','vendor','shared','bin','lib']:
        original=root/name
        if original.exists():include(original)

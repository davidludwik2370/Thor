"""Fatal diagnostics may allocate; successful check arguments still may not."""
from pathlib import Path
import hashlib
import json

PANIC={'panic','panic_contextless','unimplemented','unimplemented_contextless'}
CHECK={'assert','assert_contextless','ensure','ensure_contextless'}
ROOT=Path(__file__).resolve().parents[1]

class Failure:
    def __init__(self,check):self.c=check;self.verified=set();self.profile=None
    def identity(self,node,seen=None):
        if node is None:return None
        seen=set() if seen is None else seen
        if node in seen:return None
        seen.add(node)
        if node.kind=='Paren':return self.identity(node.role('expr'),seen)
        symbol=self.c.r.symbol(node)
        if symbol and symbol.value and not symbol.declaration.data.get('mutable') and not symbol.immutable:
            found=self.identity(symbol.value,seen)
            if found:return found
        imported=self.c.p.purity.imported(node)
        if imported and imported[0] in {'base:runtime','core:fmt','core:log'}:
            collection=imported[0].split(':')[0];root=node.source.info.get('odin_root')
            if root and Path(node.source.collections.get(collection,Path(root)/collection))==Path(root)/collection:return imported
        if node.kind=='Procedure':
            names=node.parent.group('names') if node.parent else []
            root=node.source.info.get('odin_root')
            if names and root:
                for package in ['base/runtime','core/fmt','core/log']:
                    if node.source.path.parent==Path(root)/package:return package.replace('/',':',1),names[0].text
        return None
    def verify(self,node,relative):
        root=node.source.info.get('odin_root')
        if not root:self.c.error(node,'cannot locate the SDK for fatal-reporting verification')
        path=Path(root)/relative
        if path in self.verified:return
        if self.profile is None:self.profile=json.loads((ROOT/'rust/data/noalloc-io-sdk.json').read_text())['sdk']
        source=self.c.p.sources.get(path)
        try:data=self.c.p.overlays[path] if path in self.c.p.overlays else source.raw if source else path.read_bytes()
        except OSError as error:self.c.error(node,f'cannot inspect fatal-reporting SDK source: {error}')
        if self.profile.get(relative)!=hashlib.sha256(data).hexdigest():
            self.c.error(node,f'fatal-reporting SDK profile does not match {path}; the changed implementation requires review')
        self.verified.add(path)
    def call(self,node,native):
        callee=node.role('callee');package=None;name=native
        if name not in PANIC|CHECK:
            if native is not None:return False
            symbol=self.c.r.symbol(callee)
            definition=symbol.value if symbol and not symbol.declaration.data.get('mutable') and not symbol.immutable and symbol.value and symbol.value.kind=='Procedure' else None
            if definition:identity=self.identity(definition)
            else:
                identity=self.identity(callee)
                fact=self.c.flow.fact(callee)
                if not identity and fact and fact[0]=='function':identity=self.identity(fact[1])
            if not identity:return False
            package,name=identity
        if package in {'core:fmt','core:log'}:
            if name not in {'panic','panicf','assert','assertf','ensure','ensuref'}:return False
            self.verify(node,package.replace(':','/')+'/'+package.split(':')[1]+'.odin')
        elif name not in PANIC|CHECK:return False
        else:self.verify(node,'base/runtime/core_builtin.odin')
        self.verify(node,'base/runtime/core.odin')
        if name in PANIC|{'panicf'}:
            self.c.flow.terminated=True
            return True
        from .parallelcompile import known_callback
        if name.startswith('assert') and '-disable-assert' in self.c.p.flags and (native in CHECK or known_callback(self.c.r,callee)):return True
        args=node.group('args')
        layout=None
        if package and not known_callback(self.c.r,callee):
            plan=self.c.io.plan(node)
            if plan is None:self.c.error(node,'failure-check callback argument layout is unresolved')
            layout=plan[1]
        if layout is not None:condition=layout[0][0] if layout and layout[0] else None
        else:
            condition=next((a.role('value') for a in args if a.kind=='FieldValue' and a.role('field').text=='condition'),None)
            if condition is None:condition=next((a for a in args if a.kind!='FieldValue'),None)
        # Only static facts are safe before evaluating caller-written arguments.
        fatal=self.c.static_condition(condition) is False
        for arg in args:
            value=arg.role('value') if arg.kind=='FieldValue' else arg
            if not fatal or value is condition:self.c.walk(arg)
        supplied={a.role('value') if a.kind=='FieldValue' else a for a in args}
        for group in layout or []:
            for arg in group:
                if arg not in supplied and (not fatal or arg is condition):self.c.walk(arg)
        if fatal:self.c.flow.terminated=True
        return True

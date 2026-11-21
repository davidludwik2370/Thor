"""Source-pinned SDK I/O effects. Unknown stream targets remain errors."""
from pathlib import Path
import hashlib
import json
import sys
from .semantics import descendants
from .noallocflow import UNKNOWN,arrange

ROOT=Path(__file__).resolve().parents[1]


class IO:
    def __init__(self,check):self.c=check;self.r=check.r;self.ready=False;self.sdk_ready=False

    def identity(self,node,seen=None):
        if node is None:return None
        seen=set() if seen is None else seen
        if node in seen:return None
        seen.add(node)
        if node.kind=='Paren':return self.identity(node.role('expr'),seen)
        if node.kind=='Procedure':
            names=node.parent.group('names') if node.parent else []
            if not names:return None
            name=names[0].text;path=node.source.path
            if path==ROOT/'runtime/basic/basic.thor' and name=='print':return 'thor:basic',name
            root=node.source.info.get('odin_root')
            if root and path.parent.parent==Path(root)/'core' and path.parent.name in {'fmt','io','os','bufio'}:
                return 'core:'+path.parent.name,name
        symbol=self.r.symbol(node)
        if symbol and symbol.value:
            value=self.identity(symbol.value,seen)
            if value:return value
        imported=self.c.p.purity.imported(node)
        if imported and imported[0] in {'core:fmt','core:io','core:os','core:bufio'}:
            root=node.source.info.get('odin_root')
            override=node.source.collections.get('core')
            if root and (override is None or Path(override)==Path(root)/'core'):return imported
        return None

    def value(self,node,depth):
        flow=self.c.flow
        if node.kind in {'Ident','Selector'}:
            label=node.role('field').text if node.kind=='Selector' else node.text
            if label in {'stdin','stdout','stderr'} and (identity:=self.identity(node)) and identity[0]=='core:os':
                return 'file',{'stdin':0,'stdout':1,'stderr':2}[label]
        if node.kind!='Call':return None
        identity=self.identity(node.role('callee'))
        if not identity:return None
        package,name=identity
        groups=self.plan(node)
        if not groups:return None
        arg=flow.fact(groups[1][0][0],depth+1) if groups[1] and groups[1][0] else UNKNOWN
        if package=='core:os' and name in {'to_stream','to_reader','to_writer','to_flusher'}:return 'stream',arg
        if package=='core:io' and name in {'to_reader','to_writer','to_flusher'}:return arg
        if package=='core:bufio' and name in {'writer_to_writer','writer_to_stream','reader_to_stream'}:return 'stream',flow.deref(arg)
        return None

    def plan(self,node):
        from .parallelcompile import known_callback
        from types import SimpleNamespace
        fact=self.c.flow.fact(node.role('callee'))
        target=fact[1] if fact and fact[0]=='function' else None
        runtime=target and not known_callback(self.r,node.role('callee'))
        plan=self.r.resolve_call(node,targets=[target] if runtime else None)
        if not plan:
            candidates=self.r.procedure_candidates(node.role('callee'))
            if target:signature=target.role('type')
            elif len(candidates)==1:signature=candidates[0][1]
            else:return None
            plan=SimpleNamespace(signature=signature,substitutions={})
        declared=self.r.type_of(node.role('callee'))
        signature=declared.node if declared.kind=='procedure' else plan.signature
        groups=arrange(signature,node.group('args'))
        return (plan,groups) if groups is not None else None

    def verify_sdk(self,site):
        if self.sdk_ready:return
        root=next((Path(s.info['odin_root']) for s in self.r.sources if s.info.get('odin_root')),None)
        if root is None:self.c.error(site,'cannot prove allocation freedom: missing native SDK root')
        data=json.loads((ROOT/'rust/data/noalloc-io-sdk.json').read_text())
        overlays=dict(self.c.p.overlays)
        overlays.update((s.path,s.raw) for s in self.r.sources)
        paths=[p for top in ['base','core'] for p in (root/top).rglob('*.odin') if not p.stem.endswith('_test')]
        paths.extend(ROOT/'runtime'/name for name in data['thor'])
        paths.extend(path for path in self.c.p.overlays if path.is_relative_to(root) and path not in paths)
        for path in paths:
            expected=data['sdk'].get(path.relative_to(root).as_posix()) if path.is_relative_to(root) else data['thor'].get(path.relative_to(ROOT/'runtime').as_posix())
            raw=overlays[path] if path in overlays else path.read_bytes()
            if isinstance(raw,str):raw=raw.encode()
            if expected!=hashlib.sha256(raw).hexdigest():
                self.c.error(site,f'cannot prove allocation freedom: SDK I/O effect profile does not match {path}; the changed implementation requires review')
        for source in self.r.sources:
            for col in ['base','core']:
                if source.collections.get(col) and Path(source.collections[col])!=root/col:
                    self.c.error(site,'cannot prove allocation freedom: SDK collection overrides require separate I/O review')
        self.sdk_ready=True

    def proof(self,site):
        if self.ready:return
        self.verify_sdk(site)
        root=next(Path(s.info['odin_root']) for s in self.r.sources if s.info.get('odin_root'))
        queue=[];reachable=set()
        for source in list(self.r.sources):
            entry=self.c.p.entry
            package=entry.parent if entry.is_file() else entry
            if source.excluded or source.path.stem.endswith('_test') and (not self.c.p.test or source.path.parent!=package):continue
            for node in source.nodes:
                if node.kind=='Procedure':
                    decl=node.parent;names=decl.group('names') if decl and decl.kind=='Declaration' else []
                    attrs=decl.group('attributes') if decl else []
                    if any(n.text=='main' for n in names) or any((e.role('field') if e.kind=='FieldValue' else e).text in {'init','test','export','no_alloc'} for a in attrs for e in a.group('elems')) or list(self.c.module_tags(source)):queue.append(node)
                elif node.kind=='Declaration' and node.data.get('mutable') and not any(a.kind=='Procedure' for a in node.ancestors()):queue.append(node)
        while queue:
            node=queue.pop()
            if node in reachable or not self.c.active(node):continue
            if node.source.path.parent in {root/'core/fmt',root/'core/os'}:continue
            for at in descendants(node):
                if at in reachable:continue
                reachable.add(at)
                if at.kind in {'Ident','Selector'} and (at.kind!='Ident' or self.c.p.purity.reference(at)):
                    for _,signature in self.r.procedure_candidates(at):
                        if signature.parent and signature.parent.kind=='Procedure':queue.append(signature.parent)
        for at in reachable:
            if not self.c.active(at):continue
            identity=self.identity(at) if at.kind in {'Ident','Selector'} else None
            if identity and identity[0]=='core:fmt':
                if identity[1] in {'set_user_formatters','register_user_formatter'}:
                    self.c.error(site,'cannot prove allocation freedom: the program configures a mutable user-formatter registry')
                if identity[1]=='_user_formatters':self.c.error(site,'cannot prove allocation freedom: the user-formatter registry escapes SDK control')
            if at.kind=='Call' and any(self.protected_type(self.r.type_of(a.role('value') if a.kind=='FieldValue' else a),root) for a in at.group('args')):
                identity=self.identity(at.role('callee'))
                reviewed=identity and (identity[0]=='core:os' and identity[1] in {'read','read_at','write','write_at','write_string','write_strings','flush','to_stream','to_reader','to_writer','to_flusher'} or identity[0]=='core:fmt' and identity[1] in {'fprint','fprintln','fprintf','fprintfln'})
                candidates=self.r.procedure_candidates(at.role('callee'))
                user=bool(candidates) and all(signature.parent.role('body') and not signature.source.path.is_relative_to(root) for _,signature in candidates)
                if not reviewed and not user:self.c.error(site,'cannot prove allocation freedom: native file metadata escapes to an unverified call')
            if at.kind=='Using' and any(self.protected_place(n,root) for n in at.group('values')):
                self.c.error(site,'cannot prove allocation freedom: native file metadata escapes through a using projection')
            if at.kind=='Assignment':
                for lhs in at.group('lhs'):
                    while lhs:
                        if self.protected_type(self.r.type_of(lhs),root) or self.r.type_of(lhs).kind in {'unknown','generic'} and lhs.role('field') and lhs.role('field').text in {'stream','procedure','impl'}:self.c.error(site,'cannot prove allocation freedom: native file callback metadata can be changed through an alias')
                        identity=self.identity(lhs)
                        if identity and identity[0]=='core:os' and identity[1] in {'stdin','stdout','stderr'}:
                            self.c.error(site,'cannot prove allocation freedom: a standard stream can be replaced by the program')
                        symbol=self.r.symbol(lhs)
                        lhs=symbol.projection[0] if symbol and symbol.projection else lhs.role('expr')
            if at.kind in {'Cast','AutoCast'} or at.kind=='Unary' and at.data.get('op')=='&':
                if at.role('expr') and self.protected_place(at.role('expr'),root):
                    self.c.error(site,f'cannot prove allocation freedom: native file metadata escapes through an address or cast ({at.source.path}: {at.text})')
            if at.kind=='Call' and at.source.denotes_type(at.role('callee')):
                if any(self.protected_place(a,root) for a in at.group('args')):
                    self.c.error(site,'cannot prove allocation freedom: native file metadata escapes through a conversion')
        self.ready=True

    def protected_place(self,node,root):
        for _ in range(32):
            if self.identity(node) in {('core:os','stdin'),('core:os','stdout'),('core:os','stderr')}:return True
            if self.protected_type(self.r.type_of(node),root):return True
            symbol=self.r.symbol(node)
            node=symbol.projection[0] if symbol and symbol.projection else node.role('expr')
            if node is None:return False
        return True

    @staticmethod
    def protected_type(typ,root):
        while typ.kind=='pointer':typ=typ.elem
        return typ.kind=='struct' and typ.node and typ.node.source.path.parent==root/'core/os' and typ.node.parent and any(n.text in {'File','File_Stream'} for n in typ.node.parent.group('names'))

    def call(self,node):
        callee=node.role('callee');identity=self.identity(callee)
        if (fact:=self.c.flow.fact(callee)) and fact[0]=='function':identity=self.identity(fact[1])
        if not identity:return False
        package,name=identity
        operations={
            'core:os':{'read','read_at','write','write_at','write_string','write_strings','flush','to_stream','to_reader','to_writer','to_flusher'},
            'core:io':{'read','read_at','write','write_at','write_string','write_byte','write_rune','write_int','write_i64','flush','query','to_reader','to_writer','to_flusher'},
            'core:bufio':{'writer_init_with_buf','writer_to_stream','writer_to_writer','writer_write','writer_write_string','writer_write_byte','writer_write_rune','writer_flush','reader_init_with_buf','reader_to_stream','reader_read','reader_peek','reader_discard','reader_read_byte','reader_read_rune','reader_read_slice','reader_unread_byte','reader_unread_rune'},
            'core:fmt':{p+n for p in ['','e','f','w'] for n in ['print','println','printf','printfln']},
            'thor:basic':{'print'},
        }
        if name not in operations.get(package,set()):return False
        self.proof(node)
        planned=self.plan(node)
        if not planned:self.c.error(node,'cannot prove allocation freedom: I/O signature or overload is unresolved')
        plan,groups=planned
        supplied={arg.role('value') if arg.kind=='FieldValue' else arg for arg in node.group('args')}
        for group in groups:
            for arg in group:
                if arg not in supplied:self.c.walk(arg)
        from .caller_metadata import params
        fields=params(plan.signature);facts=list(self.c.flow.arguments(plan.signature,groups))
        for i,refs in enumerate(groups):
            if len(refs)==1 and (not facts[i] or facts[i][0]!='pack'):facts[i]=self.c.flow.fact(refs[0])
        def get(i):return facts[i] if i<len(facts) else UNKNOWN
        def named(name):return next((facts[i] for i,(n,_) in enumerate(fields) if n.text==name),UNKNOWN)
        if package in {'core:os','core:io'}:
            if not name.startswith('to_'):
                mode={'read':'Read','read_at':'Read_At','write_at':'Write_At','flush':'Flush','query':'Query'}.get(name,'Write')
                self.dispatch(node,get(0),mode)
        elif package=='core:bufio':
            if name in {'writer_init_with_buf','reader_init_with_buf'}:
                ptr=get(0)
                if ptr and ptr[0]=='pointer' and ptr[1] is not None:self.c.flow.values[ptr[1]]= ('buffered_reader' if name.startswith('reader') else 'buffered'),get(1)
                else:self.c.flow.values={k:UNKNOWN for k in self.c.flow.values}
            elif not name.startswith(('writer_to_','reader_to_')):self.dispatch(node,self.c.flow.deref(get(0)),'Read' if name.startswith('reader') else 'Write')
        else:
            start=1 if name.startswith(('f','w')) else 0
            writer=get(0) if start else ('file',2 if name.startswith('e') else 1)
            self.dispatch(node,writer,'Write')
            if 'printf' in name or name.endswith('printfln'):
                start+=1
            if package=='thor:basic' and (not get(start) or get(start)[0]!='pack' or not all(print_value_known(v) for v in get(start)[1])):
                self.c.error(node,'cannot prove allocation freedom: basic.print may receive an owned logical number through any')
            if named('flush')!=('const',False):self.dispatch(node,writer,'Flush')
        if package=='core:bufio':
            if not name.endswith('init_with_buf') and '_to_' not in name:
                inner=self.c.flow.deref(get(0))
                if inner and inner[0] in {'buffered','buffered_reader'}:self.c.flow.forget(inner[1])
        elif not name.startswith('to_'):
            for fact in facts:self.c.flow.forget(fact)
        return True

    def dispatch(self,site,value,mode):
        value=self.c.flow.deref(value)
        if value and value[0]=='file':
            targets=[f.removeprefix('-target:') for f in getattr(self.c.p,'flags',[]) if f.startswith('-target:')]
            if targets and not targets[-1].startswith(('darwin_','linux_')) or not targets and sys.platform not in {'darwin','linux'}:
                self.c.error(site,'the target has no reviewed allocation-free native I/O boundary')
            return
        if value and value[0]=='stream':return self.dispatch(site,value[1],mode)
        if value and value[0]=='buffered':
            if mode in {'Write','Flush'}:return self.dispatch(site,value[1],'Write')
            return
        if value and value[0]=='buffered_reader':
            if mode=='Read':return self.dispatch(site,value[1],'Read')
            return
        if value and value[0]=='record':
            fields=dict(value[1]);target=fields.get('procedure',UNKNOWN)
            if target and target[0]=='function':
                self.c.queue.append((target[1],dict(self.c.work[1]),self.c.work[2],[*self.c.work[3],site],True,
                    (fields.get('data',UNKNOWN),('atom',mode),UNKNOWN,UNKNOWN,UNKNOWN)))
                return
        self.c.error(site,'cannot prove allocation freedom: stream callback target is unknown or can be replaced')


def print_value_known(value):
    if not value:return False
    if value[0]=='const':return True
    if value[0]=='record':return True
    if value[0]=='spread':return value[1] is not None and print_value_known(('typed',value[1]))
    if value[0]=='pack':return all(print_value_known(v) for v in value[1])
    if value[0]=='typed':return value[1].kind not in {'unknown','generic','big_integer','big_rational'} and value[1].name!='any'
    return False



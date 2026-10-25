"""Explicit, bounded native #run execution using verified dependency closures.

Each helper is an isolated throw-away project with source maps. Only necessary
declarations/imports enter it; user init/fini and entry points are never copied.
Successful contents may be cached. Automatic fn folding remains T23.
"""
import json
import platform
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .compiler import ThorError, unquote
from .diagnostics import Diagnostics
from .semantics import descendants
from . import values


class Evaluator:
    # These native packages are trusted compiler-distribution dependencies.
    # Other native collections must be inspectable project sources; an opaque
    # type-only import must not sneak unverified package initializers into #run.
    native_imports={'base:intrinsics','core:strings','core:slice','core:math'}
    def __init__(self,project,odin,timeout=5,cache=None):
        self.p=project;self.odin=odin;self.timeout=timeout
        self.results={};self.busy=set();self.guard_busy=set();self.skipped=set();self.unchecked=set()
        self.r=project.resolver;self.purity=project.purity
        self.root=Path(__file__).resolve().parents[1]
        self.cache=cache

    def fail(self,n,message):n.source.error(message,n.start,n.end)

    def native(self,n):
        if self.p.target:
            host_os={'darwin':'darwin','linux':'linux','win32':'windows'}.get(sys.platform,sys.platform)
            host_arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine().lower(),platform.machine().lower())
            if tuple(self.p.target)!=(host_os,host_arch):self.fail(n,'#run supports native targets only; host execution cannot evaluate a cross-target program.')

    def install(self,n,data):
        s=n.source;s.needs_intrinsics=s.needs_default=True
        typ=self.result_type(n.role('expr'))
        try:code=values.expression(data,typ,s.prefix+'meta')
        except (ValueError,TypeError,KeyError) as exc:self.fail(n,f'Invalid compile-time result: {exc}')
        literal=s.mapping.wrap(code,s.path,n.start,n.end,'compile-time-result')
        if values.has_owned(data):
            module=self.p.purity.module(s)
            def body(_):return values.materialize(data,typ,s.prefix+'meta',module,s.prefix+'materialized_'+str(n.start)+'_')
            name,args=s.expressions.closed(n,body,result=typ,flavor='run_value')
            runtime=f'{name}({", ".join(args)})'
            constant=(s.expressions.error(n,'A slice-containing #run result needs a runtime binding (:=); use a pure consumer to compute a scalar constant.')
                      if values.has_owned(data,slices_only=True) else literal)
            s.run_values[n]={'runtime':runtime,'constant':constant}
        else:s.run_values[n]=literal

    def result_type(self,call):
        r=call.source.resolver;plan=r.resolve_call(call)
        if not plan:self.fail(call,'#run result type requires an unambiguous resolved procedure target.')
        results=r.fields(plan.signature.role('results'))
        if len(results)!=1:self.fail(call,'#run requires exactly one supported result value.')
        annotation=results[0][1]
        substitutions=plan.substitutions
        def type_code(typ):
            if isinstance(typ,int):return str(typ)
            if typ.nominal is None and typ.kind in {'scalar','string'} and typ.name:return typ.name
            if typ.witness:
                if isinstance(typ.witness,tuple):return call.source.render(typ.witness[1])
                return f'type_of({call.source.render(typ.witness)})'
            if typ.node:return render(typ.node)
            self.fail(call,'The instantiated #run result type could not be resolved.')
        def render(node):
            if node.kind=='PolyType':
                name=node.data['name']
                return type_code(substitutions[name]) if name in substitutions else node.text.lstrip('$')
            if node.kind=='Ident':
                if node.text in substitutions:return type_code(substitutions[node.text])
                symbol=r.symbol(node)
                if symbol and symbol.node.source.path.parent!=call.source.path.parent:
                    return r.access(call.source,symbol.node.source,symbol.name)
            return node.source.splice(node,[(c.start,c.end,render(c)) for c in node.children])
        # A nongeneric procedure type supplies private result types without
        # constructing an unevaluated context-requiring call at global scope.
        if not r.generic_parameters(plan.signature):
            target=r.access(call.source,plan.symbol.node.source,plan.symbol.name)
            call.source.needs_intrinsics=True
            return f'{call.source.prefix}meta.type_proc_return_type(type_of({target}),0)'
        return render(annotation)

    def symbol_constant(self,symbol):
        return (symbol and not symbol.declaration.data.get('mutable',False) and not symbol.immutable
                and symbol.declaration.kind not in {'Field','Range','TypeSwitch'})

    def known(self,n,seen=None):
        if n is None:return True
        seen=set() if seen is None else seen
        if n in seen:return False
        seen.add(n)
        if n.kind=='Run':self.evaluate(n);return True
        if n.kind=='Directive':return n.data['name'] not in {'file','directory','line'}
        if n.kind in {'Literal','ImplicitSelector'} or n.text in {'true','false','nil'}:return True
        if n.source.denotes_type(n):
            # A generic type name is known only after specialization, even
            # though the parser correctly recognizes it as a type expression.
            return not any((symbol:=self.r.symbol(child)) and
                           symbol.declaration.kind in {'Field','PolyType','Range','TypeSwitch'}
                           for child in descendants(n) if child.kind in {'Ident','PolyType'})
        if n.kind in {'Ident','Selector'}:
            symbol=self.r.symbol(n)
            if symbol:
                return self.symbol_constant(symbol) and (symbol.value is None or self.known(symbol.value,seen))
            imported=self.purity.imported(n)
            if imported in self.purity.native_constants and n.source.import_dir(imported[0]) is None:return True
            if n.kind=='Ident':return n.text.startswith('ODIN_')
        if n.kind in {'Procedure','ProcedureGroup'}:return True
        if n.kind=='Call':
            builtin=self.purity.builtin(n)
            args=n.group('args')
            if self.purity.explicit_allocator(n):return False
            library=self.purity.library(n)
            if library and library[0]=='thor:format' and args:
                arg=args[0].role('value') if args[0].kind=='FieldValue' else args[0]
                if self.r.type_of(arg).kind not in {'scalar','enum','string'}:return False
            if n.source.denotes_type(n.role('callee')):
                typ=n.source.resolver.type_node(n.role('callee'))
                if typ.kind in {'pointer','procedure'} or typ.name in {'rawptr','cstring','any','typeid'}:return False
            if builtin=='make':
                kind=n.source.resolver.type_node(args[0]).kind if args else ''
                if kind not in {'slice','dynamic'} or len(args)>(3 if kind=='dynamic' else 2):return False
            if any(a.kind=='FieldValue' and a.role('field').text=='allocator' for a in args):return False
            if builtin=='#config':args=args[1:]
            targets=self.purity.targets(n)
            if not(builtin or self.purity.approved(n) or n.source.denotes_type(n.role('callee')) or targets):return False
            return all(self.known(a,seen.copy()) for a in args)
        if n.kind=='FieldValue':return self.known(n.role('value'),seen)
        if n.kind in {'Compound','Interpolation'}:
            if n.kind=='Compound' and self.purity.source_location_dependent(n.role('type')):return False
            return all(self.known(a,seen.copy()) for a in n.group('elems' if n.kind=='Compound' else 'args'))
        if n.kind in {'Paren','Unary','Cast'}:
            if n.kind=='Cast' and n.data['op']=='transmute' or n.kind=='Unary' and n.data['op']=='&':return False
            return self.known(n.role('expr'),seen)
        if n.kind=='Slice':return all(self.known(n.role(k),seen.copy()) for k in ['expr','low','high'])
        if n.kind=='Index':return self.known(n.role('expr'),seen.copy()) and self.known(n.role('index'),seen.copy())
        if n.kind=='Binary':
            left=n.role('left')
            if not self.known(left,seen.copy()):return False
            if n.data['op'] in {'&&','||'}:
                chosen=self.boolean(left)
                if (n.data['op']=='&&' and not chosen) or (n.data['op']=='||' and chosen):return True
            return self.known(n.role('right'),seen.copy())
        if n.kind in {'Ternary','WhenExpr'}:
            if not self.known(n.role('cond'),seen.copy()):return False
            selected=self.boolean(n.role('cond'))
            return self.known(n.role('x' if selected else 'y'),seen)
        return False

    def ensure_runs(self,n):
        for child in descendants(n):
            if child.kind=='Run' and child not in self.results and child not in self.skipped:self.evaluate(child)

    def boolean(self,n):
        if n.text in {'true','false'}:return n.text=='true'
        if n in self.guard_busy:self.fail(n,'Cyclic compile-time selection dependency.')
        self.guard_busy.add(n)
        try:
            self.ensure_runs(n)
            data=self.execute(n,condition=True)
            if data.get('kind')!='bool':self.fail(n,'Compile-time selection requires a boolean condition.')
            return data['value']
        finally:self.guard_busy.remove(n)

    def active(self,n):
        chain=list(n.ancestors())
        for parent in reversed(chain):
            if parent.kind in {'When','WhenExpr','Ternary','If'}:
                cond=parent.role('cond')
                if cond and cond.start<=n.start<n.end<=cond.end:continue
                if parent.kind in {'If','Ternary'} and not self.known(cond):continue
                if not cond:continue
                # Generic/runtime conditions are left to the native checker.
                if not self.known(cond):return None
                chosen=self.boolean(cond)
                yes=parent.role('body') if parent.kind in {'When','If'} else parent.role('x')
                no=parent.role('else') if parent.kind in {'When','If'} else parent.role('y')
                if (yes and yes.start<=n.start<n.end<=yes.end and not chosen or
                    no and no.start<=n.start<n.end<=no.end and chosen):
                    if parent.kind in {'When','WhenExpr'}:self.unchecked.add(n)
                    return False
            elif parent.kind=='Binary' and parent.data['op'] in {'&&','||'}:
                right=parent.role('right');left=parent.role('left')
                if right.start<=n.start<n.end<=right.end and self.known(left):
                    selected=self.boolean(left)
                    if (parent.data['op']=='&&' and not selected) or (parent.data['op']=='||' and selected):return False
            elif parent.kind in {'CompileSwitch','CompileSwitchExpr'}:
                selector=parent.role('selector')
                if selector and selector.start<=n.start<n.end<=selector.end:continue
                if selector and not self.known(selector):return None
                for case in parent.data['cases']:
                    if any(a<=n.start<n.end<=b for a,b in case['matches']):break
                    if not all(self.known(parent.source.at(span,parent)) for span in case['matches']):return None
                    selected=self.execute(parent,condition_case=case,extra=[parent.source.at(span,parent) for span in case['matches']]+([selector] if selector else []))
                    inside=case['start']<=n.start<n.end<=case['end']
                    if inside:
                        if not selected['value']:self.unchecked.add(n)
                        return bool(selected['value'])
                    if selected['value']:self.unchecked.add(n);return False
        return True

    def evaluate(self,n):
        if n in self.results:return self.results[n]
        if n in self.busy:self.fail(n,'Cyclic #run constant dependency.')
        self.busy.add(n)
        try:
            self.native(n)
            active=self.active(n)
            if active is None:
                self.skipped.add(n)
                return None # native selected instantiations diagnose unresolved #run
            if active is False:
                self.skipped.add(n)
                if n in self.unchecked:
                    n.source.run_values[n]='0' # discarded native arm: no value/type is observed
                    return None
                # Native inactive when arms leave this unchecked. Runtime lazy
                # operands still need their ordinary result type.
                n.source.needs_default=True
                n.source.run_values[n]=f'{self.result_type(n.role("expr"))}{{}}'
                return None
            call=n.role('expr')
            if call.kind!='Call':self.fail(n,'#run requires a direct eligible procedure call.')
            if not self.purity.targets(call):self.fail(n,'#run requires a resolved direct call; indirect and foreign calls are not eligible.')
            for arg in call.group('args'):
                if not self.known(arg):self.fail(arg,'#run arguments and required types must be compile-time known; runtime storage is not a constant.')
            self.ensure_runs(call)
            data=self.execute(call)
            self.results[n]=data;self.install(n,data)
            return data
        finally:self.busy.remove(n)

    def evaluate_all(self):
        for source in list(self.p.sources.values()):
            if source.excluded:continue
            for node in source.nodes:
                if node.kind=='Run' and node not in self.results and node not in self.skipped:self.evaluate(node)

    def closure(self,roots):
        keep=set();imports={};aliases={};procedures=set();visited=set()
        def declaration(node):
            if node in visited:return
            if node.kind=='Declaration' and (node.data.get('mutable',False) or node.data.get('immutable',False)) and node.source.scope(node).kind=='File':return
            visited.add(node);keep.add(node)
            for a in node.ancestors():
                if a.kind=='When':keep.add(a.role('cond'))
                if a.kind in {'CompileSwitch','CompileSwitchExpr'}:
                    if a.role('selector'):keep.add(a.role('selector'));visit(a.role('selector'))
                    for case in a.data['cases']:
                        for span in case['matches']:keep.add(a.source.at(span,a));visit(a.source.at(span,a))
            visit(node)
        def visit(node):
            if node is None:return
            if node.kind=='Run':
                self.evaluate(node);visit(node.role('expr'));return
            if node.kind=='Procedure':procedures.add(node)
            if node.kind=='Ident' and self.purity.reference(node):
                s=node.source
                if node.text in s.selected:aliases.setdefault(s,set()).add(node.text)
                if node.text in s.imports and self.r.lookup(node.text,node) is None:imports.setdefault(s,set()).add(node.text)
                symbol=self.r.symbol(node)
                if symbol and symbol.declaration is not node and not any(a is symbol.declaration for a in node.ancestors()):
                    # Locals of a retained procedure already belong to its body.
                    owner=self.purity.owner(symbol.declaration)
                    if owner is None or owner not in procedures:declaration(symbol.declaration)
            if node.kind=='Selector':
                symbol=self.r.symbol(node)
                if symbol:declaration(symbol.declaration)
            if node.kind=='Call' and not(self.purity.builtin(node) or self.purity.approved(node) or node.source.denotes_type(node.role('callee'))):
                targets=self.purity.targets(node)
                for proc in targets:
                    procedures.add(proc)
                    parent=proc.parent
                    if parent and parent.kind=='Declaration':declaration(parent)
            for child in node.children:visit(child)
        for node in roots:visit(node)
        # Selected aliases need the namespace import used by their rewritten AST.
        for source,names in aliases.items():
            for name in names:imports.setdefault(source,set()).add(source.selected[name].split('.')[0])
        return keep,imports,aliases,procedures

    def execute(self,n,*,condition=False,condition_case=None,extra=()):
        from .driver import Project
        error_site=n.parent if n.parent and n.parent.kind=='Run' else n
        roots=[n] if condition_case is None else list(extra)
        keep,imports,aliases,procedures=self.closure(roots)
        flags=[f for f in self.p.flags if not f.startswith(('-out:','-build-mode:','-vet','-target:')) and f not in {'-keep-executable','-no-entry-point'}]
        overlays={path:s.raw for path,s in self.p.sources.items()}
        helper=Project(self.p.entry,self.p.frontend,flags,overlays=overlays)
        helper.discover(self.root/'runtime'/'eval')
        by_original={old:new for path,old in self.p.sources.items() for new in [helper.sources[path]]}
        def node_copy(old):
            if old is None:return None
            source=by_original[old.source]
            return next((a for a in source.by_span.get((old.start,old.end),[]) if a.kind==old.kind),None)
        helper.purity.eval_mode=True
        helper.purity.active={node_copy(p) for p in procedures}
        for old,new in by_original.items():
            if old.path.is_relative_to(self.root/'runtime'):continue
            new.evaluation_keep={node_copy(k) for k in keep if k.source is old and
                                 (self.purity.owner(k) is None or self.purity.owner(k) in procedures)}
            new.evaluation_keep.discard(None)
            needed=imports.get(old,set())
            for alias in needed:
                path=old.imports.get(alias,'')
                if ':' in path and path.split(':',1)[0] in {'core','base','vendor'} and path not in self.native_imports and path.split(':',1)[0] not in self.p.collections:
                    imp=next((a for a in old.nodes if a.kind=='Import' and unquote(a.data['op'])==path),n)
                    self.fail(imp,f'#run cannot verify native package {path!r}; only approved native helper packages may enter compile-time execution.')
            for imp in new.nodes:
                if imp.kind=='Import' and (imp.data['name'] or unquote(imp.data['op']).rsplit('/',1)[-1].rsplit(':',1)[-1]) in needed:
                    new.evaluation_keep.add(imp)
            selected=aliases.get(old,set())
            new.selected={name:target for name,target in new.selected.items() if name in selected}
            new.selected_origins={name:pos for name,pos in new.selected_origins.items() if name in selected}
        # Reconstruct already evaluated values with this helper's mapping builder.
        old_p=self.p
        try:
            self.p=helper
            for old,data in self.results.items():
                # Unused materializers must not enter this closure: their
                # native result-type witnesses can reference pruned targets.
                if not any(root.source is old.source and root.start<=old.start and old.end<=root.end
                           for root in (*keep,*roots)):continue
                new=node_copy(old)
                if new:self.install(new,data)
        finally:self.p=old_p
        source=by_original[n.source];site=node_copy(n)
        # Local constant/type/procedure declarations are reintroduced in their
        # original nested lexical scopes inside the helper main.
        locals_by_scope={}
        for old in keep:
            owner=self.purity.owner(old)
            if old.kind=='Declaration' and owner and owner not in procedures:
                scope=old.source.scope(old)
                locals_by_scope.setdefault(scope,[]).append(node_copy(old))
        if condition_case is not None:
            old=source.evaluation_keep;source.evaluation_keep=None
            try:expr=source.expressions.switch_condition(site,condition_case)
            finally:source.evaluation_keep=old
        else:
            old=source.evaluation_keep;source.evaluation_keep=None
            try:expr=source.render(site)
            finally:source.evaluation_keep=old
        alias=source.prefix+'evaluation'
        emitted='true when ('+expr+') else false' if condition or condition_case is not None else expr
        result_name=source.prefix+'evaluation_result'
        output=f'{result_name}:={emitted}\n{alias}.emit({result_name})\n{helper.purity.module(source)}.destroy({result_name})'
        inner=output
        for scope in n.ancestors():
            if scope.source.is_scope(scope) and scope.kind!='File':
                old=source.evaluation_keep;source.evaluation_keep=None
                try:declarations='\n'.join(source.render(d) for d in sorted(locals_by_scope.get(scope,[]),key=lambda d:d.start))
                finally:source.evaluation_keep=old
                inner='{\n'+source.expressions.declarations(node_copy(scope))+declarations+'\n'+inner+'\n}'
        module=helper.purity.module(source);scratch=source.prefix+'evaluation_scratch'
        setup=(f'{scratch}:{module}.Scratch\n{module}.scratch_init(&{scratch})\n'
               f'defer {module}.scratch_destroy(&{scratch})\ncontext.allocator={module}.scratch_allocator(&{scratch})\n'
               'context.temp_allocator=context.allocator\n')
        addition=f'\nimport {alias} "thor:eval"\nmain :: proc() {{\n{setup}{inner}\n}}\n'
        source.expressions.helpers[source.root,alias]=source.mapping.wrap(addition,source.path,n.start,n.end,'run-helper')
        # The helper root may be a library package; give its entry package an
        # executable name without changing import paths or user sources.
        entry_dir=source.path.parent
        for unit in helper.packages.get(entry_dir,[]):
            span=unit.root.data['roles'].get('package')
            if span:unit.edits.append((*span,'main'))
        with tempfile.TemporaryDirectory(prefix='thor-run-') as directory:
            directory=Path(directory)
            helper.emit(directory/'source')
            target=helper.outputs[source.path].parent
            executable=directory/('helper.exe' if sys.platform=='win32' else 'helper')
            command=[self.odin,'build',str(target),*helper.odin_flags(),f'-out:{executable}','-json-errors']
            key=self.cache.key(self.p,helper,target,self.odin,self.root) if self.cache else None
            cached=self.cache.get(key) if self.cache else None
            if cached is not None:return cached
            try:built=subprocess.run(command,capture_output=True,timeout=max(30,self.timeout))
            except subprocess.TimeoutExpired:self.fail(error_site,'#run helper compilation timed out.')
            diagnostics=Diagnostics(helper.sources,helper.maps,helper.generated)
            if built.returncode:
                try:errors=diagnostics.translate(json.loads(built.stderr).get('errors',[]))
                except ValueError:errors=[]
                if errors:raise ThorError('Compile-time helper compilation failed.',diagnostics=errors)
                self.fail(error_site,'#run helper compilation failed: '+built.stderr.decode(errors='replace')[:1000])
            try:result=subprocess.run([str(executable)],capture_output=True,timeout=self.timeout)
            except subprocess.TimeoutExpired:self.fail(error_site,f'#run execution exceeded the {self.timeout:g} second timeout.')
            if result.returncode:
                message=result.stderr.decode(errors='replace').strip().splitlines()
                detail=next((line for line in message if 'panic' in line.lower() or 'assertion' in line.lower()),'')
                detail=re.sub(r'^.*?((?:runtime )?(?:assertion|panic)(?: failure)?\s*:)',r'\1',detail,flags=re.I)
                detail=re.sub(r'__thor_\w+','Thor expression',detail)
                self.fail(error_site,f'#run execution failed (exit {result.returncode}).'+(' '+detail[:300] if detail else ''))
            try:
                value=values.decode(result.stdout)
                values.expression(value,'T','meta')
            except values.ValueErrorProtocol as exc:self.fail(error_site,str(exc))
            if self.cache:
                if key and self.cache.key(self.p,helper,target,self.odin,self.root)!=key:
                    self.fail(error_site,'#run resource inputs changed during evaluation; compile again with stable inputs.')
                self.cache.put(key,value)
            return value

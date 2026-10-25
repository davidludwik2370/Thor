"""Bounded native constant evaluation using verified dependency closures.

Each helper is an isolated throw-away project with source maps. Only necessary
declarations/imports enter it; user init/fini and entry points are never copied.
Explicit #run and known-argument fn calls share contents caching and ownership.
"""
import json
import os
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
from .specialization import Specializations, Unresolved, type_code


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
        self.explicit_arguments=0
        self.specializations=Specializations(self)
        self.result_bindings={}
        self.r.evaluated_integer=self.constant_integer
        self.r.evaluation_active=True

    def key(self,n):return self.specializations.key(n)

    def constant_integer(self,n,seen=None):
        seen=set() if seen is None else seen
        if n in seen:return None
        seen.add(n)
        key=self.key(n)
        if key not in self.results and key not in self.busy and (n.kind=='Run' or self.automatic(n)):self.evaluate(n)
        data=self.results.get(key)
        if data and data['kind']=='integer':return int(data['value'])
        symbol=self.r.symbol(n)
        if self.symbol_constant(symbol) and symbol.value:return self.constant_integer(symbol.value,seen)
        if n.kind in {'Paren','Unary'}:
            value=self.r.constant_int(n.role('expr'))
            if value is not None and (n.kind=='Paren' or n.data['op'] in {'+','-'}):return -value if n.kind=='Unary' and n.data['op']=='-' else value
        if n.kind=='Binary' and n.data['op'] in {'+','-','*'}:
            left=self.r.constant_int(n.role('left'));right=self.r.constant_int(n.role('right'))
            if left is not None and right is not None:return {'+':lambda:left+right,'-':lambda:left-right,'*':lambda:left*right}[n.data['op']]()
        return None

    def unbound_generic(self,n):
        for owner in n.ancestors():
            if owner.kind!='Procedure':continue
            parameters=self.r.generic_parameters(owner.role('type'))
            symbols=self.r.scopes.get(owner,{})
            if any(name not in symbols or (symbols[name][0].declaration,name) not in self.specializations.bindings
                   for name in parameters):return True
        return False

    @staticmethod
    def call(n):return n.role('expr') if n.kind=='Run' else n

    def automatic(self,n):
        if n.kind!='Call' or n.parent and n.parent.kind=='Run':return False
        if any(a.kind=='Call' and self.purity.builtin(a) in self.purity.type_queries for a in n.ancestors()):return False
        symbol=self.r.symbol(n.role('callee'))
        alternatives=symbol.candidates if symbol else []
        if len(alternatives)>1 and any(any(a.kind in {'When','CompileSwitch'} for a in s.declaration.ancestors()) for s in alternatives):
            selected=[s.value for s in alternatives if s.value and s.value.kind=='Procedure' and self.active(s.value) is True]
            return len(selected)==1 and self.purity.pure(selected[0]) and len(self.r.fields(selected[0].role('type').role('results')))==1
        targets=self.purity.targets(n)
        if len(targets)!=1 or not self.purity.pure(targets[0]):return False
        return len(self.r.fields(targets[0].role('type').role('results')))==1

    def fail(self,n,message):n.source.error(message,n.start,n.end)

    def native(self,n):
        if self.p.target:
            host_os={'darwin':'darwin','linux':'linux','win32':'windows'}.get(sys.platform,sys.platform)
            host_arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine().lower(),platform.machine().lower())
            if tuple(self.p.target)!=(host_os,host_arch):self.fail(n,'Compile-time execution supports native targets only; host execution cannot evaluate a cross-target program.')

    def install(self,n,data,bindings=None):
        s=n.source;s.needs_intrinsics=s.needs_default=True
        bindings=self.specializations.relevant(n) if bindings is None else bindings
        with self.specializations.using({}):typ=self.result_type(self.call(n))
        try:code=values.expression(data,typ,s.prefix+'meta')
        except (ValueError,TypeError,KeyError) as exc:self.fail(n,f'Invalid compile-time result: {exc}')
        literal=s.mapping.wrap(code,s.path,n.start,n.end,'compile-time-result')
        if values.has_owned(data):
            module=self.p.purity.module(s)
            def body(_):return values.materialize(data,s.prefix+'Result',s.prefix+'meta',module,s.prefix+'materialized_'+str(n.start)+'_')
            name,args=s.expressions.closed(n,body,result=None,flavor='run_value_'+self.specializations.fingerprint(bindings))
            args.append(typ)
            runtime=f'{name}({", ".join(args)})'
            label='#run' if n.kind=='Run' else 'fn'
            constant=(s.expressions.error(n,f'A slice-containing {label} result needs a runtime binding (:=); use a pure consumer to compute a scalar constant.')
                      if values.has_owned(data,slices_only=True) else literal)
            value={'runtime':runtime,'constant':constant}
        else:value={'runtime':literal,'constant':literal}
        self.register(n,value,bindings)

    def register(self,n,value,bindings):
        s=n.source
        if not bindings:s.run_values[n]=value;return
        cases=getattr(s,'evaluation_cases',None)
        if cases is None:s.evaluation_cases=cases={}
        cases.setdefault(n,{})[self.specializations.fingerprint(bindings)]=(self.specializations.predicate(n,bindings),value)
        def render(expected,constant):
            if n.kind=='Run' or constant:
                code=s.expressions.error(n,'Compile-time evaluation requires resolved generic types and constant parameters at this specialization.')
            else:
                saved=s.run_values.pop(n)
                try:code=s._render(n,expected,constant)
                finally:s.run_values[n]=saved
            for predicate,value in reversed(list(cases[n].values())):
                code='('+value['constant' if constant else 'runtime']+' when ('+predicate+') else '+code+')'
            return code
        s.run_values[n]=render

    def result_type(self,call):
        plan=call.source.resolver.resolve_call(call)
        if not plan:self.fail(call,'Compile-time result type requires an unambiguous resolved procedure target.')
        if len(self.r.fields(plan.signature.role('results')))!=1:
            self.fail(call,'Compile-time execution requires exactly one supported result value.')
        source=call.source;source.needs_intrinsics=True
        saved=source.run_values.pop(call,None)
        keep=source.evaluation_keep;source.evaluation_keep=None
        queries={}
        owner=None
        for ancestor in call.ancestors():
            if ancestor.kind=='Procedure':break
            if ancestor.kind=='Declaration' and not ancestor.data.get('mutable',True):
                owner=ancestor;break
        for node in descendants(call):
            query=self.type_query(node)
            # Native constant declarations cannot name local runtime storage,
            # even inside type queries. Preserve native validation separately
            # before substituting those queries; all other witnesses retain
            # the original expression and its type/bounds/arity checks.
            local=any((symbol:=source.expressions.variable(child)) and symbol.scope.kind!='File'
                      for child in descendants(node)) if query else False
            if query and owner and owner.parent.kind in {'Block','Case'} and local:
                queries[node]=source.run_values.get(node)
                source.run_values[node]=query[0]
                if not source.purity.eval_mode:source.evaluation_query_checks.setdefault(owner,{})[node]=None
        try:code=source._render(call)
        finally:
            source.evaluation_keep=keep
            if saved is not None:source.run_values[call]=saved
            for node,value in queries.items():
                if value is None:source.run_values.pop(node,None)
                else:source.run_values[node]=value
        meta=source.prefix+'meta'
        # Native procedure_of resolves overloads and generic return types without
        # executing the call or exposing private/local type names across packages.
        return f'{meta}.type_proc_return_type(type_of({meta}.procedure_of({code})),0)'

    def symbol_constant(self,symbol):
        return (symbol and not symbol.declaration.data.get('mutable',False) and not symbol.immutable
                and symbol.declaration.kind not in {'Field','Range','TypeSwitch'})

    def type_query(self,n):
        if n.kind!='Call' or self.purity.builtin(n) not in self.purity.type_queries:return None
        args=n.group('args')
        if len(args)!=(2 if self.purity.builtin(n)=='offset_of' else 1):return None
        arg=args[0]
        typ=self.r.type_node(arg) if arg.source.denotes_type(arg) else self.r.type_of(arg)
        if typ.kind in {'generic','unknown'}:return None
        n.source.evaluation_context=n
        try:code=type_code(typ,n.source,self.r)
        except Unresolved:return None
        builtin=self.purity.builtin(n)
        if builtin!='type_of':code=builtin+'('+code+''.join(','+a.text for a in args[1:])+')'
        return code,typ

    def known(self,n,seen=None):
        if n is None:return True
        seen=set() if seen is None else seen
        if n in seen:return False
        seen.add(n)
        if self.key(n) in self.results:return True
        if self.key(n) in self.busy:self.fail(n,'Cyclic #run or automatic fn constant dependency.')
        if n.kind=='Run':self.evaluate(n);return True
        if n.kind=='Directive':return n.data['name'] not in {'file','directory','line'}
        if n.kind in {'Literal','ImplicitSelector'} or n.text in {'true','false','nil'}:return True
        if self.type_query(n):return True
        if n.source.denotes_type(n):
            # A generic type name is known only after specialization, even
            # though the parser correctly recognizes it as a type expression.
            return not any((symbol:=self.r.symbol(child)) and
                           symbol.declaration.kind in {'Field','PolyType','Range','TypeSwitch'} and
                           (symbol.declaration,symbol.name) not in self.specializations.bindings
                           for child in descendants(n) if child.kind in {'Ident','PolyType'})
        if n.kind in {'Ident','Selector'}:
            symbol=self.r.symbol(n)
            if symbol:
                if (symbol.declaration,symbol.name) in self.specializations.bindings:return True
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
            if targets and not self.explicit_arguments and not all(self.purity.pure(p) for p in targets):return False
            ready=all(self.known(a,seen.copy()) for a in args)
            if ready and self.automatic(n):self.evaluate(n)
            return ready
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
        if n.kind in {'SwitchExpr','CompileSwitchExpr'}:
            selector=n.role('selector')
            if selector and not self.known(selector,seen.copy()):return False
            for case in n.data['cases']:
                matches=[n.source.at(span,n) for span in case['matches']]
                if not all(self.known(m,seen.copy()) for m in matches):return False
                selected=self.execute(n,condition_case=case,extra=matches+([selector] if selector else []))
                if selected['value']:return self.known(n.source.at(case['result'],n),seen)
            return False
        return False

    def ensure_runs(self,n):
        for child in descendants(n):
            if child.kind=='Run' and self.key(child) not in self.results and self.key(child) not in self.skipped:self.evaluate(child)
            elif self.automatic(child) and self.key(child) not in self.busy:self.evaluate(child)

    def boolean(self,n):
        if n.text in {'true','false'}:return n.text=='true'
        key=self.key(n)
        if key in self.guard_busy:self.fail(n,'Cyclic compile-time selection dependency.')
        self.guard_busy.add(key)
        try:
            self.ensure_runs(n)
            data=self.execute(n,condition=True)
            if data.get('kind')!='bool':self.fail(n,'Compile-time selection requires a boolean condition.')
            return data['value']
        finally:self.guard_busy.remove(key)

    def active(self,n,*,specialization=False):
        chain=list(n.ancestors())
        for parent in reversed(chain):
            if specialization and parent.kind in {'If','Ternary','Binary','For','Range','SwitchExpr'}:continue
            if parent.kind in {'When','WhenExpr','Ternary','If'}:
                cond=parent.role('cond')
                if cond and cond.start<=n.start<n.end<=cond.end:continue
                if parent.kind in {'If','Ternary'} and not self.known(cond):
                    if n.kind!='Run':return None
                    continue
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
                if right.start<=n.start<n.end<=right.end and n.kind!='Run' and not self.known(left):return None
                if right.start<=n.start<n.end<=right.end and self.known(left):
                    selected=self.boolean(left)
                    if (parent.data['op']=='&&' and not selected) or (parent.data['op']=='||' and selected):return False
            elif parent.kind in {'For','Range'} and n.kind!='Run':
                body=parent.role('body')
                post=parent.role('post') if parent.kind=='For' else None
                if any(part and part.start<=n.start<n.end<=part.end for part in (body,post)):
                    cond=parent.role('cond') if parent.kind=='For' else None
                    if cond is None or not self.known(cond):return None
                    if not self.boolean(cond):return False
            elif parent.kind in {'CompileSwitch','CompileSwitchExpr','SwitchExpr'}:
                selector=parent.role('selector')
                if selector and selector.start<=n.start<n.end<=selector.end:continue
                if selector and not self.known(selector):return None
                for case in parent.data['cases']:
                    if any(a<=n.start<n.end<=b for a,b in case['matches']):break
                    if not all(self.known(parent.source.at(span,parent)) for span in case['matches']):return None
                    selected=self.execute(parent,condition_case=case,extra=[parent.source.at(span,parent) for span in case['matches']]+([selector] if selector else []))
                    inside=case['start']<=n.start<n.end<=case['end']
                    if inside:
                        if not selected['value'] and parent.kind!='SwitchExpr':self.unchecked.add(n)
                        return bool(selected['value'])
                    if selected['value']:
                        if parent.kind!='SwitchExpr':self.unchecked.add(n)
                        return False
        return True

    def evaluate(self,n):
        if self.unbound_generic(n):return None
        key=self.key(n)
        if key in self.results:return self.results[key]
        if key in self.busy:self.fail(n,'Cyclic #run or automatic fn constant dependency.')
        self.busy.add(key)
        try:
            if n.kind=='Run':self.native(n)
            active=self.active(n)
            if active is None:
                self.skipped.add(key)
                return None # native selected instantiations diagnose unresolved #run
            if active is False:
                self.skipped.add(key)
                if n in self.unchecked:
                    if not self.specializations.relevant(n):n.source.run_values[n]='0'
                    return None
                # Native inactive when arms leave this unchecked. Runtime lazy
                # operands still need their ordinary result type.
                n.source.needs_default=True
                typ=self.result_type(self.call(n))
                value={'runtime':f'({typ}{{}})','constant':f'({typ}{{}})'}
                self.register(n,value,self.specializations.relevant(n))
                return None
            call=self.call(n)
            if call.kind!='Call':self.fail(n,'#run requires a direct eligible procedure call.')
            if not self.purity.targets(call):self.fail(n,'#run requires a resolved direct call; indirect and foreign calls are not eligible.')
            if n.kind=='Run':self.explicit_arguments+=1
            try:
                for arg in call.group('args'):
                    if not self.known(arg):
                        if n.kind!='Run':return None
                        owner=self.purity.owner(n)
                        if owner and self.r.generic_parameters(owner.role('type')) and not self.specializations.relevant(n):return None
                        self.fail(arg,'#run arguments and required types must be compile-time known; runtime storage is not a constant.')
            finally:
                if n.kind=='Run':self.explicit_arguments-=1
            self.native(n)
            self.specializations.process(call)
            self.ensure_runs(call)
            data=self.execute(call)
            self.results[key]=data;self.result_bindings[key]=self.specializations.relevant(n).copy();self.install(n,data)
            return data
        finally:self.busy.remove(key)

    def evaluate_all(self):
        if not any(n.kind=='Run' or self.purity.pure(n) for source in self.p.sources.values() if not source.excluded for n in source.nodes):return
        for source in list(self.p.sources.values()):
            if source.excluded:continue
            for node in source.nodes:
                if (node.kind=='Run' or self.automatic(node)) and self.key(node) not in self.results and self.key(node) not in self.skipped:self.evaluate(node)
                if node.kind=='Call':self.specializations.process(node)
        self.resolve_local_identities()

    def resolve_local_identities(self):
        """Ask the native checker for nominal identities, without running code.

        Helpers reintroduce local type declarations in a different procedure.
        Their canonical names cannot be reused in the actual program. Probe the
        original scopes, retaining all selected generic instances and overlays.
        Only tagged metadata is read; final checking still reports user errors.
        """
        from .driver import Project
        pending={}
        for bindings in self.result_bindings.values():
            for b in bindings.values():
                node=b.typ.nominal or b.typ.node
                declaration=node.parent if node else None
                if b.value is None and b.canonical and declaration and declaration.kind=='Declaration':
                    declaration.source.evaluation_context=node
                    try:code=type_code(b.typ,declaration.source,self.r)
                    except Unresolved:self.fail(b.symbol.node,'Cannot name the nominal type in its defining scope.')
                    key=node,b.condition,code
                    pending.setdefault(key,[]).append(b)
        if not pending:return
        flags=[f for f in self.p.flags if not f.startswith(('-out:','-build-mode:')) and f!='-keep-executable']
        probe=Project(self.p.entry,self.p.frontend,flags,test=self.p.test,overlays={p:s.raw for p,s in self.p.sources.items()})
        def copy(node):return next((n for n in probe.sources[node.source.path].by_span.get((node.start,node.end),[]) if n.kind==node.kind),None)
        old_p=self.p
        try:
            self.p=probe
            for key,data in self.results.items():
                if (node:=copy(key[0])):self.install(node,data,self.result_bindings[key])
        finally:self.p=old_p
        markers={}
        for i,((node,condition,code),bindings) in enumerate(pending.items()):
            source=probe.sources[node.source.path];declaration=copy(node.parent)
            scope=source.scope(declaration)
            marker='THOR_TYPE_ID_'+str(i)+':'
            source.needs_intrinsics=True
            message=json.dumps(marker)+'+'+source.prefix+'meta.type_canonical_name('+code+')'
            code='#panic('+message+')'
            if condition:code='when '+condition+'{'+code+'}'
            source.expressions.helpers[scope,marker]=source.mapping.wrap('\n'+code+'\n',source.path,node.start,node.end,'type-probe')
            markers[marker]=bindings
        with tempfile.TemporaryDirectory(prefix='thor-type-probe-') as directory:
            target=probe.emit(Path(directory)/'source')
            command=[self.odin,'build' if probe.test else 'check',str(target),*probe.odin_flags(),'-json-errors','-max-error-count:10000']
            if probe.test:command.extend(['-build-mode:test',f'-out:{directory}/probe'])
            if probe.file_entry:command.append('-file')
            try:result=subprocess.run(command,capture_output=True,timeout=30)
            except subprocess.TimeoutExpired:self.fail(next(iter(pending))[0],'Generic type identity checking timed out.')
            try:errors=json.loads(result.stderr).get('errors',[])
            except ValueError:errors=[]
            found={}
            for error in errors:
                for message in error.get('msgs',[]):
                    for marker in markers:
                        if marker in message:found.setdefault(marker,set()).add(message.split(marker,1)[1].strip())
            for marker,bindings in markers.items():
                names=found.get(marker,set())
                if len(names)!=1:self.fail(bindings[0].symbol.node,'Cannot resolve the nominal type for this compile-time specialization.')
                for b in bindings:b.canonical=next(iter(names))
        # Canonical local identities include generated byte positions. Probe
        # statements shift later scopes, so translate their positions through
        # the same source maps used for diagnostics, then settle digit widths.
        originals={id(b):b.canonical for group in pending.values() for b in group}
        def scope_offset(source,scope):
            for a,_,file,start,end,_,exact in source.source_map.segments:
                if exact and file==str(scope.source.path) and start<=scope.start<end:return a+scope.start-start+1
            return None
        for _ in range(12):
            for source in self.p.sources.values():source.transpile()
            changed=False
            for (node,_,_),group in pending.items():
                offsets={}
                for scope in node.ancestors():
                    if not scope.source.is_scope(scope) or scope.kind in {'Procedure','File'}:continue
                    before=scope_offset(probe.sources[scope.source.path],scope)
                    after=scope_offset(self.p.sources[scope.source.path],scope)
                    if before is not None and after is not None:offsets[str(before)]=str(after)
                for b in group:
                    name=re.sub(r'::\$(\d+)',lambda m:'::$'+offsets.get(m[1],m[1]),originals[id(b)])
                    changed |= name!=b.canonical;b.canonical=name
            for key,data in self.results.items():self.install(key[0],data,self.result_bindings[key])
            if not changed:break
        else:self.fail(next(iter(pending))[0],'Local type identity scheduling did not converge.')

    def closure(self,roots):
        keep=set();imports={};aliases={};procedures=set();visited=set();queries={}
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
            query=self.type_query(node)
            if query:
                code,typ=query;queries[node]=code
                for namespace in node.source.imports:
                    if node.source.namespace_code(namespace)+'.' in code:imports.setdefault(node.source,set()).add(namespace)
                if typ.node and typ.node is not node:
                    parent=typ.node.parent
                    if parent and parent.kind=='Declaration':declaration(parent)
                    elif typ.node.kind.endswith('Type'):visit(typ.node)
                return
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
                    for alternative in symbol.candidates:
                        if alternative is not symbol and any(a.kind in {'When','CompileSwitch'} for a in alternative.declaration.ancestors()):
                            declaration(alternative.declaration)
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
        for node in roots:
            if node.kind=='Declaration':declaration(node)
            else:visit(node)
        # Selected aliases need the namespace import used by their rewritten AST.
        for source,names in aliases.items():
            for name in names:imports.setdefault(source,set()).add(source.selected[name].split('.')[0])
        return keep,imports,aliases,procedures,queries

    def execute(self,n,*,condition=False,condition_case=None,extra=()):
        from .driver import Project
        error_site=n.parent if n.parent and n.parent.kind=='Run' else n
        label='#run' if error_site.kind=='Run' else 'Compile-time'
        roots=[n] if condition_case is None else list(extra)
        bindings=self.specializations.relevant(n)
        roots += [b.typ.node.parent if b.typ.node.parent and b.typ.node.parent.kind=='Declaration' and
                  b.typ.node.kind in {'StructType','UnionType','EnumType','DistinctType','BitSetType'} else b.typ.node
                  for b in bindings.values() if b.typ.node]
        keep,imports,aliases,procedures,queries=self.closure(roots)
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
        for query,code in queries.items():
            node=node_copy(query)
            node.source.run_values[node]=code
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
                    self.fail(imp,f'Compile-time execution cannot verify native package {path!r}; unverified native packages may not enter helpers.')
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
            for result_key,data in self.results.items():
                old,_=result_key
                # Unused materializers must not enter this closure: their
                # native result-type witnesses can reference pruned targets.
                if not any(root.source is old.source and root.start<=old.start and old.end<=root.end
                           for root in (*keep,*roots)):continue
                new=node_copy(old)
                if new:
                    retained=new.source.evaluation_keep;new.source.evaluation_keep=None
                    try:self.install(new,data,self.result_bindings[result_key])
                    finally:new.source.evaluation_keep=retained
        finally:self.p=old_p
        source=by_original[n.source];site=node_copy(n)
        source.evaluation_context=n
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
        output=f'{result_name}:={emitted}\n{alias}.emit({result_name})'
        inner=output
        for scope in n.ancestors():
            if scope.source.is_scope(scope) and scope.kind!='File':
                old=source.evaluation_keep;source.evaluation_keep=None
                try:declarations='\n'.join(source.render(d) for d in sorted(locals_by_scope.get(scope,[]),key=lambda d:d.start))
                finally:source.evaluation_keep=old
                inner='{\n'+source.expressions.declarations(node_copy(scope))+declarations+'\n'+inner+'\n}'
        bridge=False
        if bindings:
            try:prelude=self.specializations.declarations(source,bindings)
            except Unresolved:bridge=True
            if not bridge:inner='{\n'+prelude+inner+'\n}'
        entry_source=source
        if bridge:
            origin=None;origin_bindings={}
            for candidate,caller_bindings in reversed(self.specializations.frames):
                entry_candidate=by_original[candidate.source];entry_candidate.evaluation_context=candidate
                try:
                    for b in bindings.values():type_code(b.typ,entry_candidate,self.r)
                except Unresolved:continue
                origin=candidate;origin_bindings=caller_bindings;break
            if origin is None:self.fail(n,'The generic specialization has no concrete call site.')
            entry_source=by_original[origin.source]
            entry_source.evaluation_context=origin
            def imported(target):
                if target.path.parent==entry_source.path.parent:return ''
                alias=entry_source.expressions.module('evaluation_dependency_'+target.prefix.strip('_'),os.path.relpath(target.path.parent,entry_source.path.parent))
                return alias+'.'
            # These imports belong only to the isolated helper. No reverse
            # dependency is introduced into the user's generic library.
            entry_source.evaluation_import=imported
            arguments=[];parameters=[];type_bindings=[]
            for b in bindings.values():
                if b.value is None:
                    parameters.append('$'+b.symbol.name+':typeid');type_bindings.append(b)
                    arguments.append(type_code(b.typ,entry_source,self.r))
                else:
                    parameters.append('$'+b.symbol.name+':'+(self.r.template(b.symbol.annotation) if b.symbol.annotation else type_code(b.typ,source,self.r)))
                    arguments.append(values.expression(b.value,type_code(b.typ,entry_source,self.r),entry_source.prefix+'meta'))
            names=source.prefix+'binding_names'
            source.needs_intrinsics=True
            metadata=f'{names}:=[{len(type_bindings)}]string{{'+','.join(source.prefix+'meta.type_canonical_name('+b.symbol.name+')' for b in type_bindings)+'}\n'
            inner=inner.replace(f'{alias}.emit({result_name})',metadata+f'{alias}.emit_specialized({result_name},{names}[:])')
            bridge_name=source.prefix+'specialized_evaluation_'+str(n.start)
            bridge_code=f'\nimport {alias} "thor:eval"\n{bridge_name}::proc('+','.join(parameters)+'){\n'+inner+'\n}\n'
            source.expressions.helpers[source.root,bridge_name]=source.mapping.wrap(bridge_code,source.path,n.start,n.end,'run-helper')
            inner=imported(source)+bridge_name+'('+','.join(arguments)+')'
            # Retain lexical type declarations at the original concrete site.
            for scope in origin.ancestors():
                if scope.source.is_scope(scope) and scope.kind!='File':
                    old=entry_source.evaluation_keep;entry_source.evaluation_keep=None
                    try:declarations='\n'.join(entry_source.render(d) for d in sorted(locals_by_scope.get(scope,[]),key=lambda d:d.start))
                    finally:entry_source.evaluation_keep=old
                    inner='{\n'+declarations+'\n'+inner+'\n}'
            if origin_bindings:
                inner='{\n'+self.specializations.declarations(entry_source,origin_bindings)+inner+'\n}'
        module=helper.purity.module(entry_source);scratch=entry_source.prefix+'evaluation_scratch'
        setup=(f'{scratch}:{module}.Scratch\n{module}.scratch_init(&{scratch})\n'
               f'defer {module}.scratch_destroy(&{scratch})\ncontext.allocator={module}.scratch_allocator(&{scratch})\n'
               'context.temp_allocator=context.allocator\n')
        addition=('' if bridge else f'\nimport {alias} "thor:eval"\n')+f'main :: proc() {{\n{setup}{inner}\n}}\n'
        entry_source.expressions.helpers[entry_source.root,alias]=entry_source.mapping.wrap(addition,source.path,n.start,n.end,'run-helper')
        # The helper root may be a library package; give its entry package an
        # executable name without changing import paths or user sources.
        entry_dir=entry_source.path.parent
        for unit in helper.packages.get(entry_dir,[]):
            span=unit.root.data.get('roles',{}).get('package')
            if span:unit.edits.append((*span,'main'))
        with tempfile.TemporaryDirectory(prefix='thor-run-') as directory:
            directory=Path(directory)
            helper.emit(directory/'source')
            target=helper.outputs[entry_source.path].parent
            executable=directory/('helper.exe' if sys.platform=='win32' else 'helper')
            command=[self.odin,'build',str(target),*helper.odin_flags(),f'-out:{executable}','-json-errors']
            key=self.cache.key(self.p,helper,target,self.odin,self.root) if self.cache else None
            cached=self.cache.get(key) if self.cache else None
            def unpack(data):
                if data.get('kind')!='specialized':return data
                for binding,name in zip((b for b in bindings.values() if b.value is None),data['types']):binding.canonical=bytes.fromhex(name['bytes']).decode('utf-8')
                return data['value']
            if cached is not None:return unpack(cached)
            try:built=subprocess.run(command,capture_output=True,timeout=max(30,self.timeout))
            except subprocess.TimeoutExpired:self.fail(error_site,label+' helper compilation timed out.')
            diagnostics=Diagnostics(helper.sources,helper.maps,helper.generated)
            if built.returncode:
                try:errors=diagnostics.translate(json.loads(built.stderr).get('errors',[]))
                except ValueError:errors=[]
                if errors:raise ThorError('Compile-time helper compilation failed.',diagnostics=errors)
                self.fail(error_site,label+' helper compilation failed: '+built.stderr.decode(errors='replace')[:1000])
            try:result=subprocess.run([str(executable)],capture_output=True,timeout=self.timeout)
            except subprocess.TimeoutExpired:self.fail(error_site,f'{label} execution exceeded the {self.timeout:g} second timeout.')
            if result.returncode:
                message=result.stderr.decode(errors='replace').strip().splitlines()
                detail=next((line for line in message if 'panic' in line.lower() or 'assertion' in line.lower()),'')
                detail=re.sub(r'^.*?((?:runtime )?(?:assertion|panic)(?: failure)?\s*:)',r'\1',detail,flags=re.I)
                detail=re.sub(r'__thor_\w+','Thor expression',detail)
                self.fail(error_site,f'{label} execution failed (exit {result.returncode}).'+(' '+detail[:300] if detail else ''))
            try:
                value=values.decode(result.stdout)
                values.expression(value,'T','meta')
            except values.ValueErrorProtocol as exc:self.fail(error_site,str(exc))
            if self.cache:
                if key and self.cache.key(self.p,helper,target,self.odin,self.root)!=key:
                    self.fail(error_site,'#run resource inputs changed during evaluation; compile again with stable inputs.')
                self.cache.put(key,value)
            return unpack(value)

"""Compare symbolic helper requirements with Python using the real Odin AST."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from thorlang.compiler import Source
from thorlang.driver import frontend_binary
from thorlang.semantics import Resolver
from thorlang.purity import Purity
from thorlang.pureborrow import Permission,OWNED
from tests import test_rust_compiler as native


def location(node,qualified):return [str(node.source.path),node.start] if qualified else node.start

def access(value,qualified=False):return [location(value.proc,qualified),value.index,list(map(list,value.path)),value.aspect]


def permission(value,qualified=False):
    kind,args=value.kind,value.args
    if kind=='constant':return [kind,args[0]]
    if kind=='access':return [kind,access(args[0],qualified)]
    if kind=='type':return [kind,args[0],*([str(args[1].source.path)] if qualified else []),args[1].start,args[1].end]
    if kind=='and':return [kind,*[permission(arg,qualified) for arg in args]]
    return [kind,[permission(arg,qualified) for arg in args]]


def canonical(packet):
    for profile in packet['profiles']:profile[2].sort(key=lambda field:json.dumps(field[0]))
    packet['profiles'].sort(key=lambda profile:profile[:2])
    packet['mutates'].sort();packet['escapes'].sort()
    return packet


class RustBorrowModelTests(unittest.TestCase):
    maxDiff=None
    @classmethod
    def setUpClass(cls):
        result=subprocess.run(['cargo','build','--release','--locked','--offline','--manifest-path',
            str(native.ROOT/'rust/Cargo.toml'),'--example','borrow_protocol'],
            capture_output=True,text=True,timeout=120)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        cls.frontend=frontend_binary('odin')[0]

    def compare_model(self,prefix,body,params='n:int',converged=True,sdk=False,clone_sequence_guard=False):
        text='package main\n'+prefix+'\nwork::fn('+params+')->int{'+body+'}\nmain::proc(){}\n'
        with tempfile.TemporaryDirectory(prefix='thor-borrow-model-') as directory:
            entry=Path(directory)/'main.thor';entry.write_text(text)
            source=Source(entry,self.frontend,cache=False)
            if sdk:
                from thorlang.driver import Project
                from thorlang.native import NativeSources
                project=Project(entry,self.frontend,['-debug'],cache_frontend=False)
                project.native_sources=NativeSources(project)
                project.resolver.native=project.native_sources
                resolver,pure=project.resolver,project.purity
            else:
                resolver=Resolver([source]);pure=Purity(SimpleNamespace(resolver=resolver,sources={source.path:source}))
            pure.calls.prepare()
            qualified=len(resolver.sources)>1
            checks={}
            for node,groups in pure.conditions.items():
                value=OWNED
                for conditions,_ in groups:
                    for condition in conditions:
                        if not isinstance(condition,Permission):raise AssertionError('unexpected reference permission '+type(condition).__name__)
                        value|=condition
                key=str(node.source.path)+':'+str(node.start) if qualified else str(node.start)
                checks[key]=permission(value,qualified)
            profiles=[]
            for call in (node for nodes in pure.borrowing.nodes.values() for node in nodes if node.kind=='Call'):
                if pure.calls.queried(call) or pure.approved(call) or pure.builtin(call) or call.source.denotes_type(call.role('callee')):continue
                targets=pure.calls.targets.get(call,pure.targets(call))
                for target in targets:
                    if target not in pure.borrowing.envs:continue
                    bound=pure.borrowing.bindings(call,target,pure.borrowing.environment(pure.owner(call)))
                    fields=[[access(item,qualified),permission(pure.borrowing.resolve(bound,item),qualified)]
                            for item in pure.borrowing.requirements.get(target,())]
                    profiles.append([location(call,qualified),location(target,qualified),fields])
            reference_converged=not any('did not converge' in message for _,message in pure.contracts.values())
            self.assertEqual(reference_converged,converged,text)
            expected=canonical({'converged':reference_converged,'checks':checks,'profiles':profiles,
                'mutates':[location(proc,qualified) for proc in pure.borrowing.mutates],
                'escapes':[[location(node,qualified),message] for node,message in pure.borrowing.escapes.items()]})
            result=subprocess.run([str(native.ROOT/'rust/target/release/examples/borrow_protocol'),str(entry),*(['--project'] if sdk else [])],
                capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            actual=canonical(json.loads(result.stdout))
            if clone_sequence_guard:
                # Rust resolves clone's generic return type here; Python keeps
                # a native sequence predicate. For this exact [][]int fixture,
                # values[0] is []int, so the two guards are equivalent. Keep
                # both snapshots explicit rather than ignoring check differences.
                offset=text.index('values[0][0]')
                path=str(entry.resolve())
                key=path+':'+str(offset)
                self.assertEqual(expected['checks'],{key:['type','sequence',path,offset,offset+len('values[0]')]})
                self.assertEqual(actual['checks'],{key:['constant',True]})
                expected['checks']=actual['checks']
            self.assertEqual(actual,expected,text)
            self.assertEqual(entry.read_text(),text)

    def test_owned_scalar_pointer_calls(self):
        self.compare_model('set::proc(value:^int){value^=9}','x:=n;set(&x);return x')

    def test_generic_copy_conditions_preserve_native_type_traits(self):
        self.compare_model('copy::proc(value:$T)->int{local:=value;local[0]=9;return local[0]}',
                           'return copy(input)','input:$T')

    def test_out_parameter_stores_preserve_borrowed_slice_aliases(self):
        self.compare_model('Box::struct{values:[]int}\nlink::proc(out:^Box,values:[]int){out.values=values}',
                           'box:Box;link(&box,values);box.values[0]=9;return 0','values:[]int')

    def test_out_parameter_stores_preserve_fresh_owned_buffers(self):
        self.compare_model('Box::struct{values:[]int}\ncreate::proc(out:^Box,n:int){out.values=make([]int,n)}',
                           'box:Box;create(&box,n);box.values[0]=9;return 0')

    def test_append_propagates_borrowed_element_headers(self):
        self.compare_model('push::proc(out:^[dynamic;2][]int,values:[]int){append(out,values)}',
                           'out:[dynamic;2][]int;push(&out,values);out[0][0]=9;return 0','values:[]int')

    def test_storing_a_local_stack_pointer_reports_the_escape(self):
        self.compare_model('store::proc(out:^^int){local:=7;out^=&local}',
                           'pointer:^int;store(&pointer);return n')

    def test_recursive_helpers_converge_on_parameter_relative_stores(self):
        self.compare_model('Box::struct{values:[]int}\nlink::proc(out:^Box,values:[]int,n:int){if n>0{link(out,values,n-1)}else{out.values=values}}',
                           'box:Box;link(&box,values,2);box.values[0]=9;return 0','values:[]int')

    def test_using_pointer_parameters_keep_the_physical_store_path(self):
        self.compare_model('Box::struct{values:[]int}\nlink::proc(using out:^Box,source:[]int){values=source}',
                           'box:Box;link(&box,input);box.values[0]=9;return 0','input:[]int')

    def test_named_arguments_resolve_the_same_parameter_paths(self):
        self.compare_model('write::proc(values:[]int,n:int){values[0]=n}',
                           'write(n=2,values=input);return input[0]','input:[]int')

    def test_value_iteration_preserves_reference_elements(self):
        self.compare_model('', 'for row in input{local:=row;local[0]=9};return 0','input:[2][]int')

    def test_reference_iteration_preserves_local_element_locations(self):
        self.compare_model('set::proc(value:^int){value^=9}',
                           'values:=[1,2];for &value in values{set(&value)};return values[0]')

    def test_type_switch_bindings_preserve_borrowed_storage(self):
        self.compare_model('Input::union{[2]int,[]int}',
                           'switch value in input{case []int:value[0]=9};return 0','input:Input')

    def test_type_queries_do_not_mutate_or_propagate_runtime_aliases(self):
        self.compare_model('set::proc(value:^int)->int{value^=9;return value^}',
                           'x:=n;_=type_of(set(&x));return x')

    def test_variadic_alias_graph_retains_reference_nonconvergence_rejection(self):
        self.compare_model('write::proc(values:[]int,n:int){values[0]=n}\nforward::proc(n:int,values:..[]int){write(n=n,values=values[0])}',
                           'forward(2,input);return input[0]','input:[]int',converged=False)

    def test_direct_variadic_reference_elements_preserve_permissions(self):
        self.compare_model('change::proc(rows:..[]int){rows[0][0]=9}',
                           'change(input);return 0','input:[]int')

    def test_variadic_slice_spread_resolves_element_permissions(self):
        self.compare_model('change::proc(rows:..[]int){rows[0][0]=9}',
                           'change(..input);return 0','input:[][]int')

    def test_variadic_array_spread_resolves_element_permissions(self):
        self.compare_model('change::proc(rows:..[]int){rows[0][0]=9}',
                           'change(**input);return 0','input:[1][]int')

    def test_installed_slice_reverse_uses_the_reference_helper_graph(self):
        self.compare_model('import "core:slice"',
                           'values:=[n,n+1];slice.reverse(values[:]);return values[0]',sdk=True)

    def test_installed_slice_clone_preserves_reference_elements(self):
        self.compare_model('import "core:slice"',
                           'values:=slice.clone(input);values[0][0]=9;return 0','input:[][]int',sdk=True,clone_sequence_guard=True)

    def test_comprehension_reference_elements_keep_the_input_provenance(self):
        self.compare_model('', 'rows:=[row for row in input];rows[0][0]=9;return 0','input:[][]int')

    def test_comprehension_copies_inline_array_elements(self):
        self.compare_model('', 'rows:=[row for row in input];rows[0][0]=9;return rows[0][0]','input:[2][2]int')

    def test_comprehension_helpers_use_the_iterator_bindings(self):
        self.compare_model('length::proc(row:[]int)->int{return len(row)}',
                           'rows:=[length(row) for row in input];return rows[0]','input:[][]int')


if __name__=='__main__':unittest.main()

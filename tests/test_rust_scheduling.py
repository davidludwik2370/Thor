"""Reference-order known/active decisions and isolated dependency scheduling."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from thorlang.driver import Project, frontend_binary
from thorlang.evaluation import Evaluator
from thorlang.compiler import ThorError
from tests import test_rust_compiler as native


class RustSchedulingTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        build=subprocess.run(['cargo','build','--release','--locked','--offline','--manifest-path',str(native.ROOT/'rust/Cargo.toml'),
                              '--example','scheduling_protocol'],capture_output=True,text=True,timeout=120)
        if build.returncode:
            raise RuntimeError(build.stdout+build.stderr)
        cls.binary=native.ROOT/'rust/target/release/examples/scheduling_protocol'
        cls.frontend=frontend_binary('odin')[0]

    def compare(self, text, flags=None, files=None, fail=None, reference_failure=None, output=None, reference_specialized_gap=None):
        flags=['-debug'] if flags is None else flags
        with tempfile.TemporaryDirectory(prefix='thor-scheduling-') as directory:
            entry=Path(directory).resolve()/'main.thor'
            entry.write_text(text)
            for name,content in (files or {}).items():
                path=entry.parent/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(content)
            project=Project(entry,self.frontend,flags,cache_frontend=False)
            evaluator=Evaluator(project,'odin')
            reference_error=None
            try:
                evaluator.evaluate_all()
            except ThorError as error:
                reference_error=error
            arguments=[str(self.binary),str(entry),*flags]
            if output is not None:
                arguments.append('emit='+str(entry.parent/'generated'))
            result=subprocess.run(arguments,capture_output=True,text=True,timeout=120)
            if fail:
                self.assertIsNotNone(reference_error)
                self.assertIn(fail,str(reference_error)+'\n'+str(reference_error.diagnostics))
                self.assertNotEqual(result.returncode,0,result.stdout)
                packet=json.loads(result.stderr)
                self.assertIn(fail,packet['message']+'\n'+str(packet['diagnostics']),result.stderr)
                self.assertFalse(packet['unsupported'],result.stderr)
                return
            if reference_failure:
                self.assertIsNotNone(reference_error)
                self.assertIn(reference_failure,str(reference_error)+'\n'+str(reference_error.diagnostics))
                self.assertEqual(result.returncode,0,result.stderr)
                if output is None:
                    return json.loads(result.stdout)['results']
            else:
                self.assertIsNone(reference_error,str(reference_error))
            self.assertEqual(result.returncode,0,result.stderr)
            if output is not None:
                packet=json.loads(result.stdout)
                manifest=json.loads((entry.parent/'generated/thor-manifest.json').read_text())
                executable=entry.parent/'program'
                build=subprocess.run(['odin','build',packet['target'],'-file',*manifest['odin_flags'],'-out:'+str(executable)],
                                     capture_output=True,text=True,timeout=60,env={**os.environ,**manifest['odin_environment']})
                self.assertEqual(build.returncode,0,build.stderr)
                run=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
                self.assertEqual(run.returncode,0,run.stderr)
                self.assertEqual(run.stdout,output)
            expected=[[[str(node.source.path),node.kind,node.start,node.end],data]
                      for (node,fingerprint),data in evaluator.results.items() if not fingerprint]
            actual=json.loads(result.stdout)['results']
            if reference_failure and reference_specialized_gap is None:
                return actual
            specialized=[[[str(node.source.path),node.kind,node.start,node.end],data]
                         for (node,fingerprint),data in evaluator.results.items() if fingerprint]
            actual_specialized=json.loads(result.stdout).get('specialized',[])
            if reference_specialized_gap is not None:
                expected_reference=[]
                native_values=reference_specialized_gap
                if isinstance(reference_specialized_gap,dict):
                    expected_reference=reference_specialized_gap['reference']
                    native_values=reference_specialized_gap['rust']
                self.assertEqual(sorted([item[1] for item in specialized],key=str),sorted(expected_reference,key=str))
                runs=[node for source in project.sources.values() for node in source.nodes if node.kind=='Run']
                self.assertEqual(len(runs),1)
                node=runs[0]
                specialized=[[[str(node.source.path),node.kind,node.start,node.end],value] for value in native_values]
            self.assertEqual(sorted(actual_specialized,key=str),sorted(specialized,key=str))
            self.assertEqual(sorted(actual,key=str),sorted(expected,key=str))
            return actual

    def program(self,body,prefix=''):
        return 'package main\n'+prefix+'\nmain::proc(){'+body+'}\n'

    def test_generic_explicit_type_and_constant_bindings(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('fmt.println(generic(int,3),generic(i64,5),generic(int,3))',
                    'import "core:fmt"\nwork::proc(n:int)->int{return n+2}\n'
                    'generic::proc($T:typeid,$N:int)->T{VALUE::#run work(N);return T(VALUE)}'),
                    flags=flags,output='5 7 5\n')

    def test_generic_inferred_element_and_dimension(self):
        self.compare(self.program('fmt.println(generic([2]int{1,2}),generic([3]f64{1,2,3}))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+2}\n'
            'generic::proc(values:[$N]$T)->int{VALUE::#run work(N);return VALUE+size_of(T)}'),output='12 13\n')

    def test_generic_named_arguments_and_defaults(self):
        self.compare(self.program('fmt.println(generic(N=5),generic(3))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+2}\n'
            'generic::proc($N:int,x:int=1)->int{return (#run work(N))+x}'),output='8 6\n')

    def test_generic_boolean_branch_selection(self):
        self.compare(self.program('fmt.println(generic(true),generic(false))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+2}\n'
            'bad::proc()->int{panic("inactive")}\n'
            'generic::proc($B:bool)->int{when B{return #run work(3)}else{return #run work(7)}}'),output='5 9\n')

    def test_nested_generic_calls_restore_caller_bindings(self):
        self.compare(self.program('fmt.println(outer(3),outer(7))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'inner::proc($N:int)->int{return #run work(N)}\n'
            'outer::proc($N:int)->int{a:=inner(N+2);return a+(#run work(N))}'),output='10 18\n')

    def test_uncalled_generic_request_stays_native_lazy(self):
        self.compare(self.program('fmt.println(7)',
            'import "core:fmt"\nunused::proc($T:typeid)->int{return #run missing()}'),output='7\n')

    def test_nested_generic_lexical_constant_shadowing(self):
        self.compare(self.program('fmt.println(outer(3),outer(8))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'outer::proc($N:int)->int{inner::proc($M:int)->int{return #run work(N+M)};return inner(2)}'),output='6 11\n')

    def test_generic_request_can_be_a_dependency_of_an_explicit_run(self):
        self.compare(self.program('fmt.println(#run generic(3))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+2}\n'
            'generic::proc($N:int)->int{return #run work(N)}'),output='5\n')

    def test_generic_unselected_panics_stay_lazy_for_each_instantiation(self):
        self.compare(self.program('fmt.println(generic(true),generic(false))',
            'import "core:fmt"\nwork::proc()->int{return 5}\nbad::proc()->int{panic("inactive")}\n'
            'generic::proc($B:bool)->int{when B{if false{_=#run bad()};return #run work()}else{when false{_=#run bad()};return 9}}'),
            output='5 9\n')

    def test_generic_automatic_fn_result_and_runtime_fallback(self):
        self.compare(self.program('fmt.println(generic(3),generic(7))',
            'import "core:fmt"\nwork::fn(n:int)->int{return n+2}\n'
            'generic::proc($N:int)->int{VALUE::work(N);runtime:=N;return VALUE+work(runtime)}'),output='10 18\n')

    def test_generic_owned_runtime_results_remain_independent(self):
        self.compare(self.program('a:=generic(2);b:=generic(3);a[0]=9;fmt.println(a,b);delete(a);delete(b)',
            'import "core:fmt"\nwork::proc(n:int)->[]int{a:=make([]int,n);a[0]=n;return a}\n'
            'generic::proc($N:int)->[]int{return #run work(N)}'),flags=native.UNCHECKED,output='[9, 0] [3, 0, 0]\n')

    def test_generic_string_constant_parameter(self):
        self.compare(self.program('fmt.println(generic("abc"),generic("abcdef"))',
            'import "core:fmt"\nwork::proc(s:string)->int{return len(s)}\n'
            'generic::proc($S:string)->int{return #run work(S)}'),output='3 6\n')

    def test_generic_enum_constant_parameter(self):
        self.compare(self.program('fmt.println(generic(Kind.one),generic(Kind.two))',
            'import "core:fmt"\nKind::enum{one,two}\nwork::proc(k:Kind)->int{return int(k)+1}\n'
            'generic::proc($K:Kind)->int{return #run work(K)}'),output='1 2\n',
            reference_specialized_gap=[{'kind':'integer','value':'1'},{'kind':'integer','value':'2'}])

    def test_imported_generic_request_retains_its_original_package(self):
        self.compare(self.program('fmt.println(lib.generic(int,3),lib.generic(i16,5))',
            'import "core:fmt"\nimport lib "lib"'),
            files={'lib/lib.thor':'package lib\nwork::proc(n:int)->int{return n+2}\ngeneric::proc($T:typeid,$N:int)->T{return T(#run work(N))}\n'},output='5 7\n')

    def test_recursive_generic_constants_are_scheduled_lazily(self):
        self.compare(self.program('fmt.println(generic(3))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n}\n'
            'generic::proc($N:int)->int{when N>0{return (#run work(N))+generic(N-1)}else{return 0}}'),output='6\n')

    def test_explicit_literal_and_constant_inputs(self):
        self.compare(self.program('_=#run work(BASE)', 'BASE::7\nwork::proc(n:int)->int{return n+2}'))

    def test_runtime_and_immutable_storage_are_not_constant_inputs(self):
        for declaration in ('x:=7','x :> 7'):
            with self.subTest(declaration=declaration):
                self.compare(self.program(declaration+';_=#run work(x)','work::proc(n:int)->int{return n+1}'),
                             fail='runtime storage is not a constant')

    def test_nested_explicit_requests_install_before_outer_execution(self):
        self.compare(self.program('_=#run work(#run leaf())','leaf::proc()->int{return 3}\nwork::proc(x:int)->int{return x*2}'))

    def test_dependency_constant_request_is_evaluated_first(self):
        results=self.compare(self.program('_=#run work()', 'VALUE::#run leaf()\nleaf::proc()->int{return 4}\nwork::proc()->int{return VALUE+2}'),
                             reference_failure="Expected a procedure type for 'type_proc_return_type'")
        # Python adds its helper-only generic parameter to leaf(), invalidating
        # the already materialized value's original signature witness.
        self.assertEqual(sorted(data['value'] for _,data in results),['4','6'])

    def test_requests_inside_a_retained_helper_are_evaluated_first(self):
        self.compare(self.program('_=#run work()', 'leaf::proc()->int{return 4}\nwork::proc()->int{x:=#run leaf();return x+1}'))

    def test_inactive_request_inside_a_dependency_remains_unchecked(self):
        self.compare(self.program('_=#run work()', 'work::proc()->int{when false{_=#run missing()};return 4}'))

    def test_debug_runtime_calls_stay_native_and_required_constants_execute(self):
        text=self.program('x:=work(3);_=x', 'work::fn(n:int)->int{return n+1}\nVALUE::work(4)')
        results=self.compare(text)
        self.assertEqual(len(results),1)
        results=self.compare(text,flags=['-o:speed'])
        self.assertEqual(len(results),2)

    def test_ordinary_nested_calls_require_explicit_run(self):
        self.compare(self.program('_=#run work(leaf())', 'leaf::proc()->int{return 3}\nwork::proc(x:int)->int{return x*2}'))
        results=self.compare(self.program('_=work(leaf())', 'leaf::proc()->int{return 3}\nwork::fn(x:int)->int{return x*2}'),flags=['-o:speed'])
        self.assertEqual(results,[])

    def test_inactive_compile_time_branch_does_not_execute(self):
        self.compare(self.program('when false{_=#run bad()}else{_=#run good()}',
            'bad::proc()->int{panic("inactive")}\ngood::proc()->int{return 5}'))

    def test_inactive_runtime_branch_and_short_circuit_do_not_execute(self):
        self.compare(self.program('if false{_=#run bad()};_=false && (#run bad_bool())',
            'bad::proc()->int{panic("inactive")}\nbad_bool::proc()->bool{panic("inactive")}'))

    def test_explicit_run_executes_even_under_runtime_condition(self):
        self.compare(self.program('flag:=true;if flag{_=#run good()}', 'good::proc()->int{return 5}'))

    def test_automatic_call_under_runtime_condition_is_left_native(self):
        results=self.compare(self.program('flag:=true;if flag{_=good(5)}', 'good::fn(n:int)->int{return n+1}'),flags=['-o:speed'])
        self.assertEqual(results,[])

    def test_type_queries_do_not_execute_operand_calls(self):
        self.compare(self.program('_=#run work()', 'bad::proc()->int{panic("unevaluated")}\nwork::proc()->int{return size_of(bad())}'))

    def test_local_scope_constants_are_known(self):
        self.compare(self.program('VALUE::3;_=#run work(VALUE)', 'work::proc(n:int)->int{return n+1}'))

    def test_multiple_results_get_the_reference_entry_error(self):
        self.compare(self.program('_=#run work()', 'work::proc()->(int,int){return 1,2}'),fail='requires exactly one supported result value')

    def test_an_unrelated_broken_file_is_not_a_file_entry_dependency(self):
        self.compare(self.program('_=#run work()', 'work::proc()->int{return 3}'),files={'broken.thor':'not valid source'})

    def test_known_switch_inputs_select_only_the_matching_result(self):
        self.compare(self.program('_=#run work(switch 3{case 1:bad();case 2..=4:7;case:0})',
            'bad::proc()->int{panic("inactive result")}\nwork::proc(n:int)->int{return n}'))

    def test_compile_switch_branch_does_not_execute_the_other_request(self):
        self.compare(self.program('#switch 3{case 1:_=#run bad();case 2..=4:_=#run good();case:_=0}',
            'bad::proc()->int{panic("inactive")}\ngood::proc()->int{return 5}'))

    def test_installed_scalar_constant_compiles_with_its_native_type(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('fmt.println(VALUE)', 'import "core:fmt"\nwork::proc(n:int)->int{return n+2}\nVALUE::#run work(3)'),flags=flags,output='5\n')

    def test_installed_owned_result_has_independent_runtime_storage(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('a:=#run work();b:=#run work();a[0]=9;if b[0]!=2{panic("independent results")};fmt.println(b[0]);delete(a);delete(b)',
                    'import "core:fmt"\nwork::proc()->[]int{a:=make([]int,2);a[0]=2;return a}'),flags=flags,output='2\n')

    def test_installed_inactive_requests_keep_runtime_branch_type_checking(self):
        self.compare(self.program('when false{_=#run missing()};if false{_=#run bad()};fmt.println(7)',
            'import "core:fmt"\nbad::proc()->int{panic("inactive")}'),output='7\n')

    def test_compile_time_comprehension_is_a_fixed_native_array(self):
        self.compare(self.program('fmt.println(VALUES)', 'import "core:fmt"\nVALUES::[x+1 for x in 0..<5 if x%2==0]'),output='[1, 3, 5]\n')

    def test_compile_time_comprehension_checks_the_entire_body(self):
        self.compare(self.program('', 'state:int\nVALUES::[state+x for x in 0..<3]'),fail='runtime storage')

    def test_flattened_comprehension_and_empty_selection(self):
        self.compare(self.program('fmt.println(VALUES,EMPTY)',
            'import "core:fmt"\nROWS::[2][2]int{{1,2},{3,4}}\nVALUES::[..row for row in ROWS]\nEMPTY::[x for x in 0..<3 if false]'),
            output='[1, 2, 3, 4] []\n')

    def test_typed_allocated_comprehension_rejects_a_constant_binding(self):
        self.compare(self.program('', 'VALUES::[]int{x for x in 0..<3}'),fail='Use bare')

    def test_type_query_of_local_storage_can_be_an_automatic_constant_argument(self):
        self.compare(self.program('local:i64;VALUE::work(size_of(local));fmt.println(VALUE)',
            'import "core:fmt"\nwork::fn(n:int)->int{return n+1}'),output='9\n')

    def test_evaluated_comprehension_length_is_available_for_generic_inference(self):
        self.compare(self.program('fmt.println(COUNT)', 'import "core:fmt"\nVALUES::[x+1 for x in 0..<3]\ncount::fn(a:[$N]int)->int{return len(a)}\nCOUNT::count(VALUES)'),output='3\n')

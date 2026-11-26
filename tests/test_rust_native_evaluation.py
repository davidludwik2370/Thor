"""Normal independent compiler commands use native compile-time execution."""
import json
import os
import unittest
from tests.cache_paths import entries as cache_entries
from tests import test_rust_compiler as native


class RustNativeEvaluationTests(unittest.TestCase):
    maxDiff=None
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_explicit_scalar_execution_in_checked_and_unchecked_programs(self):
        code=self.program('fmt.println(#run work(4))','import "core:fmt"\nwork::proc(n:int)->int{return n+2}')
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='6')

    def test_automatic_constant_calls_and_runtime_calls_keep_types(self):
        self.compare(self.program('VALUE::work(4);n:=5;fmt.println(VALUE,work(n))',
            'import "core:fmt"\nwork::fn(n:int)->int{return n+2}'),flags=native.UNCHECKED,expected='6 7')

    def test_nested_requests_install_before_outer_evaluation(self):
        self.compare(self.program('fmt.println(#run work(#run leaf()))',
            'import "core:fmt"\nleaf::proc()->int{return 4}\nwork::proc(n:int)->int{return n+2}'),expected='6')

    def test_generic_type_and_constant_frames_keep_native_signatures(self):
        self.compare(self.program('fmt.println(generic(int,3),generic(i16,5))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+2}\n'
            'generic::proc($T:typeid,$N:int)->T{return T(#run work(N))}'),expected='5 7')

    def test_owned_results_restore_independent_buffers(self):
        self.compare(self.program('a:=#run work();b:=#run work();a[0]=9;fmt.println(a,b);delete(a);delete(b)',
            'import "core:fmt"\nwork::proc()->[]int{values:=make([]int,2);values[0]=2;values[1]=4;return values}'),
            flags=native.UNCHECKED,expected='[9, 4] [2, 4]')

    def test_compile_time_comprehension_emits_native_fixed_array(self):
        self.compare(self.program('VALUES::[x*x for x in 0..<5 if x%2==0];fmt.println(VALUES)',
            'import "core:fmt"'),expected='[0, 4, 16]')

    def test_imported_sdk_call_executes_without_python(self):
        self.compare(self.program('fmt.println(#run math.sqrt(f64(81)))',
            'import "core:fmt"\nimport "core:math"'),expected='9')

    def test_impure_explicit_execution_reports_original_thor_source(self):
        self.source.write_text(self.program('_=#run work()', 'state:int\nwork::proc()->int{return state}'))
        result=self.invoke('rust',command='check',flags=['-json-errors'])
        self.assertEqual(result.returncode,1,result.stderr)
        packet=json.loads(result.stderr)
        self.assertIn('runtime storage',result.stderr)
        self.assertEqual(packet['errors'][0]['pos']['file'],str(self.source))
        self.assertNotIn('thor-rust-run-',result.stderr)

    def test_check_transpile_build_and_test_commands_materialize_requests(self):
        self.source.write_text('package main\nimport "core:testing"\nwork::proc()->int{return 6}\n'
            '@(test)\nverify::proc(t:^testing.T){value:=#run work();if value!=6{panic("wrong value")}}\nmain::proc(){}\n')
        for command in ('check','transpile','build','test'):
            with self.subTest(command=command):
                result=self.invoke('rust',command=command)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_warm_command_result_survives_unrelated_runtime_edit(self):
        self.source.write_text(self.program('fmt.println(VALUE)','import "core:fmt"\n'
            'work::proc(n:int)->int{return n+2}\nVALUE::#run work(4)'))
        cold=self.invoke('rust');self.assertEqual(cold.returncode,0,cold.stderr)
        self.source.write_text(self.source.read_text().replace('fmt.println(VALUE)','unused:=17;_=unused;fmt.println(VALUE)'))
        warm=self.invoke('rust');self.assertEqual(warm.returncode,0,warm.stderr)
        self.assertEqual(warm.stdout,'6\n')
        records=cache_entries(self.root/'cache','scalar-v1')
        self.assertTrue(records)

    def test_capabilities_report_native_evaluation_without_claiming_completion(self):
        import subprocess
        result=subprocess.run([str(native.RUST),'--capabilities'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        packet=json.loads(result.stdout)
        self.assertTrue(packet['evaluation']['isolated_native_helpers'])
        self.assertTrue(packet['evaluation']['contents_cache'])
        self.assertTrue(packet['evaluation']['big_number_results'])
        self.assertFalse(packet['complete'])
        self.assertFalse(packet['python_fallback'])


if __name__=='__main__':
    unittest.main()

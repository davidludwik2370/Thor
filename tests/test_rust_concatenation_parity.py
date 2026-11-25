"""Constant concatenation follows native types, including unresolved generics."""
import unittest
import subprocess
from tests.native_paths import EXE_SUFFIX
from tests import test_rust_compiler as native


class RustConcatenationParityTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    compare=native.RustCompilerTests.compare
    invoke=native.RustCompilerTests.invoke
    program=native.RustCompilerTests.program

    def test_distinct_arrays_produce_plain_arrays_and_slices_keep_identity(self):
        code=self.program('fmt.println(A,S);if type_of(A)!=[2]int || type_of(S)!=Numbers{panic("type identity")}',
            'import "core:fmt"\nPair::distinct [1]int\nNumbers::distinct []int\n'
            'A::Pair{1}++Pair{2}\nS::Numbers{3}++Numbers{4}')
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='[1, 2] [3, 4]')

    def test_empty_parenthesized_literals_get_context_from_the_nonempty_operand(self):
        self.compare(self.program('fmt.println(([])++([1,2])++([]))','import "core:fmt"'),expected='[1, 2]')

    def test_empty_literals_use_an_established_numeric_element_type(self):
        self.compare(self.program('fmt.println(([])++([0])++A++([]))','import "core:fmt"\nA::[1]i8{2}'),expected='[0, 2]')

    def test_unconstrained_constant_generic_array_arguments_are_native_constants(self):
        self.compare(self.program('A::[1];B::[2];fmt.println(combine(A,B))','import "core:fmt"\n'
            'combine::proc($a:$T,$b:T)->[2]int{return a++b}'),expected='[1, 2]')

    def test_bare_generic_constant_argument_records_the_reference_identity_cast_gap(self):
        self.source.write_text(self.program('fmt.println(combine([1],[2]))','import "core:fmt"\n'
            'combine::proc($a:$T,$b:T)->[2]int{return a++b}'))
        if native.reference_enabled():
            reference=self.invoke('python')
        result=self.invoke('rust')
        if native.reference_enabled():
            self.assertNotEqual(reference.returncode,0,reference.stdout)
        if native.reference_enabled():
            self.assertIn('Expected a constant value',reference.stderr)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'[1, 2]\n')
        ordinary=self.root/'ordinary.odin';executable=self.root/('ordinary'+EXE_SUFFIX)
        ordinary.write_text('package main\nimport "core:fmt"\nimport "base:intrinsics"\n'
            'combine::proc($a:$T,$b:T)->[2]int{return intrinsics.concatenate(a,b)}\n'
            'main::proc(){fmt.println(combine([1]int{1},[1]int{2}))}\n')
        built=subprocess.run(['odin','build',str(ordinary),'-file','-out:'+str(executable)],capture_output=True,text=True,timeout=60)
        self.assertEqual(built.returncode,0,built.stderr)
        ran=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
        self.assertEqual(ran.returncode,0,ran.stderr)
        self.assertEqual(ran.stdout,result.stdout)

    def test_generic_constant_lengths_and_signature_constraints(self):
        code=self.program('fmt.println(combine([1,2],[3]),sized([1,2]))','import "core:fmt"\n'
            'combine::proc($a:[$N]$T,$b:[$M]T)->[N+M]T{return a++b}\n'
            'sized::proc($a:[2]int)->int where len(a++[3])==3{return len(a++[3])}')
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='[1, 2, 3] 3')

    def test_enum_literals_retain_declaration_argument_and_return_context(self):
        self.compare(self.program('fmt.println(CHOICE,take([.Red]++[.Blue]),result())',
            'import "core:fmt"\nColor::enum{Red,Blue}\nCHOICE:[2]Color: [.Red]++[.Blue]\n'
            'take::proc(values:[2]Color)->int{return len(values)}\nresult::proc()->[2]Color{return [.Red]++[.Blue]}'),
            expected='["Red", "Blue"] 2 ["Red", "Blue"]')

    def test_pure_and_explicit_results_feed_constant_concatenation(self):
        self.compare(self.program('fmt.println(AUTO,EXPLICIT++[4])',
            'import "core:fmt"\nwork::fn()->[3]int{return [1]++[2,3]}\nAUTO::work()\nEXPLICIT::#run work()'),
            flags=native.UNCHECKED,expected='[1, 2, 3] [1, 2, 3, 4]')

    def test_imported_and_native_constants_preserve_context(self):
        self.compare(self.program('fmt.println(A++[3],verify())','import "core:fmt"\nimport "values" / A'),
            directory=True,files={'values/main.thor':'package values\nA::[2]i8{1,2}\n',
                'native.odin':'package main\nverify::proc()->bool{return true}\n'},expected='[1, 2, 3] true')

    def test_runtime_immutable_operands_remain_ineligible(self):
        self.compare(self.program('values :> [1,2];_=values++[3]'),command='check',
            fail='requires compile-time constant operands')

    def test_mixed_container_kinds_keep_the_source_error(self):
        self.compare(self.program('A::[1]int{1};B::[]int{2};_=A++B'),command='check',
            fail='cannot mix fixed arrays and slices')

    def test_constant_record_fields_and_named_type_conversions_remain_constant(self):
        self.compare(self.program('fmt.println(DATA,TEXT)',
            'import "core:fmt"\nConfig::struct{values:[2]int,text:string}\n'
            'CONFIG::Config{{1,2},"x"}\nCount::int\nDATA::CONFIG.values++[Count(3)]\nTEXT::CONFIG.text*3'),
            expected='[1, 2, 3] xxx')

    def test_runtime_record_field_is_rejected_before_constant_hoisting(self):
        self.compare(self.program('config:=Config{{1,2}};_=config.values++[3]',
            'Config::struct{values:[2]int}'),command='check',fail='requires compile-time constant operands')


if __name__=='__main__':
    unittest.main()

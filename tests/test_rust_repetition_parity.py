"""Native constant counts, string identity and lazy repetition helpers."""
import unittest
import subprocess
from tests import test_rust_compiler as native


class RustRepetitionParityTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_native_counts_conversions_and_config(self):
        code=self.program('fmt.println(TEXT,CONFIGURED,OTHER)','import "core:fmt"\nCount::distinct u32\n'
            'TEXT::"x"*N\nN::(20/4)%8\nCONFIGURED::"ab"*#config(REPETITIONS,3)\nOTHER::"z"*Count(3)')
        self.compare(code,expected='xxxxx ababab zzz')
        self.compare(code,flags=['-define:REPETITIONS=2'],expected='xxxxx abab zzz')

    def test_generic_constant_text_and_counts(self):
        code=self.program('fmt.println(repeat("ab",3),repeat("x",0))','import "core:fmt"\n'
            'repeat::proc($text:string,$n:int)->string{return text*n}')
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='ababab ')

    def test_generic_signature_and_record_type_expression(self):
        self.compare(self.program('fmt.println(bounded("ab"))','import "core:fmt"\n'
            'bounded::proc($text:string)->int where len(text*2)<10{return len(text*2)}'),expected='4')
        self.compare(self.program('_=bounded("ab",2)',
            'bounded::proc($text:string,$n:int)->int where len(text*n)<10{return n}'),command='check',
            fail='generic signature or type expression')

    def test_generic_record_repetition_records_reference_inline_type_query_gap(self):
        self.source.write_text(self.program('fmt.println(size_of(Box("x")))','import "core:fmt"\n'
            'Box::struct($text:string){data:[len(text*2)]u8}'))
        if native.reference_enabled():
            reference=self.invoke('python')
        result=self.invoke('rust')
        if native.reference_enabled():
            self.assertNotEqual(reference.returncode,0,reference.stdout)
        if native.reference_enabled():
            self.assertIn('not a type',reference.stderr)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'2\n')
        ordinary=self.root/'ordinary.odin';executable=self.root/'ordinary'
        ordinary.write_text('package main\nimport "core:fmt"\nBox::struct($text:string){data:[len(text+text)]u8}\nmain::proc(){fmt.println(size_of(Box("x")))}\n')
        built=subprocess.run(['odin','build',str(ordinary),'-file','-out:'+str(executable)],capture_output=True,text=True,timeout=60)
        self.assertEqual(built.returncode,0,built.stderr)
        ran=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
        self.assertEqual(ran.returncode,0,ran.stderr)
        self.assertEqual(ran.stdout,result.stdout)

    def test_distinct_and_typed_zero_repetition_keep_native_identity(self):
        self.compare(self.program('if type_of(TEXT)!=Label || TEXT!=Label(""){panic("distinct identity")};fmt.println(len(TEXT),type_of(TYPED)==string)',
            'import "core:fmt"\nLabel::distinct string\nTEXT::("a"+Label("b"))*0\nTYPED::("a"+string("b"))*0'),expected='0 true')

    def test_empty_string_accepts_the_widest_unsigned_count_and_cstring_context(self):
        self.compare(self.program('take(PAD+"x");take(""*max(u128));fmt.println(len(PAD))','import "core:fmt"\n'
            'PAD::" "*30\ntake::proc(value:cstring){_=value}'),flags=native.UNCHECKED,expected='30')

    def test_nested_repetitions_and_parenthesized_compounds(self):
        self.compare(self.program('fmt.println(("a"*2)*3,2*(3*"b"),("a"+"b")*3)','import "core:fmt"'),expected='aaaaaa bbbbbb ababab')

    def test_unselected_when_and_compile_switch_arms_stay_unchecked(self):
        self.compare(self.program('fmt.println(GLOBAL,SELECTED)','import "core:fmt"\n'
            'GLOBAL::"x"*2 when true else "x"*(-1)\n'
            'SELECTED::#switch{case true:"ok";case:"x"*(-1)}'),expected='xx ok')

    def test_constant_record_fields_and_native_type_query_counts(self):
        self.compare(self.program('fmt.println(TEXT,"x"*len([3]int{}),"y"*size_of([3]u8))',
            'import "core:fmt"\nOptions::struct{text:string,count:int}\nOPTIONS::Options{"ab",3}\nTEXT::OPTIONS.text*OPTIONS.count'),
            expected='ababab xxx yyy')

    def test_utf8_raw_and_binary_contents_use_native_byte_lengths(self):
        self.compare(self.program(r'''text::"é😀"*2
if text!="é😀é😀" || (`a\n`*2)!=`a\na\n` || ("\x00\xff"*2)!="\x00\xff\x00\xff" {panic("contents")}
fmt.println(len(text),len("\x00\xff"*2))''','import "core:fmt"'),expected='12 4')

    def test_invalid_counts_keep_clear_source_diagnostics(self):
        for expression,message in [('"a"*(-1)','nonnegative count'),('"a"*1.5','integer compile-time count'),
            ('"a"*true','integer compile-time count'),('"a"*"b"','integer compile-time count'),('"a"*(1<<30)','16 MiB')]:
            with self.subTest(expression=expression):self.compare(self.program('_='+expression),command='check',fail=message)

    def test_runtime_operands_stay_ineligible_even_for_zero(self):
        for body in ['text:="a";_=text*0','n:=3;_="a"*n','n:>3;_="a"*n']:
            with self.subTest(body=body):self.compare(self.program(body),command='check',fail='compile-time constant operands')

    def test_typed_string_requires_explicit_cstring_conversion(self):
        self.compare(self.program('take(TEXT)','TEXT::string("x")*2\ntake::proc(value:cstring){}'),command='check',fail="to 'cstring'")

    def test_large_counts_emit_a_small_native_constant_chain_without_execution(self):
        self.source.write_text(self.program('_=len(TEXT)','TEXT::"x"*1000000'))
        result=self.invoke('rust',command='transpile')
        self.assertEqual(result.returncode,0,result.stderr)
        paths=list((self.root/'rust').rglob('main.odin'))
        self.assertEqual(len(paths),1,paths)
        generated=paths[0].read_text()
        packets=list((self.root/'cache/eval-v1').glob('*.json'))
        self.assertLess(len(generated),30000)
        self.assertNotIn('strings.repeat',generated)
        self.assertEqual(packets,[])


if __name__=='__main__':
    unittest.main()

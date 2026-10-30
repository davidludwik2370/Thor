"""Constant-only string repetition, native concatenation and integrations."""
import json
import unittest
from tests import test_thor as base


class StringRepetitionTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_untyped_repetition_keeps_cstring_coercion_through_concatenation(self):
        self.invoke(self.program('''
take(PAD+TITLE+" (animation off)",EXPECTED+TITLE+" (animation off)")
take("a"*3,"aaa"); take(3*"b","bbb")
take("a"*u8(3),"aaa")
take("x"*0,""); take("x"*1,"x")
take(""*max(u128),"")
take(("a"*2)*3,"aaaaaa")
local::"z"*2
take(local+"!","zz!")
a:cstring=PAD
b:cstring=3*"b"
assert(string(a)==EXPECTED && string(b)=="bbb")
''','''
PAD::" "*30
EXPECTED::"                              "
TITLE::"Title"
take::proc(value:cstring,expected:string){assert(string(value)==expected)}
'''))

    def test_explicit_string_type_is_preserved_and_requires_explicit_cstring_conversion(self):
        self.invoke(self.program('_=TEXT; take(TEXT)','''
TEXT:string:"x"*2
take::proc(value:cstring){}
'''),command='check',fail="to 'cstring'")
        self.invoke(self.program('_=TEXT; take(TEXT)','''
TYPED::string("x")
TEXT::TYPED*2
take::proc(value:cstring){}
'''),command='check',fail="to 'cstring'")

    def test_zero_repetition_keeps_the_native_type_of_conditional_and_concatenated_inputs(self):
        self.invoke(self.program('''
take(("a" when true else string("b"))*0)
assert(type_of(TYPED_CONCAT)==string && TYPED_CONCAT=="")
assert(type_of(TYPED_CHOICE)==string && TYPED_CHOICE=="")
assert(type_of(DISTINCT_CONCAT)==Label && DISTINCT_CONCAT==Label(""))
assert(type_of(DISTINCT_CHOICE)==Label && DISTINCT_CHOICE==Label(""))
''','''
Label::distinct string
TYPED_CONCAT::("a"+string("b"))*0
TYPED_CHOICE::("a" when false else string("b"))*0
DISTINCT_CONCAT::("a"+Label("b"))*0
DISTINCT_CHOICE::("a" when false else Label("b"))*0
take::proc(value:cstring){assert(string(value)=="")}
'''))

    def test_literals_precedence_constants_and_both_operand_orders(self):
        self.invoke(self.program('''
assert(X=="aaa" && Y=="aaab" && Z=="aaaaaaaaaaaaz")
assert("a"*3+"b" == "aaab")
assert(2*"a"+"b"*3 == "aabbb")
assert(("a"+"b")*3 == "ababab")
assert("a"*2*3 == "aaaaaa" && 2*(3*"a") == "aaaaaa")
assert("x"*(2+3) == "xxxxx" && "x"*2**3 == "xxxxxxxx")
assert(2*3+4 == 10)
''','X::"a"*3\nY::X+"b"\nZ::4*X+"z"\n'))

    def test_zero_one_empty_unicode_raw_escapes_and_bytes(self):
        self.invoke(self.program(r'''
assert("a"*0 == "" && 0*"a" == "" && "a"*1 == "a")
assert(""*20 == "" && ""*max(u128) == "")
assert("é😀"*2 == "é😀é😀")
assert(`a\n`*2 == `a\na\n`)
assert("\n\t\"\\"*2 == "\n\t\"\\\n\t\"\\")
assert("\x00\xff"*2 == "\x00\xff\x00\xff")
assert(len("é"*3)==6 && len("\x00"*3)==3)
'''))

    def test_typed_counts_and_distinct_string_identity(self):
        self.invoke(self.program('''
assert(TEXT==Label("ababab") && type_of(TEXT)==Label)
assert(Label("z")*0 == Label("") && type_of(Label("z")*0)==Label)
assert("a"*u8(3) == "aaa" && u128(2)*"b" == "bb")
assert("a"*Count(3) == "aaa")
''','Label::distinct string\nCount::distinct u32\nTEXT::Label("ab")*3\n'))

    def test_native_constant_counts_config_and_forward_declarations(self):
        source=self.program('assert(TEXT=="xxxxx" && CONFIGURED=="ababab")','''
TEXT::"x"*N
N::(20/4)%8
CONFIGURED::"ab"*#config(REPETITIONS,3)
''')
        self.invoke(source)
        self.invoke(source.replace('CONFIGURED=="ababab"','CONFIGURED=="abab"'),flags=['-define:REPETITIONS=2'])

    def test_imported_string_and_count_constants_and_name_shadowing(self):
        self.invoke(self.program('''
assert(RESULT=="ababab!")
{TEXT::"z"; N::2; assert(TEXT*N=="zz")}
assert(lib.TEXT*lib.N=="ababab")
''','import lib "library"\nRESULT::lib.N*lib.TEXT+"!"\n'),
            files={'library/main.thor':'package library\nTEXT::"ab"\nN::3\n'},directory=True)

    def test_generic_constant_parameters_and_pure_run_results(self):
        self.invoke(self.program('''
assert(repeat("ab",3)=="ababab" && repeat("x",0)=="")
assert(AUTO=="abab" && EXPLICIT=="abab" && COMBINED=="ababababz")
assert("x"*NUMBER=="xxx")
''','''
repeat::proc($text:string,$n:int)->string{return text*n}
make_text::fn()->string{return "ab"*2}
three::fn()->int{return 3}
AUTO::make_text()
EXPLICIT::#run make_text()
NUMBER::#run three()
COMBINED::2*EXPLICIT+"z"
'''))

    def test_fixed_repetition_in_generic_constraints_and_explicit_variable_count_boundary(self):
        self.invoke(self.program('assert(bounded("ab")==4)','''
bounded::proc($text:string)->int where len(text*2)<10 {return len(text*2)}
'''))
        self.invoke(self.program('_=bounded("ab",2)','''
bounded::proc($text:string,$n:int)->int where len(text*n)<10 {return n}
'''),command='check',fail='generic signature or type expression')

    def test_conditionals_switches_array_lengths_and_interpolation(self):
        self.invoke(self.program('''
#if "x"*3 == "xxx" {assert(true)} #else {undefined_unselected()}
CHOICE::#switch "a"*2 {case "aa":7;case:undefined_unselected}
assert(CHOICE==7)
values:[len("ab"*3)]int
assert(len(values)==6)
text:=f"{3*"ab"} {switch true{case true:"x"*2;case:"y"*2}}"
assert(text=="ababab xx"); delete(text)
'''))

    def test_unselected_compile_time_branches_remain_unchecked(self):
        self.invoke(self.program('''
#if false {BAD::"x"*(-1);_=BAD} #else {assert(true)}
when false {BAD::"x"*1.5;_=BAD}
GOOD::"ok" when true else "x"*(-1)
assert(GOOD=="ok")
SELECTED::#switch {case true:"ok";case:"x"*(-1)}
assert(SELECTED=="ok")
''','''
GLOBAL::"x"*2 when "a :: b"=="a :: b" else "x"*(-1)
'''))

    def test_constant_record_fields_as_string_and_count_operands(self):
        self.invoke(self.program('assert(TEXT=="ababab" && OTHER=="ababab")','''
Options::struct{text:string,count:int}
OPTIONS::Options{"ab",3}
TEXT::OPTIONS.text*OPTIONS.count
OTHER::OPTIONS.count*OPTIONS.text
'''))

    def test_defaults_sequences_sets_and_debug_builds(self):
        self.invoke(self.program('''
assert(default_text()=="abab")
assert(("ab"*3)[-1]=='b' && ("ab"*3)[-2:]=="ab")
names:=set[string]{"a"*2,2*"b"}
assert("aa" in names && "bb" in names);delete(names)
text:="hello"*2
assert(text=="hellohello") // Borrowed constant storage: no delete(text).
''','default_text::proc(text:="ab"*2)->string{return text}\n'),flags=['-debug'])

    def test_runtime_operands_are_rejected_even_for_zero_and_one(self):
        for body in ['text:="a";_=text*3','text:="a";_=text*0','text:="a";_=1*text',
                     'n:=3;_="a"*n','n:>3;_="a"*n']:
            with self.subTest(body=body):
                self.invoke(self.program(body),command='check',fail='compile-time constant operands')

    def test_local_constant_types_and_native_type_queries(self):
        self.invoke(self.program('''
Label::distinct string
LOCAL::Label("a")*3
assert(LOCAL==Label("aaa") && type_of(LOCAL)==Label)
assert("x"*len([3]int{})=="xxx")
assert("x"*size_of([3]int)=="x"*(3*size_of(int)))
assert("x"*3 == "xxx")
'''))

    def test_negative_non_integer_and_oversized_counts_are_rejected(self):
        for expression,message in [('"a"*(-1)','nonnegative count'),
                                   ('"a"*1.5','integer compile-time count'),
                                   ('"a"*true','integer compile-time count'),
                                   ('"a"*"b"','integer compile-time count'),
                                   ('"a"*(1<<30)','16 MiB')]:
            with self.subTest(expression=expression):
                self.invoke(self.program('_='+expression),command='check',fail=message)

    def test_generated_size_is_logarithmic_and_needs_no_execution_cache(self):
        root,result=self.invoke(self.program('assert(len(TEXT)==1000000)','TEXT::"x"*1000000\n'),flags=['--cache-report'])
        self.assertEqual(json.loads(result.stdout)['thor_cache']['writes'],0)
        generated=(root/'output/main.odin').read_text()
        self.assertLess(len(generated),30000)
        self.assertNotIn('strings.repeat',generated)


if __name__=='__main__':unittest.main()

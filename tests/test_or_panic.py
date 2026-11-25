"""Optional unwraps, exact-once evaluation, failures, ownership and evaluation."""
import json
import subprocess
import unittest

from tests import test_thor as base


UNCHECKED = ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']


class OrPanicTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_success_evaluates_once_in_checked_and_unchecked_builds(self):
        text = self.program('''
calls=0
value:=get(true) or_panic
if value!=7 || calls!=1 {panic("evaluation count")}
if false && (get(false) or_panic)==0 {panic("short circuit")}
if calls!=1 {panic("inactive operand")}
''', 'calls:int\nget::proc(ok:bool)->(int,bool){calls+=1;return 7,ok}\n')
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags): self.invoke(text, flags=flags)

    def test_failure_stderr_has_original_location_expression_procedure_and_status(self):
        text = self.program('''
// 😀 no generated file should appear in the report
    _ = get() or_panic
''', 'get::proc()->(int,bool){return 0,false}\n')
        for flags in [[], UNCHECKED, ['-debug', '--track-allocations=off']]:
            with self.subTest(flags=flags):
                root, result = self.invoke(text, flags=flags, fail='panic: unexpected failure')
                line = text[:text.index('get() or_panic')].count('\n')+1
                self.assertIn(f'{root.as_posix()}/input/main.thor({line}:9)', result.stderr.replace('\\','/'))
                self.assertIn('expression: get()', result.stderr)
                self.assertIn('procedure: main', result.stderr)
                self.assertIn('error: false', result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('.odin', result.stderr)
                self.assertNotIn('__thor_', result.stderr)

    def test_error_union_status_and_multiple_value_results(self):
        self.invoke(self.program('''
a,b:=get(false) or_panic
if a!=12 || b!="hello" {panic("multiple results")}
_,_ = get(true) or_panic
''', '''
Error::enum{Bad_Input}
Status::union{Error}
get::proc(fail:bool)->(int,string,Status){if fail{return 0,"",Error.Bad_Input};return 12,"hello",nil}
'''), fail='error: Bad_Input')

    def test_native_standard_library_error(self):
        self.invoke(self.program('_=strconv.parse_int("not a number") or_panic',
                                 'import "core:strconv"\n'), fail='error: false')
        _, result = self.invoke(self.program('_=os.read_entire_file("/does/not/exist/thor-or-panic", context.allocator) or_panic',
                                 'import "core:os"\n'), fail='unexpected failure')
        self.assertIn('error:', result.stderr)
        self.assertNotIn('error: false', result.stderr)

    def test_multiple_success_values_from_native_sdk_procedures(self):
        self.invoke(self.program('''
number,consumed:=strconv.parse_f64_prefix("12.5rest") or_panic
if number!=12.5 || consumed!=4 {panic("standard-library tuple")}
read,write:=os.pipe() or_panic
os.close(read);os.close(write)
''', 'import "core:strconv"\nimport "core:os"\n'))

    def test_unwrapping_does_not_bypass_transitive_purity(self):
        _, result = self.invoke(self.program('_=choose()', '''
state:int
get::proc()->(int,bool){return state,true}
choose::fn()->int{return get() or_panic}
'''), command='check', fail='Pure code')
        self.assertIn('state', result.stderr)

    def test_map_lookup_and_type_assertions(self):
        self.invoke(self.program('''
values:map[string]int;values["a"]=42;defer delete(values)
if (values["a"] or_panic)!=42 {panic("map")}
item:any=42
if (item.(int) or_panic)!=42 {panic("any assertion")}
variant:union{int,string}=42
if (variant.(int) or_panic)!=42 {panic("union assertion")}
'''))
        for expr in ['values["missing"]', 'item.(string)']:
            with self.subTest(expr=expr):
                self.invoke(self.program('values:=map[string]int{};item:any=42;_='+expr+' or_panic'),
                            fail='expression: '+expr)

    def test_optional_allocator_results_and_minimum_spread(self):
        self.invoke(self.program('''
values:=make([]int,2) or_panic;defer delete(values)
values[0],values[1]=8,3
least:=min(..values) or_panic
if least!=3 {panic("minimum")}
'''))

    def test_generic_named_args_indirect_calls_and_optional_ok(self):
        self.invoke(self.program('''
cb:proc(int)->(int,bool)=get
if (cb(4) or_panic)!=4 {panic("indirect")}
if (generic(value=i8(3)) or_panic)!=3 {panic("generic")}
if unwrap(i16(9))!=9 {panic("generic unwrap")}
''', '''
get::proc(value:int)->(int,bool){return value,true}
generic::proc(value:$T)->(T,bool) #optional_ok {return value,true}
unwrap::proc(value:$T)->T{return generic(value) or_panic}
'''))

    def test_contextless_and_custom_panic_handler(self):
        self.invoke(self.program('if read()!=4 {panic("contextless")}', '''
get::proc "contextless"()->(int,bool){return 4,true}
read::proc "contextless"()->int{return get() or_panic}
'''))
        self.invoke(self.program('read()', '''
get::proc "contextless"()->(int,bool){return 4,false}
read::proc "contextless"(){_=get() or_panic}
'''), fail='procedure: read')
        _, result = self.invoke(self.program('''
context.assertion_failure_proc=handler
_=get() or_panic
''', '''
import "base:runtime"
import "core:os"
handler::proc(prefix,message:string,loc:runtime.Source_Code_Location)->!{
 if prefix!="panic" || loc.procedure!="main" {os.exit(2)}
 os.exit(73)
}
get::proc()->(int,bool){return 0,false}
'''), fail='')
        self.assertEqual(result.returncode, 73, result.stderr)

    def test_pure_runtime_automatic_and_explicit_execution(self):
        self.invoke(self.program('''
if choose(7)!=7 || AUTO!=7 || EXPLICIT!=7 {panic("pure optional")}
''', '''
get::fn(x:int)->(int,bool){return x,x>0}
choose::fn(x:int)->int{return get(x) or_panic}
AUTO::choose(7)
EXPLICIT::#run choose(7)
'''))

    def test_failed_compile_time_execution_includes_expression_and_error(self):
        _, result = self.invoke(self.program('_=VALUE', '''
get::fn()->(int,bool){return 0,false}
choose::fn()->int{return get() or_panic}
VALUE::#run choose()
'''), command='check', fail='unexpected failure')
        self.assertIn('get()', result.stderr)
        self.assertIn('false', result.stderr)

    def test_big_number_results_have_independent_direct_ownership(self):
        self.invoke('''package test
import "core:testing"
import "thor:big"
get::proc()->(Integer,bool){return Integer(123456789012345678901234567890),true}
@(test) works::proc(t:^testing.T){
 a:=get() or_panic
 values:map[int]Integer;values[0]=a
 b:=values[0] or_panic
 delete(values[0])
 delete(values)
 a+=1
 if b+1!=a {panic("direct clone")}
 delete(a);delete(b)
 r:=Rational("0.(3)") or_panic
 if r*3!=Rational(1) {panic("rational")}
 delete(r)
}
''', command='test', flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_nested_fstrings_switches_and_negative_index_results(self):
        self.invoke(self.program('''
values:=[]int{1,2,3}
text:=f"{get(f"hello {7}") or_panic}"
if text!="hello 7" {panic("nested strings")};delete(text)
value:=switch{case true:get("ok") or_panic;case:"fallback"}
if value!="ok" {panic("switch")}
maps:map[int][]int;maps[0]=values
if (maps[0] or_panic)[-1]!=3 {panic("index")}
delete(maps)
''', 'get::proc(text:string)->(string,bool){return text,true}\n'))

    def test_native_file_can_still_use_or_panic_as_an_identifier(self):
        self.invoke(self.program('if native()!=4 {panic("native keyword")}'), directory=True,
                    files={'native.odin':'package main\nnative::proc()->int{or_panic:=4;return or_panic}\n'})

    def test_postfix_precedence_multiline_and_value_selectors(self):
        self.invoke(self.program('''
value := get() or_panic
if value!=4 {panic("newline")}
if get() or_panic + 2 != 6 {panic("precedence")}
if (pair() or_panic).x!=7 {panic("selector")}
''', '''
get::proc()->(int,bool){return 4,true}
pair::proc()->(struct{x:int},bool){return {7},true}
'''))

    def test_success_does_not_allocate_even_with_disabled_assertions(self):
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags):
                self.invoke(self.program('''
context.allocator=mem.panic_allocator()
context.temp_allocator=mem.panic_allocator()
if (get() or_panic)!=7 {panic("value")}
''', 'import "core:mem"\nget::proc()->(int,bool){return 7,true}\n'), flags=flags)

    def test_unicode_expression_locations(self):
        _, result = self.invoke(self.program('_=get("é😀") or_panic',
                    'get::proc(text:string)->(int,bool){return 0,false}\n'), fail='unexpected failure')
        self.assertIn('expression: get("é😀")', result.stderr)

    def test_standalone_executable_reports_source_without_source_files(self):
        text = self.program('_=get() or_panic', 'get::proc()->(int,bool){return 0,false}\n')
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags):
                root, _ = self.invoke(text, command='build', flags=flags)
                source = root/'input/main.thor'
                source.unlink()
                result = subprocess.run([str(root/root.name)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(source), result.stderr)
                self.assertIn('expression: get()', result.stderr)
                self.assertIn('error: false', result.stderr)

    def test_compile_time_failure_keeps_details_and_site_in_both_position_styles(self):
        text = self.program('_=VALUE', '''
get::fn()->(int,bool){return 0,false}
choose::fn()->int{return get() or_panic}
VALUE::#run choose()
''')
        for style in ['default', 'unix']:
            with self.subTest(style=style):
                root, result = self.invoke(text, command='check', flags=['-json-errors', '-error-pos-style:'+style], fail='unexpected failure')
                errors = json.loads(result.stderr)['errors']
                error = next(e for e in errors if 'unexpected failure' in e['msgs'][0])
                self.assertIn('error: false', error['msgs'])
                self.assertEqual(error['related'][0]['pos']['file'], str((root/'input/main.thor').resolve()))
                self.assertEqual(error['related'][0]['pos']['offset'], text.encode().index(b'get() or_panic'))

    def test_status_only_calls_and_nil_error_values(self):
        self.invoke(self.program('finish(false) or_panic;finish(true) or_panic', '''
Failure::union{int}
finish::proc(fail:bool)->Failure{if fail{return 13};return nil}
'''), fail='error: 13')

    def test_load_keeps_native_constant_semantics(self):
        self.invoke(self.program('if string(DATA)!="hello" {panic("load")}',
            'DATA::#load("hello.txt") or_panic\n'), files={'hello.txt': 'hello'})
        self.invoke(self.program('_=DATA', 'DATA::#load("missing.txt") or_panic\n'),
                    fail='unexpected failure')

    def test_invalid_operands_and_inner_errors_are_source_diagnostics(self):
        for operand in ['missing()', '42', 'bad()']:
            with self.subTest(operand=operand):
                _, result = self.invoke(self.program('_='+operand+' or_panic',
                    'bad::proc()->(int,int){return 1,2}\n'), command='check', fail='Error:')
                self.assertNotIn('runtime/optional', result.stderr)
                self.assertNotIn('__thor_', result.stderr)
                self.assertNotIn("'or_else'", result.stderr)

    def test_malformed_operator_uses_thor_spelling_in_parser_errors(self):
        for body in ['_=or_panic', 'or_panic']:
            with self.subTest(body=body):
                _, result = self.invoke(self.program(body), command='check', fail='or_panic')
                self.assertNotIn('or_return', result.stderr)


if __name__ == '__main__': unittest.main()

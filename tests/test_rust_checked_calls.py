"""Ordinary calls and checked calls have distinct storage lifetimes."""
from tests.test_rust_compiler import compiler_engines
import unittest
import re
from tests import test_rust_compiler as native


class RustCheckedCallTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def tracked(self,source,files=None):
        for flags in (['-debug'],native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags,files=files)
                self.assertEqual(result.stderr,'')

    def test_scalar_contracts_do_not_specialize_unrelated_record_schemas(self):
        fields='\n'.join(f'field_{index}:int,' for index in range(128))
        functions='\n'.join(f'value_{index}::fn(n:int)->int{{return n+{index}}}' for index in range(16))
        calls='\n'.join(f'if value_{index}(n)!=n+{index}{{panic("scalar contract")}}' for index in range(16))
        code=self.program('n:=7;'+calls+'\nif read(Pair{3,4})!=7{panic("record contract")}',
            f'Unrelated::struct{{{fields}}}\nPair::struct{{a,b:int}}\n'
            'sum::proc(value:Pair)->int{return value.a+value.b}\n'
            'read::fn(value:Pair)->int{return sum(value)}\n'+functions+'\n')
        for flags in ([],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(code,flags=flags)
                for engine in compiler_engines():
                    generated=next((self.root/engine).rglob('main.odin')).read_text()
                    dispatchers=re.findall(r'(pure_validate(?:_[0-9a-f]+)?_mode)::proc[^\n]*\n(.*?)\n\}',generated,re.S)
                    self.assertEqual(len(dispatchers),2)
                    for name,dispatcher in dispatchers:
                        self.assertNotIn('type_struct_field_count',dispatcher)
                        self.assertNotIn('field_127',dispatcher)
                        self.assertIn(name+'_record(T,Result',dispatcher)
                    self.assertEqual(generated.count('field_127'),1)
                    self.assertIn('type_struct_field_count',generated)

    def test_resolved_schema_depth_falls_back_to_the_native_contract_diagnostic(self):
        records='\n'.join(f'Level_{index}::struct{{value:Level_{index+1}}}' for index in range(27))
        records+='\nLevel_27::int\nwork::fn(value:Level_0)->int{return 7}\n'
        self.compare(self.program('value:Level_0;_=work(value)',records),
            command='check',fail='deeper than 24 levels')

    def test_single_statement_returns_keep_owned_promotions_and_original_bodies(self):
        self.tracked(self.program('''
for flag in [false,true]{text:=work(flag);if text!=(flag ? "yes" : "no"){panic("checked do return")};delete(text)}
text:=leaf(true);defer delete(text);if text!="yes"{panic("ordinary do return")}
''','''
leaf::proc(flag:bool)->string{if flag do return f"yes";return "no"}
work::fn(flag:bool)->string{return leaf(flag)}
'''))

    def test_generic_interpolation_uses_selected_native_format_contracts(self):
        self.tracked(self.program('''
n:=3;value:=f64(1.5);text:=work(n);defer delete(text)
other:=work(value);defer delete(other)
if text!="value 3"||other!="value 1.5"{panic("generic format")}
''','''
leaf::proc(value:$T)->string{return f"value {value}"}
work::fn(value:$T)->string{return leaf(value)}
'''))
        self.compare(self.program('row:=Row{3};_=work(row)','''
Row::struct{value:int}
leaf::proc(value:$T)->string{return f"value {value}"}
work::fn(value:$T)->string{return leaf(value)}
'''),command='check',fail='Pure formatting supports deterministic scalar/string values only')

    def test_generic_scalar_transmute_checks_the_selected_types(self):
        self.tracked(self.program('n:=u64(0x3ff0000000000000);if work(f64,n)!=1{panic("generic transmute")}','''
leaf::proc($T:typeid,value:$S)->T{return transmute(T)value}
work::fn($T:typeid,value:$S)->T{return leaf(T,value)}
'''))
        self.compare(self.program('value:=Row{0,0};_=work(u64,value)','''
Row::struct{low,high:u32}
leaf::proc($T:typeid,value:$S)->T{return transmute(T)value}
work::fn($T:typeid,value:$S)->T{return leaf(T,value)}
'''),command='check',fail='Pure transmute requires scalar')

    def test_generic_overloads_defer_native_candidate_selection(self):
        self.tracked(self.program('n:=3;v:=f64(4);if work(n)!=3||work(v)!=4{panic("generic overloads")}','''
integer::proc(value:int)->int{return value}
real::proc(value:f64)->f64{return value}
pick::proc{integer,real}
work::fn(value:$T)->T{return pick(value)}
'''))

    def test_generic_overloads_check_only_the_selected_effects(self):
        prefix='''
state:int
integer::proc(value:int)->int{return value}
real::proc(value:f64)->f64{return value+f64(state)}
pick::proc{integer,real}
work::fn(value:$T)->T{return pick(value)}
'''
        self.tracked(self.program('n:=3;if work(n)!=3{panic("inactive overload")}',prefix))
        self.compare(self.program('n:=f64(3);_=work(n)',prefix),command='check',fail='outside the function')

    def test_reexported_helpers_keep_transitive_package_access(self):
        for extension in ('thor','odin'):
            with self.subTest(extension=extension):
                self.tracked(self.program('n:=3;if work(n)!=4{panic("transitive alias")}','''
import "bridge"
work::fn(n:int)->int{return bridge.value(n)}
'''),files={
                    'bridge/main.thor':'package bridge\nimport leaf "../leaf_'+extension+'"\nvalue::leaf.value\n',
                    'leaf_'+extension+'/main.'+extension:'package leaf\nvalue::proc(n:int)->int{return n+1}\n'})

    def test_qualified_builtin_and_constant_aliases_keep_pure_contracts(self):
        self.tracked(self.program('values:=[1,2,3];if work(values[:])!=3{panic("builtin alias")}','''
import b "base:builtin"
LENGTH::b.len
work::fn(values:[]int)->int{return LENGTH(values)}
'''))
        self.compare(self.program('values:=[1,2,3];_=work(values[:])','''
import b "base:builtin"
RELEASE::b.delete
work::fn(values:[]int)->int{RELEASE(values);return 0}
'''),command='check',fail='borrowed from an input')

    def test_qualified_type_query_alias_does_not_check_unevaluated_conversions(self):
        self.tracked(self.program('n:=3;if outer(^int,n)!=3{panic("query alias")}','''
import b "base:builtin"
QUERY::b.type_of
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{_=QUERY(cast(T)uintptr(value));return value}
    return inner(n)
}
'''))

    def test_owned_checked_results_and_ordinary_shallow_aliases(self):
        self.tracked(self.program('''n:=3
if work(n)!=3{panic("checked helper result must be independent")}
a:=[]int{n};b:=borrow(a);a[0]=9
if b[0]!=9{panic("ordinary helper keeps shallow results")}
''','''borrow::proc(values:[]int)->[]int{return values}
work::fn(n:int)->int{a:=[]int{n};b:=borrow(a);a[0]=9;return b[0]}
'''))

    def test_transitive_owned_helpers_and_deferred_result_updates(self):
        self.tracked(self.program('''n:=3
values,text:=work(n);defer delete(values);defer delete(text)
if values[0]!=9||text!="number 3"{panic("checked deferred result")}
''','''leaf::proc(n:int)->(values:[]int,text:string){values=make([]int,1);values[0]=n;text=f"number {n}";defer values[0]=9;return}
middle::proc(n:int)->([]int,string){return leaf(n)}
work::fn(n:int)->([]int,string){return middle(n)}
'''))

    def test_checked_helpers_keep_anonymous_string_cleanup(self):
        self.tracked(self.program('n:=3;if work(n)!=8{panic("anonymous string")}', '''
read::proc(text:string)->int{return len(text)}
helper::proc(n:int)->int{return read(f"number {n}")}
work::fn(n:int)->int{return helper(n)}
'''))

    def test_generic_helpers_use_concrete_result_storage(self):
        self.tracked(self.program('''n:=3;values:=work(n);defer delete(values)
if values[0]!=n{panic("generic owned result")}
''','''identity::proc(value:$T)->T{return value}
work::fn(n:int)->[]int{values:=make([]int,1);values[0]=n;return identity(values)}
'''))

    def test_multiple_result_slots_keep_concrete_and_generic_types(self):
        self.tracked(self.program('n:=3;if work(n)!=3{panic("multiple-result types")}', '''
pair::proc(value:$T)->(T,bool){return value,true}
validate::proc(ok:bool){if !ok{panic("status")}}
forward::proc(value:$T)->T{result,ok:=pair(value);validate(ok);return result}
work::fn(n:int)->int{
    result,ok:=pair(n);validate(ok)
    chosen,matched:=switch n{case 3:result,true;case:result,false}
    validate(matched)
    if value,valid:=pair(chosen);valid{return forward(value)}
    return 0
}
'''))

    def test_ignored_parameters_still_validate_and_forward_owned_results(self):
        self.tracked(self.program('''n:=3
text:=work(n);defer delete(text)
if scalar(n)!=3||text!="ignored"{panic("ignored parameters")}
''','''helper::proc(_:int)->string{return "ignored"}
scalar::fn(_:int)->int{return 3}
work::fn(_:int)->string{return helper(3)}
'''))

    def test_out_parameter_allocations_keep_the_enclosing_lifetime(self):
        self.tracked(self.program('''n:=3;values:=work(n);defer delete(values)
if len(values)!=n||values[2]!=5{panic("checked out parameter")}
''','''fill::proc(out:^[]int,n:int){out^=make([]int,n);for i in 0..<n{out^[i]=i+n}}
work::fn(n:int)->[]int{values:[]int;fill(&values,n);return values}
'''))

    def test_original_procedure_names_survive_checked_and_nested_bodies(self):
        self.tracked(self.program('''n:=3;text:=work(n);defer delete(text)
if text!="helper"{panic("procedure names")}
''','''name_length::proc()->int{return len(#procedure)}
work::fn(n:int)->string{helper::proc()->string{return #procedure};_=n;if name_length()!=11{panic("scalar name")};return helper()}
'''))

    def test_contextless_scalar_calls_remain_eligible(self):
        self.tracked(self.program('n:=3;if work(n)!=3{panic("contextless scalar")}', '''
helper::proc "contextless"(n:int)->int{return n}
work::fn(n:int)->int{return helper(n)}
'''))

    def test_contextless_owned_result_has_a_clear_rejection(self):
        self.compare(self.program('n:=3;_=work(n)', '''
helper::proc "contextless"()->string{return "hello"}
work::fn(n:int)->string{_=n;return helper()}
'''),command='check',fail='Contextless compile-time procedures cannot return strings/slices')

    def test_bare_return_does_not_become_valid_for_unnamed_owned_results(self):
        self.compare(self.program('_=work(3)','work::fn(n:int)->[]int{_=n;return}\n'),command='check',fail='return values explicitly')

    def test_required_results_are_preserved_on_checked_helpers(self):
        self.compare(self.program('n:=3;_=work(n)', '''
@(require_results) helper::proc(n:int)->int{return n}
work::fn(n:int)->int{helper(n);return n}
'''),command='check',fail='must be handled')

    def test_shared_generic_helper_selects_mixed_pure_and_ordinary_overloads(self):
        self.tracked(self.program('''i:=3;f:=f64(4)
if integer(i)!=9||floating(f)!=16{panic("mixed checked overloads")}
''','''as_int::fn(value:int)->int{return value*value}
as_float::proc(value:f64)->f64{return value*value}
square::proc{as_int,as_float}
dispatch::proc(value:$T)->T{return square(value)}
integer::fn(value:int)->int{return dispatch(value)}
floating::fn(value:f64)->f64{return dispatch(value)}
'''))

    def test_private_procedures_cannot_be_exposed_through_checked_versions(self):
        self.compare(self.program('n:=3;_=work(n)','import "library"\nwork::fn(n:int)->int{return library.hidden(n)}\n'),
                     files={'library/main.odin':'package library\n@private\nhidden::proc(n:int)->int{return n}\n'},
                     command='check',fail='private to its package')
        for prefix in ('@(private="file")\n','#+private file\n'):
            text='package main\n'+prefix+'hidden::proc(n:int)->int{return n}\n' if prefix.startswith('@') else prefix+'package main\nhidden::proc(n:int)->int{return n}\n'
            with self.subTest(prefix=prefix):
                self.compare(self.program('n:=3;_=work(n)','work::fn(n:int)->int{return hidden(n)}\n'),files={'hidden.odin':text},
                             directory=True,command='check',fail='private to its file')

    def test_same_named_file_private_helpers_keep_distinct_checked_bodies(self):
        source=self.program('n:=3;if first(n)!=4||second(n)!=5{panic("private checked names")}', '''
@(private="file") helper::proc(n:int)->int{return n+1}
first::fn(n:int)->int{return helper(n)}
''')
        files={'other.thor':'''package main
@(private="file") helper::proc(n:int)->int{return n+2}
second::fn(n:int)->int{return helper(n)}
'''}
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(source,files=files,flags=flags,directory=True)

    def test_public_generic_branch_with_eligible_arms_is_checked(self):
        self.tracked(self.program('n:=3;if work(n)!=3{panic("generic branch")}', '''
identity::proc(value:$T)->T{return value}
work::fn(value:$T)->T{when T==int{return identity(value)}else{return value}}
'''))

    def test_checked_result_copy_restores_writable_access_from_readonly_inputs(self):
        self.tracked(self.program('values :> []int{3};if work(values)!=9||values[0]!=3{panic("checked result storage")}', '''
borrow::proc(values:[]int)->[]int{return values}
work::fn(values:[]int)->int{copy:=borrow(values);copy[0]=9;return copy[0]}
'''))

    def test_alias_and_mut_permission_requests_are_not_themselves_purity_effects(self):
        self.tracked(self.program('values:=[]int{3};if work(values)!=3{panic("read-only helper effects")}', '''
read::proc(#mut values:[]int)->int{alias:=values;return alias[0]}
work::fn(values:[]int)->int{return read(values)}
'''))
        for statement in ('alias[0]=9','delete(alias)'):
            with self.subTest(statement=statement):
                self.compare(self.program('values:=[]int{3};_=work(values)', '''
change::proc(#mut values:[]int){alias:=values;'''+statement+'''}
work::fn(values:[]int)->int{change(values);return 0}
'''),command='check',fail='borrowed from an input')

    def test_imported_public_helper_keeps_private_implementation_access(self):
        self.tracked(self.program('n:=3;if work(n)!=3{panic("imported checked helper")}', '''
import "library"
work::fn(n:int)->int{values:=[]int{n};copy:=library.borrow(values);values[0]=9;return copy[0]}
'''),files={'library/main.thor':'''package library
@(private="file") implementation::proc(values:[]int)->[]int{return values}
borrow::proc(values:[]int)->[]int{return implementation(values)}
'''})

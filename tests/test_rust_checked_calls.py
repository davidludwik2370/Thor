"""Ordinary calls and checked calls have distinct storage lifetimes."""
import unittest
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

    def test_unported_public_generic_schema_retains_migration_status(self):
        import json
        self.source.write_text(self.program('n:=3;_=work(n)', '''
identity::proc(value:$T)->T{return value}
work::fn(value:$T)->T{return identity(value)}
'''))
        reference=self.invoke('python',command='check')
        self.assertEqual(reference.returncode,0,reference.stderr)
        native=self.invoke('rust',command='check',flags=['-json-errors'])
        self.assertEqual(native.returncode,3,native.stderr)
        self.assertEqual(json.loads(native.stderr)['errors'][0]['stage'],'rust-migration')

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

"""Automatic constant evaluation, generic scheduling, and ownership acceptance."""
import json
import subprocess
import unittest

from tests import test_thor as base


class AutomaticTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_debug_retains_runtime_fn_calls_but_evaluates_required_constants(self):
        root,_=self.invoke(self.program('''
runtime:=square(3)
immutable:>square(4)
constant::square(5)
fixed:[square(2)]int
assert(runtime==9&&immutable==16&&constant==25&&len(fixed)==4)
assert(take(square(2))==4)
explicit:=#run square(6)
assert(explicit==36)
''','''
square::fn(n:int)->int{result:=n*n;return result}
take::proc($N:int)->int{return N}
'''),flags=['-debug','-o:none'])
        generated=next((root/'output').rglob('main.odin')).read_text()
        self.assertIn('runtime:=square(3)',generated)
        self.assertIn('square(4)',generated)
        self.assertNotIn('constant::square(5)',generated)

    def test_debug_runtime_assertion_can_be_reached_instead_of_executed_by_helper(self):
        self.invoke(self.program('if false{_=work(3)}','work::fn(n:int)->int{assert(false);return n}\n'),
            flags=['-debug','-o:none'])

    def test_forward_and_dependent_constants_are_native_constants(self):
        self.invoke(self.program('data:[SECOND]int;assert(len(data)==256&&FIRST==16)', '''
SECOND::square(FIRST)
FIRST::square(4)
square::fn(value:int)->int{return value*value}
#assert(SECOND==256)
'''))

    def test_constant_arguments_expressions_and_nested_calls(self):
        self.invoke(self.program('assert(VALUE==17&&OTHER==25)', '''
add::fn(a,b:int)->int{return a+b}
square::fn(n:int)->int{return n*n}
BASE::3
VALUE::add(square(BASE+1),1)
OTHER::#run square(add(2,3))
'''))

    def test_known_generic_overloads_defaults_and_import_aliases(self):
        self.invoke(self.program('assert(INT==9&&FLOAT==2.25&&DEFAULT==7&&IMPORTED==16)', '''
import "library" / square
integer::fn(n:int)->int{return n*n}
floating::fn(n:f64)->f64{return n*n}
group::proc{integer,floating}
defaults::fn(n:int=7)->int{return n}
INT::group(3)
FLOAT::group(f64(1.5))
DEFAULT::defaults()
IMPORTED::square(n=4)
'''),files={'library/main.thor':'package library\nsquare::fn(n:$T)->T{return n*n}\n'})

    def test_runtime_and_immutable_arguments_remain_runtime(self):
        _,result=self.invoke(self.program('''
value:=3
value=5
fixed:>4
assert(square(value)==25&&square(fixed)==16)
''','square::fn(n:int)->int{return n*n}\n'),flags=['--cache-report'],expected=None)
        report=next(json.loads(line)['thor_cache'] for line in result.stdout.splitlines() if line.startswith('{"thor_cache"'))
        self.assertEqual(report['misses'],0)

    def test_side_effecting_argument_is_not_executed_by_a_helper(self):
        self.invoke(self.program('assert(square(next())==9&&calls==1)', '''
calls:int
next::proc()->int{calls+=1;return 3}
square::fn(n:int)->int{return n*n}
'''))

    def test_type_queries_do_not_execute_calls(self):
        self.invoke(self.program('value:type_of(explode());assert(value==0)',
            'explode::fn()->int{assert(false,"not evaluated");return 0}\n'))

    def test_known_type_queries_do_not_copy_runtime_initializers(self):
        self.invoke(self.program('''
value:=next()
BYTES::identity(size_of(value))
TYPED::bytes(type_of(value))
QUERY::identity(size_of(next()))
assert(BYTES==4&&TYPED==4&&QUERY==4&&calls==1)
''','''
calls:int
next::proc()->i32{calls+=1;return 3}
identity::fn(n:int)->int{return n}
bytes::fn($T:typeid)->int{return size_of(T)}
'''))

    def test_conditional_functions_follow_selected_declaration(self):
        self.invoke(self.program('assert(VALUE==7)', '''
when true{work::fn(n:int)->int{return n+1}}
else{work::proc(n:int)->int{assert(false);return n}}
VALUE::work(6)
'''))
        self.invoke(self.program('value:=work(6);assert(value==7&&calls==1)', '''
calls:int
when true{work::proc(n:int)->int{calls+=1;return n+1}}
else{work::fn(n:int)->int{assert(false);return n}}
'''))

    def test_known_lazy_paths_and_both_compile_switch_forms(self):
        self.invoke(self.program('''
assert(VALUE==7&&OTHER==9&&CHOICE==4&&BOOL_CHOICE==16)
_=false&&explode_bool();_=true||explode_bool()
''','''
explode::fn()->int{assert(false);return 0}
explode_bool::fn()->bool{assert(false);return false}
square::fn(n:int)->int{return n*n}
VALUE::7 when true else explode()
OTHER::true ? 9 : explode()
CHOICE::#switch square(2){case 4:square(2);case:explode()}
BOOL_CHOICE::#switch{case square(2)==4:square(4);case explode_bool():0;case:1}
when false{UNUSED::explode()}
'''))

    def test_unknown_runtime_paths_preserve_lazy_execution(self):
        self.invoke(self.program('''
flag:=false
if flag{_=explode()}
_=flag&&explode_bool()
value:=flag ? explode() : 7
choice:=switch flag{case true:explode();case:7}
for flag{_=explode()}
assert(value==7&&choice==7)
''','explode::fn()->int{assert(false);return 0}\nexplode_bool::fn()->bool{assert(false);return false}\n'))

    def test_constant_result_types_records_arrays_and_exact_bits(self):
        self.invoke(self.program('''
assert(PAIR==[7,8]&&REPORT.value==7&&TEXT=="\\x00\\xff")
assert(transmute(u64)ZERO==0x8000000000000000)
''','''
Report::struct{value:int}
pair::fn(n:int)->[2]int{return [n,n+1]}
report::fn(n:int)->Report{return {n}}
text::fn()->string{return f"\\x00\\xff"}
zero::fn()->f64{return -f64(0)}
PAIR::pair(7)
REPORT::report(7)
TEXT::text()
ZERO::zero()
'''))

    def test_ordinary_procedures_still_require_explicit_run(self):
        self.invoke(self.program('assert(VALUE==7)','work::proc()->int{return 7}\nVALUE::#run work()\n'))
        self.invoke(self.program('_=VALUE','work::proc()->int{return 7}\nVALUE::work()\n'),command='check',fail='global scope')

    def test_constant_cycles_and_execution_failures_are_diagnostics(self):
        self.invoke(self.program('_=FIRST','identity::fn(n:int)->int{return n}\nFIRST::identity(SECOND)\nSECOND::identity(FIRST)\n'),command='check',fail='Cyclic')
        self.invoke(self.program('_=VALUE','bad::fn()->int{assert(false,"oops");return 0}\nVALUE::bad()\n'),command='check',fail='execution failed')

    def test_auto_calls_use_persistent_cache_and_keep_validation(self):
        source=self.program('assert(VALUE==25)','square::fn(n:int)->int{return n*n}\nVALUE::square(5)\n')
        root,first=self.invoke(source,flags=['--cache-report'])
        report=lambda r:next(json.loads(s)['thor_cache'] for s in r.stdout.splitlines() if s.startswith('{"thor_cache"'))
        second=subprocess.run([str(base.THOR),'run',str(root/'input/main.thor'),'--output-dir',str(root/'output'),'--cache-report'],capture_output=True,text=True,timeout=40)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertGreater(report(first)['writes'],0)
        self.assertGreater(report(second)['hits'],0)
        (root/'input/main.thor').write_text(source.replace('return n*n','_=context;return n*n'))
        bad=subprocess.run([str(base.THOR),'check',str(root/'input/main.thor')],capture_output=True,text=True,timeout=40)
        self.assertNotEqual(bad.returncode,0)
        self.assertIn('ambient context',bad.stderr)


class GenericTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_inferred_array_count_parameters(self):
        self.invoke(self.program('a:[2]i32;b:[3]f64;assert(count(a)==4&&count(b)==9)', '''
square::fn(n:int)->int{return n*n}
count::proc(value:[$N]$T)->int{SIZE::square(N);return SIZE}
'''))

    def test_inferred_count_from_a_forward_evaluated_array_length(self):
        self.invoke(self.program('a:[SIZE+1]i32;assert(count(a)==100)', '''
square::fn(n:int)->int{return n*n}
count::proc(value:[$N]$T)->int{SIZE::square(N);return SIZE}
SIZE::square(3)
'''))

    def test_caller_record_types_pass_through_generic_packages(self):
        self.invoke(self.program('a:=Small{};b:=Large{};assert(library.forward(a)==4&&library.forward(b)==8)', '''
import "library"
Small::struct{x:i32}
Large::struct{x:i64}
'''),files={'library/main.thor':'''package library
bytes::fn($T:typeid)->int{return size_of(T)}
work::proc(value:$T)->int{VALUE::#run bytes(T);return VALUE}
forward::proc(value:$T)->int{return work(value)}
'''})

    def test_record_identity_from_a_nonentry_caller_package(self):
        self.invoke(self.program('assert(middle.work()==4)','import "middle"\n'),files={
            'middle/main.thor':'''package middle
import "../library"
Record::struct{x:i32}
work::proc()->int{value:=Record{};return library.bytes(value)}
''', 'library/main.thor':'''package library
width::fn($T:typeid)->int{return size_of(T)}
bytes::proc(value:$T)->int{N::#run width(T);return N}
'''})

    def test_same_spelled_local_and_generic_record_types_are_distinct(self):
        self.invoke(self.program('a:=i32(3);b:=f64(3);assert(first()==4&&second()==8&&generic(a)==4&&generic(b)==8)', '''
import "library"
first::proc()->int{Local::struct{x:i32};a:=Local{};return library.forward(a)}
second::proc()->int{Local::struct{x:i64};a:=Local{};return library.forward(a)}
generic::proc(value:$T)->int{Local::struct{x:T};a:=Local{};return library.forward(a)}
'''),files={'library/main.thor':'''package library
bytes::fn($T:typeid)->int{return size_of(T)}
work::proc(value:$T)->int{VALUE::#run bytes(T);return VALUE}
forward::proc(value:$T)->int{return work(value)}
'''})

    def test_local_type_identity_in_native_test_mode(self):
        self.invoke('''package main
import "core:testing"
import "library"
@(test) local::proc(t:^testing.T){Local::struct{x:i32};a:=Local{};testing.expect(t,library.bytes(a)==4)}
''',command='test',files={'library/main.thor':'''package library
width::fn($T:typeid)->int{return size_of(T)}
bytes::proc(value:$T)->int{N::#run width(T);return N}
'''})

    def test_type_and_constant_parameters_schedule_independently(self):
        self.invoke(self.program('a:=3;b:=f64(3);assert(work(2,a)==12&&work(3,b)==17)', '''
square::fn(n:int)->int{return n*n}
bytes::fn($T:typeid)->int{return size_of(T)}
work::proc($N:int,value:$T)->int{
 SIZE::#run square(N)
 BYTES::bytes(T)
 data:[SIZE]int
 return len(data)+BYTES
}
'''))

    def test_generic_selected_branches_do_not_execute_other_instances(self):
        self.invoke(self.program('a:=3;b:="text";assert(work(a)==9&&work(b)==7)', '''
square::fn(n:int)->int{return n*n}
explode::fn()->int{assert(false);return 0}
work::proc(value:$T)->int{
 when T==int{VALUE::square(3);return VALUE}
 else when T==string{return 7}
 else{return #run explode()}
}
'''))

    def test_nested_generic_forwarding_and_constant_expressions(self):
        self.invoke(self.program('a:=3;assert(outer(2,a)==9&&outer(4,a)==25)', '''
square::fn(n:int)->int{return n*n}
inner::proc($N:int,value:$T)->int{VALUE::square(N+1);return VALUE}
outer::proc($N:int,value:$T)->int{return inner(N,value)}
'''))

    def test_recursive_constant_generic_selection(self):
        self.invoke(self.program('a:=7;assert(work(3,a)==14)', '''
square::fn(n:int)->int{return n*n}
work::proc($N:int,value:$T)->int{
 when N==0{return 0}else{VALUE::square(N);return VALUE+work(N-1,value)}
}
'''))

    def test_const_boolean_and_string_parameters(self):
        self.invoke(self.program('a:=1;assert(work(true,"abc",a)==3&&work(false,"x",a)==0)', '''
length::fn(s:string)->int{return len(s)}
work::proc($Use:bool,$Text:string,value:$T)->int{
 when Use{VALUE::length(Text);return VALUE}else{return 0}
}
'''))

    def test_imported_generic_and_named_arguments(self):
        self.invoke(self.program('a:=3;assert(library.work(value=a,N=3)==9)', 'import "library"\n'),files={
            'library/main.thor':'package library\nsquare::fn(n:int)->int{return n*n}\nwork::proc($N:int,value:$T)->int{VALUE::square(N);return VALUE}\n'})

    def test_normal_runtime_parameter_is_not_a_generic_constant(self):
        self.invoke(self.program('a:=3;_=work(a)', '''
square::fn(n:int)->int{return n*n}
work::proc(value:$T)->int{return #run square(value)}
'''),command='check',fail='compile-time known')


class OwnedTests(unittest.TestCase):
    invoke=base.ThorTests.invoke

    def memory(self,body,prefix=''):
        return self.invoke('package main\nimport "core:testing"\nimport pure "thor:pure"\n'+prefix+
            '\n@(test) lifetime::proc(t:^testing.T){\n'+body+'\n}\n',command='test',
            flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_folded_and_runtime_results_own_independent_storage(self):
        self.memory('''
a:=work("same");b:=work("same");input:="same";runtime:=work(input)
testing.expect(t,a.title==runtime.title&&b.rows[0]=="same")
testing.expect(t,raw_data(a.title)!=raw_data(b.title)&&raw_data(a.rows[0])!=raw_data(b.rows[0]))
pure.destroy(a);pure.destroy(b);pure.destroy(runtime)
''','''
Result::struct{title:string,rows:[]string}
work::fn(input:string)->Result{data:=make([]string,1);data[0]=pure.clone(input);return {pure.clone(input),data}}
''')

    def test_repeated_use_inside_runtime_loop_materializes_each_result(self):
        self.memory('''
for i in 0..<3{
 a:=folded();b:=#run work();a[0]=i
 testing.expect(t,b[0]==7);pure.destroy(a);pure.destroy(b)
}
''','work::fn()->[]int{return pure.clone([]int{7,8})}\nfolded::proc()->[]int{return work()}\n')

    def test_specialized_owned_and_fixed_array_results(self):
        self.memory('a:=1;work(2,a);work(3,a)', '''
repeat::fn($N:int)->[N]string{result:[N]string;for i in 0..<N{result[i]=f"text"};return result}
work::proc($N:int,value:$T){result:=repeat(N);assert(len(result)==N);pure.destroy(result)}
''')

    def test_slice_containing_constants_keep_the_accepted_restriction(self):
        self.invoke('package main\nwork::fn()->[]int{return nil}\nVALUE::work()\nmain::proc(){_=VALUE}\n',command='check',fail='runtime binding')

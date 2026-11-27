"""Declared callback arity selects element or argument-bundle mapping."""
import unittest
from tests import test_pmap as base


class ParallelMapInputTests(unittest.TestCase):
    invoke=base.ParallelMapTests.invoke
    program=base.ParallelMapTests.program

    def checked_and_unchecked(self,source):
        for flags in ([],base.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_scalar_inputs_generic_aliases_and_named_result_overrides(self):
        self.checked_and_unchecked(self.program('''
values:=[]i16{2,3}
rows:=basic.pmap(values,alias,workers=4)
defer delete(rows)
renamed:=basic.pmap(jobs=values,calculate=alias,result_type=Result,workers=1)
defer delete(renamed)
if rows[1].number!=6||!rows[0].ok||renamed[0].answer!=4{panic("scalar input")}
''','''import "thor:basic"
calculate::proc(x:$T)->(number:T,ok:bool){return x*2,true}
alias::calculate
Result::struct{answer:i16,valid:bool}
'''))

    def test_struct_and_empty_struct_inputs_are_whole_values(self):
        self.checked_and_unchecked(self.program('''
values:=[2]Row{{2,3},{4,5}}
rows:=basic.pmap(values[:],sum)
defer delete(rows)
copies:=basic.pmap(values[:],identity)
defer delete(copies)
copies[0].a=9
empty:[3]struct{}
counts:=basic.pmap(empty[:],take_empty)
defer delete(counts)
if rows[1]!=9||values[0].a!=2||copies[1].b!=5||len(counts)!=3||counts[2]!=7{panic("whole struct")}
''','''import "thor:basic"
Row::struct{a,b:int}
sum::proc(row:Row)->int{return row.a+row.b}
identity::proc(row:$T)->T{return row}
take_empty::proc(row:struct{})->int{return 7}
'''))

    def test_named_results_follow_rune_and_i32_overload_identity(self):
        source = self.program('''
chars := basic.pmap([]rune{'a'}, classify)
defer delete(chars)
numbers := basic.pmap([]i32{65}, classify)
defer delete(numbers)
if chars[0].character!='a' || numbers[0].integer!=65 {panic("overload result")}
''', '''import "thor:basic"
by_rune::fn(c:rune)->(character:rune, ok:bool){return c,true}
by_i32::fn(n:i32)->(integer:i32, ok:bool){return n,true}
classify::proc{by_rune,by_i32}
''')
        for flags in ([], ['-disable-assert'], base.UNCHECKED):
            with self.subTest(flags=flags):
                self.invoke(source, flags=flags)

    def test_fixed_arrays_and_slices_are_single_arguments(self):
        self.checked_and_unchecked(self.program('''
arrays:=[][2]int{{1,2},{3,4}}
rows:=basic.pmap(arrays,sum_array)
defer delete(rows)
buffers:=[][]int{arrays[0][:],arrays[1][:]}
basic.pmap(buffers,write_slice)
if rows[1]!=7||arrays[0][0]!=11||arrays[1][0]!=13{panic("array or slice input")}
''','''import "thor:basic"
sum_array::proc(values:[2]int)->int{return values[0]+values[1]}
write_slice::proc(#mut values:[]int){values[0]+=10}
'''))

    def test_pointer_elements_and_pointer_fields_modify_original_pointees(self):
        self.checked_and_unchecked(self.program('''
values:[4]int
pointers:=[]^int{&values[0],&values[1]}
basic.pmap(pointers,set_pointer)
jobs:=[]Job{{3,&values[2]},{4,&values[3]}}
basic.pmap(jobs,set_parts)
if values!=[7,7,3,4]{panic("pointer arguments")}
''','''import "thor:basic"
Job::struct{number:int,destination:^int}
set_pointer::proc(destination:^int){destination^=7}
set_parts::proc(number:int,destination:^int){destination^=number}
'''))

    def test_struct_single_input_never_implicitly_spreads_or_takes_an_address(self):
        prefix='''import "thor:basic"
Row::struct{number:int}
integer::proc(value:int)->int{return value}
pointer::proc(value:^Row){value.number=7}
'''
        for callback in ('integer','pointer'):
            with self.subTest(callback=callback):
                self.invoke(self.program('_=basic.pmap([]Row{{1}},'+callback+')',prefix),command='check',fail='Error:')

    def test_defaults_count_as_parameters_and_variadics_use_bundles(self):
        self.checked_and_unchecked(self.program('''
rows:=basic.pmap([]struct{number:int}{{2},{3}},add)
defer delete(rows)
totals:=basic.pmap([]struct{a,b,c:int}{{1,2,3}},sum)
defer delete(totals)
if rows[1]!=8||totals[0]!=6{panic("default and variadic mode")}
''','''import "thor:basic"
add::proc(number:int,extra:int=5)->int{return number+extra}
sum::proc(values:..int)->int{total:=0;for value in values{total+=value};return total}
'''))

    def test_overloads_with_two_valid_input_modes_require_explicit_selection(self):
        prefix='''import "thor:basic"
Row::struct{a,b:int}
whole::proc(row:Row)->int{return row.a+row.b}
parts::proc(a,b:int)->int{return a*b}
choose::proc{whole,parts}
'''
        self.invoke(self.program('rows:=basic.pmap([]Row{{2,3}},choose);delete(rows)',prefix),command='check',fail='Ambiguous pmap callback')
        self.invoke(self.program('rows:=basic.pmap([]Row{{2,3}},whole);defer delete(rows);if rows[0]!=5{panic("selected callback")}',prefix))

    def test_no_results_cannot_be_assigned_and_empty_typed_inputs_keep_types(self):
        prefix='''import "thor:basic"
effect::proc(x:int){}
twice::proc(x:int)->int{return x*2}
'''
        self.invoke(self.program('nil_values:[]int;rows:=basic.pmap(nil_values,twice);defer delete(rows);basic.pmap(nil_values,effect);if len(rows)!=0{panic("empty input")}',prefix))
        self.invoke(self.program('rows:=basic.pmap([]int{1},effect);_=rows',prefix),command='check',fail='does not return a value')

    def test_indirect_callbacks_and_standard_library_overloads(self):
        self.checked_and_unchecked(self.program('''
callback:proc(int)->int=twice
rows:=basic.pmap([]int{2,3},callback)
defer delete(rows)
roots:=basic.pmap([]f64{4,9},math.sqrt)
defer delete(roots)
if rows[1]!=6||roots[1]!=3{panic("callback resolution")}
''','import "thor:basic"\nimport "core:math"\ntwice::proc(x:int)->int{return x*2}\n'))

    def test_original_argument_evaluation_order_is_retained(self):
        self.checked_and_unchecked(self.program('''
rows:=basic.pmap(workers=workers(),jobs=jobs(),calculate=twice)
defer delete(rows)
if order!=21||rows[1]!=6{panic("argument order")}
''','''import "thor:basic"
order:int
data:[2]int={2,3}
jobs::proc()->[]int{order=order*10+1;return data[:]}
workers::proc()->int{order=order*10+2;return 2}
twice::proc(x:int)->int{return x*2}
'''))

    def test_direct_inputs_work_with_fn_and_run_and_preserve_ownership(self):
        source=self.program('''
runtime_input:=3
if calculate(runtime_input)!=7{panic("runtime checked mapping")}
VALUE::#run calculate(5)
if VALUE!=11{panic("comptime mapping")}
''','''import "thor:basic"
write::proc(destination:^int){destination^+=1}
sum::proc(row:$T)->int{return row.a+row.b}
Row::struct{a,b:int}
calculate::fn(n:int)->int{
    local:=n
    basic.pmap([]^int{&local},write)
    rows:=basic.pmap([]Row{{n,local}},sum)
    return rows[0]
}
''')
        for flags in (['-debug'],base.UNCHECKED):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

    def test_direct_pointer_callbacks_cannot_write_borrowed_pure_inputs(self):
        self.invoke(self.program('values:=[]int{1};_=calculate(values)','''import "thor:basic"
write::proc(destination:^int){destination^=7}
calculate::fn(values:[]int)->int{basic.pmap([]^int{&values[0]},write);return values[0]}
'''),command='check',fail='borrowed')

    def test_generic_pure_caller_and_callback_keep_whole_element_types(self):
        source=self.program('''
VALUE::#run compute(i16(3))
runtime_value:i16=4
if VALUE!=6||compute(runtime_value)!=8{panic("generic pure mapping")}
''','''import "thor:basic"
twice::fn(x:$T)->T{return x*2}
compute::fn(x:$T)->T{
    rows:=basic.pmap([]T{x},twice)
    return rows[0]
}
''')
        for flags in (['-debug'],base.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_zero_argument_workers_advance_rng_without_reseeding_each_job(self):
        self.checked_and_unchecked(self.program('''
jobs:[16]struct{}
state:=rand.create(u64(123))
generator:=rand.default_random_generator(&state)
expected:[4]u64
for &value in expected{value=rand.uint64(generator)}
rows:=basic.pmap(jobs[:],sample,workers=4)
defer delete(rows)
for value,i in rows{if value!=expected[i%4]{panic("RNG was reset between jobs")}}
''','''import "thor:basic"
import "core:math/rand"
sample::proc()->u64{
    @(thread_local) count:int
    if count==0{rand.reset(u64(123))}
    count+=1
    return rand.uint64()
}
'''))

    def test_custom_rng_context_is_inherited_without_resetting_or_cloning(self):
        self.checked_and_unchecked(self.program('''
seed:u8=0x2a
context.random_generator={generator,&seed}
jobs:[16]struct{}
rows:=basic.pmap(jobs[:],sample,workers=4)
defer delete(rows)
for value in rows{if value!=0x2a2a2a2a2a2a2a2a{panic("custom generator was replaced")}}
''','''import "thor:basic"
import "core:math/rand"
import "base:runtime"
generator::proc(data:rawptr,mode:runtime.Random_Generator_Mode,output:[]byte){
    if mode==.Read{for &value in output{value=(^u8)(data)^}}
    else if mode==.Reset{panic("unexpected RNG reset")}
}
sample::proc()->u64{return rand.uint64()}
'''))

    def test_result_queries_see_pmap_output_without_running_or_allocating(self):
        self.invoke(self.program('''
Result::ti.result_type_of(basic.pmap([]int{1},twice))
value:Result
if len(value)!=0{panic("query evaluated pmap")}
''','import "thor:basic"\nimport ti "thor:intrinsics"\ntwice::proc(x:int)->int{return x*2}\n'))

    def test_false_generic_constraints_do_not_create_input_mode_ambiguity(self):
        self.invoke(self.program('''
rows:=basic.pmap([]Row{{2,3}},calculate)
defer delete(rows)
if rows[0]!=5{panic("constrained callback")}
''','''import "thor:basic"
Row::struct{a,b:int}
whole::proc(row:$T)->int where T==int{return 0}
parts::proc(a,b:int)->int{return a+b}
calculate::proc{whole,parts}
'''))

    def test_any_input_and_unrelated_internal_parameter_names(self):
        self.invoke(self.program('''
rows:=basic.pmap([]Row{{7}},read_any)
defer delete(rows)
if rows[0]!=7||local(false)!=3||custom([]int{2},twice,__thor_pmap_spread=true)!=5{panic("whole any or ordinary name")}
''','''import "thor:basic"
import ti "thor:intrinsics"
Row::struct{x:int}
read_any::proc(value:any)->int{return value.(Row).x}
local::proc($__thor_pmap_spread:bool)->int{return 3}
twice::proc(value:int)->int{return value*2}
custom::proc(values:[]$J,callback:$F,$R:typeid=ti.result_type_of(callback(J{})),$__thor_pmap_spread:bool)->R{
    value:=callback(values[0])
    when __thor_pmap_spread{return value+1}
    else{return value}
}
'''))

    def test_the_internal_dispatch_mode_cannot_be_overridden(self):
        self.invoke(self.program('rows:=basic.pmap([]int{1},twice,__thor_pmap_spread=true);delete(rows)',
            'import "thor:basic"\ntwice::proc(x:int)->int{return x*2}\n'),command='check',fail='argument mode is inferred')

    def test_native_callback_errors_hide_dispatch_but_preserve_user_names(self):
        _,result=self.invoke(self.program('rows:=basic.pmap([]string{"bad"},twice);delete(rows)',
            'import "thor:basic"\ntwice::proc(x:int)->int{return x*2}\n'),command='check',fail="to 'int'")
        self.assertNotIn('__thor_pmap_spread',result.stdout+result.stderr)
        _,result=self.invoke(self.program('local()',
            'local::proc($__thor_pmap_spread:bool){}\n'),command='check',fail='Error:')
        self.assertIn('__thor_pmap_spread',result.stdout+result.stderr)

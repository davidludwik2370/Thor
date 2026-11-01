"""Typed loop syntax, numeric context, checked index conversion and interactions."""
import unittest
import subprocess
from tests import test_thor as base


class TypedLoopTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_ranges_choose_numeric_type_and_preserve_range_forms(self):
        self.invoke(self.program('''
ROUNDS::3
count:=0
for i:f64 in 1..=ROUNDS {assert(type_of(i)==f64);count+=1;assert(.5**i>0)}
assert(count==3)
for i:f32 in 1..<3 {assert(type_of(i)==f32);count+=1}
for i:i8 in -2..=0 {assert(type_of(i)==i8);count+=1}
for i:u16 in 0..<2 do count+=int(i)
assert(count==9)
for i:Float in .5..=2.5 {assert(type_of(i)==Float);count+=1}
assert(count==12)
''','Float::distinct f64\n'))

    def test_typed_bounds_still_require_explicit_conversion(self):
        for bound in ['limit:int=3','limit::int(3)']:
            with self.subTest(bound=bound):
                self.invoke(self.program(bound+';for i:f64 in 1..=limit{_=i}'),command='check',fail="to 'f64'")
        self.invoke(self.program('limit:int=3;for i:f64 in 1..=f64(limit){assert(i>=1)}'))
        self.invoke(self.program('for i:i8 in 0..<300{_=i}'),command='check',fail='Cannot convert')

    def test_range_bounds_preserve_native_evaluation_order_and_frequency(self):
        self.invoke(self.program('''
order:=0;total:=0
for i:int in mark(&order,1)..=mark(&order,3) {total+=i}
// Native Odin evaluates the lower bound once and this inclusive upper bound
// before each iteration. The annotation must introduce no extra evaluations.
assert(order==1333 && total==6)
''','mark::proc(order:^int,n:int)->int{order^=order^*10+n;return n}\n'))

    def test_array_slice_dynamic_pointer_and_reference_indices(self):
        self.invoke(self.program('''
a:=[10,20,30];sum:=0
for value,(index:i8) in a{assert(type_of(index)==i8 && type_of(value)==int);sum+=value+int(index)}
assert(sum==63)
for &value,(index:u8) in a{value+=int(index)}
assert(a==[10,21,32])
slice:=a[:];for value,(index:u16) in slice{assert(value==a[index])}
growing:=make([dynamic]int);append(&growing,3,4);defer delete(growing)
for value,(index:u32) in growing{assert(value==3+int(index))}
for value,(index:u8) in &a{assert(value==a[index])}
fixed:[dynamic;4]int;append(&fixed,8,9)
for value,(index:u8) in fixed{assert(value==8+int(index))}
'''))

    def test_empty_and_largest_fitting_arrays_and_distinct_index(self):
        self.invoke(self.program('''
empty:[0]int;for _,(index:i8) in empty{panic("empty")}
nil_view:[]int;for _,(index:u8) in nil_view{panic("nil view")}
values:[128]int;count:=0
for _,(index:i8) in values {assert(index>=0);count+=1}
assert(count==128)
for _,(index:Index) in values{assert(type_of(index)==Index)}
''','Index::distinct u8\n'))

    def test_fixed_array_overflow_is_a_compile_error(self):
        self.invoke(self.program('values:[129]int;for _,(index:i8) in values{_=index}'),command='check',
                    fail="indices do not fit")

    def test_slice_index_overflow_panics_even_with_asserts_disabled(self):
        self.invoke(self.program('values:[129]int;view:=values[:];for _,(index:i8) in view{_=index}'),
                    fail='Typed loop index is out of range',flags=['-disable-assert','-no-bounds-check','-o:speed'])

    def test_readonly_index_assignment_address_and_body_shadow(self):
        for use in ['index+=1','_= &index']:
            with self.subTest(use=use):
                self.invoke(self.program('values:=[1,2];for _,(index:i8) in values{'+use+'}'),command='check',fail='immutable storage')
        self.invoke(self.program('values:=[1,2];for _,(index:i8) in values{index:=42;assert(index==42)}'))
        self.invoke(self.program('for i:f64 in 1..=2{i+=1}'),command='check',fail="Cannot assign to 'i'")

    def test_labels_break_continue_defer_and_do_bodies(self):
        self.invoke(self.program('''
values:=[2,4,6];sum,deferred:=0,0
outer: for value,(index:i8) in values {
 defer deferred+=1
 if index==0 do continue outer
 for j:int in 0..<2 {if j==1 do break;sum+=value}
 if index==2 do break outer
}
assert(sum==10 && deferred==3)
for value,(index:i8) in values do sum+=value+int(index)
assert(sum==25)
for limit:=3; i:f64 in 0..<f64(limit) do sum+=int(i)
assert(sum==28)
{defer for value,(index:i8) in values do sum+=value+int(index)}
assert(sum==43)
'''))

    def test_getter_runs_once_and_native_array_iteration_observes_mutation(self):
        self.invoke(self.program('''
data:=[1,2,3];calls:=0
for value,(index:u8) in get(&calls,data[:]){assert(value==int(index)+1)}
assert(calls==1)
for &value,(index:u8) in data{value+=10}
assert(data==[11,12,13])
''','get::proc(calls:^int,data:[]int)->[]int{calls^+=1;return data}\n'))

    def test_generic_type_annotations_fn_and_run(self):
        self.invoke(self.program('''
assert(typed(f32)==6)
values:=[2,3,4];assert(indices(values[:],u8)==12)
assert(COMPUTED==12)
''','''
typed::fn($T:typeid)->T {result:T;for i:T in 1..=3{result+=i};return result}
indices::fn(values:[]int,$I:typeid)->int{sum:=0;for value,(index:I) in values{sum+=value+int(index)};return sum}
compute::fn()->int{values:=[2,3,4];return indices(values[:],i8)}
COMPUTED::#run compute()
'''))

    def test_contextless_alias_shadow_initializer_and_integer_endpoint(self):
        self.invoke(self.program('''
assert(callback()==3)
count:=0
for number:i8 in 126..=127 {assert(number>0);count+=1}
assert(count==2)
for prototype:=f64(0); number:type_of(prototype) in 1..=2 {assert(type_of(number)==f64)}
values:=[1,2]
for value,(index:u8) in values {assert(index>=0);index:=42;assert(index==42);_=value}
''','''
callback::proc "c"()->int {
 values:=[3]
 total:=0
 for value,(index:i8) in values do total+=value+int(index)
 return total
}
'''))

    def test_dynamic_overflow_only_checks_visited_indices_and_return_exits_normally(self):
        self.invoke(self.program('''
values:[129]int
view:=values[:]
for _,(index:i8) in view {if index==1 do break}
assert(first(view)==0)
''','first::proc(values:[]int)->int{for value,(index:i8) in values{return value+int(index)};return -1}\n'),flags=['-no-bounds-check','-disable-assert'])

    def test_unsupported_annotations_and_ambiguous_group_are_rejected(self):
        for body,message in [
            ('for i:string in 1..=3{_=i}','integer or floating-point'),
            ('values:=[1,2];for value:f64 in values{_=value}','only for numeric ranges'),
            ('values:=[1,2];for value,index:i8 in values{_=value;_=index}','must be parenthesized'),
            ('for value,(index:i8) in "hello"{_=value;_=index}','only for arrays and slices'),
            ('values:=[1,2];for value,(index:f64) in values{_=value;_=index}','integer type'),
            ('for value,(index:i8) in 0..<3{_=value;_=index}','not numeric ranges'),
            ('values:=[1,2];for index,(index:i8) in values{}','Duplicate loop binding'),
            ('values:=[1,2];for value,index,(other:i8) in values{}','at most a value binding'),
            ('next::proc()->(int,int,bool){return 0,0,false};for value,(index:i8) in next(){_=value;_=index}','only for arrays and slices'),
        ]:
            with self.subTest(body=body):self.invoke(self.program(body),command='check',fail=message)

    def test_inactive_branches_do_not_instantiate_loop_contracts(self):
        self.invoke(self.program('when false {for i:string in 1..=3{_=i};values:[129]int;for _,(index:i8) in values{_=index}}'))

    def test_annotation_changes_invalidate_compile_time_results(self):
        source=self.program('assert(VALUE==8256)','''
compute::fn()->int{values:[129]int;sum:=0;for _,(index:u8) in values{sum+=int(index)};return sum}
VALUE::#run compute()
''')
        root,_=self.invoke(source,flags=['--cache-report'])
        path=root/'input/main.thor'
        path.write_text(source.replace('index:u8','index:i8'))
        result=subprocess.run([str(base.THOR),'check',str(path),'--output-dir',str(root/'output')],capture_output=True,text=True,timeout=40)
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertIn('indices do not fit',result.stderr)

    def test_literals_members_and_immutable_reference_restrictions(self):
        self.invoke(self.program('''
record:=Record{[3,4]}
for value,(index:u8) in record.items{assert(value==3+int(index))}
for value,(index:i8) in [7,8]{assert(value==7+int(index))}
''','Record::struct{items:[2]int}\n'))
        self.invoke(self.program('values:>[1,2];for &value,(index:i8) in values{value+=int(index)}'),
                    command='check',fail='immutable storage')

    def test_comments_multiline_bindings_and_parenthesized_numeric_binding(self):
        self.invoke(self.program('''
for (number: /* type context */ f64) in 1..=2 {assert(type_of(number)==f64)}
values:=[4,5]
for value,
    (index: /* checked narrowing */ i8) in values {
    assert(value==4+int(index))
}
'''))

    def test_owned_temporaries_fstrings_switches_and_negative_indices(self):
        _,result=self.invoke(self.program('''
values:=[2,3,4]
for value,(index:i8) in values {
 text:=f"value ({value}) index {index}";assert(len(text)>0);delete(text)
 selected:=switch index{case 0:values[-1];case:value}
 assert(selected>0)
}
for value,(index:i8) in from_text(f"v{7}"){assert(value==2+int(index))}
''','from_text::proc(text:string)->[2]int{assert(text=="v7");return {2,3}}\n'),flags=['-debug'])
        self.assertNotIn('still allocated',result.stderr)

"""Range membership: syntax, native typing, evaluation order and ownership."""
import unittest
from tests import test_thor as base


class RangeTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_generic_constraints_and_composed_bounds(self):
        self.invoke(self.program('''
assert(allowed(0)==0&&allowed(9)==9&&outside(10)==10)
record:Record(3);assert(len(record.data)==3)
''','''
LIMIT::10
allowed::proc($N:int)->int where N in 0..<LIMIT && N != 5{return N}
outside::proc($N:int)->int where N not_in (0..<LIMIT){return N}
Record::struct($N:int) where N in 1..=4 {data:[N]int}
'''))
        for value in [-1,5,10]:
            with self.subTest(value=value):
                self.invoke(self.program(f'_=allowed({value})',
                    'allowed::proc($N:int)->int where N in 0..<10 && N != 5{return N}\n'),command='check',fail='where')

    def test_runtime_bounds_parentheses_arithmetic_and_logical_precedence(self):
        self.invoke(self.program('''
low,high:=0,10
assert(3 in low..<high&&3!=5)
assert(3 not_in low..<3 || 3 in 1+1..<high-1)
assert(!(3 in (low..<3)))
assert((3 in low..=3)==true)
assert(3+1 not_in 0..<4)
assert(3 in ((low+1)..<(high-1)))
assert((3 in 0..<4) in [true])
assert(3 in (2 in [2] ? 1 : 8)..<10)
'''))

    def test_operands_execute_once_in_order_even_when_outside(self):
        self.invoke(self.program('''
order:=0
assert(mark(&order,3) in mark(&order,0)..<mark(&order,9));assert(order==309)
order=0
assert(mark(&order,-1) not_in mark(&order,0)..<mark(&order,9));assert(order==-91)
value:=3
assert(value in change(&value)..<10);assert(value==30)
''','''
mark::proc(order:^int,value:int)->int{order^=order^*10+value;return value}
change::proc(value:^int)->int{value^=30;return 0}
'''))

    def test_empty_reversed_inclusive_and_extreme_bounds(self):
        self.invoke(self.program('''
assert(0 not_in 0..<0&&0 in 0..=0)
for value in [-1,0,1,2,3]{assert(value not_in 2..<1&&value not_in 2..=1)}
value:i64=max(i64);assert(value in 0..=max(i64)&&value not_in 0..<max(i64))
wide:=u128(1)<<100;assert(wide in u128(0)..<(u128(1)<<101))
last:=max(u128);assert(last in u128(0)..=max(u128))
assert(3 in 0..10) // retain Odin's legacy half-open spelling
'''))

    def test_native_types_literals_enums_runes_strings_and_floats(self):
        self.invoke(self.program('''
small:i8=3;assert(small in 0..<10)
small_low,small_high:i8=0,10;assert(3 in small_low..<small_high)
number:Count=3;assert(number in 0..<10)
letter:rune='界';assert(letter in 'a'..='😀')
color:Color=.Green;assert(color in .Red..=.Blue)
text:="bee";assert(text in "a"..<"z")
fraction:f32=.5;assert(fraction in 0.0..<1.0)
zero:f64=0;nan:=zero/zero;assert(nan not_in 0.0..=1.0)
''','Count::distinct int\nColor::enum{Red,Green,Blue}\n'))

    def test_incompatible_types_and_nonconstant_constraints_still_fail(self):
        for body in ['value:=3;_=value in "a"..<"z"',
                     'value:i8=3;low:i64=0;_=value in low..<10',
                     'value:=true;_=value in false..=true',
                     'high:=10;when -1 in 0..<high {}']:
            with self.subTest(body=body):self.invoke(self.program(body),command='check',fail='Error:')

    def test_constants_config_run_automatic_and_selected_branches(self):
        self.invoke(self.program('''
assert(A&&B&&C&&AUTO&&EXPLICIT&&CHOICE==7)
value:=3;assert(inside(value))
#if 3 in 0..<4 {assert(true)} #else {undefined_unselected()}
#if -1 not_in -2..=0 {undefined_unselected()} #else {assert(true)}
''','''
A::3 in 0..<10
B::10 not_in 0..<10
C::#config(LIMIT,2) in 0..=2
CHOICE::#switch {case 3 in 0..<10:7;case:9}
inside::fn(value:int)->bool{return value in 0..<10}
AUTO::inside(3)
EXPLICIT::#run inside(9)
'''))

    def test_loop_conditions_posts_lazy_paths_and_contextless_calls(self):
        self.invoke(self.program('''
count:=0
for i:=0;i in 0..<3;i+=1 {count+=1}
assert(count==3)
for start:=1;i in 0..<3 {assert(start==1&&i in 0..<3)}
for i:=0;i<3 && i in 0..<3;i+=1 {assert(i in 0..<3)}
for i:=0;(i in 0..<3); {i+=1}
for i:=0;i in 0..<3; {i+=1}
order:=0
assert(true || mark(&order,3) in 0..<10);assert(order==0)
assert(false && mark(&order,3) in 0..<10==true || order==0)
assert(check(3)&&!check(10))
''','''
mark::proc(order:^int,value:int)->int{order^+=1;return value}
check::proc "c"(value:int)->bool{return value in 0..<10}
'''))

    def test_anonymous_strings_and_views_are_released(self):
        self.invoke('''package main
import "core:testing"
@(test) owned::proc(t:^testing.T){
 for i in 0..<4{
  testing.expect(t,f"b{i}" in f"a{i}"..<(true ? f"z{i}" : "borrowed"))
  testing.expect(t,f"xb{i}"[1:] in f"xa{i}"[1:]..<f"xz{i}"[1:])
 }
}
''',command='test',flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_native_prefix_expansion_and_existing_membership_remain_available(self):
        self.invoke(self.program('''
pair:=Pair{2,3};assert(sum(**pair)==5)
values:=[1,2];assert(2 in values)
bits:bit_set[0..<8]={2};assert(2 in bits)
for i in 0..<3 {assert(i in 0..<3)}
''','Pair::struct{a,b:int}\nsum::proc(a,b:int)->int{return a+b}\n'))

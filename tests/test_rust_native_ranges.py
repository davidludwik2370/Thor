"""Native interval iteration must bypass generic Set dispatch."""
import unittest
from tests import test_rust_compiler as native

class RustNativeRangeTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_defaulted_and_generic_parameters_exclusive_inclusive_and_parens(self):
        for bound in ['0..<limit','0..=limit','(0..<limit)','(0..=limit)']:
            for names in ['_','value']:
                extra='' if names=='_' else 'sum+=value;'
                prefix='''import "core:fmt"
work::proc(limit:=3)->(count,sum:int){for '''+names+' in '+bound+'{'+extra+'count+=1};return}\n'
                with self.subTest(bound=bound,names=names):
                    self.compare(self.program('fmt.println(work());fmt.println(work(5))',prefix),flags=native.UNCHECKED)
        prefix='''import "core:fmt"
work::proc(limit:$T)->int{count:=0;for _ in 0..<limit{count+=1};return count}
'''
        self.compare(self.program('fmt.println(work(3),work(i8(5)))',prefix),expected='3 5')

    def test_interval_bounds_preserve_native_evaluation_and_loop_control(self):
        prefix='''import "core:fmt"
calls:int
bound::proc(value:int)->int{calls+=1;return value}
'''
        source=self.program('''count:=0
outer: for value in bound(0)..<bound(4){
 defer count+=1
 if value==1 do continue
 if value==3 do break outer
}
if count!=4{panic("range side effects")}
fmt.println(count,calls)
''',prefix)
        for flags in [[],native.UNCHECKED]:self.compare(source,flags=flags,expected='4 5')

    def test_set_and_native_iterator_calls_still_dispatch_normally(self):
        prefix='''import "core:fmt"
next::proc(index:^int)->(int,bool){if index^>=3{return 0,false};value:=index^;index^+=1;return value,true}
'''
        self.compare(self.program('''values:=set[int]{1,2,3};defer delete(values)
total:=0;for value in values{total+=value}
index:=0;for value in next(&index){total+=value}
fmt.println(total,index)
''',prefix),expected='9 3')

    def test_interval_membership_contexts_static_constants_and_boundaries(self):
        prefix='''import "core:fmt"
Mode::enum{a,b,c}
'''
        source=self.program('''LOW::0;HIGH::10
A::5 in LOW..<HIGH
B::10 not_in LOW..<HIGH
C::10 in LOW..=HIGH
value:i8=5;low:i8=0;high:i8=10
mode:=Mode.b
fmt.println(A,B,C,value in low..<high,value not_in (low..=high),mode in .a..=.c)
fmt.println(f"{value in low..<high}")
''',prefix)
        for flags in [[],native.UNCHECKED]:
            self.compare(source,flags=flags,expected='true true true true false true\ntrue')

    def test_membership_evaluates_all_operands_once_in_order(self):
        prefix='''import "core:fmt"
order:int
value::proc(id,result:int)->int{order=order*10+id;return result}
'''
        source=self.program('''inside:=value(1,9) in value(2,0)..<value(3,1)
if inside||order!=123 {panic("operand order")}
order=0
outside:=value(1,0) not_in (value(2,1)..=value(3,2))
if !outside||order!=123 {panic("negated order")}
_=type_of(value(1,0) in value(2,0)..<value(3,2))
if order!=123 {panic("query ran operands")}
fmt.println(inside,outside,order)
''',prefix)
        for flags in [[],native.UNCHECKED]:
            self.compare(source,flags=flags,expected='false true 123')

    def test_generic_pure_and_compile_time_interval_membership(self):
        prefix='''import "core:fmt"
contains::fn(value:$T,low,high:T)->bool{return value in low..<high}
VALUE::#run contains(3,0,5)
'''
        self.compare(self.program('fmt.println(VALUE,contains(i8(5),0,5),contains(f64(1.5),1,2))',prefix),expected='true false true')

    def test_membership_rejects_incompatible_typed_operands(self):
        self.compare(self.program('value:i8=1;low:i64=0;high:i64=5;_=value in low..<high'),
            command='check',fail='Mismatched types')

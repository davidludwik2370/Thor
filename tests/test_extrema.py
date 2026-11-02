"""Spread min/max: real Odin execution, optional status, and source-order evaluation."""
import unittest
from tests import test_thor as base


class ExtremaTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_slice_results_empty_singleton_and_ignored_status(self):
        self.invoke(self.program('''
values := []int{3, -7, 2}
lo, lo_ok := min(..values); hi, hi_ok := max(..values)
assert(lo == -7 && hi == 3 && lo_ok && hi_ok)
assert(min(..values) == -7 && max(..values) == 3)
empty: []int
zero, ok := min(..empty); assert(zero == 0 && !ok)
zero, ok = max(..empty); assert(zero == 0 && !ok)
zero, ok = max(..[]int{7}); assert(zero == 7 && ok)
assert(max(..[]int{-5, -2}) == -2 && min(..[]int{5, 2}) == 2)
assert(max(4, ..empty) == 4)
value, valid := min(8, 3, ..empty); assert(value == 3 && valid)
value, valid = max(-9, -3, ..[]int{-7}); assert(value == -3 && valid)
'''))

    def test_arrays_one_element_multiple_expansions_and_native_forms(self):
        self.invoke(self.program('''
a := [?]int{1, 2, 3}
assert(max(**a) == 3 && min(**a) == 1)
assert(max(**[1]int{7}) == 7)
assert(max(-9, **a, 0, **[2]int{4, 5}) == 5)
assert(min(9, **a, 0) == 0)
assert(min(2, 4) == 2 && max(1, 5, 3) == 5)
assert(min(i8) == -128 && max(u8) == 255)
Pair :: struct { a, b: int }
pair := Pair{3, 5}
assert(sum(**pair) == 8)
assert(2**3 == 8)
''', 'sum::proc(a,b:int)->int{return a+b}\n'))

    def test_types_literals_strings_enums_and_named_sequences(self):
        self.invoke(self.program('''
Count :: distinct i16
Counts :: distinct []Count
values := Counts{3, -1, 2}
result, ok := min(0, ..values)
assert(type_of(result) == Count && result == -1 && ok)
empty: Counts
result, ok = max(..empty); assert(type_of(result) == Count && result == 0 && !ok)
floats := []f32{1.5, 3}
assert(type_of(max(2, ..floats)) == f32 && max(2, ..floats) == 3)
assert(min(-2, **[2]f64{1.5, 4}) == -2)
assert(min(..[]string{"pear", "apple"}) == "apple")
text, present := min(..[]string{}); assert(text == "" && !present)
assert(max("pear", ..[]string{"apple"}) == "pear")
Color :: enum {Red = 1, Green = 2, Blue = 3}
assert(max(**[2]Color{.Red, .Blue}) == .Blue)
color, has_color := min(..[]Color{}); assert(color == Color(0) && !has_color)
'''))

    def test_calls_once_in_source_order_with_array_snapshots(self):
        self.invoke(self.program('''
values := [3]int{3, 1, 2}
order := 0
lo, ok := min(mark(&order, 1), mark(&order, 2), ..view(&order, values[:]))
assert(lo == 1 && ok && order == 123)
order = 0
assert(max(mark(&order, 1), **array(&order), mark(&order, 3)) == 9)
assert(order == 123)
assert(max(**values, change(&values)) == 3 && values[0] == 100)
assert(max(**array(nil)) == 9)
assert(true || max(**array(&order)) == 9)
assert(order == 123)
''', '''
mark::proc(order:^int, value:int)->int{order^=order^*10+value;return value}
view::proc(order:^int,values:[]int)->[]int{order^=order^*10+3;return values}
array::proc(order:^int)->[2]int{if order!=nil{order^=order^*10+2};return {4,9}}
change::proc(values:^[3]int)->int{values[0]=100;return 0}
'''))

    def test_generic_and_contextless_code(self):
        self.invoke(self.program('''
values := []int{9, 2, 4}
lo, ok := lowest(values); assert(lo == 2 && ok)
assert(highest([3]int{9, 2, 4}) == 9)
assert(seeded(f64(1), []f64{2.5}) == 2.5)
assert(caller(values) == 9)
''', '''
lowest::proc(values:[]$T)->(T,bool){return min(..values)}
highest::proc(values:[$N]$T)->T{return max(**values)}
seeded::proc(seed:$T,values:[]T)->T{return max(seed,..values)}
caller::proc "contextless"(values:[]int)->int{return max(0,..values)}
'''))

    def test_aliases_shadowing_and_native_odin_passthrough(self):
        self.invoke(self.program('''
values := []int{2, 3}
assert(largest(..values) == 3 && math.max(..values) == 3)
assert(least(..values) == 2)
{
    min::proc(values:..int)->int{return 99}
    assert(min(..values) == 99)
}
assert(native() == 3)
''', '''import "core:math" / min
largest::max
least::min
'''), directory=True, files={'native.odin': '''package main
native::proc()->int{return max(2,3)}
'''})

    def test_nonempty_allocation_free_reductions(self):
        self.invoke('''package main
import "core:testing"
import "core:mem"
@(test)
reductions::proc(t:^testing.T){
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 values:=[]int{7,3,4}
 for _ in 0..<100 {
  value,ok:=min(9,..values);testing.expect(t,value==3&&ok)
  testing.expect_value(t,max(**[3]int{7,3,4}),7)
 }
 testing.expect_value(t,tracker.total_allocation_count,i64(0))
}
''', command='test', flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_pure_and_run_functions_can_reduce_sequences(self):
        self.invoke(self.program('''
assert(LOW == -2 && HIGH == 4 && REPORT.value == 0 && !REPORT.ok)
assert(pure_min([]int{7, 3}) == 3)
''', '''
pure_min::fn(values:[]int)->int{value,ok:=min(..values);return ok ? value : -99}
pure_max::fn(values:[3]int)->int{return max(**values)}
LOW::#run pure_min([]int{3,-2,4})
HIGH::#run pure_max([3]int{3,-2,4})
Report::struct{value:int,ok:bool}
summary::fn(values:[]int)->Report{value,ok:=min(..values);return {value,ok}}
REPORT::#run summary([]int{})
'''))

    def test_rejected_shapes_types_and_empty_arrays(self):
        for body, message in [
            ('_=min(**[0]int{})', 'empty fixed array'),
            ('_=max(..[2]int{1,2})', "must be a slice"),
            ('_=min(**[]int{1,2})', 'must be a fixed array'),
            ('_=min(..[]bool{true,false})', 'Error:'),
            ('x:int=2;_=max(x,..[]f64{1,2})', 'Error:'),
            ('_=max(..[]struct{x:int}{{1}})', 'Error:'),
        ]:
            with self.subTest(body=body):
                self.invoke(self.program(body), command='check', fail=message)

    def test_selected_native_helper_can_be_forced_by_run(self):
        self.invoke(self.program('assert(X==1)', '''import "core:slice" / min
work::proc(values:[]int)->int{return min(values)}
X::#run work([]int{1,2})
'''))

    def test_required_constants_and_direct_run(self):
        self.invoke(self.program('''
assert(HIGH == 8 && LOW == -2 && EMPTY == 0 && RUN == 7)
data: [HIGH]int
assert(len(data) == 8)
''', '''
HIGH :: max(**[3]int{2, 8, 3})
LOW :: min(..[]int{3, -2, 7})
EMPTY :: min(..[]int{})
RUN :: #run max(7, ..[]int{2, 4})
'''))

    def test_optional_status_fallback_and_multiple_rhs_values(self):
        self.invoke(self.program('''
empty:[]int
assert((min(..empty) or_else 99) == 99)
assert((max(7,..empty) or_else 99) == 7)
values:=[]int{2,5}
lo,hi:=min(..values),max(..values)
assert(lo==2&&hi==5)
value,ok:=forward(values);assert(value==2&&ok)
value,ok=forward(empty);assert(value==0&&!ok)
''', 'forward::proc(values:[]int)->(value:int,ok:bool){value=min(..values) or_return;return value,true}\n'))

    def test_float_special_values_match_native_left_fold(self):
        self.invoke(self.program('''
nan:=math.sqrt(f64(-1))
zero:=f64(0)
cases:=[][3]f64{{nan,-zero,zero},{zero,-zero,nan},{-zero,zero,7},{3,-5,2}}
for values in cases {
 expected_min:=min(min(values[0],values[1]),values[2])
 expected_max:=max(max(values[0],values[1]),values[2])
 actual_min:=min(**values);actual_max:=max(**values)
 assert(same(actual_min,expected_min)&&same(actual_max,expected_max))
 copy:=values
 seeded,ok:=min(copy[0],..copy[1:]);assert(ok&&same(seeded,expected_min))
}
''', '''import "core:math"
same::proc(a,b:f64)->bool{return (math.is_nan(a)&&math.is_nan(b)) || (transmute(u64)a==transmute(u64)b)}
'''))

    def test_constant_branches_type_queries_and_generic_constants(self):
        self.invoke(self.program('''
when max(**[3]int{2,8,3}) == 8 {assert(true)} else {missing()}
selected::#switch min(..[]int{2,1}) {case 1:7;case:missing}
assert(selected==7&&size(3)==4)
order:=0
assert(type_of(max(**array(&order)))==int&&order==0)
''', '''
array::proc(order:^int)->[2]int{order^+=1;return {1,2}}
size::proc($N:int)->int{VALUE::max(**[2]int{N,N+1});return VALUE}
'''))

    def test_commented_demo(self):
        import subprocess
        result = subprocess.run([str(base.THOR), 'run', str(base.ROOT/'examples'/'extrema')],
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('Spread min/max examples passed.', result.stdout)


if __name__ == '__main__':
    unittest.main()

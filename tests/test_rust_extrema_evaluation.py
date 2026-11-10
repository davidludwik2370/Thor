"""Reference automatic evaluation of expanded min/max in constant contexts."""
import unittest
from tests import test_rust_scheduling as scheduling
from tests import test_rust_compiler as native


class RustExtremaEvaluationTests(unittest.TestCase):
    maxDiff = None
    setUpClass = classmethod(scheduling.RustSchedulingTests.setUpClass.__func__)
    compare = scheduling.RustSchedulingTests.compare
    program = scheduling.RustSchedulingTests.program

    def test_fixed_arrays_materialize_native_constants(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('fmt.println(HI,LO)',
                    'import "core:fmt"\nHI::max(**[3]int{1,7,3})\nLO::min(**[3]int{2,-3,1})'),
                    flags=flags,output='7 -3\n')

    def test_slice_primary_value_and_empty_zero(self):
        self.compare(self.program('fmt.println(HI,EMPTY)',
            'import "core:fmt"\nHI::max(..[]int{1,7,3})\nEMPTY::min(..[]int{})'),output='7 0\n')

    def test_mixed_scalars_arrays_and_slice_preserve_element_coercion(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('fmt.println(HI)',
                    'import "core:fmt"\nHI::max(9,**[2]i16{1,7},..[]i16{3,12})'),flags=flags,output='12\n')

    def test_native_string_and_float_element_types(self):
        self.compare(self.program('fmt.println(TEXT,NUMBER)',
            'import "core:fmt"\nTEXT::max(**[2]string{"a","b"})\nNUMBER::min(**[2]f32{2.5,1.25})'),output='b 1.25\n')

    def test_qualified_native_math_and_selected_aliases(self):
        self.compare(self.program('fmt.println(A,B)',
            'import "core:fmt"\nimport "core:math" / min\nLOW::(min)\n'
            'A::math.max(**[3]int{1,7,3})\nB::LOW(**[3]int{2,-3,1})'),output='7 -3\n')

    def test_nested_extrema_are_known_explicit_arguments(self):
        self.compare(self.program('fmt.println(#run work(max(**[3]int{1,7,3})))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n*2}'),output='14\n')

    def test_generic_constant_extrema_have_per_specialization_results(self):
        self.compare(self.program('fmt.println(work(2),work(7))',
            'import "core:fmt"\nwork::proc($N:int)->int{VALUE::max(**[2]int{N,N+1});return VALUE}'),output='3 8\n')

    def test_runtime_spreads_remain_native_with_optional_status(self):
        results=self.compare(self.program('values:=[3]int{1,7,3};n,ok:=max(..values[:]);fmt.println(n,ok)',
            'import "core:fmt"'),output='7 true\n')
        self.assertEqual(results,[])

    def test_runtime_operand_order_and_array_snapshot_are_unchanged(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('''values:=[3]int{3,1,2};order:=0
n,ok:=min(mark(&order,1),mark(&order,2),..view(&order,values[:]))
fmt.println(n,ok,order);order=0
m:=max(mark(&order,1),**array(&order),mark(&order,3));fmt.println(m,order)
snapshot:=max(**values,change(&values));fmt.println(snapshot,values[0])''',
                    '''import "core:fmt"
mark::proc(order:^int,value:int)->int{order^=order^*10+value;return value}
view::proc(order:^int,values:[]int)->[]int{order^=order^*10+3;return values}
array::proc(order:^int)->[2]int{order^=order^*10+2;return {4,9}}
change::proc(values:^[3]int)->int{values[0]=100;return 0}'''),
                    flags=flags,output='1 true 123\n9 123\n3 100\n')

    def test_user_namespace_does_not_inherit_builtin_expansion(self):
        result=self.compare(self.program('fmt.println(math.max(..[]int{1,2}))',
            'import "core:fmt"\nimport math "lib"'),
            files={'lib/lib.thor':'package lib\nmax::proc(values:..int)->int{return 99}'},output='99\n')
        self.assertEqual(result,[])

    def test_type_queries_do_not_execute_the_array_source(self):
        results=self.compare(self.program('fmt.println(size_of(max(**values())))',
            'import "core:fmt"\nvalues::proc()->[2]int{panic("not evaluated")}'),output='8\n')
        self.assertEqual(results,[])

    def test_multiple_runtime_names_retain_optional_status(self):
        results=self.compare(self.program('a,b:=max(..[]int{1,2});fmt.println(a,b)','import "core:fmt"'),output='2 true\n')
        self.assertEqual(results,[])

    def test_scalar_builtins_stay_native_constants(self):
        results=self.compare(self.program('fmt.println(VALUE)','import "core:fmt"\nVALUE::max(1,2)'),output='2\n')
        self.assertEqual(results,[])

    def test_empty_fixed_array_has_the_reference_error(self):
        self.compare(self.program('_=VALUE','VALUE::max(**[0]int{})'),
                     fail='An empty fixed array cannot be expanded into min/max')

    def test_array_cannot_use_the_slice_spread(self):
        self.compare(self.program('_=VALUE','VALUE::max(..[2]int{1,2})'),
                     fail="A '..' argument to min/max must be a slice")


if __name__ == '__main__':
    unittest.main()

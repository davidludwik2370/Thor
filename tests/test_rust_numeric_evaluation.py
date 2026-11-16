"""Exact untyped numeric constants and native typed-arithmetic boundaries."""
import unittest
from tests import test_rust_scheduling as scheduling
from tests import test_rust_compiler as native

class RustNumericEvaluationTests(unittest.TestCase):
    maxDiff=None
    setUpClass=classmethod(scheduling.RustSchedulingTests.setUpClass.__func__)
    compare=scheduling.RustSchedulingTests.compare
    program=scheduling.RustSchedulingTests.program

    def test_wide_untyped_powers_coerce_at_the_eventual_use(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('a:u128=VALUE;b:i128=(-2)**127;fmt.println(a,b)',
                    'import "core:fmt"\nVALUE::2**100'),flags=flags,
                    output='1267650600228229401496703205376 -170141183460469231731687303715884105728\n')

    def test_positive_and_negative_constant_aliases_keep_literal_coercion(self):
        self.compare(self.program('a:u128=POS**1;b:i128=NEG**1;fmt.println(a,b)',
            'import "core:fmt"\nPOS::18446744073709551616\nNEG::-170141183460469231731687303715884105728'),
            output='18446744073709551616 -170141183460469231731687303715884105728\n')

    def test_power_grouping_and_nested_constants(self):
        self.compare(self.program('fmt.println(VALUE)', 'import "core:fmt"\nVALUE::2**3**2'),output='512\n')

    def test_normal_signed_and_unsigned_range_errors(self):
        for expression in ['2**128','(-2)**128','(-3)**127','3**200']:
            with self.subTest(expression=expression):
                self.compare(self.program('_=VALUE','VALUE::'+expression),fail="Power constant exceeds Odin's 128-bit integer range")

    def test_negative_integer_exponents_have_the_reference_error(self):
        for expression in ['2**-1','0**-1','(-1)**-2']:
            with self.subTest(expression=expression):
                self.compare(self.program('_=VALUE','VALUE::'+expression),fail='Integer power requires a nonnegative exponent')

    def test_trivial_bases_with_wide_exponents(self):
        self.compare(self.program('fmt.println(A,B,C,D)',
            'import "core:fmt"\nA::0**170141183460469231731687303715884105728\n'
            'B::1**170141183460469231731687303715884105728\n'
            'C::(-1)**170141183460469231731687303715884105729\nD::0**0'),output='0 1 -1 1\n')

    def test_wide_unsigned_native_constant_parameter(self):
        self.compare(self.program('fmt.println(generic(340282366920938463463374607431768211455))',
            'import "core:fmt"\nwork::proc(n:u128)->u128{return n-1}\n'
            'generic::proc($N:u128)->u128{return #run work(N)}'),flags=['-thread-count:1'],
            output='340282366920938463463374607431768211454\n')

    def test_typed_integer_and_floating_powers_keep_native_execution(self):
        self.compare(self.program('small:i8=3;fmt.println(small**i8(2),VALUE,FLOAT)',
            'import "core:fmt"\nVALUE::u128(340282366920938463463374607431768211455)**u128(2)\nFLOAT::2.0**3.0'),
            output='9 1 8\n')

    def test_inactive_compile_time_branch_does_not_fold_bad_power(self):
        self.compare(self.program('when false{_=2**-1};fmt.println(7)','import "core:fmt"'),output='7\n')

    def test_generic_float_parameter_conditions_use_native_rounding(self):
        for typ,argument,rounded in [('f32','16777217','16777216'),('f64','9007199254740993','9007199254740992')]:
            for flags in (['-debug'],native.UNCHECKED):
                with self.subTest(typ=typ,flags=flags):
                    self.compare(self.program('fmt.println(generic('+argument+'))',
                        'import "core:fmt"\nwork::proc()->int{return 9}\n'
                        'generic::proc($N:'+typ+')->int{when N=='+rounded+'{return #run work()}else{return 0}}'),
                        flags=flags,output=('0\n' if typ=='f32' else '9\n'))

    def test_negative_float_parameters_keep_native_coercion(self):
        self.compare(self.program('fmt.println(generic(-9007199254740993))',
            'import "core:fmt"\nwork::proc()->int{return 9}\n'
            'generic::proc($N:f64)->int{when N == -9007199254740992{return #run work()}else{return 0}}'),output='9\n')

    def test_distinct_float_parameter_keeps_native_coercion(self):
        self.compare(self.program('fmt.println(generic(-9007199254740993))',
            'import "core:fmt"\nReal::distinct f64\nwork::proc()->int{return 9}\n'
            'generic::proc($N:Real)->int{when N == -9007199254740992{return #run work()}else{return 0}}'),output='9\n',
            reference_failure='Compile-time helper compilation failed')

    def test_native_float_selection_does_not_check_an_inactive_impure_arm(self):
        self.compare(self.program('fmt.println(#run generic(9007199254740993))',
            'import "core:fmt"\nstate:int\n'
            'generic::proc($N:f64)->int{when N==9007199254740992{return 9}else{return state}}'),output='9\n')

if __name__=='__main__':
    unittest.main()

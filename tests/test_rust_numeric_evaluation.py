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
            'generic::proc($N:u128)->u128{return #run work(N)}'),output='340282366920938463463374607431768211454\n')

    def test_typed_integer_and_floating_powers_keep_native_execution(self):
        self.compare(self.program('small:i8=3;fmt.println(small**i8(2),VALUE,FLOAT)',
            'import "core:fmt"\nVALUE::u128(340282366920938463463374607431768211455)**u128(2)\nFLOAT::2.0**3.0'),
            output='9 1 8\n')

    def test_inactive_compile_time_branch_does_not_fold_bad_power(self):
        self.compare(self.program('when false{_=2**-1};fmt.println(7)','import "core:fmt"'),output='7\n')

if __name__=='__main__':
    unittest.main()

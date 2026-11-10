"""Array hint metadata preserves native constant literals and distinct targets."""
import unittest
from tests import test_rust_compiler as native

class RustArrayContextTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_record_array_fields_and_defaults_remain_constants(self):
        prefix='''import "core:fmt"
Row::struct{values:[2]int}
VALUE::Row{[1,2]}
read::proc(row:=Row{[3,4]}){fmt.println(row)}
'''
        for flags in [[],native.UNCHECKED]:
            self.compare(self.program('fmt.println(VALUE);read()',prefix),flags=flags,expected='Row{values = [1, 2]}\nRow{values = [3, 4]}')

    def test_nested_and_typed_array_constants(self):
        for declaration in ['VALUE::[[1,2],[3,4]]','VALUE:[2][2]int:[[1,2],[3,4]]']:
            with self.subTest(declaration=declaration):
                self.compare(self.program('fmt.println(VALUE)','import "core:fmt"\n'+declaration+'\n'),expected='[[1, 2], [3, 4]]')

    def test_nested_distinct_constants_keep_constant_literal_context(self):
        prefix='import "core:fmt"\nVector::distinct[2]int\nVALUE::[Vector{1,2},[3,4]]\n'
        self.compare(self.program('fmt.println(VALUE)',prefix),expected='[[1, 2], [3, 4]]')

    def test_boxed_arrays_and_runtime_distinct_targets_keep_existing_behavior(self):
        prefix='''import "core:fmt"
Box::struct{value:any}
Vector::distinct[2]int
'''
        self.compare(self.program('first:any=[1,2];second:=Box{[3,4]};third:Vector=[5,6];fmt.println(first,second.value,third)',prefix),expected='[1, 2] [3, 4] [5, 6]')

    def test_invalid_slice_and_distinct_constant_field_contexts_still_fail(self):
        self.compare(self.program('_=Box{[1,2]}','Box::struct{value:[]int}\n'),command='check',fail="to '[]int'")
        prefix='Vector::distinct[2]int\nBox::struct{value:Vector}\nVALUE::Box{[1,2]}\n'
        self.compare(self.program('_=VALUE',prefix),command='check',fail='compile-time known constant')

    def test_array_field_length_mismatch_does_not_silently_fill_values(self):
        self.compare(self.program('value:=Box{[1]};_=value','Box::struct{values:[2]int}\n'),command='check',fail="to '[2]int'")

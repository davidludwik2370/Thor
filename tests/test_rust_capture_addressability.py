"""Reference capture and addressability rules across projections and intervals."""
import unittest
from tests import test_rust_compiler as native

class RustCaptureAddressTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_projected_value_parameters_support_runtime_indices(self):
        for declaration,body in [('using row:Row',''),('row:Row','using row;')]:
            prefix='''import "core:fmt"
Row::struct{values:[3]int}
read::proc('''+declaration+''',index:int)->int{'''+body+'''return values[index]}
'''
            source=self.program('row:=Row{[1,2,3]};fmt.println(read(row,1),read(row,-1));if row.values[1]!=2{panic("copy")}',prefix)
            for flags in [[],native.UNCHECKED]:self.compare(source,flags=flags,expected='2 3')

    def test_value_iteration_records_and_arrays_remain_readable_and_readonly(self):
        prefix='Row::struct{values:[3]int}\n'
        source=self.program('''rows:=[2]Row{{[1,2,3]},{[4,5,6]}}
index:=1;total:=0
for row in rows{total+=row.values[index]}
arrays:=[2][3]int{{1,2,3},{4,5,6}}
for values in arrays{total+=values[index]}
if total!=14 {panic("iteration read")}
''',prefix)
        for flags in [[],native.UNCHECKED]:self.compare(source,flags=flags)
        self.compare(self.program('rows:=[2]Row{};index:=1;for row in rows{row.values[index]=9}',prefix),command='check',fail='Cannot')

    def test_pointer_using_fields_keep_actual_destinations_in_multi_switches(self):
        prefix='''Row::struct{a,b:int,values:[3]int}
update::proc(using row:^Row,index:int){
 values[index]=9
 a,b = switch {case a<b: b,a;case:break}
}
'''
        source=self.program('row:=Row{1,2,[3,4,5]};update(&row,1);if row.a!=2||row.b!=1||row.values[1]!=9{panic("promoted destinations")}',prefix)
        for flags in [[],native.UNCHECKED]:self.compare(source,flags=flags)

    def test_reference_type_switch_over_nested_index_retains_original_storage(self):
        prefix='Item::union{int,[3]int}\n'
        source=self.program('''rows:=[2][2]Item{{[3]int{1,2,3},int(7)},{[3]int{4,5,6},int(8)}}
y,x,index:=1,0,1
switch &value in rows[y][x]{case [3]int:value[index]=9;case int:panic("tag")}
switch value in rows[y][x]{case [3]int:if value[index]!=9{panic("switch storage")};case int:panic("tag")}
''',prefix)
        for flags in [[],native.UNCHECKED]:self.compare(source,flags=flags)
        source=self.program('values:=[1]Item{[3]int{1,2,3}};index:=1;switch value in values[0]{case [3]int:value[index]=9;case int:}',prefix)
        self.compare(source,command='check',fail='Cannot')

    def test_fixed_capacity_dynamic_copy_matches_reference_data_privileges(self):
        source=self.program('''original:[dynamic;4]int
append(&original,1,2)
copy:>original
index:=1
copy[index]=9
if original[index]!=2||copy[index]!=9{panic("fixed copy")}
''')
        for flags in [[],native.UNCHECKED]:self.compare(source,flags=flags)
        self.compare(self.program('values:> []int{1,2};index:=1;values[index]=9'),command='check',fail='read-only slice')

    def test_value_using_parameter_still_rejects_runtime_index_writes(self):
        prefix='Row::struct{values:[3]int}\nwrite::proc(using row:Row,index:int){values[index]=9}\n'
        self.compare(self.program('row:=Row{[1,2,3]};write(row,1)',prefix),command='check',fail='Cannot')

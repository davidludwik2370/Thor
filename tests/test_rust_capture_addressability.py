"""Reference capture and addressability rules across projections and intervals."""
import unittest
from tests import test_rust_compiler as native

class RustCaptureAddressTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_soa_row_references_support_comparisons_through_builtin_and_library_aliases(self):
        body = '''numbers:=[3]int{1,2,3};words:=[2]string{"one","two"}
values:=ZIP(number=numbers[:],word=words[:])
for &row,index in values {
    if row.number!=index+1 || row.word!=words[index] {panic("SOA comparison")}
    row.number+=10
}
if (numbers!=[3]int{11,12,3}) {panic("SOA reference mutation")}
'''
        for callee in ('soa_zip', 'basic.zip', 'zip'):
            source = self.program(body.replace('ZIP', callee), 'import "thor:basic" / zip\n')
            for flags in ([], native.UNCHECKED):
                with self.subTest(callee=callee, flags=flags):
                    self.compare(source, flags=flags, expected='')

    def test_soa_row_references_survive_nested_helpers_and_indexed_writes(self):
        source = self.program('''numbers:=[2]int{1,2};words:=[2]string{"one","two"}
samples:=[2][3]int{{1,2,3},{4,5,6}}
values:=basic.zip(number=numbers[:],word=words[:],samples=samples[:])
for &row,index in values {
    text:=f"{row.word}:{switch {case row.number>0:row.samples[-1]**2;case:0}}"
    expected:=switch index {case 0:"one:9";case:"two:36"}
    if text!=expected {panic("nested SOA expression")}
    delete(text)
    row.samples[-1]=switch {case row.number in [1,2]:row.samples[-1]+10;case:0}
    row.number=switch {case row.word=="one":row.number+10;case:row.number+20}
}
if numbers[0]!=11 || numbers[1]!=22 || samples[0][2]!=13 || samples[1][2]!=16 {
    panic("nested SOA mutation")
}
''', 'import "thor:basic"\n')
        for flags in ([], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(source, flags=flags, expected='')

    def test_ordinary_pointer_and_local_captures_preserve_shared_mutation_order(self):
        source = self.program('''value:=1;pointer:=&value
text:=f"{bump(pointer)}:{switch {case true:bump(&value);case:0}}:{value}"
if text!="2:3:3" || value!=3 {panic("capture aliasing or evaluation order")}
delete(text)
''', 'bump::proc(value:^int)->int{value^+=1;return value^}\n')
        for flags in ([], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(source, flags=flags, expected='')

    def test_generic_indexed_switch_assignments_render_inside_their_capture_scope(self):
        source = self.program('''row:=Row{[3]int{1,2,3}};calls:=0
update(&row,&calls)
if row.data[2]!=13 || calls!=1 {panic("indexed switch capture or repeated destination")}
''', '''Row::struct{data:[3]int}
position::proc(calls:^int)->int{calls^+=1;return -1}
update::proc(row:$T,calls:^int){
    row.data[position(calls)]=switch {case row.data[0]==1:row.data[-1]+10;case:0}
}
''')
        for flags in ([], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(source, flags=flags, expected='')

    def test_skipping_generic_indexed_multi_assignments_keeps_destination_snapshots(self):
        source = self.program('''array := [4, 5]
index := 0
write(&array, &index, true)
if index != 1 || array[0] != 9 || array[1] != 5 { panic("array snapshot") }
view := array[:]
index = 0
write(&view, &index, false)
if index != 0 || view[0] != 9 { panic("slice skip") }
values: map[int]int
defer delete(values)
write(&values, &index, true)
if index != 1 || values[0] != 9 { panic("map snapshot") }
index = 0
write(&values, &index, false)
if index != 0 || values[0] != 9 { panic("map skip") }
''', '''write :: proc(a: ^$T, i: ^int, apply: bool) {
    i^, a^[i^] = switch { case apply: 1, 9; case: break }
}
''')
        for flags in ([], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(source, flags=flags)

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

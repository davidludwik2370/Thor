"""Promoted names retain the reference resolver's types and storage origins."""
import unittest

from tests import test_rust_compiler as compiler
from tests import test_rust_reference as native
from tests import test_t07_t12 as bindings
from tests import test_using_context as contexts


class RustUsingParityTests(unittest.TestCase):
    setUpClass = classmethod(native.RustReferenceTests.setUpClass.__func__)
    invoke = native.RustReferenceTests.invoke
    program = native.RustReferenceTests.program

    def test_parameter_and_local_projections_in_lowered_expressions(self):
        code = self.program('''
row := Row{[2,3]}
if read(row)!=8 {panic("using parameter capture")}
{using local:=Row{[4,5]}; values[0]=6; if values[-1]!=5||local.values[0]!=6{panic("using local capture")}}
''', '''Row::struct{values:[2]int}
read::proc(using row:Row)->int{return values[-1] + values[0]**2 + 1}
''')
        for flags in (['-debug'], compiler.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(code,flags=flags)

    def test_using_fields_preserve_readonly_slice_lineage(self):
        prefix='''Row::struct{values:[]int}
read::proc(using row:Row)->int{return values[0]}
write::proc(using row:Row){values[0]=9}
'''
        self.invoke(self.program('data:>Row{[]int{2}};if read(data)!=2{panic("using read")}',prefix))
        for body in ('data:>Row{[]int{2}};using data;values[0]=9',
                     'data:>Row{[]int{2}};write(data)',
                     'using data:>Row{[]int{2}};copy:=values;_=copy'):
            with self.subTest(body=body):
                self.invoke(self.program(body,prefix),command='check',fail='read-only slice')

    def test_standalone_using_on_parameters_keeps_native_addressability(self):
        self.invoke(self.program('row:=Row{[2,3]};if read(row)!=7{panic("using parameter")}',
            'Row::struct{values:[2]int}\nread::proc(row:Row)->int{using row;return values[-1]+values[0]**2}\n'))

    def test_type_switch_using_fields_in_fstrings_preserve_case_types_and_reference_aliases(self):
        code=self.program('''
row:=Row{7}
value:=format(Choice(row));defer delete(value)
pointer:=format(Choice(&row));defer delete(pointer)
reference:=format_reference(Choice(row));defer delete(reference)
if value!="v:7"||pointer!="p:7"||reference!="r:8"||row.value!=7{
    panic("narrowed using capture or reference storage")
}
''','''Row::struct{value:int}
Choice::union{Row,^Row}
format::proc(choice:Choice)->string{
    switch row in choice{
    case Row:
        using row
        switch {case true:return f"v:{value}"}
    case ^Row:
        using row
        return f"p:{value}"
    }
    return ""
}
format_reference::proc(choice:Choice)->string{
    local:=choice
    #partial switch &row in local{
    case Row:
        using row
        value+=1
        return f"r:{value}"
    }
    return ""
}
''')
        for flags in (['-debug'],compiler.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(code,flags=flags)

    def test_nearest_projection_and_initializer_shadowing(self):
        self.invoke(self.program('''
value:=99
{record:=Record{4};using record
 if value!=4{panic("projection shadows enclosing local")}
 {value:=value;value+=1;if value!=5{panic("initializer projection")}}
 value=7
 if record.value!=7{panic("projected assignment")}
}
if value!=99{panic("enclosing binding")}
''','Record::struct{value:int}\n'))

    def test_nested_using_records_and_promoted_pointer_storage(self):
        self.invoke(self.program('''
cell:=Cell{1}
outer:>Outer{inner=Inner{pointer=&cell}}
using outer;using inner
pointer.value=8
if cell.value!=8{panic("nested shallow pointer")}
''','Cell::struct{value:int}\nInner::struct{pointer:^Cell}\nOuter::struct{inner:Inner}\n'))
        self.invoke(self.program('outer:>Outer{};using outer;using inner;value=9',
                    'Inner::struct{value:int}\nOuter::struct{inner:Inner}\n'),
                    command='check',fail="immutable storage 'outer'")

    def test_package_projection_uses_original_global_storage(self):
        self.invoke(self.program('update()'),directory=True,files={
            'state.thor':'package main\nRow::struct{value:int}\nSTATE:>Row{}\n',
            'write.thor':'package main\nupdate::proc(){using STATE;value=9}\n',
        },command='check',fail="immutable storage 'STATE'")


for name in ('using_field_retains_immutable_storage_origin',
             'using_an_existing_pointer_remains_shallow',
             'embedded_using_pointer_remains_shallow',
             'imported_and_mixed_file_immutable_bindings',
             'conditional_immutable_declaration_uses_selected_branch',
             'inactive_immutable_violations_are_unchecked'):
    setattr(RustUsingParityTests,'test_'+name,getattr(bindings.BatchTests,'test_'+name))


for name in ('triangle_classification_switch_with_ternary_enum_arms',
             'zip_row_using_fields_are_available_to_fstrings_and_lowered_expressions',
             'zip_using_keeps_shadowing_and_nested_slice_permissions',
             'pointer_parameter_selector_switch_and_runtime_captures',
             'standalone_using_and_using_declaration_share_field_context',
             'nested_promoted_pointer_path_retains_shallow_mutability',
             'generic_record_copy_and_pointer_specializations',
             'scalar_array_record_string_and_distinct_field_assignments',
             'private_imported_enum_uses_field_type_without_exposing_owner_type',
             'local_shadow_does_not_change_promoted_destination_type',
             'readonly_promoted_destinations_still_reject_assignment'):
    setattr(RustUsingParityTests,'test_'+name,getattr(contexts.UsingContextTests,'test_'+name))


if __name__=='__main__':unittest.main()

"""Storage protection follows the Python reference, including native type guards."""
import unittest
from tests import test_t07_t12 as reference
from tests import test_rust_reference as native
from tests import test_rust_compiler as compiler


class RustStorageParityTests(unittest.TestCase):
    setUpClass=classmethod(native.RustReferenceTests.setUpClass.__func__)
    invoke=native.RustReferenceTests.invoke
    program=native.RustReferenceTests.program

    def test_immutable_for_posts_keep_diagnostics_and_inactive_branch_semantics(self):
        for post in ['index+=1','index=index+1']:
            with self.subTest(post=post):
                code=self.program('work(int)', 'work::proc($T:typeid){when T==int{for index:>0;index<2;'+post+'{}}}\n')
                self.invoke(code,command='check',fail="immutable storage 'index'")
        code=self.program('''work(f64)
index:=0
for marker:>7;index<3;index+=1{if marker!=7{panic("marker")}}
if index!=3{panic("loop")}
''','work::proc($T:typeid){when T==int{for index:>0;index<2;index+=1{}}}\n')
        for flags in ([],compiler.UNCHECKED):self.invoke(code,flags=flags)

    def test_generic_pointer_fields_are_shallow_and_queries_do_not_run_indices(self):
        code=self.program('''
cell:=Cell{}
rows:=[2]Row{{pointer=&cell},{pointer=&cell}}
calls:=0
write(rows,&calls)
if cell.value!=9||calls!=1{panic("pointer path or type query evaluation")}
''','''Cell::struct{value:int}
Row::struct{pointer:^Cell}
next::proc(calls:^int)->int{calls^+=1;return 0}
write::proc(value:$T,calls:^int){rows:>value;rows[next(calls)].pointer.value=9}
''')
        for flags in (['-debug'],compiler.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(code,flags=flags)

    def test_arrow_value_receivers_and_pointer_receivers_keep_native_behavior(self):
        code=self.program('''
calls:=0
by_value(Value{call=read},&calls)
item:=Reference{call=write}
by_reference(&item)
if item.value!=9||calls!=1{panic("arrow receiver")}
''','''Value::struct{call:proc(s:Value,calls:^int)}
Reference::struct{value:int,call:proc(s:^Reference)}
read::proc(s:Value,calls:^int){_=s;calls^+=1}
write::proc(s:^Reference){s.value=9}
by_value::proc(value:$T,calls:^int){s:>value;s->call(calls)}
by_reference::proc(value:$T){s:>value;s->call()}
''')
        for flags in (['-debug'],compiler.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(code,flags=flags)

    def test_generic_storage_rejection_survives_unchecked_builds(self):
        code=self.program('write(Row{})','''
Cell::struct{value:int}
Row::struct{cell:Cell}
write::proc(value:$T){row:>value;row.cell.value=9}
''')
        for flags in (['-debug'],compiler.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(code,command='build',flags=flags,fail="immutable storage 'row'")

    def test_native_dependencies_preserve_immutable_array_storage(self):
        code=self.program('work()', 'VALUES:>[2]int{1,2}\n')
        for operation in ('VALUES[0]=9', 'pointer:=&VALUES[0];_=pointer',
                          'values:=VALUES[:];_=values', 'for &value in VALUES{value=9}',
                          'pointer:=raw_data(VALUES);_=pointer'):
            with self.subTest(operation=operation):
                self.invoke(code,directory=True,files={
                    'work.odin':'package main\nwork::proc(){'+operation+'}\n',
                },command='check',fail="immutable storage 'VALUES'")
        self.invoke(code,directory=True,files={
            'work.odin':'package main\nwork::proc(){total:=0;for value in (VALUES[:]){total+=value};if total!=3{panic("value loop")}}\n',
        })

    def test_native_generic_guards_preserve_header_and_do_scopes(self):
        code=self.program('work(VALUES)', 'VALUES:>[2]int{1,2}\n')
        for body in ('for i:=0;i<1;value[0]=9{i+=1}',
                     'if consume(&value[0]) {}',
                     'for i:=0;i<1;i+=1 do consume(&value[0])'):
            with self.subTest(body=body):
                self.invoke(code,directory=True,files={
                    'work.odin':'package main\nconsume::proc(p:^int)->bool{return p^>0}\nwork::proc(value:$T){'+body+'}\n',
                },command='check',fail='Cannot')


for name in (
    'immutable_inferred_and_typed_runtime_initializers_run_once',
    'immutable_multiple_expressions_evaluate_once_in_order',
    'immutable_mouse_coordinates_with_multiple_cast_and_division_expressions',
    'every_binding_in_multi_expression_declaration_is_immutable',
    'immutable_scalar_reassignments',
    'immutable_multiple_assignment_target',
    'mutable_addresses_of_direct_storage_are_rejected',
    'fixed_array_cannot_escape_as_mutable_slice',
    'immutable_array_slice_can_be_consumed_by_value_iteration',
    'immutable_array_field_and_generic_readonly_iteration',
    'immutable_imported_array_supports_immediate_readonly_iteration',
    'direct_array_mutable_iteration_is_rejected',
    'readonly_slice_views_observe_changes_through_original_storage',
    'slice_header_cannot_be_reassigned_or_addressed',
    'existing_pointer_pointee_remains_mutable',
    'existing_map_contents_remain_mutable',
    'dynamic_array_header_mutation_requires_mutable_storage',
    'copy_of_immutable_value_is_mutable',
    'nested_shadowing_parameters_and_loop_bindings',
    'same_spelling_in_other_procedure_is_mutable',
    'tuple_result_shapes_and_discard_names',
    'generic_explicit_pointer_path_remains_shallow',
    'generic_arrow_call_checks_implicit_mutable_address',
    'native_arrow_call_cannot_take_implicit_mutable_address',
    'generic_guard_preserves_control_header_scopes_and_do_bodies',
    'embedded_using_pointer_remains_shallow',
    'generic_shape_checks_distinguish_array_slice_and_map',
    'immutable_record_and_nested_array_subobjects',
    'immutable_iteration_exception_does_not_expose_mutable_aliases',
):
    setattr(RustStorageParityTests,'test_'+name,getattr(reference.BatchTests,'test_'+name))


if __name__=='__main__':unittest.main()

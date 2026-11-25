"""Captured target checks share the reference conversion fixtures."""
import json
import unittest
from tests import test_pure_conversions as reference
from tests import test_rust_reference as native
from tests import test_fstring_lifetimes as lifetimes


class RustPureConversionTests(unittest.TestCase):
    setUpClass=classmethod(native.RustReferenceTests.setUpClass.__func__)
    invoke=native.RustReferenceTests.invoke
    program=native.RustReferenceTests.program
    tracked=reference.PureConversionTests.tracked
    memory=lifetimes.FStringLifetimeTests.memory
    test_type_conversions_preserve_owned_strings_and_enclosing_call_cleanup=lifetimes.FStringLifetimeTests.test_type_conversions_preserve_owned_strings_and_enclosing_call_cleanup

    def test_native_type_proof_diagnostic_uses_the_original_conversion(self):
        code=self.program('n:=3;_=outer(^int,n)', '''
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{_=T(uintptr(value));return value}
    return inner(n)
}
''')
        _,result=self.invoke(code,command='check',flags=['-json-errors'],
                             fail='Pure code cannot convert values to pointers')
        errors=json.loads(result.stderr)['errors']
        self.assertEqual(len(errors),1,errors)
        line=next(i for i,line in enumerate(code.splitlines(),1) if 'T(uintptr(value))' in line)
        self.assertEqual(errors[0]['pos']['line'],line,errors)
        self.assertTrue(errors[0]['pos']['file'].replace('\\','/').endswith('/input/main.thor'),errors)
        for hidden in ('conversion_type','pure_type_site','Thor expression'):
            self.assertNotIn(hidden,result.stderr)


for name in (
    'captured_pointer_conversion_and_cast_are_rejected',
    'captured_distinct_and_aliased_pointer_targets_are_rejected',
    'direct_distinct_pointer_targets_are_rejected',
    'captured_record_fields_cannot_hide_pointer_operands',
    'captured_numeric_record_fields_remain_valid',
    'captured_nil_only_switch_does_not_observe_pointer_identity',
    'captured_raw_union_local_storage_and_literals_are_rejected',
    'distinct_generic_record_instantiations_are_all_checked',
    'generic_public_raw_unions_are_rejected_even_with_one_field',
    'captured_local_pointers_remain_owned_and_writable',
    'ordinary_record_is_not_confused_with_an_inaccessible_raw_union',
    'captured_numeric_conversion_keeps_constants_and_runs_operands_once',
    'captured_cast_keeps_constants',
    'captured_string_and_slice_conversions_preserve_owned_results',
    'type_queries_may_inspect_pointer_conversions',
    'supplied_arguments_do_not_check_unused_captured_defaults',
    'omitted_captured_defaults_are_checked',
    'declared_fn_defaults_remain_part_of_the_contract',
):
    setattr(RustPureConversionTests,'test_'+name,getattr(reference.PureConversionTests,'test_'+name))


if __name__=='__main__':unittest.main()

"""Pure cast contexts agree with the independent reference implementation."""
import json
import unittest
from tests import test_pure_auto_casts as reference
from tests import test_rust_reference as native


class RustPureAutoCastTests(unittest.TestCase):
    setUpClass=classmethod(native.RustReferenceTests.setUpClass.__func__)
    invoke=native.RustReferenceTests.invoke
    program=native.RustReferenceTests.program
    tracked=reference.PureAutoCastTests.tracked

    def test_checked_helper_type_error_points_to_the_requiring_fn_call(self):
        code=self.program('n:=uintptr(3);_=work(n)','''
helper::proc($T:typeid,n:uintptr)->int{pointer:T=auto_cast n;_=pointer;return 0}
work::fn(n:uintptr)->int{return helper(^int,n)}
''')
        _,result=self.invoke(code,command='check',flags=['-json-errors'],fail='Pure code cannot convert values to pointers')
        errors=json.loads(result.stderr)['errors']
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['pos']['line'],next(i for i,line in enumerate(code.splitlines(),1) if 'return helper(' in line))
        self.assertIn("Pure function 'work' cannot call 'helper'",' '.join(errors[0]['msgs']))
        self.assertIn('work -> helper',' '.join(errors[0]['msgs']))
        self.assertTrue(any(note['pos']['line']==3 for note in errors[0]['related']),errors)
        for hidden in ('pure_validate','pure_type_site','Thor expression'):
            self.assertNotIn(hidden,result.stderr)


    def test_multiple_checked_callers_keep_specialization_context_as_notes(self):
        code=self.program('n:=uintptr(3);_=good(n);_=bad(n)','''
helper::proc($T:typeid,n:uintptr)->int{pointer:T=auto_cast n;_=pointer;return 0}
good::fn(n:uintptr)->int{return helper(uintptr,n)}
bad::fn(n:uintptr)->int{return helper(^int,n)}
''')
        _,result=self.invoke(code,command='check',flags=['-json-errors'],fail='Pure code cannot convert values to pointers')
        errors=json.loads(result.stderr)['errors']
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['pos']['line'],3,errors)
        messages=' '.join(errors[0]['msgs'])
        self.assertIn('good -> helper',messages)
        self.assertIn('bad -> helper',messages)
        self.assertTrue(any(note['pos']['line']==4 for note in errors[0]['related']),errors)
        self.assertTrue(any(note['pos']['line']==5 for note in errors[0]['related']),errors)



for name in (
    'pointer_targets_are_rejected_in_all_typed_positions',
    'pointer_operands_cannot_be_hidden_by_cast_or_auto_cast',
    'numeric_contexts_preserve_values_and_operand_evaluation_count',
    'generic_call_keeps_context_from_preceding_arguments',
    'private_formal_types_stay_in_the_callee_scope',
    'sdk_generic_formals_can_supply_the_exact_cast_type',
    'wide_untyped_literals_and_enum_targets_keep_their_context',
    'ordinary_procedures_keep_native_pointer_casts',
    'unused_ordinary_defaults_keep_their_native_contract',
    'aggregate_pointer_conversions_are_rejected',
    'numeric_aggregate_casts_remain_valid',
    'auto_cast_keeps_native_generic_and_overload_inference',
    'named_variadic_and_multiple_results_preserve_context',
):
    setattr(RustPureAutoCastTests,'test_'+name,getattr(reference.PureAutoCastTests,'test_'+name))


if __name__=='__main__':unittest.main()

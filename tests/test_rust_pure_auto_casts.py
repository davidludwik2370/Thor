"""Pure cast contexts agree with the independent reference implementation."""
import unittest
from tests import test_pure_auto_casts as reference
from tests import test_rust_reference as native


class RustPureAutoCastTests(unittest.TestCase):
    setUpClass=classmethod(native.RustReferenceTests.setUpClass.__func__)
    invoke=native.RustReferenceTests.invoke
    program=native.RustReferenceTests.program
    tracked=reference.PureAutoCastTests.tracked


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

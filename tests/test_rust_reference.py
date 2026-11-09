"""Run established acceptance fixtures against both independent implementations.

The explicit lists describe migrated coverage, not a skip-on-unsupported policy.
An unsupported status fails a selected fixture just like any other mismatch.
"""
import subprocess
import unittest

from tests import test_thor as base
from tests import test_rust_compiler as rust
from tests import test_t13_t17 as expressions
from tests import test_typed_loops as loops


class RustReferenceTests(unittest.TestCase):
    setUpClass = classmethod(rust.RustCompilerTests.setUpClass.__func__)
    program = base.ThorTests.program

    def invoke(self, source, *, files=None, command='run', fail=None, flags=(),
               args=(), expected=None, directory=False):
        root, reference = base.ThorTests.invoke(self, source, files=files, command=command,
                                                fail=fail, flags=flags, args=args,
                                                expected=expected, directory=directory)
        entry = root/'input/main.thor'
        before = {p:p.read_bytes() for p in (root/'input').rglob('*') if p.is_file()}
        argv = [str(rust.RUST), command, str(entry.parent if directory else entry),
                '--output-dir', str(root/'rust'), *flags]
        if args:
            argv += ['--', *args]
        result = subprocess.run(argv, cwd=root if command == 'build' else None,
                                capture_output=True, text=True, timeout=60)
        details = ('Reference:\n'+reference.stdout+reference.stderr+
                   '\nRust:\n'+result.stdout+result.stderr)
        self.assertNotEqual(result.returncode, 3, details)
        self.assertEqual(result.returncode, reference.returncode, details)
        if fail is not None:
            self.assertIn(fail, result.stdout+result.stderr, details)
        else:
            self.assertEqual(result.stdout, reference.stdout, details)
        self.assertEqual(before, {p:p.read_bytes() for p in before})
        return root, result


EXPRESSIONS = [
    'set_literals_operations_queries_and_cleanup',
    'set_contextual_enum_literal_and_zero_initialized',
    'set_in_place_and_alias_self_operations',
    'set_results_independent_and_assignment_aliases',
    'set_preserves_native_operators_and_set_identifier',
    'generic_set_operators_iteration_length_and_cleanup',
    'generic_native_operations_keep_contextual_numeric_types',
    'contextless_expression_helpers_and_generic_type_constants',
    'negative_array_slice_dynamic_and_string_accesses',
    'negative_lvalues_compound_addresses_and_nested_arrays',
    'record_parameter_array_reads_keep_native_readonly_addressability',
    'reference_range_over_indexed_array_row_preserves_original_storage',
    'nested_temporary_array_reads_and_reference_headers_remain_valid',
    'reference_range_does_not_make_readonly_record_parameters_mutable',
    'negative_access_evaluation_order_once',
    'negative_index_signed_width_and_unsigned_preservation',
    'negative_map_keys_preserve_read_write_and_address_rules',
    'negative_generic_arrays_slices_maps_and_strings',
    'generic_negative_assignment_preserves_map_headers',
    'negative_write_operand_order_once',
    'generic_multiple_assignment_keeps_original_lhs_values',
    'very_wide_negative_bound_does_not_wrap_into_a_valid_index',
    'enum_array_and_pointer_indices_keep_native_meaning',
    'negative_const_string_index_and_slice',
    'negative_bounds_are_not_clamped',
    'compile_switch_selector_and_boolean_expressions_are_constants',
    'compile_switch_statement_visibility_return_and_loop_break',
]
LOOPS = [
    'ranges_choose_numeric_type_and_preserve_range_forms',
    'typed_bounds_still_require_explicit_conversion',
    'range_bounds_preserve_native_evaluation_order_and_frequency',
    'array_slice_dynamic_pointer_and_reference_indices',
    'empty_and_largest_fitting_arrays_and_distinct_index',
    'fixed_array_overflow_is_a_compile_error',
    'slice_index_overflow_panics_even_with_asserts_disabled',
    'readonly_index_assignment_address_and_body_shadow',
    'labels_break_continue_defer_and_do_bodies',
    'getter_runs_once_and_native_array_iteration_observes_mutation',
    'contextless_alias_shadow_initializer_and_integer_endpoint',
    'dynamic_overflow_only_checks_visited_indices_and_return_exits_normally',
    'unsupported_annotations_and_ambiguous_group_are_rejected',
    'inactive_branches_do_not_instantiate_loop_contracts',
    'literals_members_and_immutable_reference_restrictions',
    'comments_multiline_bindings_and_parenthesized_numeric_binding',
]
def _install():
    for suite, names in [(expressions.ExpressionTests, EXPRESSIONS), (loops.TypedLoopTests, LOOPS)]:
        for name in names:
            setattr(RustReferenceTests, 'test_'+name, getattr(suite, 'test_'+name))


_install()

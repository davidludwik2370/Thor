"""Run established acceptance fixtures against both independent implementations.

The explicit lists describe migrated coverage, not a skip-on-unsupported policy.
An unsupported status fails a selected fixture just like any other mismatch.
"""
import subprocess
import unittest
from unittest.mock import patch

from tests import test_thor as base
from tests import test_rust_compiler as rust
from tests import test_t13_t17 as expressions
from tests import test_typed_loops as loops
from tests import test_slice_access as slices
from tests import test_slice_copy_hardening as copies
from tests import test_comprehensions as comprehensions
from tests import test_extrema as extrema
from tests import test_t07_t12 as bindings
from tests import test_pure_calls as checked


class RustReferenceTests(unittest.TestCase):
    setUpClass = classmethod(rust.RustCompilerTests.setUpClass.__func__)
    program = base.ThorTests.program
    check = copies.SliceCopyHardeningTests.check
    memory = checked.PureCallTests.memory

    def invoke(self, source, *, files=None, command='run', fail=None, flags=(),
               args=(), expected=None, directory=False):
        with patch.object(self, 'compiler', rust.PYTHON, create=True):
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
RESOLUTION = [
    'contextual_array_through_overloaded_target',
    'contextual_array_through_generic_specialization',
    'conditional_generic_context_follows_native_branch',
    'distinct_overload_and_explicit_generic_context',
    'generic_element_type_from_another_argument',
    'generic_context_from_call_result_preserves_shadowed_type_and_evaluation',
    'context_wrappers_resolve_private_types_in_defining_package',
    'contextual_array_through_procedure_alias',
    'generic_length_and_type_parameter_inference',
    'generic_record_arguments_flow_into_result_dimensions',
    'generic_record_members_and_substitutions',
    'selective_alias_survives_shadowed_package_namespace',
]
def _install():
    for suite, names in [(expressions.ExpressionTests, EXPRESSIONS), (loops.TypedLoopTests, LOOPS),
                         (bindings.BatchTests, RESOLUTION)]:
        for name in names:
            setattr(RustReferenceTests, 'test_'+name, getattr(suite, 'test_'+name))
    # Every slice fixture, including native compile-time execution, must match.
    for suite in (slices.SliceAccessTests,copies.SliceCopyHardeningTests):
        for name in unittest.defaultTestLoader.getTestCaseNames(suite):
            setattr(RustReferenceTests,'test_borrowing_'+name[5:],getattr(suite,name))
    for name in ('explicit_collections_and_duplicates','flatten_all_destinations_and_map_last_wins',
                 'fstring_elements_keep_owned_storage_and_shallow_slice_elements',
                 'package_runtime_initializers_and_anonymous_owned_arguments',
                 'or_else_unwraps_output_once_with_lazy_fallback',
                 'distinct_containers_and_mixed_untyped_range_bounds',
                 'or_else_map_lookup_and_optional_fallback_call'):
        setattr(RustReferenceTests,'test_comprehension_'+name,getattr(comprehensions.ComprehensionTests,'test_'+name))
    for name in ('slice_results_empty_singleton_and_ignored_status',
                 'arrays_one_element_multiple_expansions_and_native_forms',
                 'calls_once_in_source_order_with_array_snapshots',
                 'generic_and_contextless_code','aliases_shadowing_and_native_odin_passthrough',
                 'nonempty_allocation_free_reductions','rejected_shapes_types_and_empty_arrays',
                 'optional_status_fallback_and_multiple_rhs_values','float_special_values_match_native_left_fold'):
        setattr(RustReferenceTests,'test_extrema_'+name,getattr(extrema.ExtremaTests,'test_'+name))
    for name in ('borrowed_views_are_owned_only_on_the_checked_path',
                 'owned_variadic_results_forward_named_only_parameters',
                 'private_procedures_cannot_be_exposed_through_checked_versions',
                 'same_named_file_private_helpers_keep_their_own_bodies',
                 'void_multiple_results_and_variadic_helpers',
                 'inspectable_contextless_and_c_helpers',
                 'type_queries_do_not_execute_or_check_ordinary_effects'):
        setattr(RustReferenceTests,'test_checked_'+name,getattr(checked.PureCallTests,'test_'+name))


_install()

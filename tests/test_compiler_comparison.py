"""The migration gate must never count gaps or crashes as successful parity."""
import unittest
from tools.compare_compilers import Observation, canonical_diagnostics, classify, split_diagnostic_stream, comparison_flags


class CompilerComparisonTests(unittest.TestCase):
    def observation(self, status=0, stdout='', diagnostics=None, stderr='', timeout=False, toolchain_failure=False):
        return Observation(status, stdout, diagnostics or [], stderr, 0.0, timeout,toolchain_failure)

    def test_migration_gaps_fail_even_if_reference_also_fails(self):
        python = self.observation(status=1)
        rust = self.observation(status=3,diagnostics=[{'messages':['Rust migration: missing feature']}])
        self.assertEqual(classify(python, rust), 'unported')

    def test_program_exit_three_is_distinct_from_a_migration_gap(self):
        python=self.observation(status=3)
        rust=self.observation(status=3)
        self.assertEqual(classify(python,rust),'match')

    def test_timeouts_and_mismatches_are_distinct(self):
        python = self.observation(stdout='expected')
        rust = self.observation(timeout=True)
        self.assertEqual(classify(python, rust), 'timeout')
        rust = self.observation(stdout='wrong')
        self.assertEqual(classify(python, rust), 'mismatch')
        rust = self.observation(status=-10)
        self.assertEqual(classify(python, rust), 'mismatch')
        python = self.observation(status=-10)
        self.assertEqual(classify(python, rust), 'mismatch')

    def test_success_and_failure_observables_must_match(self):
        python = self.observation(stdout='42\n')
        rust = self.observation(stdout='42\n')
        self.assertEqual(classify(python, rust), 'match')
        errors = [{'severity':'error', 'messages':['bad'], 'file':'main.thor', 'line':2, 'column':4}]
        python = self.observation(status=1, diagnostics=errors)
        rust = self.observation(status=1, diagnostics=errors)
        self.assertEqual(classify(python, rust), 'match')
        rust = self.observation(status=1, diagnostics=[{**errors[0], 'column':5}])
        self.assertEqual(classify(python, rust), 'mismatch')

    def test_unstructured_backend_failure_is_not_hidden(self):
        python = self.observation(status=1, stderr='backend crash')
        rust = self.observation(status=1, stderr='different failure')
        self.assertEqual(classify(python, rust), 'mismatch')

    def test_json_envelope_spacing_is_not_a_runtime_difference(self):
        first, first_residue = split_diagnostic_stream('{"error_count": 0, "errors": []}\n')
        second, second_residue = split_diagnostic_stream('{"error_count":0,"errors":[]}\n')
        self.assertEqual(first, second)
        self.assertEqual(first_residue, '')
        self.assertEqual(second_residue, '')
        python=self.observation(stderr='formatted envelope')
        rust=self.observation(stderr='compact envelope')
        python.stderr_residue=first_residue
        rust.stderr_residue=second_residue
        self.assertEqual(classify(python,rust),'match')

    def test_deterministic_native_checker_default_preserves_explicit_override(self):
        defaults=comparison_flags(['-o:speed'])
        explicit=comparison_flags(['-thread-count:4','-o:speed'])
        self.assertEqual(defaults,['-thread-count:1','-o:speed'])
        self.assertEqual(explicit,['-thread-count:4','-o:speed'])

    def test_runtime_stderr_after_json_and_failed_stdout_are_preserved(self):
        packet, residue=split_diagnostic_stream('{"error_count":0,"errors":[]}\n\nprogram stderr\n')
        self.assertEqual(packet['errors'],[])
        self.assertEqual(residue,'\nprogram stderr\n')
        errors=[{'messages':['same failure']}]
        python=self.observation(status=1,stdout='first',diagnostics=errors,stderr='extra')
        rust=self.observation(status=1,stdout='second',diagnostics=errors,stderr='extra')
        self.assertEqual(classify(python,rust),'mismatch')
        rust.stdout=python.stdout
        rust.stderr='different extra'
        self.assertEqual(classify(python,rust),'mismatch')

    def test_arbitrary_or_malformed_json_is_not_discarded(self):
        for text in ['{"errors": []}\n', '{"error_count":0,"errors":"bad"}\n',
                     'backend failure\n{"error_count":1,"errors":[]}\n']:
            with self.subTest(text=text):
                _,residue=split_diagnostic_stream(text)
                self.assertEqual(residue,text)

    def test_reported_backend_crashes_do_not_count_as_matching_rejections(self):
        python=self.observation(status=1,toolchain_failure=True)
        rust=self.observation(status=1,toolchain_failure=True)
        self.assertEqual(classify(python,rust),'mismatch')

    def test_public_error_locations_and_messages_are_preserved(self):
        errors = canonical_diagnostics({'errors':[{'type':'error', 'stage':'backend',
            'pos':{'file':'main.thor', 'line':3, 'column':8, 'offset':20}, 'msgs':['bad']} ]})
        self.assertEqual(errors, [{'severity':'error', 'messages':['bad'],
                                  'file':'main.thor', 'line':3, 'column':8}])

    def test_note_order_can_vary_but_core_messages_and_duplicates_cannot(self):
        first = canonical_diagnostics({'errors':[{'msgs':['reason', 'caller', '', 'instance', 'caller']}]})
        second = canonical_diagnostics({'errors':[{'msgs':['reason', 'caller', 'caller', 'instance']}]})
        self.assertEqual(first, second)
        missing = canonical_diagnostics({'errors':[{'msgs':['reason', 'caller', 'instance']}]})
        self.assertNotEqual(first, missing)
        changed = canonical_diagnostics({'errors':[{'msgs':['different reason', 'caller', 'caller', 'instance']}]})
        self.assertNotEqual(first, changed)

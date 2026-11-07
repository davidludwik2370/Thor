"""Unused locals must warn while successful builds retain their behavior."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests import test_thor as base
from thorlang import backend


class UnusedLocalTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def warnings(self, source, *, flags=(), command='run', expected=None):
        root, result = self.invoke(source, command=command, flags=[*flags, '-json-errors'], expected=expected)
        packet = json.loads(result.stderr)
        self.assertEqual(packet['error_count'], 0, packet)
        self.assertTrue(all(error['type'] == 'warning' for error in packet['errors']), packet)
        return root, result, packet['errors']

    def test_control_blocks_do_bodies_and_grouped_names(self):
        for body, names in [
            ('if true {value:=1}', ['value']),
            ('if true do first,second:=1,2', ['first', 'second']),
            ('if false {} else {value:>1}', ['value']),
            ('for _ in 0..<2 {value:int=1}', ['value']),
            ('if true {VALUE::1}', ['VALUE']),
            ('if true {Local::struct{value:int}}', ['Local']),
        ]:
            with self.subTest(body=body):
                _, _, errors = self.warnings(self.program(body))
                self.assertEqual([error['msgs'][0] for error in errors],
                                 [repr(name)+' declared but not used' for name in names])

    def test_initializers_and_deferred_work_run_once_checked_and_unchecked(self):
        source = self.program('''
if true {unused:=next()}
if false {never:=next()}
for _ in 0..<2 do first,second:=pair()
if true {resource:=acquire()}
if calls!=3 || closed!=1 {panic("initializer/cleanup count")}
fmt.println(calls)
''', '''import "core:fmt"
calls:int
closed:int
cleanup::proc(value:int){closed+=value}
@(deferred_out=cleanup)
acquire::proc()->int{return 1}
next::proc()->int{calls+=1;return calls}
pair::proc()->(int,int){calls+=1;return calls,calls}
''')
        for flags in [[], ['-debug'], ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']]:
            with self.subTest(flags=flags):
                _, result, errors = self.warnings(source, flags=flags, expected='3')
                self.assertEqual(len(errors), 5, errors)
                self.assertNotIn('Thor allocation tracker:', result.stderr)

    def test_warning_controls_and_other_vet_checks(self):
        source = self.program('if true {unused:=1}')
        self.warnings(source, flags=['-ignore-warnings'])
        _, result = self.invoke(source, command='check', flags=['-json-errors', '-warnings-as-errors'], fail='declared but not used')
        self.assertEqual(json.loads(result.stderr)['errors'][0]['type'], 'error')
        self.invoke(self.program('unused:=1;value:=2;_=value'), command='check',
                    flags=['-vet-unused-variables'], fail='declared but not used')
        self.invoke(self.program('if true {unused:=1}', 'import "core:fmt"\n'),
                    command='check', flags=['-vet-unused-imports'], fail="'fmt' declared but not used")
        self.invoke(self.program('outer:=1;if true {outer:=2};_=outer'),
                    command='check', flags=['-vet-shadowing'], fail='shadows declaration')

    def test_native_selection_and_existing_unused_rules(self):
        for body in ['when false {if true {unused:=missing}}',
                     'unused:=1', '{unused:=1}',
                     'if true {used:=1;_=used}',
                     'if true {_=1}',
                     'switch 1 {case 1:unused:=1}']:
            with self.subTest(body=body):
                _, _, errors = self.warnings(self.program(body))
                self.assertEqual(errors, [])

    def test_independent_errors_still_fail_with_the_unused_warning(self):
        source = self.program('if true {unused:=1};value:int="wrong";_=value')
        _, result = self.invoke(source, command='check', flags=['-json-errors'], fail='Cannot convert')
        errors = json.loads(result.stderr)['errors']
        self.assertTrue(any(error['type'] == 'warning' and 'declared but not used' in error['msgs'][0] for error in errors), errors)
        self.assertTrue(any(error['type'] == 'error' and 'Cannot convert' in error['msgs'][0] for error in errors), errors)

    def test_successful_build_produces_an_executable(self):
        root, _, errors = self.warnings(self.program('if true {unused:=1};fmt.println("ran")', 'import "core:fmt"\n'), command='build')
        self.assertEqual(len(errors), 1)
        executable = root/(root.name+('.exe' if os.name == 'nt' else ''))
        result = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ran')

    def test_original_unicode_ranges_and_later_type_errors(self):
        source = self.program('/* 😀 */if true do unused:=1;value:int="wrong";_=value')
        for limit in [[], ['-max-error-count:1']]:
            with self.subTest(flags=limit):
                _, result = self.invoke(source, command='check', flags=['-json-errors', *limit], fail='Cannot convert')
                errors = json.loads(result.stderr)['errors']
                warning = next(error for error in errors if error['type'] == 'warning')
                self.assertEqual(warning['pos']['offset'], source.encode().index(b'unused'))
                self.assertEqual(warning['pos']['end_offset']-warning['pos']['offset'], len('unused'))
                error = next(error for error in errors if error['type'] == 'error')
                self.assertEqual(error['pos']['offset'], source.encode().index(b'"wrong"'))
        _, _, errors = self.warnings(self.program('if true {тест:=1}'))
        self.assertEqual(errors[0]['pos']['end_offset']-errors[0]['pos']['offset'], len('тест'.encode()))

    def test_compiletime_calls_with_an_unused_inner_declaration_still_build(self):
        for signature in ['proc(value:$T)->T', 'fn(value:int)->int']:
            with self.subTest(signature=signature), tempfile.TemporaryDirectory(prefix='thor-unused-cache-') as directory:
                root = Path(directory)
                source = root/'main.thor'
                source.write_text('package main\nget::'+signature+
                    '{if true do unused:=1;return value}\nANSWER::#run get(7)\n'
                    'main::proc(){if ANSWER!=7{panic("result")}}\n')
                for _ in range(2):
                    result = subprocess.run([str(base.ROOT/'thor'), 'run', str(source), '-json-errors',
                                             '--output-dir', str(root/'output')], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    self.assertEqual(json.loads(result.stderr)['error_count'], 0)

    def test_small_native_error_budget_preserves_all_warnings(self):
        _, _, errors = self.warnings(self.program('if true {a:=1};if true {b:=2};if true {c:=3}'),
                                     flags=['-max-error-count:1'])
        self.assertEqual(len(errors), 3, errors)

    def test_requested_procedure_vetting_stays_strict(self):
        self.invoke(self.program('local::proc($T:typeid){};_=1'), command='check',
                    flags=['-vet-unused-procedures', '-vet-packages:main'], fail='declared but not used')
        self.invoke('#+vet unused-procedures\n'+self.program('local::proc($T:typeid){};_=1'),
                    command='check', fail='declared but not used')

    def test_clean_backend_success_keeps_one_compiler_invocation(self):
        result = subprocess.CompletedProcess(['odin', 'check', '.'], 0, stderr=b'')
        with patch('thorlang.backend.subprocess.run', return_value=result) as invoke:
            returned = backend.run(None, ['odin', 'check', '.'], stderr=subprocess.PIPE)
        self.assertIs(returned, result)
        self.assertEqual(invoke.call_count, 1)

    def test_imported_native_source_uses_the_same_local_warning_policy(self):
        source = self.program('library.work();if library.calls!=1{panic("native initializer")}', 'import "library"\n')
        _, result = self.invoke(source, flags=['-json-errors'], files={
            'library/work.odin': 'package library\ncalls:int\nnext::proc()->int{calls+=1;return calls}\nwork::proc(){if true {unused:=next()}}\n'})
        warnings = json.loads(result.stderr)['errors']
        self.assertEqual(len(warnings), 1, warnings)
        self.assertEqual(warnings[0]['type'], 'warning')
        self.assertTrue(warnings[0]['pos']['file'].endswith('/library/work.odin'), warnings)


if __name__ == '__main__':
    unittest.main()

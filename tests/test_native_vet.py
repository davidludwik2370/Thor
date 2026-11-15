"""Every native vet policy is either audited for Thor or explicitly restricted.

Native-only projects are also compared with Odin itself, including real errors.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from tests import test_rust_compiler as native
from thorlang import cli


SUPPORTED = {'vet-when-shadowing', 'vet-semicolon', 'vet-packages'}
RESTRICTED = {flag['name'] for flag in cli.CATALOG['native_flags'] if flag.get('thor_rejection')}
CLEAN = 'package main\nmain :: proc() {}\n'
FEATURES = '''package main
import "core:fmt"
import _ "thor:basic" / zip
increment :: fn(x: int) -> int { return x+1 }
ANSWER :: #run increment(2)
main :: proc() {
	numbers := [2]int{1, 2}
	words := [2]string{"one", "two"}
	values := zip(number=numbers[:], word=words[:])
	for &row, i in values {
		if row.number != i+1 || row.word != words[i] { panic("zip") }
		row.number += 1
	}
	text := f"{switch {case numbers[0]>0: increment(numbers[0]); case: 0}}"
	defer delete(text)
	odd :> []int{x**2 for x in numbers if x>0}
	defer delete(odd)
	if odd[0]!=4 || odd[-1]!=9 { panic("comprehension") }
	for i: f64 in 0..<2 { if i<0 { panic("typed loop") } }
	big_value: Integer = 123456789012345678901234567890
	defer delete(big_value)
	big_value += 2
	if big_value != Integer(123456789012345678901234567892) { panic("big value") }
	if ANSWER != 3 { panic("compile-time helper") }
	fmt.println(text)
}
'''
VIOLATIONS = {
    'vet': ('package main\nimport "core:fmt"\nmain::proc(){x:=1;fmt.println(cast(int)x)}\n', 'cast'),
    'vet-cast': ('package main\nimport "core:fmt"\nmain::proc(){x:=1;fmt.println(cast(int)x)}\n', 'cast'),
    'vet-shadowing': ('package main\nimport "core:fmt"\nmain::proc(){x:=1;{x:=2;fmt.println(x)};fmt.println(x)}\n', 'shadows'),
    'vet-when-shadowing': ('package main\nwhen true {len::3}\nmain::proc(){}\n', 'shadows'),
    'vet-semicolon': ('package main\nmain::proc(){\n\tx:=1;\n\t_=x\n}\n', 'semicolon'),
    'vet-unused': ('package main\nmain::proc(){x:=1}\n', 'not used'),
    'vet-unused-variables': ('package main\nmain::proc(){x:=1}\n', 'not used'),
    'vet-unused-procedures': ('package main\ndead::proc(){}\nmain::proc(){}\n', 'not used'),
    'vet-unused-imports': ('package main\nimport "core:fmt"\nmain::proc(){}\n', 'not used'),
    'vet-style': ('package main\nRow::struct{\n\tx:int\n}\nmain::proc(){}\n', 'comma'),
    'vet-tabs': ('package main\nmain::proc(){\n    _=0\n}\n', 'tabs'),
    'strict-style': ('package main\nRow::struct{\n\tx:int\n}\nmain::proc(){}\n', 'comma'),
    'vet-nil-deref': ('package main\nimport "core:fmt"\nmain::proc(){p:^int;fmt.println(p^)}\n', 'nil'),
    'vet-uninitialized': ('package main\nimport "core:fmt"\nmain::proc(){x:int=---;fmt.println(x)}\n', 'before'),
    'vet-atomic-access': ('package main\nimport "base:intrinsics"\nimport "core:fmt"\nx:int\nmain::proc(){_=intrinsics.atomic_add(&x,1);fmt.println(x)}\n', 'atomically'),
    'vet-using-param': ('package main\nRow::struct{x:int}\nread::proc(using row:Row)->int{return x}\nmain::proc(){_=read(Row{1})}\n', 'using'),
    'vet-using-stmt': ('package main\nRow::struct{x:int}\nmain::proc(){row:=Row{1};using row;_=x}\n', 'using'),
}


class NativeVetTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp

    def call(self, engine, *flags, command='check', entry=None, status=0):
        compiler = native.PYTHON if engine == 'python' else native.RUST
        result = subprocess.run([str(compiler), command, str(entry or self.app),
                                 '--output-dir', str(self.root/(engine+'-output')), '-thread-count:1', *flags],
                                cwd=self.root, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, status, result.stdout+result.stderr)
        return result

    def test_catalog_covers_every_installed_native_vet_flag(self):
        executable = shutil.which(os.environ.get('THOR_ODIN', 'odin'))
        if not executable:
            self.skipTest('Odin not installed')
        result = subprocess.run([executable, 'build', '-help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = set(re.findall(r'^\s+-(vet(?:-[a-z-]+)?)(?=[:\s]|$)', result.stdout, re.M))
        catalog = {flag['name'] for flag in cli.CATALOG['native_flags'] if flag['name'].startswith('vet')}
        self.assertTrue(installed, result.stdout)
        self.assertLessEqual(installed, catalog)
        self.assertEqual(catalog, SUPPORTED | (RESTRICTED-{'strict-style'}))
        self.assertEqual(set(VIOLATIONS), catalog-{'vet-packages'} | {'strict-style'})

    def test_every_unsafe_policy_is_rejected_even_on_clean_thor(self):
        self.source.write_text(CLEAN)
        for engine in ('python', 'rust'):
            for name in sorted(RESTRICTED | {'vet-future-unaudited'}):
                with self.subTest(engine=engine, name=name):
                    result = self.call(engine, '-'+name, '-json-errors', status=1)
                    packet = json.loads(result.stderr)
                    self.assertEqual(packet['error_count'], 1, packet)
                    self.assertEqual(packet['errors'][0]['type'], 'error')
                    message = ' '.join(packet['errors'][0]['msgs'])
                    self.assertIn('-'+name, message)
                    self.assertIn('Odin-only projects', message)
            for command in ('build', 'run', 'test', 'vet', 'doc', 'strip-semicolon', 'publish'):
                with self.subTest(engine=engine, command=command):
                    configuration = native.UNCHECKED if command in {'build', 'run', 'test', 'publish'} else []
                    result = self.call(engine, '--vet=unused', '-vet-unused', '-vet-packages:other',
                                       '-ignore-warnings', *configuration, command=command, status=1)
                    self.assertIn('Odin-only projects', result.stderr)
            self.assertEqual(self.source.read_text(), CLEAN)

    def test_all_native_policies_remain_available_for_odin_only_projects(self):
        self.source.unlink(missing_ok=True)
        source = self.app/'main.odin'
        for engine in ('python', 'rust'):
            for name in sorted(SUPPORTED | RESTRICTED):
                source.write_text(CLEAN)
                flag = '-vet-packages:main' if name == 'vet-packages' else '-'+name
                with self.subTest(engine=engine, name=name):
                    self.call(engine, flag)
                    self.assertEqual(source.read_text(), CLEAN)

    def test_native_only_real_violations_match_odin(self):
        self.source.unlink(missing_ok=True)
        source = self.app/'main.odin'
        executable = shutil.which(os.environ.get('THOR_ODIN', 'odin'))
        if not executable:
            self.skipTest('Odin not installed')
        for name, (raw, expected) in VIOLATIONS.items():
            source.write_text(raw)
            # The installed Odin checker has an independently reproduced race
            # while reporting vet errors. Serialize this error-oracle matrix;
            # do not retry failures or change Thor's default worker count.
            oracle = subprocess.run([executable, 'check', str(self.app), '-'+name, '-vet-packages:main', '-thread-count:1'],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(oracle.returncode, 1, name+'\n'+oracle.stdout+oracle.stderr)
            self.assertIn(expected, oracle.stderr.lower())
            for engine in ('python', 'rust'):
                with self.subTest(engine=engine, name=name):
                    result = self.call(engine, '-'+name, '-json-errors', status=1)
                    errors = json.loads(result.stderr)['errors']
                    self.assertTrue(any(expected in ' '.join(error['msgs']).lower() for error in errors), errors)
                    self.assertTrue(all(error['type']=='error' for error in errors), errors)
                    self.assertTrue(any(error.get('pos', {}).get('file')==str(source) for error in errors), errors)

    def test_supported_thor_checks_catch_real_source_errors_and_keep_locations(self):
        for name in sorted(SUPPORTED-{'vet-packages'}):
            raw, expected = VIOLATIONS[name]
            self.source.write_text('// 😀 source positions\n'+raw)
            for engine in ('python', 'rust'):
                with self.subTest(engine=engine, name=name):
                    result = self.call(engine, '-'+name, '-json-errors', status=1)
                    errors = json.loads(result.stderr)['errors']
                    self.assertTrue(any(expected in ' '.join(error['msgs']).lower() for error in errors), errors)
                    for error in errors:
                        self.assertEqual(error['type'], 'error')
                        self.assertEqual(error['pos']['file'], str(self.source))
                        self.assertGreater(error['pos']['line'], 1)
                    scoped = self.call(engine, '-'+name, '-vet-packages:other')
                    self.assertEqual(scoped.stderr, '')

    def test_supported_checks_work_together_with_generated_helpers_checked_and_unchecked(self):
        self.source.write_text(FEATURES)
        flags = ['-vet-when-shadowing', '-vet-semicolon']
        for engine in ('python', 'rust'):
            for configuration in ([], native.UNCHECKED):
                with self.subTest(engine=engine, configuration=configuration):
                    result = self.call(engine, *flags, *configuration, command='run')
                    self.assertEqual(result.stdout, '3\n')
                    self.assertEqual(result.stderr, '')

    def test_mixed_imported_excluded_sources_and_unsaved_overlays(self):
        self.source.unlink(missing_ok=True)
        source = self.app/'main.odin'
        source.write_text(CLEAN)
        excluded = self.app/('ignored_linux.thor' if os.name=='nt' else 'ignored_windows.thor')
        excluded.write_text('#+vet unused\ninvalid inactive source\n')
        for engine in ('python', 'rust'):
            self.call(engine, '-vet')
        other = self.app/'other.thor'
        other.write_text('package main\nVALUE::1\n')
        for engine in ('python', 'rust'):
            self.assertIn('Odin-only projects', self.call(engine, '-vet', status=1).stderr)
        other.unlink()
        source.write_text('package main\nimport "library"\nmain::proc(){_=library.VALUE}\n')
        library = self.app/'library'
        library.mkdir()
        (library/'value.thor').write_text('package library\nVALUE::1\n')
        for engine in ('python', 'rust'):
            self.assertIn('Odin-only projects', self.call(engine, '-vet', status=1).stderr)
        source.write_text(CLEAN)
        replacement = self.root/'replacement.thor'
        replacement.write_text('#+vet unused\n'+CLEAN)
        overlay = self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(self.source):str(replacement)}}))
        for engine in ('python', 'rust'):
            result = self.call(engine, '--overlay', overlay, status=1)
            self.assertIn('supported only in Odin source files', result.stderr)

    def test_source_tags_are_checked_without_rejecting_explicit_opt_outs(self):
        for name in sorted(RESTRICTED-{'vet', 'strict-style'}):
            self.source.write_text('#+vet '+name.removeprefix('vet-')+'\n'+CLEAN)
            for engine in ('python', 'rust'):
                with self.subTest(engine=engine, name=name):
                    result = self.call(engine, '-json-errors', status=1)
                    error = json.loads(result.stderr)['errors'][0]
                    self.assertIn('supported only in Odin source files', error['msgs'][0])
                    self.assertEqual(error['pos']['file'], str(self.source))
                    self.assertEqual(error['pos']['line'], 1)
        self.source.write_text('#+vet !unused\n'+CLEAN)
        for engine in ('python', 'rust'):
            self.call(engine)
        for name in SUPPORTED-{'vet-packages'}:
            raw, expected = VIOLATIONS[name]
            self.source.write_text('#+vet '+name.removeprefix('vet-')+'\n'+raw)
            for engine in ('python', 'rust'):
                with self.subTest(engine=engine, source_tag=name):
                    result = self.call(engine, '-json-errors', status=1)
                    self.assertIn(expected, result.stderr.lower())
        for tag in ('#+vet', '#+vet unused'):
            raw = '// Documentation: '+tag+'\n/*\n'+tag+'\n*/\n'+tag+'\n'+CLEAN
            self.source.write_text(raw)
            for engine in ('python', 'rust'):
                with self.subTest(engine=engine, source_tag=tag):
                    result = self.call(engine, '-json-errors', status=1)
                    error = json.loads(result.stderr)['errors'][0]
                    self.assertIn('supported only in Odin source files', error['msgs'][0])
                    self.assertEqual(error['pos']['line'], 5, error)
                    self.assertEqual(error['pos']['offset'], raw.rindex(tag))

    def test_full_help_documents_every_restriction(self):
        for engine in ('python', 'rust'):
            compiler = native.PYTHON if engine=='python' else native.RUST
            result = subprocess.run([str(compiler), 'help', 'build', '-full'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in RESTRICTED:
                flag = next(flag for flag in cli.CATALOG['native_flags'] if flag['name']==name)
                self.assertIn(flag['syntax']+'  [Odin-only projects]', result.stdout)
                self.assertIn(flag['thor_rejection'], result.stdout)
            self.assertIn('errors for build', result.stdout)

    def test_native_only_file_tags_keep_odin_error_and_opt_out_semantics(self):
        self.source.unlink(missing_ok=True)
        source = self.app/'main.odin'
        for tag in ('#+vet', '#+vet unused-variables', '#+vet unused-procedures'):
            declaration = 'local::proc(){}' if tag.endswith('procedures') else 'local:=1'
            raw = tag+'\npackage main\nmain::proc(){'+declaration+'}\n'
            source.write_text(raw)
            for engine in ('python', 'rust'):
                result = self.call(engine, '-json-errors', status=1)
                errors = json.loads(result.stderr)['errors']
                self.assertTrue(any('declared but not used' in ' '.join(error['msgs']) for error in errors), errors)
                self.assertTrue(all(error['type']=='error' for error in errors), errors)
        source.write_text('#+vet !unused\npackage main\nmain::proc(){local:=1}\n')
        for engine in ('python', 'rust'):
            self.call(engine, '-vet')

    def test_build_thor_vet_is_strict_for_every_check_and_ignores_warning_suppression(self):
        fixtures = {
            'immutable': ('package main\nmain::proc(){x:=1;if x!=1{panic("value")}}\n', 'thor/prefer-immutable'),
            'pure': ('package main\nvalue::proc(x:int)->int{return x+1}\nmain::proc(){_=value(1)}\n', 'thor/prefer-fn'),
            'unused': ('package main\nmain::proc(){x:=1;x=2}\n', 'thor/unused'),
            'semicolon': ('package main\nmain::proc(){};\n', 'thor/unnecessary-semicolon'),
        }
        for engine in ('python', 'rust'):
            for check, (raw, code) in fixtures.items():
                self.source.write_text(raw)
                for flags in ([], ['-ignore-warnings'], native.UNCHECKED):
                    with self.subTest(engine=engine, check=check, flags=flags):
                        result = self.call(engine, '--vet='+check, '-json-errors', *flags, command='build', status=1)
                        packet = json.loads(result.stderr)
                        errors = [error for error in packet['errors'] if error.get('code')==code]
                        self.assertTrue(errors, packet)
                        self.assertTrue(all(error['type']=='error' for error in errors), errors)
                        self.assertGreater(packet['error_count'], 0)
                for command in ('check', 'vet'):
                    result = self.call(engine, '--vet='+check, '-json-errors', command=command)
                    self.assertEqual(json.loads(result.stderr)['error_count'], 0)
                    self.assertTrue(all(error['type']=='warning' for error in json.loads(result.stderr)['errors']))

    def test_build_validation_filters_artifact_options_and_preserves_selected_checks(self):
        raw = 'package main\nimport "core:fmt"\nmain::proc(){x:=1;fmt.println(x)}\n'
        for engine in ('python', 'rust'):
            self.source.write_text(raw)
            for flags in (['-o:none'], ['-o:size'], ['-build-mode:obj', '-use-single-module'],
                          ['-debug', '--no-sanitize']):
                with self.subTest(engine=engine, flags=flags):
                    result = self.call(engine, '--vet=immutable', '-json-errors', *flags,
                                       command='build', status=1)
                    packet = json.loads(result.stderr)
                    self.assertEqual(packet['error_count'], 1, packet)
                    self.assertEqual(packet['errors'][0].get('code'), 'thor/prefer-immutable', packet)
            self.source.write_text(raw.replace('x:=1', 'x:>1'))
            result = self.call(engine, '--vet', '-vet-when-shadowing', '-json-errors',
                               *native.UNCHECKED, command='build')
            self.assertEqual(json.loads(result.stderr)['errors'], [])
            self.source.write_text('package main\nmain::proc(){if true {unused:=1}}\n')
            result = self.call(engine, '--vet=semicolon', '-json-errors', command='build')
            packet = json.loads(result.stderr)
            self.assertEqual(packet['error_count'], 0, packet)
            self.assertEqual([error['type'] for error in packet['errors']], ['warning'])
            result = self.call(engine, '--vet=semicolon', '-json-errors', '-ignore-warnings', command='build')
            self.assertEqual(json.loads(result.stderr)['errors'], [])

    def test_hidden_source_violations_cannot_silently_pass_native_vet(self):
        cases = {
            'vet-cast': 'x:=1;text:=f"{cast(int)x}";defer delete(text);fmt.println(text)',
            'vet-nil-deref': 'p:^int;text:=f"{p^}";defer delete(text);fmt.println(text)',
            'vet-uninitialized': 'x:int=---;text:=f"{x}";defer delete(text);fmt.println(text)',
            'vet-shadowing': 'x:=1;values:=[x for x in 0..<3];defer delete(values);fmt.println(x,values)',
        }
        for name, body in cases.items():
            self.source.write_text('package main\nimport "core:fmt"\nmain::proc(){'+body+'}\n')
            for engine in ('python', 'rust'):
                for selection in ([], ['--vet']):
                    with self.subTest(engine=engine, name=name, selection=selection):
                        result = self.call(engine, '-'+name, '-json-errors', *selection, command='build', status=1)
                        errors = json.loads(result.stderr)['errors']
                        self.assertEqual(len(errors), 1, errors)
                        self.assertIn('Odin-only projects', errors[0]['msgs'][0])


if __name__ == '__main__':
    unittest.main()

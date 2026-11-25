"""Terminal rendering keeps the reference's excerpts, Unicode and position styles."""
from tests.test_rust_compiler import compiler_engines
import re
import unittest
from tests import test_rust_compiler as native


class RustDiagnosticTextTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke

    def test_original_source_excerpts_tabs_unicode_and_terse_position_styles(self):
        self.source.write_text('package main\nmain::proc(){\n// é😀\n\tvalue:int="wrong"\n_=value\n}\n')
        for flags in [[], ['-terse-errors'], ['-error-pos-style:unix'],
                      ['-terse-errors', '-error-pos-style:unix']]:
            with self.subTest(flags=flags):
                if native.reference_enabled():
                    reference = self.invoke('python', command='check', flags=flags)
                actual = self.invoke('rust', command='check', flags=flags)
                if native.reference_enabled():
                    self.assertEqual(reference.returncode, 1, reference.stderr)
                self.assertEqual(actual.returncode, 1, actual.stderr)
                if native.reference_enabled():
                    self.assertEqual(actual.stdout, reference.stdout)
                if native.reference_enabled():
                    self.assertEqual(actual.stderr, reference.stderr)
                location = (rf'{re.escape(str(self.source))}:4:\d+:' if '-error-pos-style:unix' in flags
                            else rf'{re.escape(str(self.source))}\(4:\d+\):')
                self.assertRegex(actual.stderr, location + r'\nError: ')

    def test_build_and_vet_put_severity_below_the_source_location(self):
        cases = [
            ('build', 'package main\nmain::proc(){value:int="wrong";_=value}\n', [], 1, 'Error'),
            ('build', 'package main\n@(deprecated="old helper")\nhelper::proc(){}\nmain::proc(){helper()}\n',
             [], 0, 'Warning'),
            ('build', 'package main\nmain::proc(){unused:=1;_=0}\n', ['--vet=unused'], 1, 'Error'),
            ('vet', 'package main\nStrategy::enum{one,two}\nprint_board::proc(){}\nmain::proc(){}\n',
             ['--vet=unused'], 0, 'Warning'),
        ]
        for command, source, flags, status, label in cases:
            self.source.write_text(source)
            for engine in compiler_engines():
                for mode in (([],) if command == 'vet' else ([], native.UNCHECKED)):
                    with self.subTest(command=command, engine=engine, flags=flags, mode=mode):
                        result = self.invoke(engine, command=command, flags=[*flags, *mode])
                        self.assertEqual(result.returncode, status, result.stderr)
                        headers = re.findall(r'^(.+\(\d+:\d+\):)\n' + label + r': (.+)$',
                                             result.stderr, re.MULTILINE)
                        self.assertTrue(headers, result.stderr)
                        self.assertEqual(len(headers), result.stderr.count(label + ': '), result.stderr)
                        self.assertTrue(all(path.startswith(str(self.source)) for path, _ in headers), result.stderr)
                        self.assertIn('\n    ', result.stderr)
                        self.assertIn('^', result.stderr)
                        if command == 'vet':
                            self.assertIn("Type 'Strategy' declared but not used", result.stderr)
                            self.assertIn("Procedure 'print_board' declared but not used", result.stderr)

    def test_compile_time_optional_failure_marks_the_complete_original_expression(self):
        import json
        text='package main\nget::fn()->(int,bool){return 0,false}\nchoose::fn()->int{return get() or_panic}\nVALUE::#run choose()\nmain::proc(){_=VALUE}\n'
        self.source.write_text(text,newline='')
        for flags in [[], ['-error-pos-style:unix']]:
            with self.subTest(flags=flags):
                result=self.invoke('rust',command='check',flags=['-json-errors',*flags])
                self.assertEqual(result.returncode,1,result.stderr)
                error=next(e for e in json.loads(result.stderr)['errors'] if 'unexpected failure' in e['msgs'][0])
                pos=error['related'][0]['pos']
                start=text.encode().index(b'get() or_panic')
                self.assertEqual((pos['offset'],pos['end_offset']),(start,start+len('get() or_panic')))

    def test_early_parser_error_uses_unsaved_source_excerpt(self):
        import json
        self.source.write_text('package main\nmain::proc(){}\n')
        replacement=self.root/'unsaved.thor'
        replacement.write_text('package main\nmain::proc(){\n\tvalue:=or_panic\n}\n')
        overlay=self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(self.source):str(replacement)}}))
        for flags in [['--overlay',str(overlay)], ['--overlay='+str(overlay),'-error-pos-style:unix']]:
            with self.subTest(flags=flags):
                if native.reference_enabled():
                    reference=self.invoke('python',command='check',flags=flags)
                actual=self.invoke('rust',command='check',flags=flags)
                if native.reference_enabled():
                    self.assertEqual((reference.returncode,actual.returncode),(1,1),actual.stderr)
                if native.reference_enabled():
                    self.assertEqual(actual.stderr,reference.stderr)
                self.assertIn('value:=or_panic',actual.stderr)
                self.assertRegex(actual.stderr, r':\nError: ')

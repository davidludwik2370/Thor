"""Terminal rendering keeps the reference's excerpts, Unicode and position styles."""
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
                reference = self.invoke('python', command='check', flags=flags)
                actual = self.invoke('rust', command='check', flags=flags)
                self.assertEqual(reference.returncode, 1, reference.stderr)
                self.assertEqual(actual.returncode, 1, actual.stderr)
                self.assertEqual(actual.stdout, reference.stdout)
                self.assertEqual(actual.stderr, reference.stderr)

    def test_compile_time_optional_failure_marks_the_complete_original_expression(self):
        import json
        text='package main\nget::fn()->(int,bool){return 0,false}\nchoose::fn()->int{return get() or_panic}\nVALUE::#run choose()\nmain::proc(){_=VALUE}\n'
        self.source.write_text(text)
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
                reference=self.invoke('python',command='check',flags=flags)
                actual=self.invoke('rust',command='check',flags=flags)
                self.assertEqual((reference.returncode,actual.returncode),(1,1),actual.stderr)
                self.assertEqual(actual.stderr,reference.stderr)
                self.assertIn('value:=or_panic',actual.stderr)

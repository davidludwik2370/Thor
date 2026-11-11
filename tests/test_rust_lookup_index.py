"""Indexed lookup preserves reference semantics and scopes projection work."""
import json
import os
import unittest
from tests import test_rust_compiler as native


class RustLookupIndexTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare
    program = native.RustCompilerTests.program

    def profile(self, command='check', flags=()):
        path = self.root/'profile.json'
        result = self.invoke('rust', command=command, flags=flags,
                             env={**os.environ, 'THOR_PROFILE': str(path)})
        return result, json.loads(path.read_text())

    def test_profile_preserves_output_in_both_execution_configurations(self):
        self.source.write_text(self.program('fmt.println(work(3))', '''
import "core:fmt"
work::fn(n:int)->int {return n+1}
'''))
        for flags in (['-debug'], native.UNCHECKED):
            with self.subTest(flags=flags):
                plain = self.invoke('rust', flags=flags)
                measured, report = self.profile('run', flags)
                self.assertEqual(plain.returncode, 0, plain.stderr)
                self.assertEqual((measured.returncode, measured.stdout, measured.stderr),
                                 (plain.returncode, plain.stdout, plain.stderr))
                self.assertEqual(report['schema'], 1)
                self.assertGreater(report['counters']['resolver.constructions'], 0)
                self.assertGreater(report['counters']['resolver.index_reuses'], 0)
                self.assertEqual(report['counters']['resolver.constructions'],
                                 report['counters']['resolver.index_builds']+report['counters']['resolver.index_reuses'])
                self.assertGreater(report['counters']['lookup.calls'], 0)
                self.assertIn('backend.final', report['stages'])
                for stage in report['stages'].values():
                    self.assertGreater(stage['calls'], 0)
                    self.assertGreaterEqual(stage['inclusive_seconds'], stage['self_seconds'])
                    self.assertGreaterEqual(stage['self_seconds'], 0)

    def test_profile_keeps_json_error_stream_and_original_location(self):
        text = self.program('missing_name()')
        self.source.write_text(text)
        result, report = self.profile(flags=['-json-errors'])
        self.assertEqual(result.returncode, 1, result.stderr)
        packet = json.loads(result.stderr)
        error = next(error for error in packet['errors'] if 'missing_name' in str(error['msgs']))
        self.assertEqual(error['pos']['file'], str(self.source))
        self.assertEqual(error['pos']['offset'], text.index('missing_name'))
        self.assertIn('command', report['stages'])

    def test_cached_owned_values_skip_helper_preparation_after_runtime_edit(self):
        text = self.program('''values:=#run work();defer delete(values)
if values[0][0]!=2 {panic("cached contents")}''',
                            'work::proc()->[][2]int{values:=make([][2]int,1);values[0]={2,3};return values}\n')
        self.source.write_text(text)
        cold, report = self.profile('transpile', ['--cache-report'])
        self.assertEqual(cold.returncode, 0, cold.stderr)
        self.assertGreater(report['counters']['evaluation.helper_preparations'], 0)
        self.assertGreater(report['counters']['analysis.inherited_source_facts'], 0)
        self.source.write_text(text.replace('main :: proc() {', 'main :: proc() {\nunused:=17;_=unused;', 1))
        warm, report = self.profile('transpile', ['--cache-report'])
        self.assertEqual(warm.returncode, 0, warm.stderr)
        cache = json.loads(warm.stdout.splitlines()[0])
        self.assertGreater(cache['thor_prepared_hits'], 0)
        self.assertEqual(cache['thor_cache']['writes'], 0)
        self.assertEqual(report['counters'].get('evaluation.helper_preparations', 0), 0)

    def test_unrelated_scopes_do_not_multiply_promoted_field_queries(self):
        self.source.write_text(self.program('if library.read({7})!=7 {panic("using")}',
                                            'import "library"\n'))
        library = self.app/'library'
        library.mkdir()
        source = library/'library.thor'
        prefix = 'package library\nBox::struct{value:int}\nread::proc(using b:Box)->int{return value}\n'
        counts = []
        for size in (2, 200):
            source.write_text(prefix+''.join(f'other_{n}::proc(x:int)->int{{return x+1}}\n'
                                             for n in range(size)))
            result, report = self.profile()
            self.assertEqual(result.returncode, 0, result.stderr)
            counts.append(report['counters'].get('projection.reference_visits', 0))
        self.assertGreater(counts[0], 0)
        self.assertEqual(counts[0], counts[1])

    def test_package_using_shadowing_and_sibling_declarations(self):
        code = self.program('''
using library
value := VALUE
{value:=value+1; if value!=8 {panic("shadow initializer")}}
if helper()!=7 || read({9})!=9 {panic("package using")}
values:=[2,3]; out:=[x+1 for x in values if x>0]
defer delete(out)
if out[1]!=4 {panic("comprehension scope")}
''', 'import "library"\n')
        files = {'library/a.thor': 'package library\nVALUE::7\nhelper::proc()->int{return VALUE}\n',
                 'library/b.thor': 'package library\nBox::struct{value:int}\nread::proc(using b:Box)->int{return value}\n'}
        for flags in (['-debug'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(code, files=files, flags=flags)


if __name__ == '__main__':
    unittest.main()

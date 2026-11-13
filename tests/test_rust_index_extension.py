"""Appending SDK namespaces preserves scopes, projections and selected effects."""
import unittest
from tests import test_rust_compiler as native
from tests import test_rust_dependency_lookahead as lookahead
from tests import test_rust_semantic_efficiency as efficiency


class RustIndexExtensionTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program
    sdk_overlay = efficiency.RustSemanticEfficiencyTests.sdk_overlay
    chain = lookahead.RustDependencyLookaheadTests.chain
    profile = lookahead.RustDependencyLookaheadTests.profile

    def test_new_namespaces_index_each_source_once(self):
        overlay = self.chain()
        for flags in (['-debug'], native.UNCHECKED):
            counts = self.profile(overlay, flags=flags)
            self.assertGreater(counts.get('resolver.index_extensions', 0), 0, counts)
            self.assertEqual(counts.get('resolver.index_builds', 0), 1, counts)
            self.assertLessEqual(counts['resolver.syntax_sources_indexed'], counts['parser.source_requests'])

    def test_existing_using_projections_survive_dependency_appends(self):
        overlay = self.chain(body='''
items:=[1]int{7};view:>items[:]
answer:>read(Box{{2}},view);if answer!=9{panic("projection")}
''')
        declarations = '''
Cell::struct{value:int}
Box::struct{using cell:Cell}
read::proc(using box:Box, values:[]int)->int{return value+math.thor_probe_0(values)}
'''
        self.source.write_text(self.source.read_text().replace('main :: proc()', declarations+'\nmain :: proc()'))
        for flags in (['-debug'], native.UNCHECKED):
            counts = self.profile(overlay, flags=flags)
            self.assertGreater(counts.get('resolver.index_extensions', 0), 0, counts)

    def test_flag_selected_writes_remain_contextual_after_appending(self):
        overlay = self.chain(middle='''
thor_probe_1::proc(values:[]int)->int{
    when #config(THOR_INDEX_WRITES,false){values[0]+=1}
    return hop.thor_probe_2(values)
}
''')
        self.profile(overlay, flags=['-debug'])
        rejected = self.invoke('rust', command='check', flags=[
            '--overlay', str(overlay), '-define:THOR_INDEX_WRITES=true'])
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('read-only slice', rejected.stderr)
        self.assertIn(str(self.source), rejected.stderr)
        self.profile(overlay, flags=native.UNCHECKED)


if __name__ == '__main__':
    unittest.main()

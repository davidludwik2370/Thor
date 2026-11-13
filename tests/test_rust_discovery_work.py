"""Append-only dependency discovery retries pending facts, preserving diagnostics."""
import json
import os
import unittest
from tests import test_rust_compiler as native
from tests import test_rust_dependency_lookahead as lookahead
from tests import test_rust_semantic_efficiency as efficiency


class RustDiscoveryWorkTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    program=native.RustCompilerTests.program
    sdk_overlay=efficiency.RustSemanticEfficiencyTests.sdk_overlay
    chain=lookahead.RustDependencyLookaheadTests.chain
    profile=lookahead.RustDependencyLookaheadTests.profile

    def test_closed_queries_and_syntax_are_reused_while_new_namespaces_load(self):
        overlay=self.chain()
        leaves='\n'.join(f'leaf_{n}::proc(x:int)->int{{return x+{n}}}' for n in range(120))
        calls='\n'.join(f'_ = leaf_{n}({n})' for n in range(120))
        self.source.write_text(self.source.read_text().replace('main :: proc()',leaves+'\nmain :: proc()')
            .replace('items:=[1]int{7}',calls+'\nitems:=[1]int{7}'))
        counts=self.profile(overlay,command='transpile')
        self.assertGreater(counts.get('discovery.type_query_reuses',0),120,counts)
        self.assertLessEqual(counts.get('discovery.source_scans',0),counts['parser.source_requests'],counts)
        self.assertGreater(counts.get('analysis.dependency_lookahead_loads',0),0,counts)

    def test_pending_generic_calls_are_revisited_and_report_mutation(self):
        overlay=self.chain(mutates=True,middle='''
thor_probe_1::proc(values:[]$T)->T{return hop.thor_probe_2(values)}
''')
        for flags in (['-debug'],native.UNCHECKED):
            result=self.invoke('rust',command='build',flags=['--overlay',str(overlay),*flags])
            self.assertNotEqual(result.returncode,0)
            self.assertIn('read-only slice',result.stderr)
            self.assertIn(str(self.source),result.stderr)

    def test_retries_and_workers_keep_emitted_source_and_maps_identical(self):
        overlay=self.chain()
        outputs=[]
        for workers in [1,4]:
            result=self.invoke('rust',command='transpile',flags=['--overlay',str(overlay)],
                env={**os.environ,'THOR_JOBS':str(workers)})
            self.assertEqual(result.returncode,0,result.stderr)
            outputs.append({str(p.relative_to(self.root/'rust')):p.read_bytes()
                for p in (self.root/'rust').rglob('*') if p.is_file() and
                (p.suffix=='.odin' or p.name.endswith('.map.json'))})
        self.assertEqual(*outputs)


if __name__=='__main__':unittest.main()

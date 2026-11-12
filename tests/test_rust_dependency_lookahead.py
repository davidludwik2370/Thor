"""Demanded-name lookahead batches dependencies without inferring effects."""
import json
import os
import unittest
from tests import test_rust_semantic_efficiency as efficiency
from tests import test_rust_compiler as native


class RustDependencyLookaheadTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program
    sdk_overlay = efficiency.RustSemanticEfficiencyTests.sdk_overlay

    def chain(self, *, mutates=False, middle=None, extra=None, body=None):
        folders=['math','time','strconv','unicode/utf8','unicode']
        entries={}
        for i,folder in enumerate(folders):
            package=folder.rsplit('/',1)[-1]
            prefix=f'import hop "core:{folders[i+1]}"\n' if i+1<len(folders) else ''
            code=f'return hop.thor_probe_{i+1}(values)' if prefix else (
                'values[0]+=1;return values[0]' if mutates else 'return values[0]')
            declaration=f'thor_probe_{i}::proc(values:[]int)->int{{{code}}}\n'
            if i==1 and middle is not None:declaration=middle
            entries[f'core/{folder}/thor_probe_lookahead.odin']=f'package {package}\n{prefix}{declaration}'
        entries.update(extra or {})
        overlay=self.sdk_overlay(entries)
        self.source.write_text(self.program(body or '''
items:=[1]int{7};view:>items[:]
answer:>math.thor_probe_0(view)
if answer!=7{panic("lookahead result")}
''','import math "core:math"\n'))
        return overlay

    def profile(self, overlay, *, flags=(), command='run'):
        path=self.root/'profile.json'
        result=self.invoke('rust',command=command,flags=['--overlay',str(overlay),*flags],
                           env={**os.environ,'THOR_PROFILE':str(path)})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return json.loads(path.read_text())['counters']

    def test_transitive_selected_calls_need_only_one_permission_retry(self):
        overlay=self.chain()
        for flags in [['-debug'],native.UNCHECKED]:
            counts=self.profile(overlay,flags=flags)
            self.assertEqual(counts.get('permissions.dependency_retries',0),1,counts)
            self.assertEqual(counts.get('permissions.analyses',0),2,counts)
            self.assertGreaterEqual(counts.get('analysis.dependency_lookahead_loads',0),2,counts)
            self.assertLessEqual(counts['analysis.dependency_lookahead_nodes'],4096)

    def test_leaf_mutation_still_reports_the_original_readonly_argument(self):
        overlay=self.chain(mutates=True)
        for flags in [['-debug'],native.UNCHECKED]:
            result=self.invoke('rust',command='build',flags=['--overlay',str(overlay),*flags])
            self.assertNotEqual(result.returncode,0)
            self.assertIn('read-only slice',result.stderr)
            self.assertIn(str(self.source),result.stderr)
            self.assertNotIn('Undeclared name',result.stderr)

    def test_same_package_aliases_and_recursive_helpers_are_bounded(self):
        overlay=self.chain(middle='''
thor_probe_1::proc(values:[]int)->int{return helper(values,2)}
alias::hop.thor_probe_2
helper::proc(values:[]int,n:int)->int{
    if n>0{return helper(values,n-1)}
    return alias(values)
}
''')
        counts=self.profile(overlay)
        self.assertEqual(counts.get('permissions.dependency_retries',0),1,counts)
        self.assertLess(counts['analysis.dependency_lookahead_nodes'],4096)

    def test_constant_miss_resumes_the_caller_to_batch_independent_imports(self):
        overlay=self.chain(extra={
            'core/math/thor_probe_lookahead.odin':'''package math
import first "core:time"
import later "core:strconv"
thor_probe_0::proc(values:[]int)->int{
    constant:=first.THOR_PROBE_CONSTANT
    if constant!=1{return 99}
    return later.thor_probe_2(values)
}
''',
            'core/time/thor_probe_constant.odin':'package time\nTHOR_PROBE_CONSTANT::1\n',
        })
        counts=self.profile(overlay)
        self.assertEqual(counts.get('permissions.dependency_retries',0),1,counts)
        self.assertEqual(counts.get('permissions.analyses',0),2,counts)

    def test_unselected_overloads_keep_the_real_call_planner(self):
        overlay=self.chain(middle='''
thor_probe_1::proc{integer_probe,float_probe}
integer_probe::proc(values:[]int)->int{return hop.thor_probe_2(values)}
float_probe::proc(values:[]f64)->int{return 99}
''')
        for flags in [[],native.UNCHECKED]:
            counts=self.profile(overlay,flags=flags)
            self.assertGreaterEqual(counts.get('permissions.dependency_retries',0),1)
            self.assertLessEqual(counts.get('analysis.dependency_lookahead_nodes',0),4096)

    def test_generic_body_keeps_binding_sensitive_fallback(self):
        overlay=self.chain(middle='''
thor_probe_1::proc(values:[]$T)->T{return hop.thor_probe_2(values)}
''')
        for flags in [[],native.UNCHECKED]:
            counts=self.profile(overlay,flags=flags)
            self.assertGreaterEqual(counts.get('permissions.dependency_retries',0),2,counts)
            self.assertLessEqual(counts.get('analysis.dependency_lookahead_nodes',0),4096)

    def test_uncalled_nested_procedure_does_not_discover_its_imports(self):
        parsed=[]
        for size in [0,300]:
            overlay=self.chain(middle='''
import unused "core:encoding/base64"
thor_probe_1::proc(values:[]int)->int{
    nested::proc()->int{return unused.thor_probe_unused()}
    return hop.thor_probe_2(values)
}
''',extra={'core/encoding/base64/thor_probe_unused.odin':
                'package encoding_base64\nthor_probe_unused::proc()->int{return 0}\n'+
                '\n'.join(f'thor_probe_unused_{n}::proc()->int{{return {n}}}' for n in range(size))})
            counts=self.profile(overlay,command='transpile')
            parsed.append(counts['parser.nodes'])
        self.assertEqual(parsed[0],parsed[1])

    def test_inactive_conditional_reference_stays_unloaded_and_flags_change_effects(self):
        overlay=self.chain(middle='''
import unused "core:encoding/base64"
thor_probe_1::proc(values:[]int)->int{
    when #config(THOR_PROBE_BAD_BRANCH,false){return unused.thor_probe_bad(values)}
    else{return hop.thor_probe_2(values)}
}
''',extra={'core/encoding/base64/thor_probe_bad.odin':
    'package encoding_base64\nthor_probe_bad::proc(values:[]int)->int{values[0]+=1;return values[0]}\n'})
        for flags in [['-debug'],native.UNCHECKED]:self.profile(overlay,flags=flags)
        result=self.invoke('rust',command='check',flags=['--overlay',str(overlay),'-define:THOR_PROBE_BAD_BRANCH=true'])
        self.assertNotEqual(result.returncode,0)
        self.assertIn('read-only slice',result.stderr)

    def test_large_body_exhausts_only_the_optimization_budget(self):
        padding='\n'.join(f'_ = values[0]+{n}' for n in range(1400))
        overlay=self.chain(middle='thor_probe_1::proc(values:[]int)->int{\n'+padding+
                           '\nreturn hop.thor_probe_2(values)\n}\n')
        counts=self.profile(overlay,command='check')
        self.assertEqual(counts.get('analysis.dependency_lookahead_nodes',0),4096,counts)
        self.assertEqual(counts.get('analysis.dependency_lookahead_budget_exhaustions',0),1,counts)
        self.profile(overlay,flags=native.UNCHECKED)

    def test_discovery_keeps_serial_parallel_emission_and_maps_identical(self):
        overlay=self.chain()
        versions=[]
        for jobs in [1,4]:
            result=self.invoke('rust',command='transpile',flags=['--overlay',str(overlay)],
                               env={**os.environ,'THOR_JOBS':str(jobs)})
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            versions.append({str(p.relative_to(self.root/'rust')):p.read_bytes()
                for p in (self.root/'rust').rglob('*') if p.is_file() and
                (p.suffix=='.odin' or p.name.endswith('.map.json'))})
        self.assertEqual(versions[0],versions[1])

    def test_pure_helper_execution_and_warm_result_reuse_keep_the_same_value(self):
        overlay=self.chain(body='answer:=#run work();if answer!=7{panic("pure result")}')
        self.source.write_text(self.source.read_text().replace('main :: proc()', '''
work::fn()->int{storage:=[1]int{7};return math.thor_probe_0(storage[:])}
main :: proc()'''))
        for flags in [['-debug'],native.UNCHECKED]:
            self.profile(overlay,flags=flags)
            warm=self.profile(overlay,flags=flags)
            self.assertEqual(warm.get('evaluation.helper_compilations',0),0,warm)


if __name__=='__main__':unittest.main()

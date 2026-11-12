"""SDK syntax reuse preserves code, maps, permissions and cache invalidation."""
import json
import os
import unittest
from tests import test_rust_compiler as native
from tests import test_rust_semantic_efficiency as efficiency


class RustSdkEfficiencyTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    program=native.RustCompilerTests.program
    sdk_overlay=efficiency.RustSemanticEfficiencyTests.sdk_overlay

    def profile(self,flags=(),command='transpile'):
        profile=self.root/'profile.json'
        result=self.invoke('rust',command=command,flags=flags,
            env={**os.environ,'THOR_PROFILE':str(profile)})
        return result,json.loads(profile.read_text())

    def outputs(self):
        return {str(p.relative_to(self.root/'rust')):p.read_bytes()
            for p in (self.root/'rust').rglob('*') if p.is_file() and
            (p.suffix=='.odin' or p.name.endswith('.map.json'))}

    def test_warm_sdk_syntax_preserves_lowering_maps_and_unchecked_execution(self):
        self.source.write_text(self.program('''
values:=[2]int{-7,9};view:>values[:]
answer:>math.abs(view[0]);if answer!=7{panic("sdk result")}
''','import "core:math"'))
        for flags in (['-debug'],native.UNCHECKED):
            cold,_=self.profile(flags)
            self.assertEqual(cold.returncode,0,cold.stderr)
            before=self.outputs()
            warm,profile=self.profile(flags)
            self.assertEqual(warm.returncode,0,warm.stderr)
            self.assertGreater(profile['counters'].get('parser.syntax_cache_hits',0),0)
            self.assertEqual(before,self.outputs())
            uncached,profile=self.profile([*flags,'--no-cache'])
            self.assertEqual(uncached.returncode,0,uncached.stderr)
            self.assertEqual(profile['counters'].get('parser.syntax_cache_hits',0),0)
            self.assertEqual(before,self.outputs())
            run,_=self.profile(flags,command='run')
            self.assertEqual(run.returncode,0,run.stderr)

    def test_changed_sdk_overlay_invalidates_syntax_and_mutation_effects(self):
        path='core/math/thor_sdk_efficiency.odin'
        self.source.write_text(self.program('''
values:=[1]int{7};view:>values[:]
answer:>math.thor_sdk_efficiency(view);if answer!=7{panic("overlay result")}
''','import "core:math"'))
        for mutation in [False,True,False]:
            body='values[0]+=1;return values[0]' if mutation else 'return values[0]'
            overlay=self.sdk_overlay({path:'package math\nthor_sdk_efficiency::proc(values:[]int)->int{'+body+'}\n'})
            for _ in range(2):
                result,_=self.profile(['--overlay',str(overlay)],command='check')
                if mutation:
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('read-only slice',result.stderr)
                    self.assertIn(str(self.source),result.stderr)
                else:self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()

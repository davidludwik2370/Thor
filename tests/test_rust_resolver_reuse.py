"""Completed semantic state is reused only within the same command inputs."""
import json
import os
import unittest
from tests import test_rust_compiler as native


class RustResolverReuseTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    program=native.RustCompilerTests.program

    def profile(self,code,flags=(),command='run'):
        self.source.write_text(code)
        path=self.root/'profile.json'
        result=self.invoke('rust',command=command,flags=flags,
            env={**os.environ,'THOR_PROFILE':str(path)})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result,json.loads(path.read_text())['counters']

    def test_scheduler_and_lowering_resume_completed_queries(self):
        code=self.program('view:>[2]int{3,4};answer:>sum(view);if answer!=7{panic("result")}','''
sum::proc(values:[2]int)->int{return values[0]+values[1]}
''')
        for flags in (['-debug'],native.UNCHECKED):
            _,counts=self.profile(code,flags)
            self.assertGreaterEqual(counts.get('resolver.resumed_snapshots',0),2,counts)
            self.assertGreater(counts.get('resolver.context_reuses',0),0,counts)

    def test_comptime_results_contextual_arrays_and_generic_frames_do_not_leak(self):
        code=self.program('''
VALUE::#run add([2]i8{3,4});_=VALUE
a:>identity(7);b:>identity(2.5)
values:>identity([]int{7});if a!=7||b!=2.5||values[0]!=7||VALUE!=7||add([3,4])!=7{panic("bindings")}
''','''
add::fn(values:[2]i8)->int{return int(values[0]+values[1])}
identity::proc(x:$T)->T{return x}
''')
        for flags in (['-debug'],native.UNCHECKED):
            self.profile(code,flags)
            self.profile(code,flags)

    def test_changed_flags_and_new_source_keep_conditional_types_separate(self):
        code=self.program('value:>identity(VALUE);if int(value)!=EXPECTED{panic("configuration")}','''
when ODIN_DEBUG{VALUE::i8(7);EXPECTED::7}else{VALUE::i16(9);EXPECTED::9}
identity::proc(x:$T)->T{return x}
''')
        for flags in (['-debug'],native.UNCHECKED,['-debug']):self.profile(code,flags)


if __name__=='__main__':unittest.main()

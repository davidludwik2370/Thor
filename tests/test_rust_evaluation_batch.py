"""Batched scalar evaluation retains independent results and failure ordering."""
import json
import os
import shutil
import sys
import unittest
from tests import test_rust_compiler as native


class RustEvaluationBatchTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def profile(self, code, flags=(), command='run'):
        self.source.write_text(code)
        profile = self.root/'profile.json'
        result = self.invoke('rust', command=command, flags=flags,
            env={**os.environ, 'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return json.loads(profile.read_text())['counters']

    def constants(self, count, *, changed=False):
        declarations = []
        checks = []
        for index in range(count):
            offset = index + (100 if changed and index == 15 else 0)
            declarations.append(f'function_{index}::fn(n:int)->int{{return n+{offset}}}')
            declarations.append(f'VALUE_{index}::function_{index}(2)')
            checks.append(f'if VALUE_{index}!={offset+2}{{panic("value {index}")}}')
        return self.program('\n'.join(checks), '\n'.join(declarations))

    def test_batches_are_bounded_and_warm_values_remain_independent(self):
        for flags in (['-debug'], native.UNCHECKED):
            with self.subTest(flags=flags):
                code = self.constants(65)
                cold = self.profile(code, flags)
                self.assertEqual(cold.get('evaluation.batched_values'), 64, cold)
                self.assertEqual(cold.get('evaluation.helper_compilations'), 3, cold)
                warm = self.profile(code, flags)
                self.assertEqual(warm.get('evaluation.helper_compilations', 0), 0, warm)
                changed = self.profile(self.constants(65, changed=True), flags)
                self.assertEqual(changed.get('evaluation.helper_compilations'), 1, changed)

    def test_named_default_negative_cast_boolean_and_float_arguments(self):
        code = self.program('''
if A!=1||B!=7||C!=5||D!=9||!E||F||G!=1.25||H!=2.5{panic("arguments")}
''', '''
add::fn(a:int,b:int=4)->int{return a+b}
invert::fn(value:bool)->bool{return !value}
scale::fn(value:f64)->f64{return value*2.5}
type_of::proc(value:int)->int{return value}
A::add(-3)
B::add(b=5,a=2)
C::add(int(1))
D::add(5)
E::invert(false)
F::invert(true)
HALF::0.5
G::scale(HALF)
H::scale(1)
''')
        for flags in (['-debug'], native.UNCHECKED):
            counts = self.profile(code, flags)
            self.assertEqual(counts.get('evaluation.batched_values'), 8, counts)
            self.assertEqual(counts.get('evaluation.helper_compilations'), 3, counts)

    def test_disabled_cache_still_batches_without_persistent_result_reuse(self):
        for _ in range(2):
            counts = self.profile(self.constants(4), ['--no-cache'])
            self.assertEqual(counts.get('evaluation.helper_compilations'), 1, counts)
            self.assertEqual(counts.get('evaluation.batched_values'), 4, counts)

    def test_unconditional_runtime_calls_share_builds_and_debug_calls_stay_runtime(self):
        code = self.program('if first()!=5||second()!=9{panic("runtime calls")}', '''
increment::fn(value:int)->int{return value+1}
first::proc()->int{a:=increment(1);b:=increment(2);return a+b}
second::proc()->int{return increment(3)+increment(4)}
''')
        counts = self.profile(code, native.UNCHECKED)
        self.assertEqual(counts.get('evaluation.batched_values'), 4, counts)
        self.assertEqual(counts.get('evaluation.helper_compilations'), 1, counts)
        debug = self.profile(code, ['-debug'])
        self.assertEqual(debug.get('evaluation.helper_preparations', 0), 0, debug)

    def test_distinct_numeric_results_keep_their_declared_type(self):
        code = self.program('''
if A!=Tag(1)||B!=Tag(2){panic("distinct results")}
''', '''
Tag::distinct u32
tag::fn(value:u32)->Tag{return Tag(value)}
A::tag(1)
B::tag(2)
''')
        for flags in (['-debug'], native.UNCHECKED):
            counts = self.profile(code, flags)
            self.assertEqual(counts.get('evaluation.batched_values'), 2, counts)
            self.assertEqual(counts.get('evaluation.helper_compilations'), 1, counts)

    @unittest.skipUnless(os.name == "posix", "Scratch instrumentation uses a POSIX executable shim")
    def test_each_scalar_releases_its_scratch_before_serializing_the_batch(self):
        driver = self.root/'odin-scratch-probe'
        driver.write_text(f"#!{sys.executable}\n" + r'''import os,re,subprocess,sys
from pathlib import Path
args=sys.argv[1:]
if args[:1]==['build']:
    target=Path(args[1])
    files=[target] if target.is_file() else list(target.rglob('*.odin'))
    for path in files:
        raw=path.read_text()
        if 'evaluation_result := [' not in raw:
            continue
        scratch=re.search(r'(?m)^(\w+evaluation_scratch):\w+\.Scratch$',raw)
        emit=re.search(r'(?m)^\w+\.emit\(',raw)
        if scratch is None or emit is None:
            raise RuntimeError('missing batch scratch or serialization')
        check='if len('+scratch[1]+'.allocations)!=0 {panic("batch retained scalar scratch")}\n'
        path.write_text(raw[:emit.start()]+check+raw[emit.start():])
        with Path(os.environ['THOR_BATCH_PROBE_LOG']).open('a') as log:
            log.write('checked\n')
raise SystemExit(subprocess.run([os.environ['THOR_BATCH_REAL_ODIN'],*args]).returncode)
''')
        driver.chmod(0o755)
        code = self.program('if A!=1||B!=2||C!=3||D!=4{panic("scratch values")}', '''
measure::fn(value:int)->int{data:=make([]int,128);data[0]=value;return data[0]}
A::measure(1)
B::measure(2)
C::measure(3)
D::measure(4)
''')
        probe_log=self.root/'scratch-probe.log'
        for index,flags in enumerate((['-debug'], native.UNCHECKED),1):
            self.source.write_text(code)
            profile = self.root/'scratch-profile.json'
            result = self.invoke('rust', flags=[*flags,'--no-cache','--odin',str(driver)],
                env={**os.environ,'THOR_PROFILE':str(profile),
                     'THOR_BATCH_REAL_ODIN':shutil.which('odin'),
                     'THOR_BATCH_PROBE_LOG':str(probe_log)})
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(probe_log.read_text().splitlines(),['checked']*index)
            counts=json.loads(profile.read_text())['counters']
            self.assertEqual(counts.get('evaluation.batched_values'),4,counts)
            self.assertEqual(counts.get('evaluation.helper_compilations'),1,counts)

    def test_finite_deadlines_keep_per_request_execution(self):
        counts = self.profile(self.constants(4), ['--run-timeout', '3'])
        self.assertEqual(counts.get('evaluation.batched_values', 0), 0, counts)
        self.assertEqual(counts.get('evaluation.helper_compilations'), 4, counts)

    def test_constant_dependencies_keep_reference_order_and_invalidate_dependents(self):
        for reverse in (False, True):
            for flags in (['-debug'], native.UNCHECKED):
                for base in (3, 4):
                    declarations = [f'BASE::base({base})', 'DERIVED::derived(2)']
                    if reverse:
                        declarations.reverse()
                    code = self.program(f'''
if BASE!={base+1}||DERIVED!={base+3}||INDEPENDENT!=6{{panic("dependencies")}}
''', '''
base::fn(value:int)->int{return value+1}
derived::fn(value:int)->int{return BASE+value}
''' + '\n'.join(declarations) + '\nINDEPENDENT::base(5)\n')
                    self.profile(code, flags)
                    if native.reference_enabled():
                        reference = self.invoke('python', flags=flags)
                    if native.reference_enabled():
                        self.assertEqual(reference.returncode, 0, reference.stdout+reference.stderr)

    def test_default_literals_batch_and_variadic_packs_keep_individual_scratch_scopes(self):
        code = self.program('''
if A!=5||B!=6||C!=3||D!=7{panic("argument scratch")}
''', '''
count::fn(offset:int,size:int=4)->int{return size+offset}
sum::fn(values:..int)->int{result:=0;for value in values{result+=value};return result}
A::count(1)
B::count(2)
C::sum(1,2)
D::sum(3,4)
''')
        for flags in (['-debug'], native.UNCHECKED):
            counts = self.profile(code, [*flags, '--no-cache'])
            self.assertEqual(counts.get('evaluation.batched_values'), 2, counts)
            self.assertEqual(counts.get('evaluation.helper_compilations'), 3, counts)

    def test_explicit_requests_local_calls_and_generic_roots_keep_their_paths(self):
        code = self.program('''
local::square(3)
if A!=4||B!=9||C!=4||D!=9||local!=9{panic("request paths")}
''', '''
square::fn(x:int)->int{return x*x}
generic::fn(x:$T)->T{return x*x}
A::#run square(2)
B::#run square(3)
C::generic(2)
D::generic(3)
''')
        counts = self.profile(code)
        self.assertEqual(counts.get('evaluation.batched_values', 0), 0, counts)

    def test_later_compile_failure_falls_back_and_retains_its_source_location(self):
        code = self.program('_=GOOD;_=BAD', '''
good::fn(x:int)->int{return x}
bad::fn(x:int)->int{#assert(false);return x}
GOOD::good(1)
BAD::bad(2)
''')
        self.source.write_text(code)
        profile = self.root/'profile.json'
        result = self.invoke('rust', command='build', flags=['-json-errors'],
            env={**os.environ, 'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode, 1, result.stderr)
        packet = json.loads(result.stderr)
        failures = [error for error in packet['errors'] if error['type'] == 'error']
        self.assertTrue(failures, packet)
        self.assertTrue(all(error['pos']['file'] == str(self.source) for error in failures), packet)
        self.assertTrue(any('#assert' in str(error['msgs']) or 'assertion' in str(error['msgs'])
                            for error in failures), packet)
        counts = json.loads(profile.read_text())['counters']
        self.assertEqual(counts.get('evaluation.batch_fallbacks'), 1, counts)


if __name__ == '__main__':
    unittest.main()

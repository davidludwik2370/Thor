"""Native helpers reuse contents and keep compilation/ownership checks active."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from tests import test_rust_scheduling as scheduling
from tests import test_rust_compiler as native
from tests.native_paths import native_test_compiler, EXE_SUFFIX


class RustCachedExecutionTests(unittest.TestCase):
    maxDiff=None
    setUpClass=classmethod(scheduling.RustSchedulingTests.setUpClass.__func__)

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-native-cached-execution-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.source=self.root/'main.thor'
        self.cache=self.root/'cache'

    def write(self,body='fmt.println(VALUE)',prefix='work::proc(n:int)->int{return n+2}\nVALUE::#run work(4)'):
        self.source.write_text('package main\nimport "core:fmt"\n'+prefix+'\nmain::proc(){'+body+'}\n')

    def invoke(self,flags=None,enabled=True,output=None,cache=None,error=None):
        flags=['-debug'] if flags is None else flags
        arguments=[str(self.binary),str(self.source),*flags]
        if enabled:arguments.append('cache='+str(cache or self.cache))
        if output is not None:arguments.append('emit='+str(self.root/'generated'))
        environment=dict(os.environ)
        if getattr(self,'odin',None):environment['THOR_ODIN']=str(self.odin)
        result=subprocess.run(arguments,capture_output=True,text=True,timeout=120,env=environment)
        if error:
            self.assertNotEqual(result.returncode,0,result.stdout)
            packet=json.loads(result.stderr)
            self.assertIn(error,packet['message']+'\n'+str(packet['diagnostics']),result.stderr)
            self.assertFalse(packet['unsupported'])
            return packet
        self.assertEqual(result.returncode,0,result.stderr)
        packet=json.loads(result.stdout)
        if output is not None:
            manifest=json.loads((self.root/'generated/thor-manifest.json').read_text())
            executable=self.root/('program'+EXE_SUFFIX)
            build=subprocess.run(['odin','build',packet['target'],'-file',*manifest['odin_flags'],'-out:'+str(executable)],
                capture_output=True,text=True,timeout=60,env={**os.environ,**manifest['odin_environment']})
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(run.stdout,output)
        return packet

    def test_warm_contents_cache_skips_helper_execution(self):
        self.write()
        cold=self.invoke(output='6\n')
        warm=self.invoke(output='6\n')
        self.assertGreater(cold['cache']['stats']['writes'],0)
        self.assertGreater(warm['cache']['stats']['hits'],0)
        self.assertEqual(warm['cache']['stats']['writes'],0)
        self.assertEqual(cold['results'],warm['results'])

    def test_warm_hit_does_not_launch_the_helper_compiler(self):
        log=self.root/'compiler.log'
        actual=shutil.which('odin')
        sdk=subprocess.check_output([actual,'root'],text=True).strip()
        self.odin=native_test_compiler(self.root,'frontend_guard',sdk=sdk,odin=actual,
            frontend=str(self.root/'unused-frontend'),log=str(log))
        self.write()
        self.invoke()
        cold=[json.loads(line) for line in log.read_text().splitlines()]
        warm=self.invoke()
        complete=[json.loads(line) for line in log.read_text().splitlines()]
        def helpers(commands):
            return [args for args in commands if args and args[0]=='build' and any(
                arg.startswith('-out:') and Path(arg.removeprefix('-out:')).name=='helper'+EXE_SUFFIX for arg in args)]
        cold_helpers=helpers(cold);complete_helpers=helpers(complete)
        self.assertEqual(len(cold_helpers),1)
        self.assertEqual(complete_helpers,cold_helpers)
        self.assertGreater(warm['cache']['stats']['hits'],0)

    def test_changed_dependency_does_not_reuse_old_contents(self):
        self.write();self.invoke(output='6\n')
        self.write(prefix='work::proc(n:int)->int{return n+3}\nVALUE::#run work(4)')
        changed=self.invoke(output='7\n')
        self.assertEqual(changed['cache']['stats']['hits'],0)
        self.assertGreater(changed['cache']['stats']['writes'],0)

    def test_cached_result_cannot_hide_new_impurity(self):
        self.write();self.invoke(output='6\n')
        self.write(prefix='state:int\nwork::proc(n:int)->int{return n+state}\nVALUE::#run work(4)')
        self.invoke(error='runtime storage')

    def test_corrupt_result_packet_is_recomputed(self):
        self.write();self.invoke(output='6\n')
        packets=list((self.cache/'eval-v1').glob('*.json'))
        self.assertTrue(packets)
        for packet in packets:packet.write_text('{')
        recovered=self.invoke(output='6\n')
        self.assertGreater(recovered['cache']['stats']['corrupt'],0)
        self.assertGreater(recovered['cache']['stats']['writes'],0)

    def test_bypass_and_unavailable_storage_remain_optional(self):
        self.write()
        bypass=self.invoke(enabled=False,output='6\n')
        self.assertGreater(bypass['cache']['stats']['bypasses'],0)
        self.assertEqual(bypass['cache']['stats']['writes'],0)
        blocked=self.root/'blocked';blocked.write_text('not a directory')
        failed_store=self.invoke(cache=blocked,output='6\n')
        self.assertEqual(failed_store['cache']['stats']['writes'],0)

    def test_checked_and_unchecked_flags_have_separate_entries(self):
        self.write();self.invoke(output='6\n')
        unchecked=self.invoke(flags=native.UNCHECKED,output='6\n')
        self.assertEqual(unchecked['cache']['stats']['hits'],0)
        self.assertGreater(unchecked['cache']['stats']['writes'],0)
        warm=self.invoke(flags=native.UNCHECKED,output='6\n')
        self.assertGreater(warm['cache']['stats']['hits'],0)

    def test_failed_evaluation_does_not_publish_a_value(self):
        self.write(body='',prefix='work::proc()->int{panic("expected evaluation panic")}\nVALUE::#run work()')
        self.invoke(error='expected evaluation panic')
        entries=list((self.cache/'eval-v1').glob('*.json'))
        self.assertEqual(entries,[])

    def test_owned_cached_results_materialize_independent_storage(self):
        self.write(body='''tracker:mem.Tracking_Allocator
mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
defer mem.tracking_allocator_destroy(&tracker)
context.allocator=mem.tracking_allocator(&tracker)
a:=#run work();b:=#run work();a[0]=9
fmt.println(a,b);delete(a);delete(b)
if len(tracker.allocation_map)!=0 || len(tracker.bad_free_array)!=0 {panic("cached ownership")}
''',prefix='import "core:mem"\nwork::proc()->[]int{values:=make([]int,2);values[0]=3;return values}')
        self.invoke(flags=native.UNCHECKED,output='[9, 0] [3, 0]\n')
        warm=self.invoke(flags=native.UNCHECKED,output='[9, 0] [3, 0]\n')
        self.assertGreaterEqual(warm['cache']['stats']['hits'],2)

    def test_nested_requests_reuse_their_separate_entries(self):
        self.write(prefix='leaf::proc()->int{return 3}\nwork::proc(n:int)->int{return n*2}\nVALUE::#run work(#run leaf())')
        self.invoke(output='6\n')
        warm=self.invoke(output='6\n')
        self.assertGreaterEqual(warm['cache']['stats']['hits'],2)

    def test_cached_local_type_specializations_keep_their_identities(self):
        self.write(body='Local::struct{x:int};Other::struct{x,y:int};fmt.println(generic(Local),generic(Other))',
            prefix='work::proc(n:int)->int{return n+1}\ngeneric::proc($T:typeid)->int{return #run work(size_of(T))}')
        self.invoke(output='9 17\n')
        warm=self.invoke(output='9 17\n')
        self.assertGreaterEqual(warm['cache']['stats']['hits'],2)


if __name__=='__main__':unittest.main()

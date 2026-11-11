"""Native invocation controls preserve reference cache and diagnostic contracts."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from tests import test_rust_compiler as native


class RustCliControlTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    program=native.RustCompilerTests.program

    def test_missing_and_unknown_commands_are_usage_errors(self):
        for arguments in ([], ['unknown-command'], ['--unknown-option']):
            for compiler in (native.PYTHON, native.RUST):
                with self.subTest(arguments=arguments, compiler=compiler):
                    result=subprocess.run([str(compiler), *arguments],capture_output=True,text=True,timeout=20)
                    self.assertEqual(result.returncode,2,result.stderr)
                    self.assertIn('usage:',result.stderr.lower())
                    self.assertNotIn('has not been ported',result.stderr)

    def write(self,body='fmt.println(VALUE)',prefix='work::proc(n:int)->int{return n+3}\nVALUE::#run work(4)'):
        self.source.write_text(self.program(body,'import "core:fmt"\n'+prefix+'\n'))

    def cache_packet(self,engine,flags=(),command='run',fail=None):
        result=self.invoke(engine,command,flags=[*flags,'--cache-report','-json-errors'])
        if fail:
            self.assertNotEqual(result.returncode,0,result.stdout)
            self.assertIn(fail,result.stderr)
        else:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report,*lines=result.stdout.splitlines()
        report=json.loads(report)
        self.assertEqual(set(report),{'thor_cache','thor_prepared_hits'})
        self.assertEqual(set(report['thor_cache']),{'hits','misses','writes','corrupt','bypasses'})
        return report,lines,result

    def test_cache_reports_cold_warm_and_disabled_execution(self):
        self.write()
        for engine in ['python','rust']:
            with self.subTest(engine=engine):
                cache=self.root/(engine+'-cache')
                cold,lines,_=self.cache_packet(engine,['--cache-dir='+str(cache)])
                self.assertEqual(lines,['7'])
                self.assertGreater(cold['thor_cache']['writes'],0)
                warm,lines,_=self.cache_packet(engine,['--cache-dir='+str(cache)])
                self.assertEqual(lines,['7'])
                self.assertGreater(warm['thor_cache']['hits'],0)
                self.assertEqual(warm['thor_cache']['writes'],0)
                disabled,lines,_=self.cache_packet(engine,['--cache-dir='+str(cache),'--no-cache'])
                self.assertEqual(lines,['7'])
                self.assertEqual(disabled['thor_cache']['writes'],0)

    def test_clear_cache_recomputes_and_preserves_unmanaged_entries(self):
        self.write()
        cache=self.root/'clear-cache'
        self.cache_packet('rust',['--cache-dir',str(cache)])
        preserved=[];deleted=[]
        for namespace in ['eval-v1','prepared-v1','identity-v1','scalar-v1','rust-parse-v1']:
            directory=cache/namespace;directory.mkdir(exist_ok=True)
            foreign=directory/'notes.json';foreign.write_text('unmanaged');preserved.append(foreign)
            stale=directory/('a'*64+'.json');stale.write_text('{}');deleted.append(stale)
            target=self.root/(namespace+'.target');target.write_text('owned elsewhere')
            link=directory/('b'*64+'.json');link.symlink_to(target);preserved.extend([link,target])
        report,lines,_=self.cache_packet('rust',['--cache-dir',str(cache),'--clear-cache'])
        self.assertEqual(lines,['7'])
        self.assertGreater(report['thor_cache']['writes'],0)
        for path in deleted:self.assertFalse(path.exists(),path)
        for path in preserved:self.assertTrue(path.exists(),path)

    def test_run_timeout_is_forwarded_and_reports_cache_counters_on_failure(self):
        self.write(prefix='spin::proc()->int{for{}}\nVALUE::#run spin()')
        for engine in ['python','rust']:
            with self.subTest(engine=engine):
                report,lines,result=self.cache_packet(engine,['--no-cache','--run-timeout=0.05'],command='check',fail='second timeout')
                self.assertEqual(lines,[])
                self.assertGreater(json.loads(result.stderr)['error_count'],0)

    def test_invalid_timeout_and_runtime_argument_options_have_usage_status(self):
        self.write(prefix='VALUE::7')
        cases=[['--run-timeout=0'],['--run-timeout=-1'],['--run-timeout=301'],
               ['--run-timeout=nan'],['--run-timeout=inf'],['--run-timeout=bad'],
               ['--track-allocations=bad']]
        for engine in ['python','rust']:
            for flags in cases:
                with self.subTest(engine=engine,flags=flags):
                    result=self.invoke(engine,'check',flags=flags)
                    self.assertEqual(result.returncode,2,result.stderr)
        result=self.invoke('rust','check',args=['not-compiler-flags'])
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertIn('arguments after --',result.stderr)

    def test_equals_options_and_separate_runtime_arguments(self):
        self.write(prefix='VALUE::7')
        for engine in ['python','rust']:
            with self.subTest(engine=engine):
                output=self.root/(engine+'-explicit-output')
                result=subprocess.run([str(native.PYTHON if engine=='python' else native.RUST),
                    'run',str(self.source),'--output-dir='+str(output),'--cache-dir='+str(self.root/'cache'),
                    '--diagnostic-format=json','--track-allocations=off','--','argument'],
                    cwd=self.root,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(result.stdout,'7\n')
                self.assertTrue((output/'thor-manifest.json').is_file())
                self.assertEqual(json.loads(result.stderr)['error_count'],0)

    def test_raw_diagnostics_retain_generated_odin_locations(self):
        self.source.write_text(self.program('n:int="wrong";_=n'))
        for engine in ['python','rust']:
            with self.subTest(engine=engine):
                raw=self.invoke(engine,'check',flags=['--raw-diagnostics'])
                self.assertNotEqual(raw.returncode,0)
                self.assertIn('.odin',raw.stderr)
                self.assertIn('Cannot convert',raw.stderr)
                mapped=self.invoke(engine,'check',flags=['-json-errors'])
                self.assertNotEqual(mapped.returncode,0)
                packet=json.loads(mapped.stderr)
                self.assertTrue(all(error['pos']['file']==str(self.source) for error in packet['errors']))

    def test_compiler_looking_options_after_separator_reach_the_program(self):
        self.write(prefix='VALUE::7')
        for engine in ['python','rust']:
            for argument in ['--help','--version','--diagnostic-format=json']:
                with self.subTest(engine=engine,argument=argument):
                    result=self.invoke(engine,args=[argument])
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertEqual(result.stdout,'7\n')
                    self.assertEqual(result.stderr,'')
    def toolchain_wrapper(self):
        import shutil, sys
        actual=shutil.which('odin')
        wrapper=self.root/'odin-wrapper'
        outputs=[str(self.root/engine) for engine in ['python','rust']]
        wrapper.write_text('#!'+sys.executable+'\nimport os,signal,sys\n'+
            'args=sys.argv[1:]\n'+f'outputs={outputs!r}\n'+
            'if args and args[0]=="check" and any(args[1].startswith(path+os.sep) for path in outputs):\n'+
            ' mode=os.environ["THOR_TEST_TOOLCHAIN_MODE"]\n'+
            ' print("native tool detail",file=sys.stderr,flush=True)\n'+
            ' if mode=="signal":os.kill(os.getpid(),signal.SIGTERM)\n'+
            ' raise SystemExit(0 if mode=="warning" else 1)\n'+
            f'os.execv({actual!r},[{actual!r},*args])\n')
        wrapper.chmod(0o755)
        return wrapper

    def test_plain_toolchain_output_obeys_warning_controls(self):
        self.write(prefix='VALUE::7')
        wrapper=self.toolchain_wrapper()
        for mode in ['warning','error']:
            for flags in [[],['-ignore-warnings'],['-warnings-as-errors']]:
                packets=[]
                for engine in ['python','rust']:
                    with self.subTest(engine=engine,mode=mode,flags=flags):
                        result=self.invoke(engine,'check',flags=['--odin',str(wrapper),'-json-errors',*flags],
                            env={**os.environ,'THOR_TEST_TOOLCHAIN_MODE':mode})
                        packet=json.loads(result.stderr)
                        packets.append((result.returncode,packet))
                        self.assertEqual(result.returncode,1 if mode=='error' or '-warnings-as-errors' in flags else 0,result.stderr)
                        if packet['errors']:
                            self.assertEqual(packet['errors'][0]['stage'],'toolchain')
                            self.assertIsNone(packet['errors'][0]['pos'])
                self.assertEqual(*packets)

    @unittest.skipIf(os.name=='nt','POSIX termination signal')
    def test_backend_signal_retains_name_status_and_native_detail(self):
        self.write(prefix='VALUE::7')
        wrapper=self.toolchain_wrapper()
        packets=[]
        for engine in ['python','rust']:
            result=self.invoke(engine,'check',flags=['--odin',str(wrapper),'-json-errors'],
                env={**os.environ,'THOR_TEST_TOOLCHAIN_MODE':'signal'})
            self.assertEqual(result.returncode,143,result.stderr)
            packet=json.loads(result.stderr)
            self.assertIn('SIGTERM (signal 15)',packet['errors'][0]['msgs'][0])
            self.assertEqual(packet['errors'][0]['msgs'][1],'native tool detail')
            packets.append(packet)
        self.assertEqual(*packets)

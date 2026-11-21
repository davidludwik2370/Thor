"""Native invocation controls preserve reference cache and diagnostic contracts."""
from tests.test_rust_compiler import compiler_engines
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, call, patch
from tests import test_rust_compiler as native


class LongProcessTimeoutTests(unittest.TestCase):
    def test_long_deadline_retries_the_same_process_and_keeps_its_output(self):
        from thorlang.backend import run_process

        process = MagicMock()
        process.__enter__.return_value = process
        process.returncode = 0
        process.communicate.side_effect = [subprocess.TimeoutExpired(['helper'], 86400),
                                           (b'complete output', b'')]
        with patch('thorlang.backend.subprocess.Popen', return_value=process) as opening:
            with patch('thorlang.backend.time.monotonic', side_effect=[0, 0, 86400, 86400]):
                result = run_process(['helper'], input=b'input', capture_output=True, timeout=200000)
        self.assertEqual(result.stdout, b'complete output')
        self.assertEqual(opening.call_count, 1)
        self.assertEqual(process.communicate.call_args_list,
                         [call(b'input', timeout=86400), call(None, timeout=86400)])
        process.kill.assert_not_called()

    def test_long_deadline_expiry_kills_the_helper_and_reports_the_full_limit(self):
        from thorlang.backend import run_process

        process = MagicMock()
        process.__enter__.return_value = process
        process.communicate.side_effect = subprocess.TimeoutExpired(
            ['helper'], 86400, output=b'partial output', stderr=b'partial error')
        with patch('thorlang.backend.subprocess.Popen', return_value=process):
            with patch('thorlang.backend.time.monotonic', side_effect=[0, 0, 200000]):
                with patch('thorlang.backend.os.name', 'posix'):
                    with self.assertRaises(subprocess.TimeoutExpired) as caught:
                        run_process(['helper'], capture_output=True, timeout=200000)
        self.assertEqual(caught.exception.timeout, 200000)
        self.assertEqual(caught.exception.stdout, b'partial output')
        self.assertEqual(caught.exception.stderr, b'partial error')
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()

    def test_cancellation_and_checked_failures_preserve_cleanup_and_output(self):
        from thorlang.backend import run_process

        process = MagicMock()
        process.__enter__.return_value = process
        process.communicate.side_effect = KeyboardInterrupt
        with patch('thorlang.backend.subprocess.Popen', return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                run_process(['helper'], capture_output=True, timeout=1e12)
        process.kill.assert_called_once_with()
        process.communicate.side_effect = None
        process.communicate.return_value = (b'output', b'failure')
        process.returncode = 1
        with patch('thorlang.backend.subprocess.Popen', return_value=process):
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                run_process(['helper'], capture_output=True, check=True, timeout=1e12)
        self.assertEqual(caught.exception.stdout, b'output')
        self.assertEqual(caught.exception.stderr, b'failure')


class RustCliControlTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    program=native.RustCompilerTests.program

    def test_no_arguments_shows_short_help_through_native_launchers(self):
        for compiler in (native.ROOT/'thor', native.ROOT/'thor-rust', native.RUST):
            with self.subTest(compiler=compiler):
                result = subprocess.run([str(compiler)], cwd=self.root,
                                        capture_output=True, text=True, timeout=20)
                help_result = subprocess.run([str(compiler), 'help'], cwd=self.root,
                                             capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertEqual(help_result.stderr, '')
                self.assertLess(len(result.stdout), len(help_result.stdout))
                self.assertIn('Usage: thor ', result.stdout)
                self.assertIn('[source]', result.stdout)
                self.assertIn('defaults to .', result.stdout)
                for option in ('build', 'run', 'test', 'new', 'vet', 'publish',
                               '-debug', '-o:MODE', '-out:PATH', '--output-dir', '--no-cache',
                               'thor help', 'thor help <command>'):
                    self.assertIn(option, result.stdout)
                self.assertNotIn('--app-resource', result.stdout)
                self.assertNotIn('--run-timeout', result.stdout)

    def test_full_help_groups_options_and_explains_modes_without_a_toolchain(self):
        environment = {**os.environ, 'PATH': str(self.root/'no-tools'), 'THOR_ODIN': 'missing-odin'}
        outputs = []
        for arguments in (['help', '-full'], ['-help', '-full']):
            result = subprocess.run([str(native.RUST), *arguments], cwd=self.root,
                                    env=environment, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])
        help_text = outputs[0]
        self.assertIn('[source]', help_text)
        self.assertIn('defaults to .', help_text)
        for heading in ('Artifact options: build, run, test, publish',
                        'Application packaging: publish only', 'Project creation: new only',
                        'Corrections: vet only', 'Program arguments: run, test'):
            self.assertIn(heading, help_text)
        for value in ('none', 'minimal', 'size', 'speed', 'aggressive',
                      'auto', 'off', 'basic', 'trace', 'gui', 'console',
                      'text', 'json', 'immutable', 'pure', 'unused', 'semicolon'):
            self.assertRegex(help_text, r'(?m)^\s+'+value+r'\s+\S')
        self.assertIn('--run-timeout SEC            Limit each compile-time execution in seconds\n'
                      '                               0 means unlimited (default)', help_text)
        self.assertNotIn('300', help_text)
        self.assertNotIn('Fractional', help_text)

    def test_command_help_includes_only_applicable_option_groups(self):
        cases = {
            'transpile': (['--run-timeout', '--output-dir'], ['-o:MODE', '--vet[', '--app-name']),
            'check': (['--vet[', '--run-timeout'], ['-o:MODE', '--apply', '--app-name']),
            'build': (['-o:MODE', '--track-allocations'], ['--app-name', '--apply', '-- arguments']),
            'run': (['-o:MODE', '-- arguments'], ['--app-name', '--apply']),
            'test': (['-o:MODE', '-- arguments'], ['--track-allocations', '--app-name', '--apply']),
            'vet': (['--apply', '--ask', '--vet['], ['-o:MODE', '--app-name']),
            'publish': (['--app-name', '--app-type', '-o:MODE'], ['--apply', '--parent']),
            'new': (['--parent', '--template', '--open', '--gitignore'], ['--run-timeout', '--vet[']),
            'ast': (['--cache-dir', '--overlay'], ['--run-timeout', '--app-name', '--source-maps']),
        }
        for command, (included, excluded) in cases.items():
            with self.subTest(command=command):
                result = subprocess.run([str(native.RUST), 'help', command], cwd=self.root,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertIn('Usage: thor '+command+' ', result.stdout)
                if command == 'new':
                    self.assertIn('Usage: thor new <name>', result.stdout)
                else:
                    self.assertIn('[source]', result.stdout)
                    self.assertIn('defaults to .', result.stdout)
                for option in included:
                    self.assertIn(option, result.stdout)
                for option in excluded:
                    self.assertNotIn(option, result.stdout)
        self.assertEqual(list(self.root.iterdir()), [self.app])

    def test_missing_project_names_and_unknown_commands_are_usage_errors(self):
        for arguments in (['new'], ['new', '--no-open'], ['unknown-command'], ['--unknown-option']):
            for compiler in ([native.PYTHON,native.RUST] if native.reference_enabled() else [native.RUST]):
                with self.subTest(arguments=arguments, compiler=compiler):
                    result=subprocess.run([str(compiler), *arguments],cwd=self.root,capture_output=True,text=True,timeout=20)
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
        for engine in compiler_engines():
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
        for engine in compiler_engines():
            for seconds in ['0.05', '1e-300']:
                with self.subTest(engine=engine, seconds=seconds):
                    report,lines,result=self.cache_packet(engine,['--no-cache','--run-timeout='+seconds],command='check',fail='second timeout')
                    self.assertEqual(lines,[])
                    self.assertGreater(json.loads(result.stderr)['error_count'],0)

    def test_zero_timeout_runs_helpers_in_both_value_option_forms(self):
        self.write()
        for engine in compiler_engines():
            for timeout in [['--run-timeout', '0'], ['--run-timeout=0']]:
                for flags in (['-debug'], native.UNCHECKED):
                    with self.subTest(engine=engine, timeout=timeout, flags=flags):
                        _, lines, _ = self.cache_packet(engine, ['--no-cache', *timeout, *flags])
                        self.assertEqual(lines, ['7'])

    def test_large_positive_timeouts_compile_and_execute_helpers(self):
        self.write()
        for engine in compiler_engines():
            for timeout in (['--run-timeout', '301'], ['--run-timeout=3600.5'],
                            ['--run-timeout', '1e12']):
                for flags in (['-debug'], native.UNCHECKED):
                    with self.subTest(engine=engine, timeout=timeout, flags=flags):
                        _, lines, _ = self.cache_packet(engine, ['--no-cache', *timeout, *flags])
                        self.assertEqual(lines, ['7'])

    @unittest.skipIf(os.name == 'nt', 'POSIX helper wrapper')
    def test_default_timeout_allows_helpers_past_the_old_five_second_limit(self):
        import shutil
        import sys

        self.write()
        actual = shutil.which('odin')
        wrapper = self.root/'delayed-odin'
        log = self.root/'delayed-helpers.log'
        helper_script = (f'#!{sys.executable}\nimport os,sys,time\n'
                         'time.sleep(5.2)\n'
                         'executable = sys.argv[0]+".native"\n'
                         'os.execv(executable, [executable, *sys.argv[1:]])\n')
        wrapper.write_text(f'#!{sys.executable}\n'+'''import subprocess
import sys
from pathlib import Path
'''+f'actual = {actual!r}\nhelper_script = {helper_script!r}\nlog = Path({str(log)!r})\n'+r'''
arguments = sys.argv[1:]
status = subprocess.run([actual, *arguments]).returncode
if status == 0 and arguments and arguments[0] == 'build':
    output = next((Path(arg[5:]) for arg in arguments if arg.startswith('-out:')), None)
    if (output is not None and output.name == 'helper'
            and output.parent.name.startswith(('thor-run-', 'thor-rust-run-'))):
        output.rename(str(output)+'.native')
        output.write_text(helper_script)
        output.chmod(0o755)
        with log.open('a') as stream:
            stream.write('delayed\n')
raise SystemExit(status)
''')
        wrapper.chmod(0o755)
        for engine in compiler_engines():
            for flags in (['-debug'], native.UNCHECKED):
                with self.subTest(engine=engine, flags=flags):
                    _, lines, _ = self.cache_packet(engine, ['--no-cache', '--odin', str(wrapper), *flags])
                    self.assertEqual(lines, ['7'])
        delayed_helpers = log.read_text().splitlines()
        self.assertEqual(delayed_helpers, ['delayed']*(2*len(compiler_engines())))

    def test_invalid_timeout_and_runtime_argument_options_have_usage_status(self):
        self.write(prefix='VALUE::7')
        cases=[['--run-timeout=-1'],
               ['--run-timeout=nan'],['--run-timeout=inf'],['--run-timeout=bad'],
               ['--track-allocations=bad']]
        for engine in compiler_engines():
            for flags in cases:
                with self.subTest(engine=engine,flags=flags):
                    result=self.invoke(engine,'check',flags=flags)
                    self.assertEqual(result.returncode,2,result.stderr)
        result=self.invoke('rust','check',flags=['--run-timeout=1e100'])
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertIn('too large to represent',result.stderr)
        result=self.invoke('rust','check',args=['not-compiler-flags'])
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertIn('arguments after --',result.stderr)

    def test_equals_options_and_separate_runtime_arguments(self):
        self.write(prefix='VALUE::7')
        for engine in compiler_engines():
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
        for engine in compiler_engines():
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
        for engine in compiler_engines():
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
        outputs=[str(self.root/engine) for engine in compiler_engines()]
        wrapper.write_text('#!'+sys.executable+'\nimport os,signal,sys\n'+
            'args=sys.argv[1:]\n'+f'outputs={outputs!r}\n'+
            'if args and args[0]=="check" and any(args[1].startswith(path+os.sep) for path in outputs):\n'+
            ' mode=os.environ["THOR_TEST_TOOLCHAIN_MODE"]\n'+
            ' print("native tool detail",file=sys.stderr,flush=True)\n'+
            ' if mode=="source-signal":\n'+
            '  import json,pathlib\n'+
            '  path=pathlib.Path(args[1]);path=next(path.rglob("*.odin")) if path.is_dir() else path\n'+
            '  raw=path.read_text();offset=raw.index("VALUE");line=raw[:offset].count("\\n")+1;column=offset-raw.rfind("\\n",0,offset)\n'+
            '  print(json.dumps({"errors":[{"type":"error","stage":"checker","pos":{"file":str(path),"offset":offset,"end_offset":offset+5,"line":line,"column":column},"msgs":["Simulated source failure"]}]}),file=sys.stderr,flush=True)\n'+
            ' if mode in {"signal","source-signal"}:os.kill(os.getpid(),signal.SIGTERM)\n'+
            ' raise SystemExit(0 if mode=="warning" else 1)\n'+
            f'os.execv({actual!r},[{actual!r},*args])\n')
        wrapper.chmod(0o755)
        return wrapper

    def test_vet_validation_preserves_native_warning_policy(self):
        import shutil
        import sys
        self.write(body='fmt.println(increment(VALUE))',prefix='increment::proc(n:int)->int{return n+1}\nVALUE::7')
        actual=shutil.which('odin')
        wrapper=self.root/'warning-policy-compiler'
        log=self.root/'warning-policy.jsonl'
        wrapper.write_text('#!'+sys.executable+'\nimport json,os,pathlib,sys\n'+
            f'args=sys.argv[1:]\nroot={str(self.root)!r}\nlog=pathlib.Path({str(log)!r})\n'+
            'if args and args[0] in {"check","build"} and (args[1].startswith(root+os.sep) or "thor-vet" in args[1]):\n'+
            ' with log.open("a") as output:output.write(json.dumps(args)+"\\n")\n'+
            f'os.execv({actual!r},[{actual!r},*args])\n')
        wrapper.chmod(0o755)
        original=self.source.read_text()
        for engine in compiler_engines():
            self.source.write_text(original)
            log.unlink(missing_ok=True)
            result=self.invoke(engine,'vet',flags=['--apply','--odin',str(wrapper),'-warnings-as-errors','-thread-count:1'])
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            attempts=[json.loads(line) for line in log.read_text().splitlines()]
            validation=[arguments for arguments in attempts if 'thor-vet' in arguments[1]]
            self.assertTrue(validation,attempts)
            for arguments in validation:self.assertIn('-warnings-as-errors',arguments)

    def test_generic_linker_collision_retries_once_without_hiding_source_or_external_errors(self):
        import shutil
        import sys
        self.write(body='if VALUE!=7 {panic("value")}',prefix='VALUE::7')
        actual=shutil.which('odin')
        wrapper=self.root/'module-compiler'
        log=self.root/'module-attempts.jsonl'
        wrapper.write_text('#!'+sys.executable+'\n'+'''import json,os,pathlib,sys
args=sys.argv[1:]
'''+f'root={str(self.root)!r}\nlog=pathlib.Path({str(log)!r})\n'+'''if args and args[0]=="build" and args[1].startswith(root+os.sep):
 with log.open('a') as output:output.write(json.dumps(args)+'\\n')
 mode=os.environ['THOR_TEST_MODULE_FAILURE']
 if '-use-single-module' not in args or mode=='always':
  if mode=='source':
   source=pathlib.Path(args[1]);source=next(source.rglob('*.odin')) if source.is_dir() else source
   print(json.dumps({'errors':[{'type':'error','pos':{'file':str(source),'offset':0,'line':1,'column':1},'msgs':['Source failure']}]}),file=sys.stderr)
  print("duplicate symbol '_external_name'" if mode=='external' else "duplicate symbol '_sample::helper:proc(v:int)->(:int)'",file=sys.stderr)
  raise SystemExit(1)
'''+f'os.execv({actual!r},[{actual!r},*args])\n')
        wrapper.chmod(0o755)
        for engine in compiler_engines():
            for mode,count in [('recover',2),('always',2),('source',1),('external',1)]:
                with self.subTest(engine=engine,mode=mode):
                    log.unlink(missing_ok=True)
                    result=self.invoke(engine,'build',flags=['--odin',str(wrapper),'-use-separate-modules','-thread-count:1','-json-errors'],
                        env={**os.environ,'THOR_TEST_MODULE_FAILURE':mode})
                    self.assertEqual(result.returncode,0 if mode=='recover' else 1,result.stderr)
                    attempts=[json.loads(line) for line in log.read_text().splitlines()]
                    self.assertEqual(len(attempts),count,attempts)
                    if count==2:
                        self.assertIn('-use-single-module',attempts[1])
                        self.assertNotIn('-use-separate-modules',attempts[1])
                    if mode=='source':self.assertIn('Source failure',result.stderr)

    @unittest.skipIf(os.name=='nt','POSIX termination signal')
    def test_backend_signal_keeps_preceding_source_diagnostics_and_original_positions(self):
        self.write(prefix='VALUE::7')
        wrapper=self.toolchain_wrapper()
        packets=[]
        for engine in compiler_engines():
            result=self.invoke(engine,'check',flags=['--odin',str(wrapper),'-json-errors'],
                env={**os.environ,'THOR_TEST_TOOLCHAIN_MODE':'source-signal'})
            self.assertEqual(result.returncode,143,result.stderr)
            packet=json.loads(result.stderr)
            self.assertEqual(len(packet['errors']),2,packet)
            primary,failure=packet['errors']
            self.assertEqual(primary['msgs'],['Simulated source failure'])
            self.assertEqual(primary['pos']['file'],str(self.source))
            self.assertEqual((primary['pos']['line'],primary['pos']['column']),
                (self.source.read_text().split('VALUE',1)[0].count('\n')+1,1))
            self.assertEqual(failure['stage'],'toolchain')
            self.assertIn('SIGTERM (signal 15)',failure['msgs'][0])
            self.assertNotIn('no source location',failure['msgs'][0])
            self.assertEqual(failure['msgs'][1],'native tool detail')
            packets.append(packet)
        for packet in packets[1:]:
            self.assertEqual(packet,packets[0])

    @unittest.skipIf(os.name=='nt','POSIX termination signal')
    def test_vet_rejects_invalid_corrections_when_source_errors_precede_a_backend_crash(self):
        import shutil,sys
        self.source.write_text(self.program('value:=7;if value!=7{panic("value")}',prefix='VALUE::7'))
        original=self.source.read_bytes()
        actual=shutil.which('odin')
        wrapper=self.root/'vet-odin-wrapper'
        wrapper.write_text('#!'+sys.executable+'\n'+
            'import json,os,pathlib,signal,sys\nargs=sys.argv[1:]\n'+
            'if args and args[0]=="check" and "thor-vet-" in args[1]:\n'+
            ' path=pathlib.Path(args[1]);path=next(path.rglob("*.odin")) if path.is_dir() else path\n'+
            ' raw=path.read_text();offset=raw.index("VALUE");line=raw[:offset].count("\\n")+1;column=offset-raw.rfind("\\n",0,offset)\n'+
            ' print(json.dumps({"errors":[{"type":"error","stage":"checker","pos":{"file":str(path),"offset":offset,"end_offset":offset+5,"line":line,"column":column},"msgs":["Invalid proposed correction"]}]}),file=sys.stderr,flush=True)\n'+
            ' os.kill(os.getpid(),signal.SIGTERM)\n'+
            f'os.execv({actual!r},[{actual!r},*args])\n')
        wrapper.chmod(0o755)
        result=self.invoke('rust','vet',flags=['--apply','--odin',str(wrapper),'-json-errors'])
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.source.read_bytes(),original)
        self.assertNotIn('Applied',result.stdout)

    def test_plain_toolchain_output_obeys_warning_controls(self):
        self.write(prefix='VALUE::7')
        wrapper=self.toolchain_wrapper()
        for mode in ['warning','error']:
            for flags in [[],['-ignore-warnings'],['-warnings-as-errors']]:
                packets=[]
                for engine in compiler_engines():
                    with self.subTest(engine=engine,mode=mode,flags=flags):
                        result=self.invoke(engine,'check',flags=['--odin',str(wrapper),'-json-errors',*flags],
                            env={**os.environ,'THOR_TEST_TOOLCHAIN_MODE':mode})
                        packet=json.loads(result.stderr)
                        packets.append((result.returncode,packet))
                        self.assertEqual(result.returncode,1 if mode=='error' or '-warnings-as-errors' in flags else 0,result.stderr)
                        if packet['errors']:
                            self.assertEqual(packet['errors'][0]['stage'],'toolchain')
                            self.assertIsNone(packet['errors'][0]['pos'])
                for packet in packets[1:]:
                    self.assertEqual(packet,packets[0])

    @unittest.skipIf(os.name=='nt','POSIX termination signal')
    def test_backend_signal_retains_name_status_and_native_detail(self):
        self.write(prefix='VALUE::7')
        wrapper=self.toolchain_wrapper()
        packets=[]
        for engine in compiler_engines():
            result=self.invoke(engine,'check',flags=['--odin',str(wrapper),'-json-errors'],
                env={**os.environ,'THOR_TEST_TOOLCHAIN_MODE':'signal'})
            self.assertEqual(result.returncode,143,result.stderr)
            packet=json.loads(result.stderr)
            self.assertIn('SIGTERM (signal 15)',packet['errors'][0]['msgs'][0])
            self.assertEqual(packet['errors'][0]['msgs'][1],'native tool detail')
            packets.append(packet)
        for packet in packets[1:]:
            self.assertEqual(packet,packets[0])

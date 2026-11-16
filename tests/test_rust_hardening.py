"""Cross-feature storage and type contexts used by the native hardening pass."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import unittest

from tests import test_rust_compiler as native


class RustHardeningTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare
    program = native.RustCompilerTests.program

    def test_returned_views_keep_permissions_across_generic_optional_and_switch_contexts(self):
        cases = [
            ('direct', 'relay :: proc(value:[]int)->[]int {return value}',
             'selected :> relay(view)'),
            ('generic', 'relay :: proc(value:$T)->T {return value}',
             'selected :> relay(view)'),
            ('optional', 'relay :: proc(value:$T)->(T,bool) #optional_ok {return value,true}',
             'selected :> relay(view) or_else nil'),
            ('record', '''Holder :: struct($T:typeid) {value:T}
relay :: proc(box:Holder($T))->T {return box.value}''',
             'holder :> Holder([]int){view}\nselected :> relay(holder)'),
            ('array', 'relay :: proc(value:$T)->T {return value}',
             'rows :> [1][]int{view}\nselected :> relay(rows[0])'),
            ('switch', '', '''pick := len(values)>0
selected :> switch pick {case true: view; case: values}'''),
        ]
        for flags in (['-debug'], native.UNCHECKED+['--track-allocations=basic']):
            for name, prefix, selection in cases:
                with self.subTest(flags=flags, case=name):
                    code = self.program('''
values := #run create_values()
defer delete(values)
view :> values
'''+selection+'''
copy := slice.clone(selected)
defer delete(copy)
copy[0] = 9
if values[0]!=0 || copy[0]!=9 {panic("independent copy")}
''', '''import "core:slice"
create_values :: fn()->[]int {return [x*x for x in 0..<3]}
'''+prefix)
                    run = self.compare(code, flags=flags, expected='')
                    self.assertEqual(run.stderr, '')
                    self.source.write_text(code.replace('copy[0] = 9', 'selected[0] = 9'))
                    invalid = self.invoke('rust', command='run', flags=flags+['-json-errors'])
                    self.assertEqual(invalid.returncode, 1, invalid.stderr)
                    packet = json.loads(invalid.stderr)
                    self.assertIn('read-only slice', invalid.stderr.lower())
                    offset = self.source.read_bytes().index(b'selected[0] = 9')
                    self.assertTrue(any(error['pos']['offset'] <= offset < error['pos']['end_offset']
                                        for error in packet['errors']), packet)


@unittest.skipUnless(os.name == 'posix', 'native Unix helper process groups')
class RustHelperLifecycleTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp

    def cancel(self, phase, flags):
        marker = self.root/'helper.json'
        wrapper = self.root/'odin-wrapper'
        wrapper.write_text('#!'+sys.executable+'\n'+'''import json,os,subprocess,sys,time
from pathlib import Path
args=sys.argv[1:]
phase=os.environ['THOR_TEST_HELPER_PHASE']
helper=args and args[0]=='build' and any('thor-rust-run-' in arg for arg in args)
frontend=args and args[0]=='build' and any('rust-frontend-' in arg for arg in args)
selected=(helper and phase=='build' or frontend and phase=='frontend'
          or args==['root'] and phase=='identity' or args==['version'] and phase=='version')
if selected:
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    paths=[arg.removeprefix('-out:') for arg in args]
    directory=next((str(parent) for arg in paths for parent in Path(arg).parents
                    if parent.name.startswith(('thor-rust-run-','rust-frontend-'))),None)
    Path(os.environ['THOR_TEST_HELPER_MARKER']).write_text(json.dumps(
        {'parent':os.getpid(),'child':child.pid,'directory':directory}))
    time.sleep(30)
else:
    executable=os.environ['THOR_TEST_REAL_ODIN']
    os.execv(executable,[executable,*args])
''')
        wrapper.chmod(0o755)
        self.source.write_text('''package main
calculate :: proc(n:int)->int {return n+1}
VALUE :: #run calculate(2)
main :: proc() {}
''')
        environment = {**os.environ, 'THOR_TEST_HELPER_PHASE':phase,
                       'THOR_TEST_HELPER_MARKER':str(marker),
                       'THOR_TEST_REAL_ODIN':shutil.which('odin')}
        environment.pop('THOR_FRONTEND_ODIN',None)
        process = subprocess.Popen([str(native.RUST),'build',str(self.source),
                                    '--odin',str(wrapper),'-json-errors',*flags],
                                   env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        descendants = None
        try:
            deadline = time.monotonic()+20
            while not marker.exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), 'helper did not start')
            descendants = json.loads(marker.read_text())
            process.send_signal(signal.SIGTERM)
            stdout,stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 143, stdout+stderr)
            if descendants['directory']:
                self.assertFalse(Path(descendants['directory']).exists())
            for name in ['parent','child']:
                status = subprocess.run(['ps','-o','stat=','-p',str(descendants[name])],
                                        capture_output=True,text=True,timeout=3)
                self.assertTrue(status.returncode!=0 or status.stdout.strip().startswith('Z'),
                                'helper descendant survived cancellation '+str(descendants))
        finally:
            if descendants is None and marker.exists():
                descendants = json.loads(marker.read_text())
            if descendants:
                for name in ['parent','child']:
                    try:
                        os.kill(descendants[name], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_cancelling_helper_compilation_reaps_descendants_and_temporary_sources(self):
        self.cancel('build', ['-debug'])

    def test_cancelling_toolchain_identity_query_reaps_descendants(self):
        self.cancel('identity', native.UNCHECKED)

    def test_cancelling_frontend_version_query_reaps_descendants(self):
        self.cancel('version', ['-debug'])

    def test_cancelling_frontend_build_reaps_descendants_and_temporary_sources(self):
        self.cancel('frontend', native.UNCHECKED)


if __name__ == '__main__':
    unittest.main()

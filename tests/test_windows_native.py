"""Native Windows installation, relocation, resources and library packaging."""
import json
import os
from pathlib import Path
import shutil
import shlex
import struct
import subprocess
import tempfile
import unittest
import zipfile
import zlib
from tests import test_rust_compiler as native


@unittest.skipUnless(os.name=='nt','Native Windows executable integration')
class WindowsNativeTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor Windows héllo ')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.project=self.root/'source';self.project.mkdir()
        self.output=self.root/'output';self.output.mkdir()
        self.environment={**os.environ,'THOR_TEST_COMPILER':str(native.RUST)}

    def call(self,*args,compiler=None,cwd=None):
        result=subprocess.run([str(compiler or native.RUST),*map(str,args)],cwd=cwd or self.project,
            env=self.environment,capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def program(self,text):
        (self.project/'main.thor').write_text(text)

    def test_no_alloc_windows_identity_clock_and_native_boundaries(self):
        self.program('''package main
import win "core:sys/windows"
import "core:time"
import "core:os"
@(no_alloc)
main::proc(){_=win.GetCurrentThreadId();_=win.GetCurrentProcessId();_=os.get_pid();_=time.now();_=time.tick_now()}
''')
        self.call('check','.')
        for flags in [[],native.UNCHECKED]:self.call('run','.',*flags)
        self.program('''package main
import win "core:sys/windows"
@(no_alloc) main::proc(){_=win.VirtualAlloc(nil,4096,win.MEM_COMMIT|win.MEM_RESERVE,win.PAGE_READWRITE)}
''')
        result=subprocess.run([str(native.RUST),'check',str(self.project)],env=self.environment,capture_output=True,text=True,timeout=60)
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertIn('no_alloc',result.stderr)

    def test_relocated_installation_builds_runs_tests_and_vets(self):
        install=self.root/'installed tools';install.mkdir()
        binary=install/'thor.exe';shutil.copy2(native.RUST,binary)
        for name in ['frontend','runtime','templates']:shutil.copytree(native.ROOT/name,install/name)
        self.environment.pop('THOR_ROOT',None)
        self.call('new','app',compiler=binary,cwd=self.project)
        app=self.project/'app'
        (app/'app.thor').write_text('''package main
import "core:fmt"
import basic "thor:basic"
@(no_alloc) show::proc(){fmt.println(7);basic.print(8)}
work::fn(x:int)->int{return x*x}
VALUE::#run work(7)
main::proc(){show();values~:>[x*x for x in 0..<3];fmt.println(VALUE,values)}
''')
        (app/'verify_test.thor').write_text('package main\nimport "core:testing"\n@(test) verify::proc(t:^testing.T){testing.expect(t,VALUE==49)}\n')
        for flags in [[],native.UNCHECKED]:
            result=self.call('run','.',*flags,compiler=binary,cwd=app)
            self.assertEqual(result.stdout.strip(),'7\n8\n49 [0, 1, 4]')
        for command in ['check','test','vet']:self.call(command,'.',compiler=binary,cwd=app)
        tasks=json.loads((app/'.vscode/tasks.json').read_text())['tasks']
        self.assertEqual(tasks[0]['command'],str(binary))

    def test_migration_validation_uses_native_artifact_extensions(self):
        source=self.project/'main.odin'
        source.write_text('''package main
import "core:testing"
@(export) answer::proc()->int{return 42}
@(test) verify::proc(t:^testing.T){testing.expect(t,answer()==42)}
main::proc(){}
''')
        odin=self.environment.get('THOR_ODIN','odin')
        for mode in ['exe','test','dll','obj','llvm-ir']:
            native_args=['build',str(self.project),'-build-mode:'+mode,'-thread-count:1']
            output=self.root/('migrated-'+mode)
            command=shlex.join([odin,*native_args])
            result=self.call('migrate',str(self.project),'--output-dir',output,'--command',command)
            self.assertTrue((output/self.project.name/'main.thor').is_file())
            translated=shlex.split(result.stdout.strip())
            self.assertEqual(translated.pop(0),'thor')
            self.call(*translated)

    def icon(self):
        def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
        raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',16,16,8,6,0,0,0))
        raw+=chunk(b'IDAT',zlib.compress((b'\0'+b'\x14\x50\xc8\xff'*16)*16))+chunk(b'IEND',b'')
        (self.project/'assets').mkdir(exist_ok=True)
        (self.project/'assets/icon.png').write_bytes(raw)

    def unpack(self,artifact):
        target=self.root/'moved package';target.mkdir()
        with zipfile.ZipFile(artifact) as archive:archive.extractall(target)
        binaries=list(target.rglob('*.exe'))
        self.assertEqual(len(binaries),1)
        return binaries[0]

    def subsystem(self,binary):
        raw=binary.read_bytes();pe=struct.unpack_from('<I',raw,0x3c)[0]
        self.assertEqual(raw[pe:pe+4],b'PE\0\0')
        return struct.unpack_from('<H',raw,pe+24+68)[0]

    def test_console_publish_icon_resources_and_arguments_survive_relocation(self):
        self.icon();(self.project/'assets/data.txt').write_text('resource content')
        self.program('''package main
import "thor:app"
import "core:os"
import "core:fmt"
main::proc(){p:=app.resource_path("assets/data.txt");defer delete(p);data,err:=os.read_entire_file(p,context.allocator);if err!=nil{panic("resource")};defer delete(data);fmt.println(string(data),os.args[1])}
''')
        result=self.call('publish','.', '--app-type','console','--app-name','Windows Test','--publish-dir',self.output,*native.UNCHECKED)
        executable=self.unpack(Path(result.stdout.strip()))
        self.assertEqual(self.subsystem(executable),3)
        self.project.rename(self.root/'hidden source')
        run=subprocess.run([str(executable),'hello world'],cwd=self.output,capture_output=True,text=True,timeout=30)
        self.assertEqual((run.returncode,run.stdout.strip()),(0,'resource content hello world'),run.stderr)
        self.assertTrue(list(executable.parent.rglob('thor-publish.json')))

    def test_gui_publish_uses_windows_subsystem_and_runs_without_source(self):
        self.program('''package main
import "core:os"
main::proc(){err:=os.write_entire_file(os.args[1],"GUI executable ran");if err!=nil{panic("write")}}
''')
        result=self.call('publish','.', '--app-type','gui','--publish-dir',self.output)
        executable=self.unpack(Path(result.stdout.strip()));self.assertEqual(self.subsystem(executable),2)
        self.project.rename(self.root/'hidden source');marker=self.output/'gui-result.txt'
        run=subprocess.run([str(executable),str(marker)],cwd=self.output,capture_output=True,text=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(marker.read_text(),'GUI executable ran')

    def test_linked_native_dll_is_packaged_and_loaded_after_relocation(self):
        libraries=self.project/'libs';libraries.mkdir()
        source=libraries/'answer.c';source.write_text('__declspec(dllexport) int answer(void){return 42;}\n')
        build=subprocess.run(['cl','/nologo','/LD',str(source),'/link','/OUT:'+str(libraries/'answer.dll')],cwd=libraries,capture_output=True,text=True,timeout=90)
        self.assertEqual(build.returncode,0,build.stdout+build.stderr)
        self.program('''package main
import "core:fmt"
foreign import native "libs/answer.lib"
foreign native {answer::proc "c" ()->i32 ---}
main::proc(){value:=answer();if value!=42{panic("DLL")};fmt.println(value)}
''')
        result=self.call('publish','.', '--app-type','console','--publish-dir',self.output)
        executable=self.unpack(Path(result.stdout.strip()));self.assertTrue((executable.parent/'answer.dll').is_file())
        self.project.rename(self.root/'hidden source')
        run=subprocess.run([str(executable)],cwd=self.output,capture_output=True,text=True,timeout=30)
        self.assertEqual((run.returncode,run.stdout.strip()),(0,'42'),run.stderr)

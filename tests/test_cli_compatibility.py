"""Help tiers and native commands/flags retain source-facing Thor behavior."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
from tests import test_rust_compiler as native


class CompilerOutputTests(unittest.TestCase):
    def test_mixed_progress_and_diagnostics_preserve_both(self):
        from thorlang.backend import decode
        diagnostic={'type':'error','pos':{'file':'input.thor','offset':4},'msgs':['undeclared name']}
        wire=b'Checking...\n'+json.dumps({'errors':[diagnostic]}).encode()+b'\nTotal Time: 3 ms\n'
        packet=decode(wire,False,True)
        self.assertEqual(packet['errors'],[diagnostic])
        self.assertEqual(packet['information'],'Checking...\n\nTotal Time: 3 ms\n')
        self.assertEqual(decode(b'link failure',False,True)['errors'][0]['type'],'error')
        self.assertEqual(decode(b'real warning',True,False)['errors'][0]['type'],'warning')
        self.assertEqual(decode(b'Total Time: 1 ms\n',True,True)['errors'],[])
        failed=decode(b'{"errors":[]}\nlinker failed\n',False,True)
        self.assertEqual(failed['errors'][0]['type'],'error')
        self.assertIn('linker failed',failed['errors'][0]['msgs'][0])


class CliCompatibilityTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp

    @unittest.skipUnless(os.name=='nt','Windows preserves full 32-bit process exit statuses')
    def test_windows_runtime_exit_statuses_preserve_the_full_failure_code(self):
        for code in [37,0xC0000005,0x80000000]:
            self.source.write_text('package main\nimport "core:os"\nmain::proc(){os.exit('+str(code)+')}\n',newline='')
            for flags in [[],native.UNCHECKED]:
                with self.subTest(code=code,flags=flags):
                    self.call('rust','run',self.source,*flags,status=code)

    def call(self, engine, *args, status=0, cwd=None, env=None):
        compiler=native.PYTHON if engine=='python' else native.RUST
        result=subprocess.run([*([sys.executable] if engine=='python' else []), str(compiler), *map(str,args)], cwd=cwd or self.app,
                              env=env,capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,status,result.stdout+result.stderr)
        return result

    def write(self, raw=None):
        self.source.write_text(raw or '''package main
import "core:fmt"
import "core:testing"
// A documented pure procedure.
work :: fn(x: int) -> int { return x+1 }
Thing :: struct {
    value: int,
    name: string,
}
VALUE :: 7
@(private) hidden :: proc() {}
main :: proc() { fmt.println(f"value {work(VALUE)}") }
@(test) smoke :: proc(t: ^testing.T) { _=t; if work(VALUE)!=8 { panic("test") } }
''')

    def test_help_tiers_topics_and_removed_spellings_without_project_or_tools(self):
        env={**os.environ,'PATH':str(self.root/'no-tools'),'THOR_ODIN':'missing-odin'}
        for engine in ['python','rust']:
            overview=self.call(engine,cwd=self.root,env=env).stdout
            standard=self.call(engine,'help',cwd=self.root,env=env).stdout
            full=self.call(engine,'help','-full',cwd=self.root,env=env).stdout
            self.assertLess(len(overview),len(standard));self.assertLess(len(standard),len(full))
            self.assertIn('--run-timeout',standard);self.assertNotIn('-microarch:',standard)
            self.assertIn('-microarch:',full);self.assertIn('-vet-shadowing',full)
            for command in ['new','migrate','build','run','test','check','vet','publish','transpile','ast','doc','strip-semicolon','bundle','help','version','root','report']:
                with self.subTest(engine=engine,command=command):
                    topic=self.call(engine,'help',command,cwd=self.root,env=env).stdout
                    exhaustive=self.call(engine,'help',command,'-full',cwd=self.root,env=env).stdout
                    self.assertIn('Usage: thor '+command,topic)
                    if command != 'help': self.assertEqual(self.call(engine,command,'-help',cwd=self.root,env=env).stdout,topic)
                    self.assertEqual(self.call(engine,'help','-full',command,cwd=self.root,env=env).stdout,exhaustive)
            for args in [('-h',),('--help',),('help','nonesuch'),('run','-full'),('build','--help')]:
                result=self.call(engine,*args,cwd=self.root,env=env,status=2)
                self.assertIn('thor help',result.stderr)

    def test_information_commands_do_not_load_a_project(self):
        for engine in ['python','rust']:
            version=self.call(engine,'version',cwd=self.root)
            self.assertEqual(version.stdout.strip(),'Thor '+native.VERSION if hasattr(native,'VERSION') else 'Thor 0.41.6')
            root=self.call(engine,'root',cwd=self.root)
            self.assertEqual(Path(root.stdout.strip()),native.ROOT)
            self.assertEqual(root.stderr,'')
            report=self.call(engine,'report',cwd=self.root)
            self.assertIn('Thor:',report.stdout);self.assertIn('Native compiler:',report.stdout)
            self.assertIn('version:',report.stdout)
            for query in ['-target:?','-microarch:?','-target-features:?']:
                result=self.call(engine,'build',query,cwd=self.root)
                self.assertGreater(len(result.stdout),80);self.assertEqual(result.stderr,'')
            bad=self.call(engine,'build','-target:nonexistent','-microarch:?',cwd=self.root,status=1)
            self.assertTrue(bad.stderr)

    def test_explicit_file_test_and_other_build_modes(self):
        self.write()
        for engine in ['python','rust']:
            self.call(engine,'check',self.source,'-file')
            run=self.call(engine,'run',self.source,'-file','-build-mode:exe',*native.UNCHECKED)
            self.assertEqual(run.stdout,'value 8\n')
            tests=self.call(engine,'test','-build-mode:test')
            self.assertIn('The test was successful',tests.stdout+tests.stderr)
            for mode,suffix in [('obj','.o'),('llvm-ir','.ll'),('assembly','.S')]:
                out=self.root/(engine+'-'+mode+suffix)
                if mode=='llvm-ir': out.mkdir()
                self.call(engine,'build','-use-single-module','-build-mode:'+mode,'-out:'+str(out))
                self.assertTrue(out.exists(),str(out))
                if mode=='llvm-ir': self.assertTrue(list(out.glob('*.ll')))
            self.call(engine,'build','-use-single-module','-build-mode:llvm-ir')
            self.assertTrue(list(self.app.glob('*.ll')))
            for args in [('run','-build-mode:obj'),('test','-build-mode:exe'),('check','-file'),('check','-build-mode:test'),('build','-build-mode:exe','-build-mode:obj')]:
                self.call(engine,*args,status=2)

    def test_reports_timings_warning_policy_and_dependency_paths(self):
        self.write('''package main
import "core:fmt"
unused_global :: 42
main :: proc() { fmt.println("reports") }
''')
        for engine in ['python','rust']:
            for command, flag, text in [('build','-print-linker-flags',''),('check','-show-import-graph',str(self.app)),('check','-show-unused-with-location','unused_global'),('check','-show-defineables','')]:
                result=self.call(engine,command,flag)
                self.assertTrue(result.stdout,flag);self.assertIn(text,result.stdout)
                if flag=='-show-unused-with-location':self.assertIn(str(self.source),result.stdout)
            system_calls=self.call(engine,'build','-show-system-calls','-warnings-as-errors','-json-errors')
            self.assertEqual(json.loads(system_calls.stderr)['errors'],[])
            self.assertIn('[SYSTEM CALL]',json.loads(system_calls.stderr)['information'])
            timing=self.call(engine,'check','-show-timings','-warnings-as-errors','-json-errors')
            packet=json.loads(timing.stderr)
            self.assertEqual(packet['errors'],[])
            for phase in ['Thor compilation timings','preparation','lowering','native','Total Time']:
                self.assertIn(phase,packet['information'])
            deps=self.root/(engine+'-dependencies.json')
            self.call(engine,'build','-export-dependencies:json','-export-dependencies-file:'+str(deps))
            document=json.loads(deps.read_text())
            self.assertIn(str(self.source),json.dumps(document))
            self.assertNotIn('/input/main.odin',json.dumps(document))
            makefile=self.root/(engine+'-dependencies.d')
            self.call(engine,'build','--output-dir',self.root/(engine+' generated'),'-export-dependencies:make','-export-dependencies-file:'+str(makefile))
            self.assertIn(str(self.source).replace(' ',r'\ '),makefile.read_text())

    def test_native_vet_retains_errors_and_avoids_compiler_support_packages(self):
        self.write('''package main
import "core:fmt"
work :: fn(x: int) -> int { return x+1 }
main :: proc() {
    values :> [1,2,3]
    fmt.println(f"value {work(values[0])}")
}
''')
        other_os='linux' if os.name=='nt' else 'windows'
        (self.app/f'excluded_{other_os}.thor').write_text('not valid source on this platform\n')
        for engine in ['python','rust']:
            for flag in ['-vet-semicolon', '-vet-when-shadowing']:
                with self.subTest(engine=engine,flag=flag):
                    # The installed Odin revision has an upstream scoped-vet parser crash.
                    self.call(engine,'check',flag,'-thread-count:1')
            for flag in ['-vet', '-vet-style', '-strict-style']:
                self.assertIn('Odin-only projects',self.call(engine,'check',flag,status=1).stderr)
        self.write('''package main
main :: proc() {
    if true {
        x := 10
    }
}
''')
        for engine in ['python','rust']:
            self.assertIn('Odin-only projects',self.call(engine,'check','-vet-unused-variables',status=1).stderr)
            self.assertIn('Warning:',self.call(engine,'check').stderr)

    def test_documentation_preserves_original_signatures_comments_and_format(self):
        self.write()
        for engine in ['python','rust']:
            full=self.call(engine,'doc').stdout
            self.assertIn('work :: fn(x: int) -> int',full)
            self.assertIn('A documented pure procedure.',full)
            self.assertIn(self.source.name,full)
            self.assertEqual(full.count('value: int'),1)
            self.assertEqual(full.count('name: string'),1)
            self.assertNotIn('hidden ::',full);self.assertNotIn('__thor_',full)
            short=self.call(engine,'doc','-short').stdout
            self.assertNotIn('A documented pure procedure.',short)
            ordered=self.call(engine,'doc','-in-source-order').stdout
            self.assertLess(ordered.index('work ::'),ordered.index('Thing ::'))
            output=self.root/(engine+'-docs.odin-doc')
            self.call(engine,'doc','-doc-format','-out:'+str(output))
            data=output.read_bytes()
            self.assertEqual(data[:8],b'odindoc\0')
            self.assertEqual(struct.unpack_from('<I',data,16)[0],len(data))
            checksum=0x811c9dc5
            for byte in data[60:]:checksum=((checksum^byte)*0x01000193)&0xffffffff
            self.assertEqual(struct.unpack_from('<I',data,24)[0],checksum)
            self.assertIn(str(self.source).encode(),data)
            self.assertIn(b'fn(x: int) -> int',data)

    def test_semicolon_cleanup_preserves_headers_comments_strings_and_permissions(self):
        raw='''package main;
import "core:fmt";
main :: proc() {
    text :> "semi;colon"; // comment ; retained
    for i:=0;
        i<1;
        i+=1 {
        fmt.println(f"{text}");
    }
    a:=1; b:=2
    fmt.println(a,b);
}
'''
        for engine in ['python','rust']:
            self.source.write_bytes(raw.replace('\n','\r\n').encode())
            self.source.chmod(0o640)
            backup=Path(str(self.source)+'~backup.thor-temp');backup.unlink(missing_ok=True)
            result=self.call(engine,'strip-semicolon','-keep-temp-files','-json-errors')
            packet=json.loads(result.stderr);self.assertEqual(packet['errors'],[])
            self.assertIn('Files stripped',packet['information'])
            self.assertEqual(backup.read_bytes(),raw.replace('\n','\r\n').encode())
            self.assertEqual(self.source.stat().st_mode&0o777,0o640)
            edited=self.source.read_bytes()
            self.assertNotIn(b'package main;',edited)
            self.assertIn(b'for i:=0;',edited);self.assertIn(b'i<1;',edited)
            self.assertIn(b'a:=1; b:=2',edited)
            self.assertIn(b'"semi;colon"',edited);self.assertIn(b'// comment ; retained',edited)
            self.assertIn(b'\r\n',edited)
            self.call(engine,'check')
            self.call(engine,'strip-semicolon')
            self.assertEqual(self.source.read_bytes(),edited)
            self.source.write_bytes(b'package main;\nmain :: proc() { missing() }\n')
            before=self.source.read_bytes()
            self.call(engine,'strip-semicolon',status=1)
            self.assertEqual(self.source.read_bytes(),before)

    def test_vet_checks_tests_by_building_without_running_its_validation_probes(self):
        self.write('''package main
import "core:os"
import "core:testing"
main :: proc() {}
@(test) smoke :: proc(t: ^testing.T) {
    _=t
    value := 1
    if value!=1 { panic("values") }
    err := os.write_entire_file("executed-tests.txt", "ok")
    if err!=nil { panic("write") }
}
''')
        for engine in ['python','rust']:
            marker=self.app/'executed-tests.txt';marker.unlink(missing_ok=True)
            result=self.call(engine,'test','--vet=immutable')
            self.assertIn("Declare 'value' immutable",result.stderr)
            self.assertEqual(marker.read_text(),'ok')

    def test_collection_documentation_and_mapped_errors_remain_source_facing(self):
        library=self.root/'libraries'/'lib';library.mkdir(parents=True)
        source=library/'lib.thor';source.write_text('package lib\nanswer :: fn() -> int { return 42 }\n')
        self.write('package main\nmain :: proc() { missing() }\n')
        for engine in ['python','rust']:
            result=self.call(engine,'doc','local:lib','-collection:local='+str(library.parent))
            self.assertIn('answer :: fn()',result.stdout)
            self.assertIn('lib.thor',result.stdout)
            result=self.call(engine,'check','-show-timings','-json-errors',status=1)
            packet=json.loads(result.stderr)
            self.assertEqual(packet['error_count'],1)
            self.assertIn('Undeclared name',packet['errors'][0]['msgs'][0])
            self.assertEqual(packet['errors'][0]['pos']['file'],str(self.source))

    def test_android_bundle_delegates_without_transpiling(self):
        fake=self.root/'fake-odin'
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        fake.chmod(0o755)
        for engine in ['python','rust']:
            result=self.call(engine,'bundle','android','--odin',fake,'-android-keystore:key',cwd=self.root)
            self.assertEqual(result.stdout.splitlines(),['bundle','android','.','-android-keystore:key'])
            self.call(engine,'bundle','ios',cwd=self.root,status=2)
            self.assertFalse((self.root/'.thor-cache').exists())

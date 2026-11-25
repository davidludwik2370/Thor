"""Exercise a staged native toolchain with Python and Cargo unavailable.

The Python reference remains available as thor-python. Installing the prebuilt
Rust executable as thor lets existing project tasks keep their command paths.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests import test_rust_compiler as native


class NativeTestSetupTests(unittest.TestCase):
    def test_prebuilt_setup_keeps_the_published_executable_in_place(self):
        with tempfile.TemporaryDirectory(prefix='thor-prebuilt-setup-') as directory:
            binary=Path(directory)/'compiler'
            binary.write_bytes(b'prebuilt fixture')
            with patch.object(native,'RUST',binary), patch.dict(os.environ,{'THOR_TEST_PREBUILT':'1'}), \
                    patch.object(native.subprocess,'run') as build:
                native.RustCompilerTests.setUpClass()
                from tests import test_migrate as migration
                with patch.object(migration,'COMPILER',binary):
                    migration.MigrationTests.setUpClass()
            build.assert_not_called()
            self.assertEqual(binary.read_bytes(),b'prebuilt fixture')

    def test_missing_prebuilt_compiler_is_an_error(self):
        with tempfile.TemporaryDirectory(prefix='thor-prebuilt-missing-') as directory:
            with patch.object(native,'RUST',Path(directory)/'missing'), \
                    patch.dict(os.environ,{'THOR_TEST_PREBUILT':'1'}), \
                    patch.object(native.subprocess,'run') as build:
                with self.assertRaisesRegex(RuntimeError,'Prebuilt test compiler is missing'):
                    native.RustCompilerTests.setUpClass()
            build.assert_not_called()

    def test_ordinary_setup_still_builds_the_compiler(self):
        with patch.dict(os.environ,{'THOR_TEST_PREBUILT':'0'}), \
                patch.object(native.shutil,'which',return_value='cargo'), \
                patch.object(native.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as build:
            native.RustCompilerTests.setUpClass()
        build.assert_called_once()
        self.assertIn('--release',build.call_args.args[0])


@unittest.skipIf(os.name == 'nt', 'POSIX installation and dependency sentinels')
class RustInstallationTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-native-install-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.toolchain = self.root/'toolchain'
        self.toolchain.mkdir()
        self.compiler = self.toolchain/'thor'
        shutil.copy2(native.RUST, self.compiler)
        shutil.copy2(native.ROOT/'mkthor', self.toolchain/'mkthor')
        for name in ('frontend', 'runtime', 'templates'):
            shutil.copytree(native.ROOT/name, self.toolchain/name)
        blocked = self.root/'blocked-tools'
        blocked.mkdir()
        self.log = self.root/'unexpected-dependency.log'
        for name in ('python', 'python3', 'cargo', 'rustc'):
            sentinel = blocked/name
            sentinel.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$THOR_TEST_DEPENDENCY_LOG"\nexit 97\n')
            sentinel.chmod(0o755)
        self.environment = {
            **os.environ,
            'THOR_ROOT': str(self.toolchain),
            'PATH': str(blocked)+os.pathsep+os.environ.get('PATH', ''),
            'THOR_TEST_DEPENDENCY_LOG': str(self.log),
        }
        self.app = self.root/'sample'

    def invoke(self, *arguments, cwd=None):
        result = subprocess.run([str(self.compiler), *arguments], cwd=cwd or self.root,
                                env=self.environment, capture_output=True, text=True,
                                timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else '')
        return result

    def write_program(self):
        self.invoke('new', 'sample')
        (self.app/'sample.thor').write_text('''package main
import "thor:basic" / print
import "core:slice"
twice :: fn(n:int) -> int {
    values := make([]int, 2)
    values[0] = n
    return values[0]*2
}
VALUE :: #run twice(21)
main :: proc() {
    values := [x*x for x in 0..<3]
    defer delete(values)
    view :> values
    copy := slice.clone(view)
    defer delete(copy)
    copy[0] = 9
    if VALUE != 42 || view[0] != 0 || copy[0] != 9 { panic("native installation") }
    number: Integer = 123456789012345678901234567890
    defer delete(number)
    print(f"native {VALUE}: {number}")
}
''')

    def test_private_odin_is_used_for_builds_and_queries_without_global_path_or_environment(self):
        odin=shutil.which('odin')
        self.assertIsNotNone(odin)
        sdk=subprocess.run([odin,'root'],capture_output=True,text=True,check=True).stdout.strip()
        (self.toolchain/'odin').symlink_to(Path(sdk),target_is_directory=True)
        blocked=self.root/'blocked-tools'
        sentinel=blocked/'odin'
        sentinel.write_text('#!/bin/sh\nprintf "unexpected Odin PATH lookup\\n" >> "$THOR_TEST_DEPENDENCY_LOG"\nexit 97\n')
        sentinel.chmod(0o755)
        self.environment['PATH']=str(blocked)+os.pathsep+os.defpath
        for key in ('THOR_ROOT','THOR_ODIN','ODIN_ROOT','THOR_FRONTEND_ODIN'):
            self.environment.pop(key,None)
        commands=self.root/'commands';commands.mkdir()
        alias=commands/'thor';alias.symlink_to(self.compiler)
        self.compiler=alias
        queried=self.invoke('root')
        self.assertEqual(Path(queried.stdout.strip()).resolve(),self.toolchain)
        self.app.mkdir()
        source=self.app/'main.thor'
        source.write_text('package main\nimport "core:fmt"\nmain::proc(){fmt.println("private Odin OK")}\n')
        for flags in ([],native.UNCHECKED):
            result=self.invoke('run',source,*flags)
            self.assertEqual(result.stdout.strip(),'private Odin OK')
        self.invoke('report')

    @unittest.skipUnless(sys.platform=='darwin','native macOS sanitizer linker/runtime')
    def test_private_sdk_asan_uses_its_runtime_and_merges_user_linker_flags(self):
        moved=self.root/'toolchain with spaces'
        self.toolchain.rename(moved);self.toolchain=moved;self.compiler=moved/'thor'
        odin=Path(shutil.which('odin'))
        sdk=Path(subprocess.run([str(odin),'root'],capture_output=True,text=True,check=True).stdout.strip())
        symbolizer=next((p for p in [Path('/opt/homebrew/opt/llvm/bin/llvm-symbolizer'),Path('/usr/local/opt/llvm/bin/llvm-symbolizer')] if p.is_file()),None)
        if symbolizer is None:self.skipTest('LLVM resource fixture is not installed')
        resources=sorted(symbolizer.resolve().parents[1].glob('lib/clang/*/lib/darwin'))
        if not resources:self.skipTest('LLVM compiler-rt fixture is missing')
        private=self.toolchain/'odin';private.mkdir()
        shutil.copy2(odin,private/'odin')
        for name in ('base','core','vendor','shared'):
            if (sdk/name).exists():(private/name).symlink_to(sdk/name,target_is_directory=True)
        resource=private/'clang-resource/lib';resource.mkdir(parents=True)
        (resource/'darwin').symlink_to(resources[-1],target_is_directory=True)
        (private/'libexec').mkdir();(private/'libexec/llvm-symbolizer').symlink_to(symbolizer)
        shutil.copy2(native.ROOT/'llvm-symbolizer-thor',self.toolchain/'llvm-symbolizer-thor')
        self.environment['PATH']=str(self.root/'blocked-tools')+os.pathsep+os.defpath
        for key in ('THOR_ROOT','THOR_ODIN','ODIN_ROOT','THOR_FRONTEND_ODIN'):self.environment.pop(key,None)
        self.app.mkdir();source=self.app/'main.thor';program=self.app/'program'
        source.write_text('package main\nimport "core:fmt"\nmain::proc(){values:=make([]int,8);delete(values);fmt.println(values[2])}\n')
        for flags in ([],native.UNCHECKED):
            self.invoke('build',source,'-debug','--track-allocations=off','-extra-linker-flags:-Wl,-headerpad_max_install_names',
                        '--output-dir',self.root/'asan-generated','-out:'+str(program),*flags)
            manifest=json.loads((self.root/'asan-generated/thor-manifest.json').read_text())
            linker=[f for f in manifest['odin_flags'] if f.startswith('-extra-linker-flags:')]
            self.assertEqual(len(linker),1)
            self.assertIn('-Wl,-headerpad_max_install_names',linker[0]);self.assertIn('clang-resource',linker[0])
            result=subprocess.run([str(program)],env={**self.environment,'ASAN_OPTIONS':'abort_on_error=0'},capture_output=True,text=True,timeout=30)
            self.assertNotEqual(result.returncode,0,result.stderr)
            self.assertIn(str(source),result.stderr)

    def test_default_output_cannot_replace_a_launcher_through_a_filesystem_alias(self):
        self.app.mkdir()
        (self.app/'main.thor').write_text('package main\nmain :: proc() {}\n')
        alias = self.toolchain/self.toolchain.name
        os.link(self.compiler, alias)
        original = self.compiler.read_bytes()
        for flags in ([], native.UNCHECKED):
            with self.subTest(flags=flags):
                result = subprocess.run([str(self.compiler), 'build', str(self.app), *flags],
                                        cwd=self.toolchain, env=self.environment,
                                        capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
                self.assertIn('would overwrite a Thor launcher', result.stderr)
                self.assertEqual(self.compiler.read_bytes(), original)
                self.assertEqual(alias.read_bytes(), original)

    def test_native_named_thor_runs_builds_and_uses_the_project_debug_task(self):
        self.write_program()
        expected = 'native 42: 123456789012345678901234567890\n'
        for flags in (['-debug'], native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result = self.invoke('run', '.', *native.runtime_flags(flags), cwd=self.app)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, '')
        self.invoke('build', '.', cwd=self.app)
        executable = self.app/self.app.name
        run = subprocess.run([str(executable)], env=self.environment,
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, expected)
        task = json.loads((self.app/'.vscode/tasks.json').read_text())['tasks'][0]
        self.assertEqual(task['command'], str(self.compiler))
        args = [arg.replace('${workspaceFolder}', str(self.app)) for arg in task['args']]
        self.invoke(*args, cwd=self.app)
        manifest = json.loads((self.app/'.vscode/bin/generated/thor-manifest.json').read_text())
        self.assertEqual(manifest['engine'], 'rust')
        self.assertTrue(list((self.app/'.vscode/bin/generated').rglob('*.map.json')))

    def test_native_named_thor_checks_tests_vets_and_publishes(self):
        self.write_program()
        (self.app/'sample_test.thor').write_text('''package main
import "core:testing"
@(test)
works :: proc(t:^testing.T) { testing.expect(t, VALUE == 42) }
''')
        self.invoke('check', '.', cwd=self.app)
        self.invoke('test', '.', cwd=self.app)
        self.invoke('vet', '.', cwd=self.app)
        self.invoke('publish', '.', cwd=self.app)
        if sys.platform == 'darwin':
            self.assertTrue((self.app/'sample.app/Contents/MacOS/sample').is_file())
        self.assertFalse(self.log.exists())

    def test_mkthor_uses_the_installed_compiler_without_python(self):
        result = subprocess.run([str(self.toolchain/'mkthor'), 'sample',
                                 '--parent', str(self.root), '--no-open'],
                                env=self.environment, cwd=self.root,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((self.app/'sample.thor').read_bytes(),
                         (self.toolchain/'templates/main.thor').read_bytes())
        self.assertFalse(self.log.exists())

    def test_native_launcher_uses_prebuilt_compiler_and_reports_missing_build(self):
        launchers = [self.toolchain/'thor', self.toolchain/'thor-rust']
        for launcher in launchers:
            shutil.copy2(native.ROOT/launcher.name, launcher)
        binary = self.toolchain/'rust/target/release/thor-rust'
        binary.parent.mkdir(parents=True)
        shutil.copy2(native.RUST, binary)
        for launcher in launchers:
            name = 'sample-'+launcher.name
            for command in (['--version'], ['new', name]):
                result = subprocess.run([str(launcher), *command], env=self.environment,
                                        cwd=self.root, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertEqual((self.root/name/(name+'.thor')).read_bytes(),
                             (self.toolchain/'templates/main.thor').read_bytes())
            help_result = subprocess.run([str(launcher), 'help'], env=self.environment,
                                         cwd=self.root, capture_output=True, text=True, timeout=10)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn('thor help', help_result.stdout)
        binary.unlink()
        for launcher in launchers:
            missing = subprocess.run([str(launcher), '--version'], env=self.environment,
                                     cwd=self.root, capture_output=True, text=True, timeout=10)
            self.assertEqual(missing.returncode, 127, missing.stderr)
            self.assertIn('cargo build --release', missing.stderr)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()


class RelocatedRuntimeIdentityTests(unittest.TestCase):
    """Review contracts must follow the installed support collection, not Cargo."""
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def test_relocated_runtime_callbacks_types_and_comptime(self):
        with tempfile.TemporaryDirectory(prefix='thor relocated runtime ') as directory:
            root=Path(directory).resolve()
            install=root/'installation';install.mkdir()
            compiler=install/('thor.exe' if os.name=='nt' else 'thor')
            shutil.copy2(native.RUST,compiler)
            for name in ('frontend','runtime','templates'):
                shutil.copytree(native.ROOT/name,install/name)
            app=root/'project';app.mkdir()
            (app/'main.thor').write_text('''package main
import "thor:basic"
import "thor:parallel"
import "core:fmt"
import sets "thor:sets"
twice::proc(x:$T)->T{return x*2}
runtime_callback:proc(int)->int=twice
compute::fn(x:int)->int{
    rows:=parallel.pmap([]int{x,x+1},twice,workers=2)
    return rows[1]
}
VALUE::#run compute(7)
@(no_alloc) show::proc(){basic.print(VALUE)}
main::proc(){
    rows:=parallel.pmap([]int{2,3},twice,workers=2);defer delete(rows)
    runtime_rows:=parallel.pmap([]int{4,5},runtime_callback,workers=2);defer delete(runtime_rows)
    named:=basic.pmap([]int{6},twice);defer delete(named)
    view:>[]int{1,2}
    basic.print(view)
    if rows[1]!=6||runtime_rows[1]!=10||named[0]!=12||VALUE!=16{panic("relocated callback")}
    unique:=sets.to_set([]int{2,2,3});defer delete(unique)
    if len(unique)!=2||3 not_in unique{panic("relocated set type")}
    huge:Integer=123456789012345678901234567890;defer delete(huge)
    show();fmt.println(f"{huge}")
}
''',encoding='utf8',newline='')
            env={**os.environ};env.pop('THOR_ROOT',None)
            for flags in ([],native.UNCHECKED):
                with self.subTest(flags=flags):
                    result=subprocess.run([str(compiler),'run',str(app),'-thread-count:1',*flags],
                        env=env,cwd=root,capture_output=True,text=True,timeout=90)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertEqual(result.stdout.strip(),'[1, 2]\n16\n123456789012345678901234567890')
            # A user collection override must not inherit scheduler privileges.
            custom=root/'custom';(custom/'parallel').mkdir(parents=True)
            (custom/'parallel/pmap.thor').write_text('''package parallel
state:int
pmap::proc(value:int)->int{state+=1;return value}
''',encoding='utf8')
            (app/'main.thor').write_text('''package main
import "thor:parallel"
compute::fn(value:int)->int{return parallel.pmap(value)}
main::proc(){_=compute(1)}
''',encoding='utf8')
            rejected=subprocess.run([str(compiler),'check',str(app),'-thread-count:1',
                '-collection:thor='+str(custom)],env=env,cwd=root,capture_output=True,text=True,timeout=90)
            self.assertNotEqual(rejected.returncode,0,rejected.stdout+rejected.stderr)
            self.assertIn("runtime storage 'state' outside the function",rejected.stderr.lower())

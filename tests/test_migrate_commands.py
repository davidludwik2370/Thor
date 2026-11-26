"""Command translation is source independent and executable after migration."""
from tests.native_paths import rust_binary, directory_alias
from tests.test_rust_compiler import compiler_paths
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest

from tests import test_migrate as support
from tests import test_native_vet as policies

ROOT=support.ROOT
COMPILER=support.COMPILER


class MigrationCommandTests(unittest.TestCase):
    setUpClass=classmethod(support.MigrationTests.setUpClass.__func__)
    setUp=support.MigrationTests.setUp
    write=support.MigrationTests.write
    snapshot=support.MigrationTests.snapshot
    migrate=support.MigrationTests.migrate

    def invoke(self,*args,status=0,env=None):
        result=subprocess.run([str(COMPILER),'migrate',*map(str,args)],cwd=self.root,
            capture_output=True,text=True,env=env,timeout=120)
        self.assertEqual(result.returncode,status,result.stdout+result.stderr)
        return result

    def execute(self,line,status=0):
        arguments=shlex.split(line)
        self.assertEqual(arguments.pop(0),'thor')
        result=subprocess.run([str(COMPILER),*arguments],cwd=self.root,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,status,result.stdout+result.stderr)
        return result

    def test_command_only_batch_needs_neither_sources_nor_an_odin_installation(self):
        before=set(self.root.iterdir())
        environment={**os.environ,'PATH':'','THOR_ODIN':'missing-odin'}
        result=self.invoke('--command','odin build ./missing -o:speed',
            '--command=odin test ./missing -define:MODE=2',env=environment)
        self.assertEqual(result.stdout,'thor build ./missing -o:speed --odin-compatible\nthor test ./missing -define:MODE=2 --odin-compatible\n')
        self.assertEqual(result.stderr,'')
        self.assertEqual(set(self.root.iterdir()),before)

    def test_absolute_windows_paths_are_literal_in_command_only_translation(self):
        command=r'odin build C:\work\game -out:C:\build\game.exe -collection:local=C:\shared'
        result=self.invoke('--command',command)
        arguments=shlex.split(result.stdout)
        self.assertEqual(arguments,['thor','build',r'C:\work\game',r'-out:C:\build\game.exe',
            r'-collection:local=C:\shared','--odin-compatible'])

    def test_windows_executable_extension_case_is_preserved(self):
        compiler=r'C:\tools\Odin\odin.EXE'
        result=self.invoke('--command',f'"{compiler}" build .')
        self.assertEqual(shlex.split(result.stdout),['thor','build','.','--odin-compatible','--odin',compiler])

    @unittest.skipUnless(os.name=='nt','Native Windows UNC command syntax')
    def test_unc_paths_are_literal_without_contacting_the_share(self):
        result=self.invoke('--command',r'odin build "\\server\share with spaces\game"')
        self.assertEqual(shlex.split(result.stdout),['thor','build',r'\\server\share with spaces\game','--odin-compatible'])

    def test_literal_quoting_empty_arguments_unicode_and_runtime_boundary(self):
        command='odin run "my game/main.odin" -file -define:TEXT=\'hello "🌩" $HOME $(touch marker)\' -- "" "two words" --help "a\\q"'
        result=self.invoke('--command',command)
        actual=shlex.split(result.stdout)
        original=shlex.split(command)
        original[0]='thor'
        original[2]='my game/main.thor'
        original.insert(original.index('--'),'--odin-compatible')
        self.assertEqual(actual,original)
        self.assertFalse((self.root/'marker').exists())

    def test_invalid_batch_is_atomic_and_shell_evaluation_is_rejected(self):
        for invalid in ['', 'echo odin build .','odin unknown .','odin build',
                'odin build . && touch marker','odin build "$(touch marker)"',
                'odin build .\nodin test .',"odin build 'unfinished",'odin build *.odin']:
            with self.subTest(command=invalid):
                result=self.invoke('--command','odin build .','--command',invalid,status=2)
                self.assertEqual(result.stdout,'')
                self.assertFalse((self.root/'marker').exists())

    def test_command_only_options_require_an_explicit_project_for_output_paths(self):
        self.invoke('--command','odin build .','--output-dir',self.root/'out',status=2)
        self.invoke('--command',status=2)

    def test_explicit_odin_executable_and_native_policy_mapping(self):
        result=self.invoke('--command','"/custom toolchain/odin" build . -vet --ignored',status=2)
        self.assertIn('native Odin compiler flag',result.stderr)
        result=self.invoke('--command','"/custom toolchain/odin" build . -vet')
        self.assertEqual(shlex.split(result.stdout),['thor','build','.','-vet','--odin-compatible','--odin','/custom toolchain/odin'])

    def test_output_directory_contains_the_project_and_reports_stay_on_stderr(self):
        self.write('package main\nmain::proc(){}\n')
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',f'odin build {shlex.quote(str(self.source))} -o:speed')
        self.assertTrue((self.output/'main.thor').is_file())
        self.assertFalse((self.output.parent/'main.thor').exists())
        self.assertEqual(shlex.split(result.stdout),['thor','build',str(self.output),'-o:speed','--odin-compatible'])
        self.assertIn('Migrated',result.stderr)
        self.execute(result.stdout)

    def test_relative_paths_collections_defines_outputs_and_multiple_commands(self):
        self.write('''package main
import "custom:values"
import "core:fmt"
import "core:testing"
main::proc(){fmt.println(values.fn)}
@(test) smoke::proc(t:^testing.T){testing.expect(t,values.fn==7)}
''',{'packages/values/lib.odin':'package values\nfn::#config(VALUE,7)\n'})
        flags='-collection:custom=original/sample/packages -define:VALUE=7'
        suffix='.exe' if os.name=='nt' else ''
        build=f'odin build ./original/sample -o:speed {flags} -out:original/sample/program{suffix}'
        test=f'odin test ./original/sample {flags}'
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',build,'--command',test)
        lines=result.stdout.splitlines()
        self.assertEqual(len(lines),2)
        normalized=lines[0].replace('\\','/')
        self.assertIn('./converted/sample',normalized)
        self.assertIn('-collection:custom=converted/sample/packages',normalized)
        self.assertIn('-out:converted/sample/program',normalized)
        self.execute(lines[0])
        self.execute(lines[1])
        run=subprocess.run([str(self.output/('program'+suffix))],capture_output=True,text=True,timeout=10)
        self.assertEqual((run.returncode,run.stdout),(0,'7\n'),run.stderr)

    def test_file_command_and_runtime_arguments_use_the_copied_source(self):
        self.write('package main\nimport "core:fmt"\nimport "core:os"\nmain::proc(){fmt.println(os.args[1:])}\n')
        command=f'odin run {self.source}/main.odin -file -- "two words" --help'
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',command)
        self.assertIn('/main.thor',result.stdout.replace('\\','/'))
        actual=self.execute(result.stdout)
        self.assertIn('two words',actual.stdout)
        self.assertIn('--help',actual.stdout)

    def test_collection_parent_roots_rebase_the_entry_and_only_owned_imports(self):
        self.write('''package main
import "local:sample/inside"
import "local:external"
import linked "local:sample/linked"
import "core:fmt"
main::proc(){fmt.println(inside.value+external.value+linked.value)}
''',{'inside/lib.odin':'package inside\nvalue::7\n'})
        external=self.source.parent/'external'
        external.mkdir()
        (external/'lib.odin').write_text('package external\nvalue::2\n')
        linked=self.source.parent/'linked-library'
        linked.mkdir()
        (linked/'lib.odin').write_text('package linked\nvalue::3\n')
        directory_alias(self.source/'linked',linked)
        command=f'odin run local:sample -collection:local={self.source.parent} -thread-count:1'
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',command)
        self.assertIn(str(self.output),result.stdout)
        self.assertIn('import "inside"',(self.output/'main.thor').read_text())
        self.assertIn('import "local:external"',(self.output/'main.thor').read_text())
        self.source.rename(self.source.with_name('original-hidden'))
        (self.output/'inside/lib.thor').write_text('package inside\nvalue::9\n')
        self.assertEqual(self.execute(result.stdout).stdout,'14\n')

    def test_shared_migration_flags_are_retained_in_emitted_commands(self):
        self.write('package main\nimport "core:fmt"\nVALUE::#config(VALUE,0)\nmain::proc(){fmt.println(VALUE)}\n')
        result=self.invoke(self.source,'--output-dir',self.output.parent,
            '-collection:local=..','-define:VALUE=9',
            '--command','odin run local:sample -thread-count:1')
        self.assertIn('-define:VALUE=9',result.stdout)
        self.assertIn(str(self.output),result.stdout)
        self.source.rename(self.source.with_name('original-hidden'))
        self.assertEqual(self.execute(result.stdout).stdout,'9\n')

    def test_editor_tasks_preserve_toolchain_paths_and_runtime_arguments(self):
        import shutil
        odin=shutil.which('odin')
        arguments=['run','main.odin','-file','-thread-count:1','--','main.odin','-vet']
        tasks={'version':'2.0.0','tasks':[
            {'label':'process','type':'process','command':odin,'args':arguments},
            {'label':'shell','type':'shell','command':shlex.join([odin,*arguments])}]}
        self.write('package main\nimport "core:fmt"\nimport "core:os"\nmain::proc(){fmt.println(os.args[1:])}\n',
            {'.vscode/tasks.json':json.dumps(tasks)})
        native=subprocess.run([odin,*arguments],cwd=self.source,capture_output=True,text=True,timeout=45)
        self.assertEqual(native.returncode,0,native.stderr)
        self.migrate()
        process,shell=json.loads((self.output/'.vscode/tasks.json').read_text())['tasks']
        self.assertIn('--odin-compatible',process['args'])
        self.assertEqual(process['args'][process['args'].index('--odin')+1],odin)
        self.assertEqual(process['args'][process['args'].index('--')+1:],['main.odin','-vet'])
        for command in [[str(COMPILER),*process['args']],
                shlex.split(shell['command'].replace('thor ',shlex.quote(str(COMPILER))+' ',1))]:
            actual=subprocess.run(command,cwd=self.output,capture_output=True,text=True,timeout=60)
            self.assertEqual((actual.returncode,actual.stdout),(0,native.stdout),actual.stderr)

    def test_other_native_file_commands_keep_their_original_source_paths(self):
        self.write('package main\nmain::proc(){}\n')
        other=self.root/'other.odin'
        other.write_text('package main\nmain::proc(){}\n')
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',f'odin build {other} -file -thread-count:1')
        self.assertIn(str(other),result.stdout)
        self.execute(result.stdout)

    def test_translated_build_retains_native_default_output_and_accepts_thor_features(self):
        self.write('package main\nmain::proc(){}\n')
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',f'odin build {self.source} -thread-count:1')
        (self.output/'main.thor').write_text('''package main
import "core:fmt"
double::fn(x:int)->int{return x*2}
main::proc(){fmt.println(f"answer {double(7)}")}
''')
        self.execute(result.stdout)
        binary=self.root/('sample.exe' if os.name=='nt' else 'sample')
        self.assertTrue(binary.is_file())
        actual=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
        self.assertEqual((actual.returncode,actual.stdout),(0,'answer 14\n'),actual.stderr)

    def test_file_input_output_name_matches_odin_and_directory_extensions_are_preserved(self):
        self.write('package main\nmain::proc(){}\n')
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',f'odin build {self.source}/main.odin -file -thread-count:1')
        self.execute(result.stdout)
        self.assertTrue((self.root/('main.exe' if os.name=='nt' else 'main')).is_file())
        directory=self.invoke('--command','odin build ./project.odin -o:speed').stdout
        self.assertIn('./project.odin',directory)

    def test_native_output_names_trim_whitespace_and_avoid_adjacent_file_directories(self):
        self.write('package main\nmain::proc(){}\n')
        spaced=self.source.with_name(' spaced')
        self.source.rename(spaced)
        self.source=spaced
        self.output=self.output.with_name(spaced.name)
        (self.source/'main').mkdir()
        commands=[shlex.join(['odin','build',str(self.source),'-thread-count:1']),
            shlex.join(['odin','build',str(self.source/'main.odin'),'-file','-thread-count:1'])]
        outputs=[self.root/('spaced.exe' if os.name=='nt' else 'spaced'),self.root/('main.exe' if os.name=='nt' else 'main.bin')]
        for command,output in zip(commands,outputs):
            native=subprocess.run(shlex.split(command),cwd=self.root,capture_output=True,text=True,timeout=45)
            self.assertEqual(native.returncode,0,native.stderr)
            self.assertTrue(output.is_file(),str(output))
            output.unlink()
        result=self.invoke(self.source,'--output-dir',self.output.parent,
            *[argument for command in commands for argument in ['--command',command]])
        for line,output in zip(result.stdout.splitlines(),outputs):
            self.execute(line)
            self.assertTrue(output.is_file(),str(output))

    def test_default_library_object_and_assembly_outputs_match_native_names(self):
        self.write('package main\n@(export) exported::proc()->int{return 7}\nmain::proc(){}\n')
        self.migrate()
        extensions=[('dll','dll'),('static','lib'),('obj','obj'),('assembly','S')] if os.name=='nt' else [('dll','dylib' if os.uname().sysname=='Darwin' else 'so'),('static','a'),('obj','o'),('assembly','S')]
        for mode,extension in extensions:
            with self.subTest(mode=mode):
                flags=[f'-build-mode:{mode}','-use-single-module','-thread-count:1']
                control=self.root/f'native-control.{extension}'
                baseline=subprocess.run([os.environ.get('THOR_ODIN','odin'),'build',str(self.source),
                    *flags,'-out:'+str(control)],cwd=self.root,capture_output=True,text=True,timeout=60)
                command=shlex.join(['odin','build',str(self.output),*flags])
                result=self.invoke('--command',command)
                translated=self.execute(result.stdout,status=baseline.returncode)
                if baseline.returncode==0:
                    self.assertTrue(control.is_file())
                    self.assertTrue((self.root/f'sample.{extension}').is_file())
                else:
                    # Some SDK/linker combinations cannot produce a given native
                    # artifact. Preserve the failure instead of treating a Thor
                    # invocation as a successful library build.
                    self.assertIn('Error',baseline.stderr+baseline.stdout)
                    self.assertIn('Error',translated.stderr+translated.stdout)

    def test_attribute_values_still_lower_thor_constant_expressions(self):
        self.write('package main\nmain::proc(){}\n')
        self.migrate()
        (self.output/'main.thor').write_text('''package main
@(export, link_name="native_"+"fn"*2)
work::proc()->int{return 7}
main::proc(){if work()!=7{panic("attribute value")}}
''')
        for compiler in compiler_paths(COMPILER):
            result=subprocess.run([str(compiler),'run',str(self.output),'-thread-count:1'],
                cwd=self.root,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_tests_only_project_migrates_and_test_command_runs_checked_and_unchecked(self):
        self.write('''package sample
import "core:testing"
@(test) smoke::proc(t:^testing.T){or_panic:=7;testing.expect(t,or_panic==7)}
''')
        (self.source/'main.odin').rename(self.source/'main_test.odin')
        self.migrate()
        self.assertTrue((self.output/'main_test.thor').exists())
        for flags in [[],support.UNCHECKED]:
            command=' '.join(['odin','test',shlex.quote(str(self.source)),*flags])
            translation=self.invoke('--command',command)
            translated=translation.stdout.replace(str(self.source),str(self.output))
            self.execute(translated)

    def test_test_only_command_validates_without_running_the_tests(self):
        self.write('''package main
import "core:testing"
import "core:os"
@(test) marker::proc(t:^testing.T){
 _=t
 err:=os.write_entire_file("TEST_RAN","yes")
 if err!=nil{panic("marker")}
}
''')
        (self.source/'main.odin').rename(self.source/'main_test.odin')
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',f'odin test {self.source}')
        self.assertFalse((self.source/'TEST_RAN').exists())
        self.assertFalse((self.root/'TEST_RAN').exists())
        self.execute(result.stdout)
        self.assertEqual((self.root/'TEST_RAN').read_text(),'yes')

    def test_native_reports_are_redirected_during_validation(self):
        self.write('package main\nVALUE::#config(VALUE,7)\nmain::proc(){_=VALUE}\n',{'definitions.csv':'keep original'})
        before=self.snapshot()
        command=f'odin build {self.source} -export-defineables:{self.source}/definitions.csv'
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',command)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual((self.output/'definitions.csv').read_text(),'keep original')
        self.execute(result.stdout)
        self.assertNotEqual((self.output/'definitions.csv').read_text(),'keep original')
        self.assertEqual(self.snapshot(),before)

    def test_native_vet_policies_keep_original_success_and_failure_behavior(self):
        self.write('package main\nmain :: proc() {\n\tx := 7\n\t_ = x\n}\n')
        self.migrate()
        for policy,(violation,fragment) in policies.VIOLATIONS.items():
            with self.subTest(policy=policy):
                native_flags=['-'+policy,'-thread-count:1']
                if policy=='vet-unused-procedures':native_flags.append('-vet-packages:main')
                translation=self.invoke('--command',' '.join(['odin','check',shlex.quote(str(self.output)),*native_flags])).stdout
                (self.output/'main.thor').write_text('package main\nmain :: proc() {\n\tx := 7\n\t_ = x\n}\n')
                self.execute(translation)
                (self.output/'main.thor').write_text(violation)
                failed=self.execute(translation,status=1)
                self.assertIn(fragment,failed.stderr)

    def test_command_native_vet_builds_with_checked_and_unchecked_flags(self):
        self.write('package main\nimport "core:fmt"\nmain :: proc() {\n\tvalues := [2]int{3,4}\n\ti := 1\n\tfmt.println(values[i])\n}\n')
        commands=[f'odin build {self.source} -vet -thread-count:1 '+ ' '.join(flags) for flags in [[],support.UNCHECKED]]
        result=self.invoke(self.source,'--output-dir',self.output.parent,*[argument for command in commands for argument in ['--command',command]])
        for line in result.stdout.splitlines():self.execute(line)

    def test_native_checks_cover_source_metadata_and_generated_helper_hygiene(self):
        self.write('''package main
low, high, middle, index, row, result, loc, procedure :: 0,0,0,0,0,0,0,0
observe :: proc(x: int, text := #caller_expression(x), location := #caller_location) {
	_ = x
	_ = text
	_ = location
}
main :: proc() {
	defer { _ = #branch_location }
	observe(7)
	_ = low+high+middle+index+row+result+loc+procedure
}
''')
        for flags in ['-vet -vet-tabs','-vet-shadowing','-strict-style']:
            with self.subTest(flags=flags):
                if self.output.exists():
                    import shutil
                    shutil.rmtree(self.output)
                suffix='.exe' if os.name=='nt' else ''
                command=f'odin build {self.source} {flags} -thread-count:1 -out:{self.root}/metadata-program{suffix}'
                result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',command)
                self.execute(result.stdout)

    def test_custom_attribute_keys_keep_reserved_spellings(self):
        self.write('''package main
@(fn, set, or_panic, Integer, Rational)
work::proc()->int{return 7}
main::proc(){_=work()}
''')
        command=f'odin build {self.source} -custom-attribute:fn,set,or_panic,Integer,Rational'
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',command)
        self.assertIn('@(fn, set, or_panic, Integer, Rational)',(self.output/'main.thor').read_text())
        self.execute(result.stdout)

    def test_external_reserved_fields_enum_values_and_named_arguments(self):
        external=self.root/'external'
        (external/'values').mkdir(parents=True)
        (external/'values/lib.odin').write_text('''package values
Kind::enum{or_panic}
Item::struct{or_panic:int}
make::proc(or_panic:int)->Item{return {or_panic}}
''')
        self.write('''package main
import "custom:values"
main::proc(){
 first:=values.make(or_panic=7)
 second:=values.Item{or_panic=first.or_panic}
 kind:values.Kind=.or_panic
 if second.or_panic!=7 || kind!=.or_panic{panic("external names")}
}
''')
        command=f'odin run {self.source} -collection:custom={external}'
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',command)
        self.execute(result.stdout)

    def test_native_package_policy_selectors_keep_original_package_names(self):
        # A test package permits an arbitrary package name without an entry point.
        self.write('package or_panic\nimport "core:testing"\n@(test) smoke::proc(t:^testing.T){testing.expect(t,true)}\n')
        command=f'odin test {self.source} -vet -vet-packages:or_panic -thread-count:1'
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',command)
        self.assertIn('package or_panic',(self.output/'main.thor').read_text())
        self.execute(result.stdout)

    @unittest.skipUnless(os.name=='posix','POSIX directory aliases')
    def test_project_directory_alias_maps_to_its_own_copied_folder(self):
        self.write('package main\nimport "core:fmt"\nmain::proc(){fmt.println(ODIN_BUILD_PROJECT_NAME)}\n')
        alias=self.root/'alias'
        alias.symlink_to(self.source,target_is_directory=True)
        suffix='.exe' if os.name=='nt' else ''
        command=f'odin run {alias} -thread-count:1 -out:{self.root}/alias-program{suffix}'
        native=subprocess.run(shlex.split(command),cwd=self.root,capture_output=True,text=True,timeout=45)
        self.assertEqual(native.returncode,0,native.stderr)
        result=self.invoke(alias,'--output-dir',self.output.parent,'--command',command)
        self.assertTrue((self.output/'main.thor').exists())
        self.assertIn('/converted/sample',result.stdout)
        self.assertEqual(self.execute(result.stdout).stdout,native.stdout)

    def test_all_packages_test_configuration_and_compiler_modes(self):
        self.write('''package main
import "lib"
import "core:testing"
main::proc(){_=lib.value}
@(test) root_test::proc(t:^testing.T){testing.expect(t,lib.value==7)}
''',{'lib/lib.odin':'package lib\nvalue::#config(VALUE,7)\n',
            'lib/lib_test.odin':'package lib\nimport "core:testing"\n@(test) library_test::proc(t:^testing.T){testing.expect(t,value==7)}\n'})
        command=f'odin test {self.source} -all-packages -define:VALUE=7 -define:ODIN_TEST_THREADS=1'
        result=self.invoke(self.source,'--output-dir',self.output.parent,'--command',command)
        actual=self.execute(result.stdout)
        self.assertIn('Finished',actual.stdout+actual.stderr)
        for flags in ['-build-mode:obj -use-single-module','-build-mode:dll','-bedrock','-no-rtti -disable-init-fini -disable-non-constant-globals','-disallow-do']:
            with self.subTest(flags=flags):
                (self.output/'main.thor').write_text('package main\n@(export) exported::proc()->int{return 7}\nmain::proc(){}\n')
                suffix=('.dll' if '-build-mode:dll' in flags else '.obj' if '-build-mode:obj' in flags else '.exe') if os.name=='nt' else ''
                command=f'odin build {self.output} {flags} -out:{self.root}/mode-output{suffix}'
                translated=self.invoke('--command',command).stdout
                native_source=self.source/'main.odin'
                native_source.write_text((self.output/'main.thor').read_text())
                native=subprocess.run(['odin','build',str(self.source),*shlex.split(flags),'-out:'+str(self.root/('native-mode-output'+suffix))],
                    cwd=self.root,capture_output=True,text=True,timeout=45)
                self.execute(translated,status=0 if native.returncode==0 else 1)

    @unittest.skipUnless(COMPILER==rust_binary(),'Reference launcher uses the published release executable')
    def test_python_launcher_uses_the_same_source_independent_translation(self):
        result=subprocess.run([str(ROOT/'thor-python'),'migrate','--command','odin test ./missing -o:speed'],
            cwd=self.root,capture_output=True,text=True,timeout=10)
        self.assertEqual((result.returncode,result.stdout),(0,'thor test ./missing -o:speed --odin-compatible\n'),result.stderr)

"""Real Odin-to-Thor conversions preserve source trees and executable behavior."""
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest

from tests.native_paths import rust_binary, native_test_compiler, EXE_SUFFIX, directory_aliases, file_reference
ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path(os.environ.get('THOR_MIGRATE_TEST_COMPILER', rust_binary()))
UNCHECKED = ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_rust_compiler import using_prebuilt_compiler
        if 'THOR_MIGRATE_TEST_COMPILER' not in os.environ and not using_prebuilt_compiler(COMPILER):
            build = subprocess.run(['cargo','build','--release','--locked','--offline','--manifest-path',str(ROOT/'rust/Cargo.toml')],
                                   capture_output=True,text=True,timeout=600)
            if build.returncode:
                raise RuntimeError(build.stdout+build.stderr)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-migrate-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root/'original'/'sample'
        self.source.mkdir(parents=True)
        self.output = self.root/'converted'/'sample'

    def write(self, text, files=None):
        (self.source/'main.odin').write_text(text)
        for name, value in (files or {}).items():
            path = self.source/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(value.encode() if isinstance(value,str) else value)

    def snapshot(self):
        return {str(path.relative_to(self.source)): (path.read_bytes(),stat.S_IMODE(path.stat().st_mode))
                for path in self.source.rglob('*') if path.is_file() and not path.is_symlink()}

    def migrate(self, flags=(), status=0, destination=True):
        saved = self.snapshot()
        result = subprocess.run([str(COMPILER),'migrate',str(self.source),
            *(['--output-dir',str(self.output.parent)] if destination else []),'-thread-count:1',*flags],
            cwd=self.output.parent if not destination else self.root,capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,status,result.stdout+result.stderr)
        self.assertEqual(self.snapshot(),saved)
        return result

    def compare_builds(self, flags=(), expected=None):
        binary = self.root/('original-program.exe' if os.name=='nt' else 'original-program')
        built = subprocess.run(['odin','build','.', '-thread-count:1',*flags,'-out:'+str(binary)],
            cwd=self.source,capture_output=True,text=True,timeout=45)
        self.assertEqual(built.returncode,0,built.stderr)
        baseline = subprocess.run([str(binary)],cwd=self.source,capture_output=True,text=True,timeout=10)
        built = subprocess.run([str(COMPILER),'build','.', '-thread-count:1',*flags],
            cwd=self.output,capture_output=True,text=True,timeout=60)
        self.assertEqual(built.returncode,0,built.stdout+built.stderr)
        actual = subprocess.run([str(self.output/(self.output.name+('.exe' if os.name=='nt' else '')))],cwd=self.output,capture_output=True,text=True,timeout=10)
        self.assertEqual((actual.returncode,actual.stdout),(baseline.returncode,baseline.stdout),actual.stderr)
        if expected is not None:
            self.assertEqual(actual.stdout,expected)

    def test_default_destination_assets_comments_unicode_line_endings_and_modes(self):
        text = '''package main
import "core:fmt"
// fn set or_panic Integer Rational 😀
main::proc(){fn:=7;fmt.println(fn,"fn set or_panic Integer Rational")}
'''
        text=text.replace('\n','\r\n')
        self.write(text, {'assets/data.bin':b'\x00\xff\x01','.vscode/settings.json':'{"hello":true}',
            '.git/config':'saved git data','.thor-cache/old':'cache','empty/.keep':''})
        (self.source/'main.odin').chmod(0o640)
        original_mode=stat.S_IMODE((self.source/'main.odin').stat().st_mode)
        self.output.parent.mkdir()
        self.migrate(destination=False)
        self.assertEqual((self.output/'assets/data.bin').read_bytes(),b'\x00\xff\x01')
        self.assertFalse((self.output/'.git').exists())
        self.assertFalse((self.output/'.thor-cache').exists())
        self.assertEqual(stat.S_IMODE((self.output/'main.thor').stat().st_mode),original_mode)
        migrated=(self.output/'main.thor').read_bytes()
        self.assertIn(b'// fn set or_panic Integer Rational',migrated)
        self.assertIn(b'"fn set or_panic Integer Rational"',migrated)
        self.assertEqual(migrated.count(b'\r\n'),text.count('\r\n'))
        self.compare_builds(expected='7 fn set or_panic Integer Rational\n')

    def test_all_five_names_scopes_generics_named_results_and_fresh_names(self):
        self.write('''package main
import "core:fmt"
fn_odin::99
fn_odin_2::98
fn::proc($T:typeid,set:T)->T{return set}
choose::proc(Integer:int,Rational:=2)->(or_panic:int){or_panic=Integer+Rational;return}
main::proc(){
 set:=fn(int,7)
 {set:=set+1;fmt.print(set," ")}
 fmt.println(set,choose(Integer=3,Rational=4),fn_odin,fn_odin_2)
}
''')
        report=self.migrate().stderr
        self.assertIn('fn -> fn_odin_3',report)
        for flags in [[],UNCHECKED]:
            self.compare_builds(flags,expected='8  7 7 99 98\n')

    def test_package_marker_preserves_existing_header_and_line_endings(self):
        source = ('// package decoy /*odin-bounds*/\r\n'
                  'package main // existing comment\r\n'
                  'main :: proc() {}')
        already = 'package main\t/*odin-bounds*/\r\nhelper :: proc() {}'
        (self.source / 'main.odin').write_bytes(source.encode())
        (self.source / 'helper.odin').write_bytes(already.encode())
        self.migrate()
        self.assertEqual((self.output / 'main.thor').read_bytes(),
                         source.replace('main //', 'main /*odin-bounds*/ //').encode())
        self.assertEqual((self.output / 'helper.thor').read_bytes(), already.encode())

    def test_cross_package_import_aliases_promoted_fields_enums_and_tests(self):
        self.write('''#+feature using-stmt
package main
import Integer "lib"
import Rational "core:fmt"
main::proc(){
 value:=Integer.make(Integer=3)
 using value
 Rational.println(value.or_panic,set,Integer.choose(.fn),Integer.fn(value))
}
''',{'lib/lib.odin':'''package lib
Inner::struct{set:int}
Item::struct{using inner:Inner,or_panic:int}
Kind::enum{fn,set,or_panic}
make::proc(Integer:int)->Item{return {Inner{Integer},Integer+1}}
choose::proc(value:Kind)->int{#partial switch value{case .fn:return 7;case:return 8}}
fn::proc(item:Item)->int{return item.or_panic+item.set}
''','lib/lib_test.odin':'''package lib
import "core:testing"
@(test) test_value::proc(t:^testing.T){or_panic:=choose(.fn);testing.expect(t,or_panic==7)}
'''})
        self.migrate()
        self.compare_builds(expected='4 3 7 7\n')
        result=subprocess.run([str(COMPILER),'test','lib','-thread-count:1'],cwd=self.output,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_external_members_and_implicit_enum_names_are_preserved(self):
        self.write('''package main
import "core:fmt"
import "core:mem"
import "core:reflect"
main::proc(){
 data:[3]byte
 mem.set(&data[0],7,3)
 kind:reflect.Type_Kind=.Integer
 local:=struct{Integer:int}{Integer=9}
 fmt.println(data,int(kind),local.Integer)
}
''')
        self.migrate()
        migrated=(self.output/'main.thor').read_text()
        self.assertIn('mem.set(',migrated)
        self.assertIn('=.Integer',migrated)
        self.compare_builds()

    def test_platform_files_and_inactive_when_branches_are_converted(self):
        foreign='windows' if os.name!='nt' else 'linux'
        self.write('''package main
import "core:fmt"
when ODIN_OS==.Windows{fn::7}else{fn::8}
main::proc(){fmt.println(fn)}
''',{f'other_{foreign}.odin':'''package main
unused::proc(){set:=[2]int{1,2};or_panic:=set[0];_=or_panic}
''','tagged.odin':'''#+build windows
package main
tagged::proc(){Integer:=3;_=Integer}
'''})
        self.migrate()
        self.assertIn('set_odin', (self.output/f'other_{foreign}.thor').read_text())
        self.assertIn('Integer_odin',(self.output/'tagged.thor').read_text())
        self.compare_builds()

    def test_native_vet_tags_and_project_identity(self):
        self.write('''#+vet unused style
#+build-project-name sample
package main
import "core:fmt"
main::proc(){Integer:=7;fmt.println(Integer,ODIN_BUILD_PROJECT_NAME)}
''')
        result=self.migrate()
        self.assertIn('vet tag',result.stderr)
        self.compare_builds(expected='7 sample\n')

    def test_dry_run_validates_without_creating_the_destination(self):
        self.write('package main\nmain::proc(){or_panic:=7;_=or_panic}\n')
        result=self.migrate(['--dry-run'])
        self.assertIn('Dry run passed',result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.parent.exists())

    def test_colliding_destination_filenames_are_rejected(self):
        self.write('package main\nmain::proc(){}\n',{'main.thor':'keep me'})
        result=self.migrate(status=1)
        self.assertIn('filename conflict',result.stderr)
        self.assertFalse(self.output.exists())

    def test_existing_destination_and_overlapping_paths_are_rejected(self):
        self.write('package main\nmain::proc(){}\n')
        self.output.mkdir(parents=True)
        (self.output/'keep').write_text('keep')
        result=self.migrate(status=1)
        self.assertIn('already exists',result.stderr)
        self.assertEqual((self.output/'keep').read_text(),'keep')
        self.output=self.source/'inside'
        result=self.migrate(status=1)
        self.assertIn('overlap',result.stderr)

    def test_original_compile_errors_leave_no_destination(self):
        self.write('package main\nmain::proc(){_=missing}\n')
        result=self.migrate(status=1)
        self.assertIn('Odin could not check',result.stderr)
        self.assertIn('missing',result.stderr)
        self.assertFalse(self.output.exists())

    def test_default_import_names_and_package_headers(self):
        self.write('''package main
import "or_panic"
import "set"
import "core:fmt"
main::proc(){fmt.println(or_panic.value,set.value)}
''',{'or_panic/lib.odin':'package or_panic\nvalue::7\n','set/lib.odin':'package set\nvalue::8\n'})
        self.migrate()
        text=(self.output/'main.thor').read_text()
        self.assertIn('import or_panic_odin "or_panic"',text)
        self.assertIn('import set_odin "set"',text)
        self.compare_builds(expected='7 8\n')

    def test_configuration_keys_keep_original_define_names(self):
        self.write('''package main
import "core:fmt"
Integer::#config(Integer,7)
main::proc(){fmt.println(Integer)}
''')
        self.migrate(['-define:Integer=9'])
        self.assertIn('#config(Integer,7)',(self.output/'main.thor').read_text())
        self.compare_builds(['-define:Integer=9'],expected='9\n')
        result=subprocess.run([str(COMPILER),'run','.','-define:Integer=9','-thread-count:1'],
            cwd=self.output,capture_output=True,text=True,timeout=60)
        self.assertEqual((result.returncode,result.stdout),(0,'9\n'),result.stderr)

    def test_embedded_source_paths_literals_constants_concatenation_and_generics(self):
        self.write('''package main
import "core:fmt"
DIRECT::#load("data/payload.odin",string)
PATH::"data/payload.odin"
CONSTANT::#load(PATH,string)
JOINED::#load("data/payload"+".odin",string)
DIRECTORY::#load(#directory+"data/payload.odin",string)
HASH::#load_hash(PATH,"crc32")
read::proc($path:string)->string{return #load(path,string)}
main::proc(){fmt.println(DIRECT,CONSTANT,JOINED,DIRECTORY,read("data/payload.odin"),#exists(PATH));fmt.println(HASH)}
''',{'data/payload.odin':'hello'})
        self.migrate()
        text=(self.output/'main.thor').read_text()
        self.assertIn('"data/payload.thor"',text)
        self.assertIn('"data/payload"+".thor"',text)
        self.compare_builds()

    def test_all_colliding_configuration_keys_keep_their_native_define_names(self):
        names=['fn','set','or_panic','Integer','Rational']
        declarations='\n'.join(f'{name}::#config({name},1)' for name in names)
        self.write('package main\nimport "core:fmt"\n'+declarations+
            '\nPAREN::#config(((or_panic)),1)\nmain::proc(){fmt.println('+','.join(names)+',PAREN)}\n')
        flags=[f'-define:{name}={index+2}' for index,name in enumerate(names)]
        self.migrate(flags)
        text=(self.output/'main.thor').read_text()
        for name in names:self.assertIn(f'#config({name},1)',text)
        self.assertIn('#config(((or_panic)),1)',text)
        self.compare_builds(flags,expected='2 3 4 5 6 4\n')
        result=subprocess.run([str(COMPILER),'run','.',*flags,'-thread-count:1'],
            cwd=self.output,capture_output=True,text=True,timeout=60)
        self.assertEqual((result.returncode,result.stdout),(0,'2 3 4 5 6 4\n'),result.stderr)

    def test_absolute_embedded_source_path_is_rebased_to_the_copy(self):
        payload=self.source/'data/payload.odin'
        self.write('package main\nimport "core:fmt"\nPATH::'+json.dumps(str(payload))+
            '\nmain::proc(){fmt.println(#load(PATH,string),#exists(PATH))}\n',{'data/payload.odin':'hello'})
        self.migrate()
        text=(self.output/'main.thor').read_text()
        self.assertIn('#directory',text)
        self.assertNotIn(str(payload),text)
        self.compare_builds(expected='hello true\n')

    def test_sdk_collection_abi_is_preserved_when_migrating_its_source_tree(self):
        self.write('''package main
import probe "core:probe"
call :: probe.set
main :: proc() {
    if probe.set(fn=6)!=7 || call(fn=7)!=8 {panic("native SDK ABI")}
}
''', {'core/probe/probe.odin':'''package probe
set :: proc(fn:int)->int {return fn+1}
'''})
        native=Path(shutil.which('odin')).resolve()
        directories=[(self.source/'base',native.parent/'base'),(self.source/'vendor',native.parent/'vendor')]
        if os.name=='nt':
            # ODIN_ROOT also selects the native linker. This intentionally
            # minimal SDK needs the selected backend's ordinary Windows linker.
            (self.source/'bin').mkdir()
            file_reference(self.source/'bin/radlink.exe',native.parent/'bin/radlink.exe')
        for entry in (native.parent/'core').iterdir():
            target=self.source/'core'/entry.name
            if not target.exists():
                if entry.is_dir():directories.append((target,entry))
                else:file_reference(target,entry)
        directory_aliases(directories)
        wrapper=native_test_compiler(self.root,'frontend_guard',sdk=str(self.source),
            odin=str(native),frontend=str(self.root/'unused-frontend'))
        self.migrate(['--odin',str(wrapper)])
        migrated=(self.output/'main.thor').read_text()
        self.assertIn('probe.set(fn=6)',migrated)
        self.assertIn('call(fn=7)',migrated)
        self.assertIn('set_odin :: proc(fn_odin', (self.output/'core/probe/probe.thor').read_text())
        for flags in [[],UNCHECKED]:
            result=subprocess.run([str(COMPILER),'run',str(self.output),'--odin',str(wrapper),
                '-thread-count:1',*flags],cwd=self.root,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_local_collections_and_defines_keep_the_same_meaning(self):
        self.write('''package main
import "custom:values"
import "core:fmt"
main::proc(){fmt.println(values.fn,values.COUNT)}
''',{'packages/values/lib.odin':'package values\nfn::7\nCOUNT::#config(COUNT,1)\n'})
        self.migrate(['-collection:custom=packages','-define:COUNT=3'])
        self.compare_builds(['-collection:custom=packages','-define:COUNT=3'],expected='7 3\n')

    @unittest.skipUnless(os.name=='posix', 'POSIX symlinks')
    def test_internal_links_and_directories_with_source_extensions(self):
        self.write('package main\nmain::proc(){or_panic:=7;_=or_panic}\n',
                   {'assets.odin/data.txt':'asset','assets/data.bin':b'\x00\xff'})
        (self.source/'asset-alias').symlink_to(self.source/'assets/data.bin')
        (self.source/'source-alias.txt').symlink_to('main.odin')
        (self.source/'directory-alias').symlink_to('assets.odin',target_is_directory=True)
        self.migrate()
        self.assertEqual((self.output/'asset-alias').read_bytes(),b'\x00\xff')
        self.assertEqual((self.output/'source-alias.txt').resolve(),self.output/'main.thor')
        self.assertTrue((self.output/'assets.odin').is_dir())
        self.assertEqual((self.output/'directory-alias/data.txt').read_text(),'asset')
        self.compare_builds()

    @unittest.skipUnless(shutil.which('clang') and shutil.which('ar'), 'C compiler and archiver')
    def test_foreign_link_names_prefixes_and_multi_variable_declarations(self):
        self.write('''package main
import "core:fmt"
foreign import native "libfixture.a"
@(link_prefix="original_")
foreign native {
 fn::proc()->i32---
 set,or_panic:i32
}
main::proc(){fmt.println(fn(),set,or_panic)}
''',{'fixture.c':'int original_fn(void){return 7;}\nint original_set=8;\nint original_or_panic=9;\n'})
        compiled=subprocess.run(['clang','-c','fixture.c','-o','fixture.o'],cwd=self.source,capture_output=True,text=True)
        self.assertEqual(compiled.returncode,0,compiled.stderr)
        archived=subprocess.run(['ar','rcs','libfixture.a','fixture.o'],cwd=self.source,capture_output=True,text=True)
        self.assertEqual(archived.returncode,0,archived.stderr)
        report=self.migrate().stderr
        self.assertIn('preserved native link names',report)
        for flags in [[],UNCHECKED]:
            self.compare_builds(flags,expected='7 8 9\n')

    @unittest.skipUnless(shutil.which('clang') and shutil.which('ar'), 'C compiler and archiver')
    def test_initialized_export_groups_preserve_symbols_comments_and_evaluation(self):
        self.write('''package main
import "core:fmt"
counter: int
initialize::proc "contextless"(value:i32)->i32{counter=counter*10+int(value);return value}
@(export, link_prefix="native_")
fn /* first, exported variable */, set: i32 = initialize(7), /* second, initializer */ initialize(8)
@(export, link_prefix="native_")
Integer, Rational :: proc "c"()->i32{return 9}, proc "c"()->i32{return 10}
foreign import native "libfixture.a"
foreign native { inspect::proc()->i32--- }
main::proc(){fmt.println(fn,set,Integer(),Rational(),inspect(),counter)}
''',{'fixture.c':'''extern int native_fn, native_set;
extern int native_Integer(void), native_Rational(void);
int inspect(void){return native_fn+native_set+native_Integer()+native_Rational();}
'''})
        compiled=subprocess.run(['clang','-c','fixture.c','-o','fixture.o'],cwd=self.source,capture_output=True,text=True)
        self.assertEqual(compiled.returncode,0,compiled.stderr)
        archived=subprocess.run(['ar','rcs','libfixture.a','fixture.o'],cwd=self.source,capture_output=True,text=True)
        self.assertEqual(archived.returncode,0,archived.stderr)
        self.migrate()
        text=(self.output/'main.thor').read_text()
        self.assertIn('/* first, exported variable */',text)
        self.assertIn('/* second, initializer */',text)
        for flags in [[],UNCHECKED]:
            self.compare_builds(flags,expected='7 8 9 10 34 78\n')
            compatible=subprocess.run([str(COMPILER),'run','.', '--odin-compatible','-vet','-thread-count:1',*flags],
                cwd=self.output,capture_output=True,text=True,timeout=60)
            self.assertEqual((compatible.returncode,compatible.stdout),(0,'7 8 9 10 34 78\n'),compatible.stderr)

    def test_reflected_or_panic_members_and_colliding_type_references(self):
        self.write('''#+feature using-stmt
#+feature dynamic-literals
package main
import "core:fmt"
import "core:reflect"
Integer :: distinct i16
Rational :: 12
Kind :: enum { zero, or_panic = Rational }
Item :: struct { or_panic, second: Integer }
Bits :: bit_field u8 { or_panic: u8 | 3, set: u8 | 2 }
pick :: proc(x: $T) -> Integer { return x.or_panic }
make_item :: proc($T: typeid) -> T { return T{or_panic=8} }
map_value :: proc(or_panic: int) -> int {
    table := map[int]int{or_panic=17}
    defer delete(table)
    return table[or_panic]
}
main :: proc() {
    item := Item{or_panic=3, second=4}
    using item
    or_panic += 2
    k: Kind = .or_panic
    bits := Bits{or_panic=6}
    fmt.println(reflect.struct_field_names(Item)[0], k, int(k), size_of(item.second), pick(item))
    fmt.println(reflect.bit_field_names(Bits)[0], bits.or_panic, make_item(Item).or_panic, map_value(3))
}
''')
        self.migrate()
        for flags in ([], UNCHECKED):
            self.compare_builds(flags, expected='or_panic or_panic 12 2 5\nor_panic 6 8 17\n')

    def test_member_names_preserve_reflection_and_generic_sdk_interoperability(self):
        self.write('''#+feature using-stmt
package main
import "core:fmt"
import "core:reflect"
Kind::enum{Integer,Rational,fn,set}
set::[2]int{99,100}
fn::proc()->int{return 99}
Item::struct{set:[2]int,fn:proc()->int,Integer:int,Rational:int,or_panic:int}
is_integer::proc(value:$T)->bool{return value==.Integer}
change::proc(item:^$T){item.set[0]+=1;item.or_panic+=2}
main::proc(){
 _=fn()
 _=set[0]
 item:=Item{{1,2},proc()->int{return 7},3,4,5}
 change(&item)
 using item
 if set[0]==2 {}
 value:=fn()
 {}
 fmt.println(value,set,Integer,Rational,item.or_panic,is_integer(Kind.Integer),is_integer(reflect.Type_Kind.Integer))
}
''')
        self.migrate()
        text=(self.output/'main.thor').read_text()
        self.assertIn('set:[2]int',text)
        self.assertIn('enum{Integer,Rational,fn,set}',text)
        self.assertIn('(set)[0]',text)
        self.assertIn('(fn)()',text)
        for flags in [[],UNCHECKED]:
            self.compare_builds(flags)

    def test_external_enum_contexts_in_maps_bit_sets_calls_returns_and_switches(self):
        self.write('''#+feature dynamic-literals
package main
import "core:fmt"
import "core:reflect"
Local::enum{Integer}
echo::proc(value:reflect.Type_Kind)->reflect.Type_Kind{return .Integer}
main::proc(){
 kind:reflect.Type_Kind=.Integer
 lookup:=map[reflect.Type_Kind]int{.Integer=7}
 defer delete(lookup)
 bits:=bit_set[reflect.Type_Kind]{.Integer}
 #partial switch kind {case .Integer:fmt.print(lookup[.Integer]);case:}
 fmt.println(echo(.Integer)==kind,.Integer in bits,Local.Integer)
}
''')
        self.migrate()
        self.compare_builds()

    def test_structured_editor_tasks_and_launch_settings(self):
        artifact='debug'+EXE_SUFFIX
        tasks={'version':'2.0.0','tasks':[{'label':'debug','type':'process','command':'odin',
            'args':['build','main.odin','-file','-debug','-vet','-thread-count:1','-out:'+artifact],'options':{'cwd':'${workspaceFolder}'},'problemMatcher':[]}]}
        launch={'version':'0.2.0','configurations':[{'name':'Mine','type':'lldb','request':'launch','program':'${workspaceFolder}/'+artifact,
            'preLaunchTask':'debug','args':['keep me']}]}
        self.write('package main\nmain::proc(){}\n',{
            '.vscode/tasks.json':json.dumps(tasks),'.vscode/launch.json':json.dumps(launch)})
        self.migrate()
        task=json.loads((self.output/'.vscode/tasks.json').read_text())['tasks'][0]
        self.assertEqual(task['command'],'thor')
        self.assertIn('main.thor',task['args'])
        self.assertIn('-vet',task['args'])
        self.assertIn('--odin-compatible',task['args'])
        config=json.loads((self.output/'.vscode/launch.json').read_text())['configurations'][0]
        self.assertEqual(config['type'],'thor-lldb')
        self.assertEqual(config['args'],['keep me'])
        self.assertTrue(config['manifest'].endswith('thor-manifest.json'))
        result=subprocess.run([str(COMPILER),*task['args']],cwd=self.output,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        manifest=config['manifest'].replace('${workspaceFolderBasename}',self.output.name).replace('${workspaceFolder}',str(self.output))
        self.assertTrue(Path(manifest).is_file(),manifest)
        result=subprocess.run([str(self.output/artifact)],cwd=self.output,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_absolute_editor_paths_are_rebased_and_complex_shell_tasks_are_reported(self):
        tasks={'version':'2.0.0','tasks':[
            {'label':'build','type':'process','command':'odin','args':['build',str(self.source/'main.odin'),'-file'],
             'options':{'cwd':str(self.source)}},
            {'label':'custom','type':'shell','command':'echo ready # && odin build main.odin'},
            {'label':'subdirectory','type':'shell','command':'odin build .','options':{'cwd':'${workspaceFolder}/lib'}}]}
        self.write('package main\nmain::proc(){}\n',{'.vscode/tasks.json':json.dumps(tasks)})
        report=self.migrate().stderr
        converted=json.loads((self.output/'.vscode/tasks.json').read_text())['tasks']
        self.assertEqual(converted[0]['options']['cwd'],'${workspaceFolder}')
        self.assertEqual(converted[0]['args'][1],'${workspaceFolder}/main.thor')
        self.assertEqual(converted[1],tasks['tasks'][1])
        self.assertEqual(converted[2],tasks['tasks'][2])
        self.assertIn('shell command invokes Odin',report)
        self.assertIn('custom working directory',report)

    def test_raw_strings_and_comments_that_look_like_vet_tags(self):
        self.write('''package main
import "core:fmt"
TEXT::`hello
#+vet unused style
world`
/*
#+vet unused
*/
main::proc(){fmt.println(TEXT)}
''')
        self.migrate()
        self.assertEqual((self.output/'main.thor').read_text(),
                         (self.source/'main.odin').read_text().replace('package main', 'package main /*odin-bounds*/', 1))
        self.compare_builds()

    def test_shell_editor_tasks_convert_commands_and_keep_literal_text(self):
        command='mkdir -p "${workspaceFolder}/.vscode/bin" && odin build "main.odin" -file -debug -vet -thread-count:1 -o:none -out:"${workspaceFolder}/.vscode/bin/debug" && echo "odin build main.odin"'
        tasks={'version':'2.0.0','tasks':[{'label':'debug','type':'shell','command':command}]}
        self.write('package main\nmain::proc(){}\n',{'.vscode/tasks.json':json.dumps(tasks)})
        self.migrate()
        task=json.loads((self.output/'.vscode/tasks.json').read_text())['tasks'][0]
        self.assertIn('&& thor build "main.thor"',task['command'])
        self.assertIn('echo "odin build main.odin"',task['command'])
        self.assertIn('mkdir -p "${workspaceFolder}/.vscode/bin"',task['command'])
        self.assertIn('-vet',task['command'])
        self.assertIn('--odin-compatible',task['command'])
        if os.name=='nt':
            # This fixture deliberately preserves a POSIX shell script. Native
            # Windows process tasks are executed by the structured-task test.
            return
        converted=task['command'].replace('${workspaceFolder}',str(self.output)).replace('thor build ',shlex.quote(str(COMPILER))+' build ')
        built=subprocess.run(converted,shell=True,cwd=self.output,capture_output=True,text=True,timeout=60)
        self.assertEqual(built.returncode,0,built.stdout+built.stderr)

    @unittest.skipUnless(os.name=='posix', 'POSIX source symlinks')
    def test_source_symlinks_become_independent_converted_files(self):
        outside=self.root/'external.odin'
        outside.write_text('package main\nimport "core:fmt"\nmain::proc(){or_panic:=7;fmt.println(or_panic)}\n')
        (self.source/'main.odin').symlink_to(outside)
        original=outside.read_bytes()
        self.migrate()
        self.assertFalse((self.output/'main.thor').is_symlink())
        self.assertIn('or_panic_odin',(self.output/'main.thor').read_text())
        self.assertEqual(outside.read_bytes(),original)
        self.compare_builds(expected='7\n')

    def test_inactive_invalid_archived_source_does_not_block_the_working_project(self):
        self.write('package main\nmain::proc(){}\n',{'archive/fragment.odin':'this is an unfinished draft\n'})
        report=self.migrate().stderr
        self.assertIn('archived/inactive',report)
        self.assertEqual((self.output/'archive/fragment.thor').read_text(),'this is an unfinished draft\n')
        self.compare_builds()

    def test_unicode_paths_and_unchecked_migration_validation(self):
        self.source=self.root/'é 😀 project'
        self.source.mkdir()
        self.output=self.root/'out'/'é 😀 project'
        self.write('package main\nimport "core:fmt"\nmain::proc(){/* 😀 */or_panic:=7;fmt.println(or_panic)}\n')
        self.migrate(UNCHECKED)
        self.compare_builds(UNCHECKED,expected='7\n')

    def test_concurrent_migrations_publish_only_one_copy(self):
        self.write('package main\nmain::proc(){or_panic:=7;_=or_panic}\n')
        command=[str(COMPILER),'migrate',str(self.source),'--output-dir',str(self.output.parent),'-thread-count:1']
        saved=self.snapshot()
        first=subprocess.Popen(command,cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        second=subprocess.Popen(command,cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        outputs=[first.communicate(timeout=90),second.communicate(timeout=90)]
        self.assertEqual(sorted([first.returncode,second.returncode]),[0,1],outputs)
        self.assertEqual(self.snapshot(),saved)
        self.assertIn('or_panic_odin',(self.output/'main.thor').read_text())
        self.compare_builds()

    @unittest.skipUnless(os.name=='posix', 'Executable test compiler wrapper')
    def test_changed_source_is_detected_before_publication(self):
        self.write('package main\nmain::proc(){or_panic:=7;_=or_panic}\n')
        changed='package main\nmain::proc(){or_panic:=8;_=or_panic}\n'
        wrapper=self.root/'odin-wrapper'
        wrapper.write_text('#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\n'
            f'if sys.argv[1:2]==["version"]:Path({str(self.source/"main.odin")!r}).write_text({changed!r})\n'
            f'os.execv({shutil.which("odin")!r},[{shutil.which("odin")!r},*sys.argv[1:]])\n')
        wrapper.chmod(0o755)
        result=subprocess.run([str(COMPILER),'migrate',str(self.source),'--output-dir',str(self.output.parent),
            '--odin',str(wrapper),'-thread-count:1'],cwd=self.root,capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertIn('source project changed',result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual((self.source/'main.odin').read_text(),changed)

    def test_fresh_names_avoid_capture_by_external_promoted_fields(self):
        external=self.root/'external'
        (external/'values').mkdir(parents=True)
        (external/'values/lib.odin').write_text('package values\nData::struct{fn_odin:int}\n')
        self.write('''#+feature using-stmt
package main
import "custom:values"
import "core:fmt"
fn::7
main::proc(){data:=values.Data{9};using data;fmt.println(fn,fn_odin)}
''')
        flags=['-collection:custom='+str(external)]
        report=self.migrate(flags).stderr
        self.assertIn('fn -> fn_odin_2',report)
        self.compare_builds(flags,expected='7 9\n')

    def test_large_assets_are_copied_byte_for_byte(self):
        data=bytes(range(256))*8192
        self.write('package main\nmain::proc(){}\n',{'assets/large.bin':data})
        self.migrate()
        self.assertEqual((self.output/'assets/large.bin').read_bytes(),data)

    def test_future_using_member_does_not_capture_prior_global_access(self):
        self.write('''#+feature using-stmt
package main
import "core:fmt"
values:=[2]int{90,91}
Item::struct{values:[2]int}
main::proc(){
 index:=1
 first:=values[index]
 item:=Item{{7,8}}
 using item
 fmt.println(first,values[index])
}
''')
        self.migrate()
        self.compare_builds(expected='91 8\n')
        result=subprocess.run([str(COMPILER),'run','.','-thread-count:1'],
            cwd=self.output,capture_output=True,text=True,timeout=60)
        self.assertEqual((result.returncode,result.stdout),(0,'91 8\n'),result.stderr)

    def test_external_reserved_declaration_is_available_through_its_package(self):
        external=self.root/'external'
        (external/'values').mkdir(parents=True)
        (external/'values/lib.odin').write_text('package values\nor_panic::7\n')
        self.write('package main\nimport "custom:values"\nmain::proc(){_=values.or_panic}\n')
        flags=['-collection:custom='+str(external)]
        self.migrate(flags)
        self.assertIn('values.or_panic',(self.output/'main.thor').read_text())
        self.compare_builds(flags,expected='')

    def test_cli_requires_a_directory_and_rejects_irrelevant_options(self):
        self.write('package main\nmain::proc(){}\n')
        for args,message in [([], 'requires an Odin project directory'),
                ([str(self.source/'main.odin')], 'expects a project directory'),
                ([str(self.source),'--missing'],'unknown migration option'),
                ([str(self.source),'-out:program'],'does not apply to migration')]:
            with self.subTest(args=args):
                result=subprocess.run([str(COMPILER),'migrate',*args],cwd=self.root,
                    capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,2,result.stdout+result.stderr)
                self.assertIn(message,result.stderr)
        self.assertFalse(self.output.exists())

    def test_all_audited_name_collisions(self):
        cases=json.loads((ROOT/'docs/odin-superset-audit-2026-11-13/cases.json').read_text())
        names={'local_or_panic','or_panic_field','or_panic_parameter','or_panic_enum','or_panic_label','or_panic_import',
               'fn_call_then_block','fn_initializer_then_block','fn_initializer_silent_type_change','fn_call_arrow_receiver',
               'set_array_length','set_bool_condition','set_switch_condition','Integer_import_alias','Rational_import_alias'}
        cases=[case for case in cases if case['name'] in names]
        self.assertEqual({case['name'] for case in cases},names)
        for index,case in enumerate(cases):
            with self.subTest(case=case['name']):
                if self.output.exists():shutil.rmtree(self.output)
                self.write(case['source'])
                self.migrate()
                self.compare_builds()

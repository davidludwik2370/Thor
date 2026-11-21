"""Identical Odin/Thor programs must retain native type and source semantics."""
from tests.test_rust_compiler import compiler_variants
import json
import subprocess
import tempfile
from pathlib import Path
import unittest

from tests import test_rust_compiler as base

AUDIT=base.ROOT/'docs/odin-superset-audit-2026-11-13'
FIXED={
    'array_parenthesized_element','slice_parenthesized_element','array_bitfield_element','array_parenthesized_literal',
    'load_constant','load_concatenation','load_directory','load_hash_constant','load_directory_constant','exists_constant',
    'caller_location','source_directives','source_line_after_native_access','source_procedure_inside_native_index',
    'caller_expression_native_access','compiler_root_identity','compiler_project_name_directory',
    'build_project_tag_file','build_project_tag_directory','custom_thor_collection_index','custom_thor_collection_simple',
}


class NativeSourceIdentityTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)

    def test_frontend_tag_storage_and_type_parsing_with_checks_disabled(self):
        with tempfile.TemporaryDirectory(prefix='thor-unchecked-frontend-') as temporary:
            root=Path(temporary).resolve();binary=root/'frontend'
            result=subprocess.run(['odin','build',str(base.ROOT/'frontend'),*base.UNCHECKED,
                '-out:'+str(binary)],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stderr)
            source=root/'main.thor'
            source.write_text('''#+build-project-name no1 no2 no3 no4 no5 no6 no7 no8 no9, original_project !other
package main
T::[2](int)
S::[](int)
B::[2]bit_field u8 {a:u8|1}
V::#config(or_panic,7)
maybe::proc()->(int,bool){return 7,true}
main::proc(){defer{_=#branch_location};values:=T{1,2};_=values;selected:=maybe() or_panic;_=selected;_=owner.or_panic(or_panic=7)}
''')
            for project in ['original_project','other']:
                result=subprocess.run([str(binary),str(source),'--project-name='+project,'--package'],
                    capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(bool(json.loads(result.stdout).get('excluded')),project=='other')
                if project == 'original_project':
                    self.assertTrue(any(node.get('kind')=='Directive' and node.get('name')=='branch_location'
                                        for node in json.loads(result.stdout)['nodes']))
                    self.assertTrue(any(node.get('kind')=='Ident' and node.get('name')=='or_panic'
                                        for node in json.loads(result.stdout)['nodes']))
                    self.assertTrue(any(node.get('kind')=='OrPanic'
                                        for node in json.loads(result.stdout)['nodes']))
            native=root/'main_windows.odin'
            native.write_text('''#+build windows
#+vet unused
package main
main::proc(){Integer:=7;or_panic:=Integer;_=or_panic}
''')
            result=subprocess.run([str(binary),str(native),'--odin','--migration'],
                capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            document=json.loads(result.stdout)
            self.assertFalse(document.get('excluded',False))
            raw=native.read_bytes()
            identifiers=[raw[span[0]:span[1]].decode()
                         for span in document['migration_identifiers']]
            self.assertIn('Integer',identifiers)
            self.assertIn('or_panic',identifiers)
            self.assertEqual(len(document['migration_file_tags']),2)

    def verify(self,text,*,files=None,flags=(),directory=False,project_name='original_project'):
        with tempfile.TemporaryDirectory(prefix='thor-native-identity-') as temporary:
            root=Path(temporary).resolve();app=root/project_name;app.mkdir()
            for name,content in (files or {}).items():
                path=app/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
            text=text.replace('ABSOLUTE_PAYLOAD',str(app/'payload.txt')).replace('TEST_PROJECT_NAME',app.name)
            native=app/'main.odin';native.write_text(text)
            source=app/'main.thor';source.write_text(text)
            flags=[f.replace(':PLACEHOLDER','='+str(app/'custom')) for f in flags]
            executable=root/'native'
            result=subprocess.run(['odin','build',str(app if directory else native),*([] if directory else ['-file']),
                '-thread-count:1',*flags,'-out:'+str(executable)],capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stderr)
            expected=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            native.unlink()
            for engine,compiler in compiler_variants():
                with self.subTest(engine=engine):
                    result=subprocess.run([str(compiler),'run',str(app if directory else source),
                        '--output-dir',str(root/(engine+'-unrelated-output')),'-thread-count:1',*flags],
                        cwd=root,capture_output=True,text=True,timeout=60)
                    from thorlang.sourceidentity import hash_bytes
                    def normalize(value):
                        for path in [source,native]:
                            value=value.replace(str(path),'<source>').replace(path.name,'<source>')
                            value=value.replace('Fx'+format(hash_bytes(str(path).encode()),'x'),'<file-hash>')
                            if 'p:=proc()' in text:
                                offset=len(text[:text.index('p:=proc()')+3].encode())
                                name=f'main::main$anon-main:{path.name}:{offset}'
                                value=value.replace('Px'+format(hash_bytes(name.encode()),'x'),'<anonymous-hash>')
                        return value
                    self.assertEqual(result.returncode,expected.returncode,result.stdout+result.stderr)
                    self.assertEqual(normalize(result.stdout),normalize(expected.stdout),result.stderr)

    def test_five_audited_defects_checked_and_unchecked(self):
        cases=[case for case in json.loads((AUDIT/'cases.json').read_text()) if case['name'] in FIXED]
        self.assertEqual({c['name'] for c in cases},FIXED)
        for case in cases:
            for release in [[],base.UNCHECKED]:
                with self.subTest(case=case['name'],release=bool(release)):
                    # Audit tag cases use their directory basename by design.
                    text=case['source'].replace(case['name'],'TEST_PROJECT_NAME') if case['name'].startswith('build_project_tag') else case['source']
                    self.verify(text,files=case.get('files'),flags=[*case.get('flags',[]),*release],directory=case.get('directory_entry',False))

    def test_metadata_flags_helpers_anonymous_scopes_and_named_arguments(self):
        text='''package main
import "core:fmt"
import "base:runtime"
record :: proc(value:int, expression:=#caller_expression(value), call:=#caller_expression, loc:=#caller_location) {
 fmt.println(value,expression,call,loc.file_path,loc.line,loc.column,loc.procedure)
}
main :: proc() {
 values:=[2]int{3,7};index:=1
 fmt.println(#file,#directory,#line,#procedure)
 fmt.println(#location(),#location(record),#location(index))
 record(values[index]+1)
 record(value=values[index],expression="explicit")
 alias:=record
 alias(values[index])
 p:=proc(){ fmt.println(#procedure,#location(),#location().procedure) }
 p()
 runtime_proc:=proc(loc:runtime.Source_Code_Location){fmt.println(loc)}
 runtime_proc(#location())
}
'''
        for setting in ['normal','filename','none','obfuscated']:
            with self.subTest(mode=setting):self.verify(text,flags=['-source-code-locations:'+setting])

    def test_resources_config_generic_constants_and_subpackage_origin(self):
        self.verify('''package main
import "core:fmt"
import "sub"
PATH::#config(PATH,"payload.txt")
read::proc($path:string)->string{return #load(path,string)}
main::proc(){fmt.println(read(PATH),sub.read(),#exists(PATH),#load_hash(PATH,"fnv64a"))}
''',files={'payload.txt':'outer','sub/payload.txt':'inner','sub/lib.odin':'''package sub
read::proc()->string {PATH::"payload"+".txt";return #load(PATH,string)}
'''})

    def test_unicode_resource_paths_and_literal_caller_expressions(self):
        self.verify('''package main
import "core:fmt"
PATH::"payload😀.txt"
DATA::#load(PATH,string)
record::proc(value:string,text:=#caller_expression(value),loc:=#caller_location){fmt.println(value,text,loc.file_path)}
main::proc(){record("😀");fmt.println(#file,#directory,DATA)}
''',files={'payload😀.txt':'unicode resource'},project_name='project ü😀')

    def test_custom_collection_support_namespace_collision_and_native_dependencies(self):
        self.verify('''package main
import "core:fmt"
import "thor:thing"
main::proc(){values:=[2]int{thing.answer(),9};i:=0;fmt.println(values[i])}
''',files={'custom/thing/thing.odin':'package thing\nanswer::proc()->int{return 7}\n'},
            flags=['-collection:thor:PLACEHOLDER','-collection:__thor_runtime:PLACEHOLDER'])

    def test_selected_overloaded_generic_and_imported_caller_defaults(self):
        self.verify('''package main
import "core:fmt"
import "sub"
record_int::proc(value:int, expression:=#caller_expression(value), loc:=#caller_location){fmt.println(expression,loc.file_path,loc.line,loc.column,loc.procedure)}
record_string::proc(value:string, expression:=#caller_expression(value), loc:=#caller_location){fmt.println(expression,loc.file_path,loc.line,loc.column,loc.procedure)}
record::proc{record_int,record_string}
generic::proc(value:$T, expression:=#caller_expression(value), loc:=#caller_location){fmt.println(expression,loc.file_path,loc.line,loc.column,loc.procedure)}
main::proc(){
 values:=[2]int{3,7};i:=0
 record(values[i]);record("abc")
 generic(values[i] + 2)
 generic(proc(x:int)->int{return x+1})
 sub.record(values[i])
 inner::proc(text:=#procedure){fmt.println(text)}
 inner()
 /* 😀 Unicode columns */ sub.record(values[i])
}
''',files={'sub/sub.odin':'''package sub
import "core:fmt"
record::proc(value:int,expression:=#caller_expression(value),loc:=#caller_location){fmt.println(expression,loc.file_path,loc.line,loc.column,loc.procedure)}
'''})

    def test_project_tags_group_storage_and_native_dependencies(self):
        # More names than the parser's initial scratch capacity, multiple
        # comma groups, exclusions, and a native dependency with its own tag.
        self.verify('''#+build-project-name wrong1 wrong2 wrong3 wrong4 wrong5 wrong6 wrong7 wrong8 wrong9, TEST_PROJECT_NAME !other
package main
import "core:fmt"
import "sub"
main::proc(){fmt.println(sub.VALUE,ODIN_BUILD_PROJECT_NAME)}
''',directory=True,files={'sub/sub.odin':'''#+build-project-name TEST_PROJECT_NAME
package sub
VALUE::7
'''.replace('TEST_PROJECT_NAME','original_project')})

    def test_custom_collection_combines_with_big_numbers_fstrings_and_comptime(self):
        with tempfile.TemporaryDirectory(prefix='thor-custom-support-') as temporary:
            root=Path(temporary).resolve()
            (root/'custom/thing').mkdir(parents=True)
            (root/'custom/thing/thing.odin').write_text('package thing\nVALUE::41\n')
            source=root/'main.thor'
            source.write_text('''package main
import "core:fmt"
import "thor:thing"
calc::fn(x:int)->int{return x+thing.VALUE}
VALUE::#run calc(1)
main::proc(){number:Integer=123456789012345678901234567890;defer delete(number);fmt.println(f"{VALUE} {number}")}
''')
            for engine,compiler in compiler_variants():
                for flags in [[],base.UNCHECKED]:
                    with self.subTest(engine=engine,flags=flags):
                        result=subprocess.run([str(compiler),'run',str(source),'--output-dir',str(root/engine),
                            '-collection:thor='+str(root/'custom'),*flags],cwd=root,capture_output=True,text=True,timeout=60)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertEqual(result.stdout.strip(),'42 123456789012345678901234567890')

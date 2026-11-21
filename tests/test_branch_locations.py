"""Native defer exit locations survive Thor lowering and helper captures."""
from tests.test_rust_compiler import compiler_variants
import subprocess
import tempfile
from pathlib import Path
import unittest

from tests import test_rust_compiler as native
from tests import test_native_source_identity as identity

PREFIX = '''package main
import "core:fmt"
import "base:runtime"
record::proc(label:string,loc:runtime.Source_Code_Location){fmt.printf("%s|%s|%d|%d|%s\\n",label,loc.file_path,loc.line,loc.column,loc.procedure)}
'''

EXITS = PREFIX + '''main::proc(){
 defer record("return",#branch_location)
 late:=1
 defer fmt.println("late",late)
 late=9
 {
  defer record("scope",#branch_location)
 }
 for i in 0..<2 {defer record("continue",#branch_location);continue}
 outer: for {defer record("break",#branch_location);break outer}
 switch 1 {case 1: defer record("case",#branch_location);_=2}
 defer {
  defer record("nested defer",#branch_location)
  record("defer body",#branch_location)
 }
 numbers:=[2]int{7,8}
 defer fmt.println(numbers[int(#branch_location.line)%2])
 return
}
'''


class BranchLocationTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    verify = identity.NativeSourceIdentityTests.verify

    def test_native_numeric_token_boundaries(self):
        from thorlang.hygiene import tokens
        parsed=[token for _,_,token in tokens(b'12345 -12 .75 1_000.25e-12 0x1e+3 0h3f800000 2i 1..=2')]
        self.assertEqual(parsed,[
            b'12345',b'-',b'12',b'.75',b'1_000.25e-12',b'0x1e',b'+',b'3',
            b'0h3f800000',b'2i',b'1',b'.',b'.',b'=',b'2',
        ])

    def test_native_exit_selection_order_and_helper_captures(self):
        for flags in [[], native.UNCHECKED, ['-debug']]:
            with self.subTest(flags=flags):
                self.verify(EXITS, flags=flags)

    def test_all_native_source_location_display_modes(self):
        for mode in ['normal', 'filename', 'none', 'obfuscated']:
            with self.subTest(mode=mode):
                self.verify(EXITS, flags=['-source-code-locations:'+mode])

    def test_named_nested_and_generic_procedure_exits(self):
        text=PREFIX+'''main::proc(){
 nested::proc(){defer record("nested",#branch_location);return}
 nested()
 generic::proc(value:$T){defer record("generic",#branch_location);_=value}
 generic(7)
}
'''
        for mode in ['normal','filename','none','obfuscated']:
            with self.subTest(mode=mode):
                self.verify(text,flags=['-source-code-locations:'+mode])

    def test_plain_identifier_keeps_its_native_meaning_without_mapping_support(self):
        text='package main\nmain::proc(){branch_location:=7;_=branch_location}\n'
        self.verify(text)
        with tempfile.TemporaryDirectory(prefix='thor-branch-ordinary-') as temporary:
            root=Path(temporary).resolve();source=root/'main.thor';source.write_text(text)
            for engine,compiler in compiler_variants():
                result=subprocess.run([str(compiler),'transpile',str(source),'--output-dir',str(root/engine)],
                    capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
                lowered=Path(result.stdout.strip()).read_text()
                self.assertNotIn('map_branch_location',lowered)
                self.assertNotIn('Branch_Location_Row',lowered)

    def test_implicit_returns_optional_exits_and_deferred_loop_bodies(self):
        self.verify(PREFIX + '''maybe::proc(ok:bool)->(int,bool){return 7,ok}
optional::proc()->(result:int,ok:bool){
 defer record("or_return",#branch_location)
 result=maybe(false) or_return
 return result,true
}
implicit::proc(){
 defer record("implicit",#branch_location)
}
main::proc(){
 implicit()
 _,_=optional()
 for i in 0..<2 {
  defer record("or_continue",#branch_location)
  value:=maybe(false) or_continue
  _=value
 }
 for {
  defer record("or_break",#branch_location)
  value:=maybe(false) or_break
  _=value
 }
 numbers:=[2]int{3,7}
 defer for number in numbers {fmt.println(number,#branch_location)}
}
''')

    def test_imported_native_and_unicode_source(self):
        self.verify(PREFIX + '''import "sub"
main::proc(){
 defer record("main",#branch_location)
 sub.run()
 /* 😀 before exit */ return
}
''', files={'sub/sub.odin': '''package sub
import "core:fmt"
run::proc(){
 defer fmt.println(#branch_location)
}
'''}, project_name='branch ü😀')

    def test_case_exit_endpoints_and_nested_procedure_contexts(self):
        text = PREFIX + '''main::proc(){
 values:=[2]int{3,7};i:=1
 switch 1 {
 case 1:
  defer record("index endpoint",#branch_location)
  _=values[i]
 }
 switch 1 {
 case 1:
  defer record("nested declaration endpoint",#branch_location)
  local::proc(){defer record("local",#branch_location)}
 }
 switch 1 {
 case 1:
  defer record("fallthrough",#branch_location)
  fallthrough
 case 2:
  defer record("after fallthrough",#branch_location)
  _=values[i]+values[i]
 }
}
'''
        for mode in ['normal','filename','none','obfuscated']:
            with self.subTest(mode=mode):
                self.verify(text,flags=['-source-code-locations:'+mode])

    def test_thor_expression_and_fstring_helpers(self):
        template = PREFIX + '''print_line::proc(line:i32){fmt.printf("line=%d\\n",line)}
main::proc(){
 values:=[2]int{3,7}
 defer {
  $MESSAGE$
  fmt.println(message)
  $PRINT_LINE$
  fmt.println(values[int(#branch_location.line)%2])
  $COLLECTION$
  fmt.println(collected)
  delete(collected)
 }
 return
}
'''
        normal = template.replace('$MESSAGE$', 'message := int(#branch_location.line)%2==0 ? "even" : "odd"').replace('$PRINT_LINE$', 'print_line(#branch_location.line)').replace('$COLLECTION$', 'collected:=make([]int,3);for i in 0..<3{collected[i]=int(#branch_location.line)+i}')
        thor = template.replace('$MESSAGE$', 'message :> switch int(#branch_location.line)%2 {case 0:"even";case:"odd"}').replace('$PRINT_LINE$', 'fmt.println(f"line={#branch_location.line}")').replace('$COLLECTION$', 'collected :> [int(#branch_location.line)+i for i in 0..<3]')
        with tempfile.TemporaryDirectory(prefix='thor-defer-helpers-') as temporary:
            root = Path(temporary).resolve()
            baseline = root/'main.odin'
            baseline.write_text(normal)
            source = root/'main.thor'
            source.write_text(thor)
            for flags in [[], native.UNCHECKED, ['-debug']]:
                reference = subprocess.run(['odin','run',str(baseline),'-file','-thread-count:1',*flags],
                    capture_output=True,text=True,timeout=45)
                self.assertEqual(reference.returncode,0,reference.stderr)
                for engine,compiler in compiler_variants():
                    with self.subTest(engine=engine,flags=flags):
                        result = subprocess.run([str(compiler),'run',str(source),'--output-dir',str(root/engine),
                            '-thread-count:1',*flags],cwd=root,capture_output=True,text=True,timeout=60)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertEqual(result.stdout,reference.stdout,result.stderr)
                        self.assertNotIn('allocation(s)',result.stderr)

    def test_native_builtin_shadowing_keeps_mapper_hygiene(self):
        self.verify('''package main
import "core:fmt"
import "base:runtime"
import native "base:builtin"
int::struct{}
i32::struct{}
bool::struct{}
string::struct{}
len::proc(value:native.int)->native.int{return value}
record::proc(loc:runtime.Source_Code_Location){fmt.println(loc)}
main::proc(){defer record(#branch_location)}
''')

    def test_computed_literal_case_endpoint_uses_original_expression_end(self):
        value='#run work()'
        template=PREFIX+'''work::$PROC$()->int{return 12345}
main::proc(){
 switch 1 {
 case 1:
  defer record("computed endpoint",#branch_location)
  _=$VALUE$
 }
}
'''
        normal=template.replace('$PROC$','proc').replace('$VALUE$',' '*(len(value)-1)+'12345')
        thor=template.replace('$PROC$','fn').replace('$VALUE$',value)
        with tempfile.TemporaryDirectory(prefix='thor-defer-computed-') as temporary:
            root=Path(temporary).resolve()
            baseline=root/'main.odin';baseline.write_text(normal)
            source=root/'main.thor';source.write_text(thor)
            for flags in [[],native.UNCHECKED,['-source-code-locations:obfuscated']]:
                reference=subprocess.run(['odin','run',str(baseline),'-file','-thread-count:1',*flags],
                    capture_output=True,text=True,timeout=45)
                self.assertEqual(reference.returncode,0,reference.stderr)
                for engine,compiler in compiler_variants():
                    with self.subTest(engine=engine,flags=flags):
                        result=subprocess.run([str(compiler),'run',str(source),'--output-dir',str(root/engine),
                            '-thread-count:1',*flags],cwd=root,capture_output=True,text=True,timeout=60)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        from thorlang.sourceidentity import hash_bytes
                        def normalize(text):
                            for path in [baseline,source]:
                                text=text.replace(str(path),'<source>')
                                text=text.replace('Fx'+format(hash_bytes(str(path).encode()),'x'),'<file-hash>')
                            return text
                        self.assertEqual(normalize(result.stdout),normalize(reference.stdout),result.stderr)

    def test_native_placement_errors_and_lazy_inactive_branches(self):
        self.verify(PREFIX + '''main::proc(){
 when false {_=#branch_location}
 fmt.println("inactive")
}
''')
        with tempfile.TemporaryDirectory(prefix='thor-branch-errors-') as temporary:
            root = Path(temporary).resolve()
            source = root/'main.thor'
            source.write_text('package main\nmain::proc(){_=#branch_location}\n')
            for engine,compiler in compiler_variants():
                result = subprocess.run([str(compiler),'check',str(source),'--output-dir',str(root/engine)],
                    capture_output=True,text=True,timeout=30)
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn("#branch_location may only be used within a 'defer'",result.stderr)
                self.assertIn(str(source),result.stderr)
                self.assertNotIn('__thor_',result.stderr)

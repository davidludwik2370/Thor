"""Unused for-loop bindings have checked fixes; other unused names stay manual."""
from tests.test_rust_compiler import compiler_engines
import json
import stat
import subprocess
import unittest
from tests import test_unused_vet as usage
from tests import test_rust_compiler as native


class IteratorVetTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=usage.UnusedVetTests.setUp
    write=usage.UnusedVetTests.write
    invoke=usage.UnusedVetTests.invoke

    def applied(self, text, expected, flags=()):
        reports=[]
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                self.source.chmod(0o640)
                _,warnings=self.invoke(engine,'--vet=unused')
                self.assertEqual(self.source.read_bytes(),text.encode())
                self.assertTrue(warnings)
                self.assertTrue(all(item['code']=='thor/unused' and 'fix' in item for item in warnings),warnings)
                reports.append(warnings)
                _,remaining=self.invoke(engine,'--vet=unused','--apply')
                self.assertEqual(remaining,[])
                self.assertEqual(self.source.read_bytes(),expected.encode())
                self.assertEqual(stat.S_IMODE(self.source.stat().st_mode),0o640)
                _,second=self.invoke(engine,'--vet=unused','--apply')
                self.assertEqual(second,[])
                if flags:self.invoke(engine,*flags,command='run')
        self.assertEqual(len(reports),len(compiler_engines()))
        for report in reports[1:]:
            self.assertEqual(report,reports[0])

    def test_first_second_single_and_both_unused_bindings(self):
        text='''package main
main::proc(){
 values:>[1,2]
 for hand, i in values {_=i}
 for used, index in values {_=used}
 for counter in 0..<3 {_=0}
 for element, position in values {_=0}
 for _, last_index in values {_=0}
}
'''
        expected=text.replace('for hand,','for _,').replace('for used, index','for used').replace(
            'for counter in','for _ in').replace('for element, position','for _').replace('for _, last_index','for _')
        self.applied(text,expected)

    def test_reference_bindings_labels_and_do_bodies_preserve_comments(self):
        text='''// 😀 original positions
package main
main::proc(){
 values:=[1,2]
 outer: for & /* keep this */ unused_reference in values {break outer}
 for & /* used reference */ used_reference in values {used_reference+=1}
 for &other, used_index in values do _=used_index
 for unused in 0..<2 do _=0
}
'''
        expected=text.replace('& /* keep this */ unused_reference',' /* keep this */ _').replace('&other,','_,').replace('for unused in','for _ in')
        self.applied(text,expected)

    def test_comments_commas_unicode_and_crlf_are_preserved(self):
        text='''// 😀
package main
main::proc(){
 values:>[1,2]
 for value /* before, */ , /* after, */ unused in values {_=value}
 for value, // line comment
     unused_line in values {_=value}
 for value, /* block
 comment */ unused_block in values {_=value}
}
'''.replace('\n','\r\n')
        expected=text.replace(', /* after, */ unused',' /* after, */ ').replace('unused_line','_').replace('unused_block','_')
        self.applied(text,expected)

    def test_typed_value_and_index_checks_are_retained(self):
        text='''package main
main::proc(){
 for counter:f32 in 0..<3 {_=0}
 values:>[1,2]
 for value,(index:i8) in values {_=value}
 for both,(position:u8) in values {_=0}
}
'''
        expected=text.replace('counter:f32','_:f32').replace('(index:i8)','(_:i8)').replace('for both,','for _,').replace('(position:u8)','(_:u8)')
        for flags in ([],native.UNCHECKED):
            with self.subTest(flags=flags):self.applied(text,expected,flags)

    def test_typed_unused_index_overflow_still_fails_at_runtime(self):
        text='''package main
allocate::proc(n:int)->[]int{return make([]int,n)}
main::proc(){
 values:=allocate(200);defer delete(values)
 for value,(unused_index:i8) in values {_=value}
}
'''
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                compiler=native.PYTHON if engine=='python' else native.RUST
                command=[str(compiler),'run',str(self.source),'--output-dir',str(self.output)]
                before=[]
                for flags in ([],native.UNCHECKED):
                    result=subprocess.run([*command,*flags],cwd=self.root,capture_output=True,text=True,timeout=90)
                    self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertIn('index',result.stderr.lower())
                    before.append(result.returncode)
                self.invoke(engine,'--vet=unused','--apply')
                self.assertIn('(_:i8)',self.source.read_text())
                for flags,status in zip(([],native.UNCHECKED),before):
                    after=subprocess.run([*command,*flags],cwd=self.root,capture_output=True,text=True,timeout=90)
                    self.assertEqual(after.returncode,status,after.stdout+after.stderr)
                    self.assertIn('index',after.stderr.lower())

    def test_regular_locals_parameters_switches_and_comprehensions_stay_manual(self):
        text='''package main
import "core:testing"
callback::proc(t:^testing.T){local:=1;local=2}
accept::proc(client:proc(^testing.T)){client(nil)}
single_run::proc(){}
Choice::union{int,string}
main::proc(){
 accept(callback)
 local:=3
 for item in [1,2] {_=item}
 choice:=Choice(1)
 switch variant in choice {case int:_=0;case string:_=0}
 values:=[1 for item in [1,2]];defer delete(values)
}
'''
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                _,errors=self.invoke(engine,'--vet=unused','--apply')
                self.assertTrue(errors)
                self.assertTrue(all('fix' not in item for item in errors),errors)
                self.assertEqual(self.source.read_text(),text)
                self.assertIn("'t' declared but not used",[item['msgs'][0] for item in errors])
                self.assertIn("Procedure 'single_run' declared but not used",[item['msgs'][0] for item in errors])

    def test_shadowing_writes_discards_fstrings_and_deferred_uses(self):
        text='''package main
import "core:fmt"
main::proc(){
 values:=[1,2]
 for shadowed in values {shadowed:=3;_=shadowed}
 for value, unused_index in values {fmt.println(f"{value}")}
 for value in values {defer fmt.println(value)}
 for &value in values {value+=1}
 for value, i in values {_=value;_=i}
}
'''
        expected=text.replace('for shadowed in','for _ in').replace('for value, unused_index in','for value in')
        self.applied(text,expected)

    def test_maps_strings_enum_iteration_unrolled_loops_and_iterator_procs(self):
        text='''package main
E::enum{first,last}
next::proc(state:^int)->(int,int,bool){state^+=1;return state^,state^*2,state^<3}
main::proc(){
 m:=make(map[int]int);defer delete(m);m[1]=2
 for unused_key,value in m {_=value}
 for key,unused_value in m {_=key}
 for rune,unused_offset in "abc" {_=rune}
 for unused_enum in E {_=0}
 #unroll for unused,i in [1,2] {_=i}
 state:=0
 for value,unused_index in next(&state) {_=value}
}
'''
        expected=text.replace('unused_key,','_,').replace('key,unused_value','key').replace('rune,unused_offset','rune').replace('for unused_enum','for _').replace('for unused,i','for _,i').replace('value,unused_index','value')
        self.applied(text,expected)

    def test_iteration_counts_getters_and_iterator_state_are_preserved(self):
        text='''package main
calls:int
get::proc()->[3]int{calls+=1;return {1,2,3}}
next::proc(state:^int)->(int,int,bool){state^+=1;return state^,state^*2,state^<3}
main::proc(){
 total:=0
 for unused in get(){total+=1}
 if total!=3 || calls!=1 {panic("getter evaluation")}
 state:=0
 for value,unused_index in next(&state) {total+=value}
 if total!=6 || state!=3 {panic("iterator state")}
}
'''
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                self.invoke(engine,'--vet=unused','--apply')
                self.assertIn('for _ in get()',self.source.read_text())
                for flags in ([],native.UNCHECKED):self.invoke(engine,*flags,command='run')

    def test_ask_default_checks_and_unused_rule_selection(self):
        text='package main\nmain::proc(){for unused in 0..<2 {_=0}}\n'
        for engine in compiler_engines():
            for answer,changed in [('n\n',False),('y\n',True)]:
                with self.subTest(engine=engine,answer=answer):
                    self.write(text)
                    self.invoke(engine,'--vet=unused','--ask',input=answer)
                    self.assertEqual(self.source.read_text(),text.replace('for unused','for _') if changed else text)
            self.write(text)
            self.invoke(engine,'--vet=immutable','--apply')
            self.assertEqual(self.source.read_text(),text)
            self.invoke(engine,'--apply')
            self.assertEqual(self.source.read_text(),text.replace('for unused','for _'))

    def test_combined_corrections_rebase_remaining_manual_warnings(self):
        text='''package main
import "core:thread"
helper::proc(parameter:int)->int{return 1}
main::proc(){
 values:>[1,2]
 for value, unused_index in values {_=value}
 unused_local:=3
 _=helper(0)
}
'''
        expected=text.replace('import "core:thread"\n','').replace('helper::proc','helper::fn').replace(', unused_index','')
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                _,warnings=self.invoke(engine,'--apply')
                self.assertEqual(self.source.read_text(),expected)
                self.assertEqual([item['msgs'][0] for item in warnings],
                                 ["'parameter' declared but not used","'unused_local' declared but not used"])
                for item,name in zip(warnings,('parameter','unused_local')):
                    self.assertNotIn('fix',item)
                    self.assertEqual(item['pos']['offset'],expected.index(name))
                _,second=self.invoke(engine,'--apply')
                self.assertEqual(warnings,second)


if __name__=='__main__':unittest.main()

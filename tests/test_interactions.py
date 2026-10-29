"""Cross-feature regression cases identified during the post-v1 audit."""
import unittest
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from tests import test_thor as base
from tests import test_diagnostics as diagnostics


class InteractionTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_generic_map_lookups_retain_optional_status_and_fallback(self):
        self.invoke('''package main
Box::struct{items:map[int]int}
read::proc(box:$B,key:int)->(int,bool){return box.items[key]}
fallback::proc(box:$B,key:int)->int{return box.items[key] or_else -1}
propagate::proc(box:$B,key:int)->(value:int,ok:bool){
    value=box.items[key] or_return
    return value,true
}
local::proc(box:$B,key:int)->bool{
    value,ok:=box.items[key]
    var:int;found:bool
    var,found=box.items[key]
    return ok && found && value==var
}
main::proc(){
    box:=Box{items=make(map[int]int)};defer delete(box.items)
    box.items[-1]=7
    value,ok:=read(box,-1);assert(ok && value==7 && local(box,-1))
    missing,exists:=read(box,2);assert(!exists && missing==0)
    assert(fallback(box,2)==-1 && fallback(box,-1)==7)
    found,success:=propagate(box,-1);assert(success && found==7)
    absent,failed:=propagate(box,2);assert(!failed && absent==0)
}
''')

    def test_run_retains_only_resolved_local_and_imported_overload_bodies(self):
        self.invoke('''package main
import "lib"
integer::proc(n:int)->int{return n+1}
text::proc(s:string)->int{_=context;return len(s)}
choose::proc{integer,text}
A::#run choose(4)
B::#run lib.choose('界')
main::proc(){assert(A==5 && B==0x754c)}
''',files={'lib/lib.thor':'''package lib
runes::proc(r:rune)->int{return int(r)}
strings::proc(s:string)->int{_=context;return len(s)}
choose::proc{runes,strings}
'''})

    def test_basic_collections_power_switches_and_owned_strings_in_debug_test_mode(self):
        self.invoke('''package integration
import _ "thor:basic" / range, filter, sum, to_set, to_slice
import "core:testing"
leaf::proc(n:int)->string{if n==0 do return "zero";return f"value {n ** 2}"}
report::fn(n:int)->string{return leaf(n)}
SEED::#run sum([]int{1,2,3})
Color::enum{Red,Blue}
CHOSEN:[2]Color:#switch SEED{case 6:[.Red,.Blue];case:[.Blue,.Red]}
@(test) combined::proc(t:^testing.T){
    data:=range(1,8);defer delete(data)
    selected:=filter(data,proc(n:int)->bool{return n in 2..=6 && n%2==0})
    defer delete(selected)
    unique:=to_set(selected);defer delete(unique)
    values:=to_slice(unique);defer delete(values)
    lowest,ok:=min(..values)
    largest:=max(..values)
    testing.expect(t,ok && lowest==2 && largest==6 && sum(values)==12)
    testing.expect(t,CHOSEN[-1]==.Blue)
    label:=report(largest);defer delete(label)
    text:=f"{switch lowest {case 2:f"{label} / {2 ** 3}";case:f"bad"}}"
    defer delete(text)
    testing.expect(t,text=="value 36 / 8")
}
''',command='test',flags=['-debug','-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_duplicate_file_declaration_precedes_compile_time_execution(self):
        declaration='allowed_size::proc($N:int)->int where N in [2,4,8]{return N}\n'
        _,result=self.invoke('package main\nspin::fn()->int{for{};return 0}\nWAIT::spin()\n'+
            declaration+declaration+'main::proc(){}\n',command='check',flags=['--no-cache','--run-timeout','0.1'],fail="Redeclaration of 'allowed_size'")
        self.assertNotIn('timed out',result.stderr)

    def test_early_redeclarations_preserve_conditional_scopes_and_blank_names(self):
        self.invoke('''package main
when true {VALUE::2}else{VALUE::4}
#if true {selected::proc()->int{return 2}}#else{selected::proc()->int{return 4}}
_::1
_::2
A::struct{value:int}
B::struct{value:int}
main::proc(){value:=VALUE;{value:=3;assert(value==3)};assert(value==2&&selected()==2)}
''')

    @unittest.skipIf(os.name=='nt','POSIX graceful compiler cancellation')
    def test_cancellation_stops_helper_and_removes_temporary_project(self):
        with tempfile.TemporaryDirectory(prefix='thor-cancellation-') as directory:
            root=Path(directory);scratch=root/'scratch';scratch.mkdir()
            source=root/'main.thor';source.write_text('package main\nspin::fn()->int{for{};return 0}\nVALUE::spin()\nmain::proc(){_=VALUE}\n')
            process=subprocess.Popen([str(base.THOR),'check',str(source),'--no-cache','--run-timeout','60','--output-dir',str(root/'out')],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={**os.environ,'TMPDIR':str(scratch)})
            try:
                deadline=time.monotonic()+15
                while time.monotonic()<deadline and process.poll() is None:
                    if list(scratch.glob('thor-run-*/helper')):break
                    time.sleep(.02)
                self.assertTrue(list(scratch.glob('thor-run-*/helper')),'Native helper did not start')
                process.send_signal(signal.SIGTERM)
                stdout,stderr=process.communicate(timeout=5)
                self.assertEqual(process.returncode,143,stderr.decode())
                self.assertNotIn(b'Traceback',stderr)
                self.assertFalse(list(scratch.glob('thor-run-*')),'Cancelled helper left its work directory')
            finally:
                if process.poll() is None:process.kill();process.communicate()

    def test_unentered_loop_posts_do_not_evaluate_known_calls(self):
        self.invoke(self.program('''
for i:=0;i<0;i=explode(){}
i:=0
for ;false;i=explode(){}
assert(i==0)
''','explode::fn()->int{assert(false,"unselected loop post");return 0}\n'))

    def test_wide_unsigned_indices_cannot_truncate_into_valid_accesses(self):
        for expression in ['_=a[index]','a[index]=9','_=a[index:]','_=a[:index]']:
            with self.subTest(expression=expression):
                self.invoke(self.program('a:=[7,8];index:u128=1<<100;'+expression),fail='Bounds check failed')

    def test_type_queries_preserve_native_rejections(self):
        for expression,message in [('cast(i32)"bad"','cast'),('[2]int{}[7]','bounds')]:
            for declaration in ['N::id(size_of(EXPR))','main::proc(){N::id(size_of(EXPR));_=N}','main::proc(){n:=id(size_of(EXPR));_=n}']:
                with self.subTest(expression=expression,declaration=declaration):
                    text='package main\nid::fn(n:int)->int{return n}\n'+declaration.replace('EXPR',expression)+'\n'
                    if declaration.startswith('N::'):text+='main::proc(){_=N}\n'
                    self.invoke(text,command='check',fail=message)

    def test_invalid_cast_in_local_storage_type_query_is_rejected(self):
        self.invoke(self.program('text:="bad";N::id(size_of(cast(i32)text));_=N',
            'id::fn(n:int)->int{return n}\n'),command='check',fail='cast')

    def test_switch_arrays_negative_index_sets_and_nested_strings_own_temporaries(self):
        self.invoke('''package main
import "core:testing"
@(test) combined::proc(t:^testing.T){
 for flag in [false,true]{
  data:>[10,20]
  text:=f"last {(switch flag{case true:data;case:[30,40]})[-1]} / {f"set {len(set[int]{1}|set[int]{2})}"}"
  testing.expect(t,text==(flag ? "last 20 / set 2" : "last 40 / set 2"))
  delete(text)
 }
}
''',command='test',flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_generic_compile_switch_nested_interpolation_and_constant_dependencies(self):
        self.invoke(self.program('assert(TEXT=="outer inner 9"&&OTHER=="outer inner 0")','''
square::fn(n:int)->int{return n*n}
text::fn($N:int)->string{return f"outer {f"inner {#switch N{case 2:square(3);case:0}}"}"}
TEXT::text(2)
OTHER::text(3)
'''))


class InteractionDiagnostics(unittest.TestCase):
    invoke=diagnostics.DiagnosticTests.invoke
    at=diagnostics.DiagnosticTests.at

    def test_type_query_rejection_preserves_unicode_source_location(self):
        source='package main\nid::fn(n:int)->int{return n}\nmain::proc(){_="😀";text:="bad";N::id(size_of(cast(i32)text));_=N}\n'
        errors=self.at(source,'text));',message='cast')
        self.assertNotIn('thor-run-',str(errors))

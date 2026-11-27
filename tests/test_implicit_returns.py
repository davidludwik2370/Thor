"""Final expressions in fn bodies use ordinary return semantics."""
import json
import os
import subprocess
import unittest
from tests import test_rust_compiler as base


class ImplicitReturnTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke
    compare=base.RustCompilerTests.compare

    def test_values_early_returns_comments_and_semicolons(self):
        text='''package main
import "core:fmt"
quad::fn(x:int)->int {4*x}
absolute::fn(x:int)->int {
    if x<0 {return -x}
    x; // the final expression still supplies the result
}
local::fn(x:int)->(result:int){n:=x+1;n*2 /* trailing comment */}
saved::fn(x:int)->int{value:=x;defer value+=1;value}
main::proc(){n:=3;fmt.println(quad(n),absolute(-5),absolute(n),local(n),saved(n))}
'''
        for flags in [[],['-debug'],base.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(text,flags=flags,expected='12 5 3 8 3')

    def test_generics_callbacks_aliases_and_compile_time_modes(self):
        text='''package main
import "core:fmt"
quad::fn(x:$T)->T{4*x}
alias::quad
apply::fn(x:$T,callback:fn(T)->T)->T{callback(x)}
A::quad(3)
B::#run apply(5,alias)
C::#run quad(i16(7))
main::proc(){n:=2;cb:fn(int)->int=quad;fmt.println(A,B,C,apply(n,cb),apply(n,fn(x:int)->int{x+1}))}
'''
        for mode in ['required','normal','aggressive','override']:
            with self.subTest(mode=mode):self.compare(text,flags=['--comptime='+mode],expected='12 20 28 8 3')

    def test_contextual_literals_and_multi_result_forwarding(self):
        self.compare('''package main
import "core:fmt"
Mode::enum{First,Second}
Result::struct{x,y:int}
record::fn(x:int)->Result{({x,x+1})}
array::fn(x:int)->[2]int{[x,x+1]}
mode::fn()->Mode{.Second}
pair::proc(x:int)->(a,b:int){return x,x+1}
forward::fn(x:int)->(a,b:int){pair(x)}
main::proc(){n:=4;a,b:=forward(n);fmt.println(record(n),array(n),mode(),a,b)}
''',flags=['-debug'],expected='Result{x = 4, y = 5} [4, 5] Second 4 5')

    def test_returned_function_literals_and_nested_proc_boundaries(self):
        self.compare('''package main
import "core:fmt"
Callback::fn(int)->int
factory::fn()->Callback {fn(x:int)->int{x*3}; ;}
work::fn(x:int)->int{
    local::proc(v:int)->int{return v+1}
    local(x)
}
main::proc(){n:=4;cb:=factory();fmt.println(cb(n),work(n))}
''',flags=['-debug'],expected='12 5')

    def test_owned_results_defer_and_runtime_copies(self):
        text='''package main
import "core:fmt"
import pure "thor:pure"
copy_values::fn(values:[]int)->[]int {values}
make_values::fn(n:int)->[]int{values:=make([]int,2);values[0]=n;values[1]=n+1;defer values[1]+=1;values}
text::fn(x:int)->string{f"value {x}"}
main::proc(){
    source:=[2,3]
    copied:=copy_values(source[:]);defer pure.destroy(copied)
    copied[0]=9
    if source[0]!=2 {panic("aliased input")}
    values:=make_values(5);defer pure.destroy(values)
    label:=text(7);defer delete(label)
    fmt.println(copied,values,label)
}
'''
        for flags in [['-debug'],base.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(text,flags=flags,expected='[9, 3] [5, 7] value 7')

    def test_allocated_compile_time_results(self):
        self.compare('''package main
import "core:fmt"
import pure "thor:pure"
values::fn(x:int)->[]int{[]int{x,x+1}}
main::proc(){a:=#run values(4);defer pure.destroy(a);b:=#run values(4);defer pure.destroy(b);a[0]=9;fmt.println(a,b)}
''',expected='[9, 5] [4, 5]')

    def test_directive_expressions_and_shadowed_panic_name(self):
        self.compare('''package main
import "core:fmt"
quad::fn(x:int)->int{4*x}
forced::fn()->int{#run quad(3)}
runtime::fn(x:int)->int{#runtime quad(x)}
converted::fn(x:i16)->int{cast(int)x}
inline_quad::fn(x:int)->int{#force_inline quad(x)}
shadowed::fn(x:int)->int{panic::fn(n:int)->int{n+1};panic(x)}
main::proc(){n:=4;fmt.println(forced(),runtime(n),converted(7),shadowed(n),inline_quad(n))}
''',flags=['-debug'],expected='12 16 7 5 16')

    def test_diverging_calls_and_void_functions_keep_existing_semantics(self):
        self.compare('''package main
import "core:fmt"
stop::proc()->!{panic("stop")}
panic_alias::panic
value::fn(x:int)->int{if x>0{return x};panic_alias("negative")}
owned::fn(x:int)->[]int{if x>0{return []int{x}};stop()}
verify::fn(x:int){assert(x>0)}
main::proc(){n:=4;verify(n);a:=owned(n);defer delete(a);fmt.println(value(n),a)}
''',flags=['-debug'],expected='4 [4]')

    def test_statement_tails_and_nested_block_tails_still_need_return(self):
        for body in ['x:=4','x:=4;x+=1','if x>0 {x} else {0}',
                     '{x}','for i in 0..<x {i}','defer assert(x>0)']:
            with self.subTest(body=body):
                self.compare('package main\nbad::fn(x:int)->int{'+body+'}\nmain::proc(){_=bad(3)}\n',
                             command='check',fail='return')

    def test_wrong_values_and_ordinary_proc_are_rejected(self):
        for signature,body in [('fn(x:int)->int','"bad"'),('fn(x:int)->(int,int)','x'),
                               ('fn(x:int)->int','assert(x>0)'),('proc(x:int)->int','x*4')]:
            with self.subTest(signature=signature,body=body):
                result=self.compare('package main\nbad::'+signature+'{'+body+'}\nmain::proc(){_=bad(3)}\n',
                                    command='check',fail='Error')
                self.assertNotIn('overlapping source transformations',result.stderr)

    def test_incomplete_tail_selector_remains_a_located_compile_error(self):
        result=self.compare('package main\nMode::enum{First}\nchoose::fn()->Mode{.}\nmain::proc(){_=choose()}\n',
                            command='check',fail="expected an identifier after '.'")
        self.assertNotIn("expected '}', got 'EOF'",result.stderr)

    def test_unchecked_shared_frontend_keeps_return_spans_and_odin_syntax(self):
        binary=self.root/('frontend.exe' if os.name=='nt' else 'frontend')
        built=subprocess.run([os.environ.get('THOR_ODIN','odin'),'build',str(base.ROOT/'frontend'),
            '-custom-attribute:synchronizes','-o:speed','-disable-assert','-no-bounds-check','-no-type-assert',
            '-out:'+str(binary)],capture_output=True,text=True,timeout=60)
        self.assertEqual(built.returncode,0,built.stderr)
        source='package main\nquad::fn(x:int)->int{4*x}\nmain::proc(){}\n'
        self.source.write_text(source)
        parsed=subprocess.run([str(binary),str(self.source)],capture_output=True,text=True,timeout=15)
        self.assertEqual(parsed.returncode,0,parsed.stderr+parsed.stdout)
        packet=json.loads(parsed.stdout)
        tails=[feature for feature in packet['features'] if feature['kind']=='ImplicitReturn']
        self.assertEqual(len(tails),1)
        tail=tails[0]
        self.assertEqual(source[tail['start']:tail['end']],'4*x')
        returns=[node for node in packet['nodes'] if node['kind']=='Return']
        self.assertEqual(len(returns),1)
        self.assertEqual(returns[0]['lists']['values'],[[tail['start'],tail['end']]])
        self.source.write_text(source.replace('fn(', 'proc('))
        native=subprocess.run([str(binary),str(self.source),'--odin'],capture_output=True,text=True,timeout=15)
        self.assertEqual(native.returncode,0,native.stderr+native.stdout)
        packet=json.loads(native.stdout)
        self.assertFalse(any(feature['kind']=='ImplicitReturn' for feature in packet['features'] or []))
        self.assertFalse(any(node['kind']=='Return' for node in packet['nodes']))


if __name__=='__main__':unittest.main()

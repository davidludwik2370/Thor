"""Pure callback contracts, erased native ABI, ownership and compile-time calls."""
import re
import unittest
from tests import test_rust_compiler as base
UNCHECKED=base.UNCHECKED

class PureCallbackTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke
    compare=base.RustCompilerTests.compare
    program=base.RustCompilerTests.program

    def test_runtime_callbacks_aliases_and_literals(self):
        source='''package main
import "core:fmt"
import "core:os"
Callback :: fn(x:int)->int
Alias :: Callback
square :: fn(x:int)->int{return x*x}
triple :: fn(x:int)->int{return x*3}
apply :: fn(val:int,func:Alias)->int{return func(val)}
main :: proc(){
    callback:Callback=square
    if len(os.args)>1 {callback=triple}
    alias:=callback
    fmt.println(apply(4,alias),apply(5,fn(x:int)->int{return x+1}))
}
'''
        for flags in [['-debug'],UNCHECKED]:
            for args,expected in [((), '16 6'),(('select',),'12 6')]:
                with self.subTest(flags=flags,args=args):self.compare(source,flags=flags,args=args,expected=expected)

    def test_generic_callbacks_and_run(self):
        self.compare('''package main
import "core:fmt"
square::fn(x:$T)->T{return x*x}
apply::fn(x:$T,cb:fn(T)->T)->T{return cb(x)}
apply_constant::fn(x:int,$cb:fn(int)->int)->int{return cb(x)}
A::#run apply(7,square)
B::#run apply_constant(8,square)
main::proc(){n:=9;fmt.println(A,B,apply(n,square))}
''',expected='49 64 81')

    def test_ordinary_proc_cannot_enter_pure_slot(self):
        for body in ['cb:Callback=impure','cb:Callback=square;cb=impure','_=apply(2,impure)',
                     'cb:=square;cb=impure','cb:=Callback(impure)']:
            with self.subTest(body=body):
                self.compare('''package main
Callback::fn(int)->int
state:int
impure::proc(x:int)->int{state+=1;return x}
square::fn(x:int)->int{return x*x}
apply::fn(x:int,cb:Callback)->int{return cb(x)}
main::proc(){'''+body+'}\n',command='check',flags=['-debug'],fail='callback contract')

    def test_nested_storage_and_return_contracts(self):
        base='''package main
Callback::fn(int)->int
Box::struct{callback:Callback}
square::fn(x:int)->int{return x*x}
ordinary::proc(x:int)->int{return x}
'''
        for extra,body in [('', 'box:=Box{ordinary};_=box'),('', 'box:=Box{square};box.callback=ordinary'),
                           ('bad::proc()->Callback{return ordinary}\n','_=bad()'),
                           ('consume::proc(p:^proc(int)->int){p^=ordinary}\n','cb:Callback=square;consume(&cb)'),
                           ('consume::proc(values:[]proc(int)->int){values[0]=ordinary}\n','values:=[]Callback{square};consume(values)')]:
            with self.subTest(body=body):self.compare(base+extra+'main::proc(){'+body+'}\n',command='check',fail='callback contract')

    def test_owned_callback_results_remain_independent(self):
        text='''package main
import "core:fmt"
Copy::fn([]int)->[]int
copy_values::fn(values:[]int)->[]int{return values}
apply::fn(values:[]int,cb:Copy)->[]int{return cb(values)}
main::proc(){values:=[2,3];cb:Copy=copy_values;result:=apply(values[:],cb);defer delete(result);result[0]=7;if values[0]!=2{panic("aliased")};fmt.println(result)}
'''
        for flags in [['-debug'],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(text,flags=flags,expected='[7, 3]')

    def test_generic_result_callbacks_and_type_parameter_forwarding(self):
        self.compare('''package main
import "core:fmt"
F::fn(int)->int
square::fn(x:int)->int{return x*x}
choose::fn(cb:F)->F{return cb}
forward::fn(x:$T)->T{return x}
apply::fn(x:$T,cb:fn(T)->T)->T{return cb(x)}
main::proc(){cb:=choose(forward(square));n:=7;fmt.println(apply(n,cb))}
''',flags=['-debug'],expected='49')

    def test_constant_callback_alias_and_inner_run(self):
        self.compare('''package main
import "core:fmt"
square::fn(x:$T)->T{return x*x}
alias::square
work::fn($cb:fn(int)->int)->int{return #run cb(7)}
main::proc(){fmt.println(work(alias))}
''',expected='49')

    def test_inferred_default_fn_and_explicit_proc_erasure(self):
        self.compare('''package main
import "core:fmt"
choose::fn(data:[2]$E,less:=fn(a,b:E)->bool{return a<b})->E{return less(data[0],data[1])?data[0]:data[1]}
square::fn(x:int)->int{return x*x}
main::proc(){plain:proc(int)->int=square;fmt.println(choose([7,3]),plain(4))}
''',expected='3 16')

    def test_mutable_collections_cannot_erase_or_inject_purity(self):
        for body in ['values:[dynamic]F;append(&values,ordinary);defer delete(values)',
                     'a:=[1]F{square};b:=[1]proc(int)->int{ordinary};copy(a[:],b[:])',
                     'cb:F=transmute(F)ordinary',
                     'cb:F=square;p:=cast(rawptr)&cb;_=p',
                     'cb:F=true?square:ordinary;_=cb',
                     'values:=[]F{square,ordinary};_=values']:
            with self.subTest(body=body):
                self.compare('''package main
F::fn(int)->int
square::fn(x:int)->int{return x*x}
ordinary::proc(x:int)->int{return x}
main::proc(){'''+body+'}\n',command='check',fail='callback contract')

    def test_forwarded_multi_results_cannot_hide_impure_callbacks(self):
        self.compare('''package main
F::fn(int)->int
ordinary::proc(x:int)->int{return x}
source::proc()->(int,proc(int)->int){return 1,ordinary}
bad::proc()->(int,F){return source()}
main::proc(){_,_=bad()}
''',command='check',fail='callback contract')
        for declaration in ['a,b:F=source()', 'a:F=square;b:F=square;a,b=source()']:
            with self.subTest(declaration=declaration):
                self.compare('''package main
F::fn(int)->int
square::fn(x:int)->int{return x*x}
ordinary::proc(x:int)->int{return x}
source::proc()->(F,proc(int)->int){return square,ordinary}
main::proc(){'''+declaration+';_=a;_=b}\n',command='check',fail='callback contract')

    def test_unknown_runtime_callback_is_not_compile_time_constant(self):
        self.compare('''package main
F::fn(int)->int
square::fn(x:int)->int{return x*x}
apply::fn(cb:F)->int{return #run cb(7)}
main::proc(){cb:F=square;_=apply(cb)}
''',command='check',fail='#run')

    def test_void_fn_types_and_nil_checks(self):
        self.compare('''package main
Action::#type fn()
noop::fn(){}
apply::fn(cb:Action){if cb!=nil{cb()}}
main::proc(){apply(noop);apply(nil)}
''',flags=['-debug'],expected='')

    def test_generic_callback_type_parameters(self):
        self.compare('''package main
import "core:fmt"
square::fn(x:int)->int{return x*x}
apply::fn(value:int,callback:$F)->int{return callback(value)}
VALUE::#run apply(6,square)
main::proc(){cb:=square;n:=7;fmt.println(VALUE,apply(n,cb))}
''',expected='36 49')
        self.compare('''package main
ordinary::proc(x:int)->int{return x}
apply::fn(value:int,callback:$F)->int{return callback(value)}
main::proc(){_=apply(7,ordinary)}
''',command='check',fail='ordinary procedure values')

    def test_using_fields_and_imported_aliases(self):
        self.compare('''package main
import "library" / Callback, square
import "core:fmt"
Box::struct{callback:Callback}
apply::fn(x:int,box:Box)->int{using box;return callback(x)}
VALUE::#run apply(8,Box{square})
main::proc(){box:=Box{square};using box;fmt.println(VALUE,callback(7))}
''',files={'library/main.thor':'package library\nCallback::fn(int)->int\nsquare::fn(x:$T)->T{return x*x}\n'},expected='64 49',directory=True)

    def test_callback_signature_cannot_hide_impure_nesting(self):
        for prefix,body,message in [
            ('F::fn(int)->int\nBad::fn "c"(int)->int','', 'Odin calling convention'),
            ('F::fn(int)->int\nBad::struct #raw_union{cb:F,bits:uintptr}','', 'Raw unions'),
            ('F::fn(int)->int\nBox::struct($T:typeid) #raw_union{value:T,bits:uintptr}\nBad::Box(F)','', 'Raw unions'),
            ('F::fn(int)->int\nforeign import lib "system:c"\nforeign lib{get::proc()->F---}', '', 'bodyless'),
            ('F::fn(int)->int', 'cb:F=---;_=cb', 'Uninitialized'),
            ('F::fn(int)->int\nsquare::fn(x:int)->int{return x*x}\neq::fn(a,b:F)->bool{return a==b}', '_=eq(square,square)', 'pointer identities'),
        ]:
            with self.subTest(prefix=prefix):
                self.compare('package main\n'+prefix+'\nmain::proc(){'+body+'}\n',command='check',fail=message)

    def test_overload_selection_preserves_purity(self):
        prefix='''package main
F::fn(int)->int
square::fn(x:int)->int{return x*x}
float_value::proc(x:f64)->f64{return x}
functions::proc{square,float_value}
'''
        self.compare(prefix+'main::proc(){cb:F=functions;if cb(7)!=49{panic("overload")}}\n',flags=['-debug'],expected='')
        self.compare(prefix.replace('square::fn','square::proc')+'main::proc(){cb:F=functions;_=cb}\n',command='check',fail='callback contract')

    def test_type_queries_and_safe_callback_conversions(self):
        self.compare('''package main
import "core:fmt"
square::fn(x:int)->int{return x*x}
F::type_of(square)
Alias::(F)
convert::fn(cb:Alias)->F{return F(cb)}
main::proc(){cb:=F(square);other:=convert(cb);fmt.println(cb(7),other(8))}
''',flags=['-debug'],expected='49 64')

    def test_callback_abi_and_dispatch_match_plain_proc_in_native_ir(self):
        text='''package main
import "core:fmt"
import "core:os"
F::fn(int)->int
square::fn(x:int)->int{return x*x}
triple::fn(x:int)->int{return x*3}
apply_fn::#force_no_inline fn(x:int,cb:F)->int{return cb(x)}
apply_proc::#force_no_inline proc(x:int,cb:proc(int)->int)->int{return cb(x)}
constant_fn::#force_no_inline fn(x:int,$cb:F)->int{return cb(x)}
constant_proc::#force_no_inline proc(x:int,$cb:proc(int)->int)->int{return cb(x)}
main::proc(){cb:F=square;if len(os.args)>1{cb=triple};n:=len(os.args);fmt.println(apply_fn(n,cb),apply_proc(n,cb),constant_fn(n,square),constant_proc(n,square))}
'''
        out=self.root/'llvm';out.mkdir()
        self.compare(text,command='build',flags=[*UNCHECKED,'-use-single-module','-build-mode:llvm-ir','-out:'+str(out)])
        ir='\n'.join(path.read_text() for path in out.glob('*.ll'))
        bodies={name:(head,body) for head,body in re.findall(r'^(define[^\n]+)\n(.*?)^}',ir,re.M|re.S)
                for name in ['apply_fn','apply_proc','constant_fn','constant_proc'] if re.search(r'(?:\.|::)'+name+r'(?=[:"(])',head)}
        self.assertEqual(set(bodies),{'apply_fn','apply_proc','constant_fn','constant_proc'},'\n'.join(head for head,_ in re.findall(r'^(define[^\n]+)\n(.*?)^}',ir,re.M|re.S) if 'apply' in head or 'constant' in head))
        for name,(head,body) in bodies.items():
            self.assertEqual(len(re.findall(r'\bcall[^\n]* %[^\n]*\(',body)),int(name.startswith('apply')),(head,body))
            self.assertNotIn('scratch',body)
            self.assertNotIn('clone',body)
        fn_args=bodies['apply_fn'][0].rsplit('"(',1)[1]
        proc_args=bodies['apply_proc'][0].rsplit('"(',1)[1]
        self.assertEqual(fn_args,proc_args)
        self.assertEqual(bodies['constant_fn'][0].rsplit('"(',1)[1],bodies['constant_proc'][0].rsplit('"(',1)[1])

    def test_generic_record_callback_type_arguments(self):
        self.compare('''package main
F::fn(int)->int
Box::struct($T:typeid){value:T}
square::fn(x:int)->int{return x*x}
read::fn(box:Box(F),n:int)->int{return box.value(n)}
main::proc(){box:=Box(F){square};if read(box,7)!=49{panic("generic record")}}
''',flags=['-debug'],expected='')

    def test_selected_generic_branches_and_callback_factories(self):
        prefix='''package main
F::fn(int)->int
square::fn(x:int)->int{return x*x}
ordinary::proc(x:int)->int{return x}
select::proc($T:typeid)->F{when T==int{return square}else{return ordinary}}
factory::fn()->F{return fn(x:int)->int{return x+2}}
'''
        for flags in [['-debug'],[],UNCHECKED]:
            with self.subTest(flags=flags):
                self.compare(prefix+'main::proc(){cb:=select(int);other:=factory();if cb(7)!=49||other(8)!=10{panic("callbacks")}}\n',flags=flags,expected='')
        self.compare(prefix+'main::proc(){_=select(string)}\n',command='check',fail='callback contract')

    def test_constant_inline_callbacks_and_captured_types_with_inner_run(self):
        for literal in ['fn(x:int)->int{return x*x}','fn(x:$T)->T{return x*x}']:
            with self.subTest(literal=literal):
                self.compare('package main\nimport "core:fmt"\nwork::fn($cb:fn(int)->int)->int{return #run cb(7)}\nmain::proc(){fmt.println(work('+literal+'))}\n',expected='49')
        self.compare('''package main
import "core:fmt"
work::fn($cb:fn(int)->int)->int{return #run cb(7)}
outer::proc(value:$E)->int{return work(fn(x:E)->int{return int(x)*2})}
main::proc(){fmt.println(outer(7))}
''',expected='14')

    def test_constant_imported_record_callbacks(self):
        self.compare('''package main
import "library"
import "core:fmt"
work::fn($cb:library.Callback)->int{return #run cb(library.Record{7})}
main::proc(){fmt.println(work(library.read))}
''',files={'library/main.thor':'''package library
Record::struct{value:int}
Callback::fn(Record)->int
read::fn(value:Record)->int{return value.value}
'''},directory=True,expected='7')

    def test_owned_records_keep_callbacks_during_recursive_clone_and_destroy(self):
        text='''package main
import "core:fmt"
import "thor:pure"
F::fn(int)->int
Box::struct{callback:F,values:[]int}
square::fn(x:int)->int{return x*x}
create::fn(n:int)->Box{return {square,[]int{n,n+1}}}
main::proc(){n:=3;box:=create(n);defer pure.destroy(box);box.values[0]=7;fmt.println(box.callback(8),box.values)}
'''
        for flags in [['-debug'],[*UNCHECKED,'--track-allocations=basic']]:
            with self.subTest(flags=flags):
                self.compare(text,flags=flags,expected='64 [7, 4]')

    def test_readonly_callback_contracts_with_compile_time_literals(self):
        self.compare('''package main
import "core:fmt"
first::fn(#ro values:[]int)->int{return values[0]}
work::fn($cb:fn(#ro values:[]int)->int)->int{return #run cb([]int{7})}
main::proc(){fmt.println(work(first))}
''',expected='7')

    def test_computed_callback_can_run_inside_compile_time_expression(self):
        self.compare('''package main
import "core:fmt"
F::fn(int)->int
make::fn()->F{return fn(x:int)->int{return x*2}}
apply::fn(x:int,cb:F)->int{return cb(x)}
VALUE::#run apply(7,make())
main::proc(){fmt.println(VALUE)}
''',expected='14')

    def test_native_type_comparisons_cannot_hide_impure_callback_branches(self):
        prefix='''package main
F::fn(x:int)->int
P::proc(x:int)->int
square::fn(x:int)->int{return x*x}
ordinary::proc(x:int)->int{return x}
'''
        for condition in ['F==P','type_of(square)==F','[1]F==[1]P','(proc(F)->F)==(proc(P)->P)']:
            with self.subTest(condition=condition):
                bad=prefix+'choose::proc()->F{when '+condition+'{return ordinary}else{return square}}\nmain::proc(){_=choose()}\n'
                self.compare(bad,command='check',fail='callback contract')
                good=bad.replace('{return ordinary}else{return square}','{return square}else{return ordinary}')
                self.compare(good,flags=['-debug'],expected='')
        named=prefix.replace('P::proc(x:int)','P::proc(y:int)')
        good=named+'choose::proc()->F{when F==P{return ordinary}else{return square}}\nmain::proc(){_=choose()}\n'
        self.compare(good,flags=['-debug'],expected='')
        self.compare(good.replace('{return ordinary}else{return square}','{return square}else{return ordinary}'),command='check',fail='callback contract')

    def test_native_proc_trait_preserves_generic_callback_contracts(self):
        prefix='''package main
import "base:intrinsics"
F::fn(int)->int
square::fn(x:int)->int{return x*x}
ordinary::proc(x:int)->int{return x}
choose::proc(value:$T)->F{when intrinsics.type_is_proc(T){return value}else{return square}}
'''
        self.compare(prefix+'main::proc(){cb:=choose(square);other:=choose(7);if cb(3)!=9||other(4)!=16{panic("traits")}}\n',flags=['-debug'],expected='')
        self.compare(prefix+'main::proc(){_=choose(ordinary)}\n',command='check',fail='callback contract')

if __name__=='__main__':unittest.main()

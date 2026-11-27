"""Final expressions in fn bodies use ordinary return semantics."""
import json
import os
import re
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

    def test_tail_switch_generic_power_and_runtime_views_agree_across_modes(self):
        source = '''package main
import "core:fmt" / println
score :: fn(row: [2]$T, i: int) -> T {
    switch i {
    case < 0: row[i] ** 2
    case: row[i]
    }
}
expected :: #run score([2]int{2,3}, -1)
main :: proc() {
    rows ~:= make([][2]int, 2)
    rows[0] = {2,3}
    rows[1] = {4,5}
    total := 0
    for row, (i: u8) in rows {
        total += score(row, int(i)-1)
    }
    println(f"{expected}/{total}")
}
'''
        for mode in ('required', 'normal', 'aggressive', 'override'):
            for flags in ([], ['-disable-assert'], base.UNCHECKED):
                with self.subTest(mode=mode, flags=flags):
                    self.compare(source, flags=['--comptime='+mode, *flags], expected='9/13')

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
import "core:slice"
copy_values::fn(values:[]int)->[]int {slice.clone(values)}
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
values::fn(x:int)->[]int{pure.clone([]int{x,x+1})}
main::proc(){a:=#run values(4);defer pure.destroy(a);b:=#run values(4);defer pure.destroy(b);a[0]=9;fmt.println(a,b)}
''',expected='[9, 5] [4, 5]')

    def test_trailing_comparison_and_selectorless_switches(self):
        text='''package main
import "core:fmt"
f4::fn(x:int)->int {
    switch x {
    case < 10: 0
    case < 20: 1
    case < 30: 2
    case:      3
    }; // tail comments and semicolons keep the value
}
choose::fn(x:int)->int{switch{case x<0:-x;case:x}}
main::proc(){n:=12;fmt.println(f4(n),f4(25),f4(40),choose(-4),choose(n))}
'''
        for flags in [[],['-debug'],base.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(text,flags=flags,expected='1 2 3 4 12')

    def test_switch_tail_generics_callbacks_and_compile_time_modes(self):
        text='''package main
import "core:fmt"
choose::fn(x:$T)->T{switch x{case < 10:x+1;case:x*2}}
alias::choose
apply::fn(x:int,callback:fn(int)->int)->int{callback(x)}
compiled::fn(x:$T)->T{#switch{case T==int:x+3;case:undefined}}
A::choose(4)
B::#run apply(12,alias)
C::compiled(6)
main::proc(){n:=14;callback:fn(int)->int=choose;fmt.println(A,B,C,apply(n,callback))}
'''
        for mode in ['required','normal','aggressive','override']:
            with self.subTest(mode=mode):self.compare(text,flags=['--comptime='+mode],expected='5 24 9 28')

    def test_switch_tail_multi_results_and_contextual_literals(self):
        self.compare('''package main
import "core:fmt"
Mode::enum{Low,High}
Record::struct{value:int}
pair::fn(x:int)->(number:int,mode:Mode){switch x{case < 10:x,.Low;case:x+1,.High}}
forward::fn(x:int)->(int,Mode){switch x{case < 0:pair(1);case:pair(x)}}
record::fn(x:int)->Record{switch x{case < 10:{x};case:{x+1}}}
nested::fn(x:int)->int{switch x{case < 0:switch{case x < -5:1;case:2};case:3}}
main::proc(){n:=12;a,b:=forward(n);fmt.println(a,b,record(n),nested(-2),nested(n))}
''',flags=['-debug'],expected='13 High Record{value = 13} 2 3')

    def test_compile_partial_directive_wrapped_and_composed_switch_tails(self):
        self.compare('''package main
import "core:fmt"
Mode::enum{First,Second}
Record::struct{a,b:int}
compiled::fn()->int{#switch 2{case 1:undefined;case:7}}
partial::fn(x:Mode)->int{#partial switch x{case .First:1;case:2}}
complete::fn(x:Mode)->int{switch x{case .First:4;case .Second:5}}
literal::fn(x:int)->Record{#partial Record{a=x,b=x+1}}
wrapped::fn(x:int)->int{#no_bounds_check #type_assert switch x{case < 0:0;case:x}}
composed::fn(x:int)->int{switch x{case < 0:0;case:x}+2}
main::proc(){n:=3;fmt.println(compiled(),partial(.Second),complete(.First),literal(n),wrapped(n),composed(n))}
''',flags=['-debug'],expected='7 2 4 Record{a = 3, b = 4} 3 5')

    def test_owned_switch_tails_keep_input_views_independent(self):
        text='''package main
import "core:fmt"
import pure "thor:pure"
choose::fn(values:[]int,copy:bool)->[]int{switch{case copy:pure.clone(values);case:pure.clone([]int{7,8})}}
main::proc(){source:=[2,3];a:=choose(source[:],true);defer pure.destroy(a);b:=#run choose([]int{4,5},true);defer pure.destroy(b);a[0]=9;b[0]=6;if source[0]!=2{panic("aliased switch result")};fmt.println(a,b,source)}
'''
        for flags in [['-debug'],base.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(text,flags=flags,expected='[9, 3] [6, 5] [2, 3]')

    def test_explicit_and_implicit_owned_switch_arms_and_multi_results(self):
        template='''package main
import "core:fmt"
import pure "thor:pure"
choose::fn(values:[]int,n:int)->[]int{RETURN switch n{case < 0:switch n{case -1:pure.clone(values);case:pure.clone([]int{7,8})};case:pure.clone([]int{9,10})}}
pair::fn(values:[]int,n:int)->(data:[]int,count:int){RETURN switch n{case < 0:pure.clone(values),n;case:pure.clone([]int{6,7}),n+1}}
compiled::fn()->([]int,int){RETURN #switch{case true:pure.clone([]int{4,5}),2;case:undefined,undefined}}
saved::fn(n:int)->[]int{data:=make([]int,1);data[0]=n;defer data[0]+=1;RETURN switch n{case < 0:pure.clone([]int{0});case:data}}
main::proc(){
 source:=[2,3];n:=-1
 a:=choose(source[:],n);defer pure.destroy(a)
 b:=choose(source[:],-2);defer pure.destroy(b)
 c:=choose(source[:],1);defer pure.destroy(c)
 data,count:=pair(source[:],0);defer pure.destroy(data)
 values,size:=compiled();defer pure.destroy(values)
 after:=saved(5);defer pure.destroy(after)
 a[0]=11;if source[0]!=2{panic("borrowed switch result")}
 fmt.println(a,b,c,data,count,values,size,after)
}
'''
        for keyword in ['', 'return ']:
            for flags in [['-debug'],base.UNCHECKED]:
                with self.subTest(keyword=keyword,flags=flags):
                    self.compare(template.replace('RETURN ',keyword),flags=flags,
                                 expected='[11, 3] [7, 8] [9, 10] [6, 7] 1 [4, 5] 2 [6]')

    def test_owned_switch_diverging_arms_and_callback_results(self):
        self.compare('''package main
import "core:fmt"
import pure "thor:pure"
Callback::fn(int)->int
twice::fn(x:int)->int{x*2}
factory::fn(x:int)->Callback{switch x{case 0:fn(v:int)->int{v+1};case:twice}}
apply::fn(x:int)->int{callback:=factory(0);callback(x)}
guarded::fn(n:int)->[]int{switch n{case < 0:panic("negative");case:pure.clone([]int{n})}}
never::fn(n:int)->[]int{switch n{case < 0:panic("negative");case:panic("positive")}}
A::#run apply(4)
main::proc(){n:=3;callback:=factory(n);values:=guarded(n);defer delete(values);fmt.println(A,callback(n),values)}
''',flags=base.UNCHECKED,expected='5 6 [3]')

    def test_owned_record_and_big_number_switch_tails(self):
        text='''package main
import "core:fmt"
import "thor:big"
import pure "thor:pure"
Report::struct{values:[]int,count:int}
record::fn(n:int)->Report{switch n{case 0:{pure.clone([]int{n}),n};case:{pure.clone([]int{n+1}),n+1}}}
integer::fn(n:int)->Integer{switch n{case < 0:Integer(-n);case:Integer(n+1)}}
rational::fn(n:int)->Rational{switch n{case 0:Rational(1,3);case:Rational(n,2)}}
main::proc(){
 n:=3;report:=record(n);defer pure.destroy(report)
 a:=integer(n);defer delete(a);b:=rational(n);defer delete(b)
 c:=#run integer(4);defer delete(c);a+=2
 s:=big.to_string(a);defer delete(s);t:=big.to_string(b);defer delete(t);u:=big.to_string(c);defer delete(u)
 fmt.println(report.values,report.count,s,t,u)
}
'''
        for flags in [['-debug'],[*base.UNCHECKED,'--track-allocations=basic']]:
            with self.subTest(flags=flags):
                result=self.compare(text,flags=flags,expected='[4] 4 6 3/2 5')
                self.assertEqual(result.stderr,'')

    def test_statement_switches_and_non_tail_values_keep_existing_behavior(self):
        self.compare('''package main
import "core:fmt"
Union::union{int,string}
explicit::fn(x:int)->int{switch x{case < 10:return x;case:return x+1}}
initialized::fn(x:int)->int{switch n:=x+1;n{case < 10:return n;case:return 0}}
typed::fn(value:Union)->int{switch item in value{case int:return item;case string:return len(item);case:return 0}}
non_tail::fn(x:int)->int{n:=0;switch x{case < 10:n=1;case:n=2};n}
labeled::fn(x:int)->int{out:switch x{case 0:break out;case:return x};3}
void::fn(x:int){switch x{case 0:assert(x==0);case:assert(x!=0)}}
ordinary::proc(x:int)->int{switch x{case < 10:return x;case:return 0}}
main::proc(){n:=4;void(n);fmt.println(explicit(n),initialized(n),typed(Union("abc")),non_tail(n),labeled(0),ordinary(n))}
''',flags=['-debug'],expected='4 5 3 1 3 4')

    def test_invalid_switch_tails_use_existing_expression_diagnostics(self):
        for result,expression in [
            ('int','switch x{case < 10:1}'),
            ('int','switch x{case 0:1;case:break}'),
            ('int','switch x{case 0:1;case:"wrong"}'),
            ('[]int','switch x{case 0:[]int{1};case:"wrong"}'),
            ('(int,int)','switch x{case 0:1,2;case:3}'),
            ('int','#partial switch x{case < 10:1}'),
        ]:
            with self.subTest(expression=expression):
                call='a,b:=f(4);_=a;_=b' if result=='(int,int)' else '_=f(4)'
                implicit='package main\nf::fn(x:int)->'+result+'{'+expression+'}\nmain::proc(){'+call+'}\n'
                actual=self.compare(implicit,command='check',fail='Error')
                explicit=self.compare(implicit.replace('{'+expression+'}','{return '+expression+'}'),command='check',fail='Error')
                self.assertNotIn('overlapping source transformations',actual.stderr)
                self.assertNotIn('__thor_',actual.stderr)
                self.assertNotIn('could not build',actual.stderr)
                self.assertEqual(re.findall(r'^Error:.*',actual.stderr,re.M),re.findall(r'^Error:.*',explicit.stderr,re.M))

    def test_related_conditional_comprehension_and_fstring_tails(self):
        self.compare('''package main
import "core:fmt"
conditional::fn(x:int)->int{x if x>0 else -x}
compile_choice::fn(x:$T)->T{x+1 when T==int else x}
ternary::fn(x:int)->int{x>0 ? x : -x}
values::fn(x:int)->[]int{[n*2 for n in 0..<x]}
text::fn(x:int)->string{f"value {x}"}
maybe::proc(x:int)->(int,bool){return x,x>0}
fallback::fn(x:int)->int{maybe(x) or_else 5}
contains::fn(x:int)->bool{x in 0..<4}
main::proc(){n:=3;a:=values(n);defer delete(a);s:=text(n);defer delete(s);fmt.println(conditional(-n),compile_choice(n),ternary(-n),a,s,fallback(-1),contains(n))}
''',flags=['-debug'],expected='3 4 3 [0, 2, 4] value 3 5 true')

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
import pure "thor:pure"
stop::proc()->!{panic("stop")}
panic_alias::panic
value::fn(x:int)->int{if x>0{return x};panic_alias("negative")}
owned::fn(x:int)->[]int{if x>0{return pure.clone([]int{x})};stop()}
verify::fn(x:int){assert(x>0)}
main::proc(){n:=4;verify(n);a:=owned(n);defer delete(a);fmt.println(value(n),a)}
''',flags=['-debug'],expected='4 [4]')

    def test_statement_tails_and_nested_block_tails_still_need_return(self):
        for body in ['x:=4','x:=4;x+=1','if x>0 {x} else {0}',
                     '{x}','for i in 0..<x {i}','defer assert(x>0)']:
            with self.subTest(body=body):
                self.compare('package main\nbad::fn(x:int)->int{'+body+'}\nmain::proc(){_=bad(3)}\n',
                             command='check',fail='return')

    def test_speculative_switch_records_are_rewound_without_losing_functions(self):
        functions='\n'.join('f'+str(i)+'::fn(x:int)->int{switch x{case 0:consume(fn(v:int)->int{local:>v+1;local});return x;case:return x+1}} // retained comment '+str(i) for i in range(40))
        text='package main\nimport "core:fmt"\nconsume::fn(callback:fn(int)->int){_=callback(0)}\n'+functions+'\nmain::proc(){n:=0;fmt.println(f0(n),f39(n))}\n'
        self.compare(text,flags=['-debug'],expected='0 0')

    def test_do_and_nested_statement_switches_retain_existing_restrictions(self):
        self.compare('package main\nf::fn(x:int)->int do switch x{case 0:1;case:2}\nmain::proc(){_=f(3)}\n',
                     command='check',fail="'do' for procedure bodies is not allowed")
        for body in ['{switch x{case 0:1;case:2}}','if x>0{switch x{case 1:1;case:2}}',
                     'switch x{case 0:x=1;case:x=2}']:
            with self.subTest(body=body):
                self.compare('package main\nf::fn(x:int)->int{'+body+'}\nmain::proc(){_=f(3)}\n',command='check',fail='Error')

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
        source='package main\nquad::fn(x:int)->int{4*x}\nchoose::fn(x:int)->int{switch x{case 0:1;case:2}}\nmain::proc(){}\n'
        self.source.write_text(source)
        parsed=subprocess.run([str(binary),str(self.source)],capture_output=True,text=True,timeout=15)
        self.assertEqual(parsed.returncode,0,parsed.stderr+parsed.stdout)
        packet=json.loads(parsed.stdout)
        tails=[feature for feature in packet['features'] if feature['kind']=='ImplicitReturn']
        self.assertEqual(len(tails),2)
        tail=next(item for item in tails if source[item['start']:item['end']]=='4*x')
        self.assertEqual(source[tail['start']:tail['end']],'4*x')
        self.assertTrue(any(source[item['start']:item['end']]=='switch x{case 0:1;case:2}' for item in tails))
        returns=[node for node in packet['nodes'] if node['kind']=='Return']
        self.assertEqual(len(returns),2)
        returned=next(node for node in returns if node['start']==tail['start'])
        self.assertEqual(returned['lists']['values'],[[tail['start'],tail['end']]])
        self.source.write_text(source.replace('fn(', 'proc('))
        native=subprocess.run([str(binary),str(self.source),'--odin'],capture_output=True,text=True,timeout=15)
        self.assertEqual(native.returncode,0,native.stderr+native.stdout)
        packet=json.loads(native.stdout)
        self.assertFalse(any(feature['kind']=='ImplicitReturn' for feature in packet['features'] or []))
        self.assertFalse(any(node['kind']=='Return' for node in packet['nodes']))
        body='v+1'
        for _ in range(20):
            body='switch v{case 0:consume(fn(v:int)->int{'+body+'});return v;case:return v+1}'
        self.source.write_text('package main\nconsume::fn(callback:fn(int)->int){_=callback(0)}\nf::fn(v:int)->int{'+body+'}\nmain::proc(){}\n')
        nested=subprocess.run([str(binary),str(self.source)],capture_output=True,text=True,timeout=15)
        self.assertEqual(nested.returncode,0,nested.stderr+nested.stdout)
        packet=json.loads(nested.stdout)
        self.assertEqual(len([feature for feature in packet['features'] if feature['kind']=='ImplicitReturn']),1)
        spans=[(edit['start'],edit['end']) for edit in packet['edits']]
        self.assertEqual(len(spans),len(set(spans)))


if __name__=='__main__':unittest.main()

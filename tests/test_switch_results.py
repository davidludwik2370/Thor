"""Native multiple-value semantics and assignment-only switch cancellation."""
import unittest
from tests import test_thor as base


class SwitchResultTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_both_selector_forms_and_lazy_ordered_results(self):
        self.invoke(self.program('''
order=0
a,b,c:=switch mark(2){case 1:mark(9),"bad",false;case 2:mark(3),"ok",true;case:mark(8),"bad",false}
assert(a==3&&b=="ok"&&c&&order==23)
order=0
a,b,c=switch{case mark(1)==0:mark(9),"bad",false;case mark(2)==2:mark(4),"yes",false;case:mark(8),"bad",true}
assert(a==4&&b=="yes"&&!c&&order==124)
''','order:int\nmark::proc(x:int)->int{order=order*10+x;return x}\n'))

    def test_context_per_destination_with_promoted_fields(self):
        self.invoke(self.program('r:=Record{};write(&r);assert(r.a==2&&r.b==3&&r.kind==.B&&r.entry.x==4)', '''
Deg::distinct f64
Kind::enum{A,B}
Entry::struct{x:int}
Record::struct{a:f32,b:Deg,kind:Kind,entry:Entry}
write::proc(using r:^Record){a,b,kind,entry=switch{case true:2,3,.B,{4};case:5,6,.A,{7}}}
'''))

    def test_swaps_aliases_and_original_lhs_indices(self):
        self.invoke(self.program('''
a,b:=1,2;a,b=(switch{case true:b,a;case:a,b});assert(a==2&&b==1)
values:=[4,5];i:=0
i,values[i]=switch{case true:1,9;case:0,0}
assert(i==1&&values[0]==9&&values[1]==5)
a,a=switch{case true:7,8;case:0,0};assert(a==8)
'''))

    def test_multi_result_calls_nested_switches_and_returns(self):
        self.invoke(self.program('a,b:=choose(1);assert(a==7&&b=="ok");a,b=choose(2);assert(a==8&&b=="nested")', '''
pair::proc()->(int,string){return 7,"ok"}
choose::proc(n:int)->(int,string){return switch n{case 1:pair();case:switch{case true:8,"nested";case:9,"bad"}}}
'''))

    def test_inferred_arrays_strings_sets_and_later_operations(self):
        self.invoke(self.program('''
ary,text,names:=switch{case true:[1,2],"ok",set[string]{"a"};case:[3,4],"bad",set[string]{"b"}}
assert(ary[-1]==2&&text[0]=='o'&&"a" in names)
defer delete(names)
'''))

    def test_skip_single_multi_nondefault_and_parenthesized(self):
        self.invoke(self.program('''
a,b:=1,2
a,b=(switch{case false:3,4;case:break});assert(a==1&&b==2)
a,b=switch 1{case 1:break;case:3,4};assert(a==1&&b==2)
a=switch{case false:9;case:break};assert(a==1)
a,b=switch{case true:b,a;case:break};assert(a==2&&b==1)
'''))

    def test_skip_preserves_lhs_evaluation_order_and_negative_index(self):
        self.invoke(self.program('''
data:=[4,5];order=0
data[index()],data[-1]=switch mark(2){case 2:mark(3),mark(4);case:break}
assert(order==1234&&data==[3,4])
order=0
data[index()],data[-1]=switch mark(2){case 1:8,9;case:break}
assert(order==12&&data==[3,4])
''','order:int\nindex::proc()->int{order=order*10+1;return 0}\nmark::proc(x:int)->int{order=order*10+x;return x}\n'))

    def test_skip_generic_arrays_slices_maps_and_map_keys(self):
        self.invoke(self.program('''
a:=[4,5];i:=0;write(&a,&i,true);assert(i==1&&a[0]==9&&a[1]==5)
s:=a[:];i=0;write(&s,&i,false);assert(i==0&&s[0]==9)
m:map[int]int;defer delete(m);i=0
write(&m,&i,true);assert(i==1&&m[0]==9)
i=0;write(&m,&i,false);assert(i==0&&m[0]==9)
m[-1]=switch{case true:3;case:break};assert(m[-1]==3)
''','write::proc(a:^$T,i:^int,apply:bool){i^,a^[i^]=switch{case apply:1,9;case:break}}\n'))

    def test_skip_for_post_and_outer_loop_control(self):
        self.invoke(self.program('''
a:=1;visited:=0
outer:for i in 0..<3{a=switch i{case 1:break;case:a+1};visited+=1}
assert(a==3&&visited==3)
for i:=0;i<3;i=switch{case true:i+1;case:break}{visited+=1}
assert(visited==6)
'''))

    def test_compile_switch_multi_constants_visibility_and_unchecked_arms(self):
        self.invoke(self.program('''
assert(A==2&&B=="ok")
x,y::(#switch{case true:3,"local";case:undefined,undefined})
#assert(x==3&&y=="local")
a,b:=#switch 2{case 1:undefined,undefined;case 2:7,"yes";case:undefined,undefined}
assert(a==7&&b=="yes")
a,b=#switch{case false:undefined,undefined;case:9,"last"};assert(a==9&&b=="last")
c,d::#switch{case true:5,"typed";case:undefined}
#assert(c==5&&d=="typed")
''','A,B::#switch 2{case 2:2,"ok";case:undefined,undefined}\n'))

    def test_compile_switch_skip_and_multi_result_return(self):
        self.invoke(self.program('''
a,b:=f();assert(a==1&&b=="yes")
a,b=#switch{case true:break;case:undefined,undefined};assert(a==1&&b=="yes")
a,b=#switch{case false:break;case:3,"ok"};assert(a==3&&b=="ok")
''','f::proc()->(int,string){return #switch{case true:1,"yes";case:undefined,undefined}}\n'))

    def test_pure_and_run_aggregate_with_multi_assignment(self):
        self.invoke(self.program('assert(R.a==2&&R.b==1);r:=f(Record{3,4});assert(r.a==4&&r.b==3)', '''
Record::struct{a,b:int}
f::fn(value:Record)->Record{r:=value;r.a,r.b=switch{case true:r.b,r.a;case:panic("bad")};return r}
R::#run f(Record{1,2})
'''))

    def test_nonreturning_calls_supply_no_multi_result_values(self):
        self.invoke(self.program('a,b:=f(true);assert(a==3&&b=="ok")',
            'f::proc(ok:bool)->(int,string){return switch{case ok:3,"ok";case:panic("bad")}}\n'))
        self.invoke(self.program('a,b:=switch{case true:1,2;case:no_value()};if a!=1||b!=2{panic("wrong results")}',
            'no_value::proc()->!{panic("bad")}\n'))

    def test_mismatched_arity_and_missing_default(self):
        for expr in ['switch{case true:1,2;case:3,4,5}', 'switch{case true:1,2}']:
            with self.subTest(expr=expr):
                self.invoke(self.program('a,b:='+expr+';_=a;_=b'),command='check',fail='Error:')
        self.invoke(self.program('a,b:=switch{case true:1,2;case:3};_=a;_=b'),command='check',fail='Error:')

    def test_break_rejected_in_value_and_declaration_contexts(self):
        for body in ['a:=switch{case true:1;case:break};_=a',
                     'a:>switch{case true:1;case:break};_=a',
                     'a:int=switch{case true:1;case:break};_=a',
                     'a:=1;a+=switch{case true:1;case:break}',
                     'a:=1;a=1+(switch{case true:1;case:break})',
                     '_=len(switch{case true:"ok";case:break})',
                     'a,b:=switch{case true:1,2;case:break};_=a;_=b']:
            with self.subTest(body=body):
                self.invoke(self.program(body),command='check',fail='break in a switch expression')

    def test_readonly_destinations_still_rejected(self):
        self.invoke(self.program('a:>1;b:=2;a,b=switch{case true:3,4;case:break}'),command='check',fail='immutable')

    def test_call_argument_expansion_with_independent_contexts(self):
        self.invoke(self.program('take(switch{case true:.B,{7};case:.A,{9}})', '''
Kind::enum{A,B}
Record::struct{x:int}
take::proc(kind:Kind,record:Record,extra:=9){assert(kind==.B&&record.x==7&&extra==9)}
'''))

    def test_global_multiple_results_initialize_once_and_in_dependency_order(self):
        self.invoke(self.program('assert(count==1&&a==7&&text=="ok"&&later==8&&values[-1]==2&&nested_n==9&&nested_s=="nested")', '''
count:int
mark::proc "contextless"()->int{count+=1;return 7}
a,text:=switch{case true:mark(),"ok";case:0,"bad"}
later:=a+1
values,other:>switch{case true:[1,2],3;case:[4,5],6}
nested_n,nested_s:=switch{case true:(switch{case true:9;case:0}),"nested";case:0,"other"}
'''))

    def test_generic_multi_call_arms_and_compile_return_call(self):
        self.invoke(self.program('a,b:=choose(f32(7));assert(a==7&&b=="ok");a,b=compiled(f32(8));assert(a==8&&b=="ok")', '''
pair::proc(x:$T)->(T,string){return x,"ok"}
choose::proc(x:$T)->(T,string){return switch{case true:pair(x);case:x,"bad"}}
compiled::proc(x:$T)->(T,string){return #switch{case true:pair(x);case:undefined,undefined}}
'''))

    def test_compile_switch_in_for_initialization(self):
        self.invoke(self.program('''
count:=0
for a,b:=#switch{case true:1,2;case:undefined,undefined};a<3;a+=1{assert(b==2);count+=1}
for a,b:=#switch{case true:1,2;case:undefined};a<3;a+=1{assert(b==2);count+=1}
assert(count==4)
'''))

    def test_blank_results_release_only_anonymous_allocations(self):
        source=self.program('''
a,_:=switch{case true:1,f"hello {2}";case:2,f"bye {3}"}
if a!=1{panic("wrong string arm")}
_,a=switch{case true:set[int]{1,2},3;case:set[int]{3},4}
if a!=3{panic("wrong set arm")}
_,a=switch{case true:f"other {4}",5;case:break}
if a!=5{panic("wrong optional arm")}
named:=f"named {6}";defer delete(named)
_,a=switch{case true:named,7;case:f"unselected {a}",8}
if named!="named 6"||a!=7{panic("named owner was released")}
x,_:=#switch{case true:9,f"compile choice {3}";case:undefined,undefined}
if x!=9{panic("wrong compile-time string arm")}
_,x=#switch{case true:set[int]{7},10;case:undefined,undefined}
if x!=10{panic("wrong compile-time set arm")}
_,x=switch{case true:named[1:],11;case:f"unselected {x}",12}
if named!="named 6"||x!=11{panic("borrowed view was released")}
''')
        for flags in (['-debug'], ['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert',
                                   '--track-allocations=basic']):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags,expected='')
                self.assertEqual(result.stderr,'')

    def test_pure_borrowed_second_result_is_still_protected(self):
        self.invoke(self.program('a:=[1,2];_=bad(a[:])', '''
bad::fn(input:[]int)->int{
 n,alias:=switch{case true:1,input;case:2,input}
 alias[0]=9
 return n
}
'''),command='check',fail='borrowed')

    def test_compile_switch_results_retain_untyped_string_constants(self):
        self.invoke(self.program('take(PAD+TITLE);#assert(PAD+TITLE=="   hello")', '''
PAD,TITLE::#switch{case true:" "*3,"hello";case:undefined}
take::proc(value:cstring){_=value}
'''))

    def test_compile_switch_panic_arms_preserve_declaration_context_and_syntax(self):
        self.invoke(self.program('''
#assert(A==1&&B==2)
a,b:int=#switch{case false:panic("bad");case:3,4}
assert(a==3&&b==4&&global_a==5&&global_b==6)
''','''
A,B::#switch{case true:1,2;case:panic("bad")}
global_a,global_b:=#switch{case true:5,6;case:panic("bad")}
'''))
        self.invoke(self.program('a,b:int=#switch{case true:panic("bad");case:1,2};_=a;_=b'),command='check')


if __name__ == '__main__': unittest.main()

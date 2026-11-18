"""Typed lazy fallback lists and their per-position discard contract."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests import test_thor as base

UNCHECKED=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']


class OrElseListTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_lazy_order_exact_once_and_mixed_result_types(self):
        source=self.program('''
order=0
a,b:>read(true) or_else (f32(mark(2)),text())
if a!=7||b!="success"||order!=1{panic("success evaluation")}
order=0
c,d:>read(false) or_else (f32(mark(2)),text())
if c!=2||d!="fallback"||order!=123{panic("fallback evaluation")}
''','''order:int
mark::proc(n:int)->int{order=order*10+n;return n}
text::proc()->string{_=mark(3);return "fallback"}
read::proc(ok:bool)->(f32,string,bool){_=mark(1);return 7,"success",ok}
''')
        for flags in ([],UNCHECKED,['-debug']):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_discard_positions_declarations_and_assignments(self):
        self.invoke(self.program('''
_,b:>pair() or_else (_,1)
a,_:=pair() or_else (2,_)
_,_ = pair() or_else (_,_)
_,n,_:>triple() or_else (_,3,_)
a,_=pair() or_else (4,_)
if a!=4||b!=1||n!=3{panic("discards")}
''','pair::proc()->(int,int,bool){return 0,0,false}\ntriple::proc()->(int,int,int,bool){return 0,0,0,false}\n'))

    def test_discard_mismatches_have_original_slot_locations(self):
        prefix='pair::proc()->(int,int,bool){return 0,0,false}\n'
        for body,message in [
            ('_,b:=pair() or_else (0,1);_=b',"position 1 is discarded"),
            ('a,b:=pair() or_else (_,1);_=a;_=b',"position 1 uses '_'"),
            ('a,_:=pair() or_else (_,1);_=a',"position 1 uses '_'"),
            ('_,b:=pair() or_else (1,_);_=b',"position 1 is discarded"),
        ]:
            with self.subTest(body=body):self.invoke(self.program(body,prefix),command='check',fail=message)

    def test_contextual_literals_enums_records_arrays_and_distinct_types(self):
        self.invoke(self.program('''
kind,record,array,number:>get() or_else (.B,{x=4},[7,8],2)
if kind!=.B||record.x!=4||array[1]!=8||number!=2{panic("contextual fields")}
''','''Kind::enum{A,B}
Record::struct{x:int}
Number::distinct f64
get::proc()->(Kind,Record,[2]i16,Number,bool){return .A,{}, {},0,false}
'''))

    def test_fallback_arity_and_type_errors(self):
        prefix='pair::proc()->(i8,string,bool){return 0,"",false}\n'
        for values,message in [('(0,)','at least two'),('(0,"",3)','has 3 values'),('(256,"")','256')]:
            with self.subTest(values=values):self.invoke(self.program('a,b:=pair() or_else '+values+';_=a;_=b',prefix),command='check',fail=message)
        self.invoke(self.program('a,b:=triple() or_else (0,1);_=a;_=b',
            'triple::proc()->(int,int,int,bool){return 0,0,0,false}\n'),command='check',fail='3 successful results')

    def test_returns_arguments_and_existing_fallback_calls(self):
        prefix='''pair::proc()->(int,string,bool){return 0,"",false}
fallback::proc()->(int,string){return 7,"ok"}
choose::proc()->(int,string){return pair() or_else (7,"ok")}
take::proc(n:int,s:string){if n!=7||s!="ok"{panic("arguments")}}
'''
        source=self.program('take(choose());take(pair() or_else (7,"ok"));_,s:=pair() or_else fallback();if s!="ok"{panic("existing call")}',prefix)
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)
        self.invoke(self.program('',prefix.replace('return pair() or_else (7,"ok")','return pair() or_else (_,"ok")')),command='check',fail='no matching discarded assignment target')
        self.invoke(self.program('take(pair() or_else (_,"ok"))',prefix),command='check',fail='no matching discarded assignment target')

    def test_optional_fallback_calls_retain_native_single_value_semantics(self):
        source=self.program('''
single:=one() or_else fallback()
overloaded:=one() or_else choose(7)
generic:=one() or_else generic_fallback(7)
value,status:=pair() or_else (7,true)
if single!=7||overloaded!=7||generic!=7||value!=7||!status{panic("optional fallback result arity")}
''','''one::proc()->(int,bool){return 0,false}
pair::proc()->(int,bool,bool){return 0,false,false}
fallback::proc()->(int,bool)#optional_ok{return 7,true}
integer_fallback::proc(n:int)->(int,bool)#optional_ok{return n,true}
string_fallback::proc(n:string)->(string,bool)#optional_ok{return n,true}
choose::proc{integer_fallback,string_fallback}
generic_fallback::proc(n:$T)->(T,bool)#optional_ok{return n,true}
''')
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)
        self.invoke(self.program('a,b:=pair() or_else fallback();_=a;_=b',
            'pair::proc()->(int,bool,bool){return 0,false,false}\nfallback::proc()->(int,bool)#optional_ok{return 7,true}\n'),
            command='check',fail='single value')

    def test_parentheses_comments_trailing_commas_and_outer_commas(self):
        self.invoke(self.program('''
_,n:>pair() or_else ((
    _, // deliberately ignored
    (1+2),
))
a,b:=one() or_else (7),8
c,d:=pair() or_else (proc()->(int,int){return 4,5})()
if n!=3||a!=7||b!=8||c!=4||d!=5{panic("grouping")}
''','pair::proc()->(int,int,bool){return 0,0,false}\none::proc()->(int,bool){return 0,false}\n'))

    def test_offset_with_other_assignment_values_and_parallel_lhs(self):
        self.invoke(self.program('''
first,_,last:=7,pair() or_else (_,8)
ary:=[6,7]
indexed,_,tail:=ary[0],pair() or_else (_,9)
items_map:=make(map[int]int)
defer delete(items_map)
items_map[0]=5
mapped,_,final:=items_map[0],pair() or_else (_,10)
items:=[4,5];i:=0
i,items[i]=pair() or_else (1,9)
if first!=7||last!=8||i!=1||items[0]!=9||items[1]!=5||indexed!=6||tail!=9||mapped!=5||final!=10{panic("parallel assignment")}
''','pair::proc()->(int,int,bool){return 0,0,false}\n'))

    def test_generic_overloaded_aliased_and_native_calls(self):
        self.invoke(self.program('''
_,n:>alias(i16(2)) or_else (_,7)
_,logical:>info.cpu_core_count() or_else (_,1)
a,b:=choose(f32(2)) or_else (3,4)
if n!=7||logical<1||a!=3||b!=4{panic("generic/native")}
''','''import "core:sys/info"
generic::proc(x:$T)->(T,T,bool){return x,x,false}
alias::generic
other::proc(x:string)->(string,string,bool){return x,x,false}
choose::proc{generic,other}
'''))

    def test_error_union_status_and_nested_fallback_lists(self):
        self.invoke(self.program('''
_,n:=get() or_else (_,1)
a,b:=(outer() or_else (0,0,false)) or_else (2,3)
if n!=1||a!=2||b!=3{panic("nested fallback")}
''','''Error::enum{Bad}
Status::union{Error}
get::proc()->(int,int,Status){return 0,0,Error.Bad}
outer::proc()->(int,int,bool,bool){return 0,0,false,false}
'''))

    def test_fn_and_run_keep_purity_and_result_types(self):
        source=self.program('VALUE::#run calculate(2);if VALUE!=7{panic("comptime fallback")}', '''
pair::proc(n:int)->(int,int,bool){return n,n,false}
calculate::fn(n:int)->int{_,value:>pair(n) or_else (_,n+5);return value}
''')
        self.invoke(source)
        self.invoke(source.replace('n+5','impure()').replace('pair::proc','state:int\nimpure::proc()->int{state+=1;return state}\npair::proc'),command='check',fail='runtime storage')

    def test_readonly_slice_provenance_is_preserved_by_position(self):
        prefix='get::proc()->([]int,[]int,bool){return nil,nil,false}\n'
        self.invoke(self.program('a,b:=get() or_else ([]int{1},[]int{2});a[0]=3;b[0]=4',prefix))
        self.invoke(self.program('frozen:>[]int{1};_,b:=get() or_else (_,frozen);_=b',prefix),command='check',fail='read-only slice')

    def test_allocated_big_numeric_and_string_defaults_own_their_storage(self):
        source=self.program('''
value,fraction,text:=get() or_else (123456789012345678901234567890,0.125,f"hello {7}")
if value!=Integer(123456789012345678901234567890)||fraction!=Rational(1,8)||text!="hello 7"{panic("owned fallback")}
delete(value);delete(fraction);delete(text)
''','get::proc()->(Integer,Rational,string,bool){return {},{},"",false}\n')
        for flags in (['-debug'],UNCHECKED):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

    def test_anonymous_fstrings_in_argument_fallbacks_are_released(self):
        source=self.program('take(get() or_else (f"hello {7}",8))','''
get::proc()->(string,int,bool){return "",0,false}
take::proc(text:string,n:int){if text!="hello 7"||n!=8{panic("argument lifetime")}}
''')
        _,result=self.invoke(source,flags=['-debug'])
        self.assertNotIn('still allocated',result.stderr)

    def test_discarded_big_results_and_zero_defaults_do_not_leak(self):
        source=self.program('''
_,value:=get() or_else (_,7)
_,success:=success_pair() or_else (_,9)
whole,fraction:=numbers() or_else ({},{})
if value!=7||success!=8||whole!=0||fraction!=0{panic("zero defaults")}
delete(whole);delete(fraction)
''','''get::proc()->(Integer,int,bool){return 0,0,false}
success_pair::proc()->(Integer,int,bool){return 123456789012345678901234567890,8,true}
numbers::proc()->(Integer,Rational,bool){return 0,0,false}
''')
        _,result=self.invoke(source,flags=['-debug'])
        self.assertNotIn('still allocated',result.stderr)

    def test_control_headers_deferred_assignment_and_immutable_destinations(self):
        source=self.program('''
sum:=0
for _,i:=pair() or_else (_,1);i<3;i+=1{sum+=i}
if _,value:=pair() or_else (_,7);value==7{sum+=value}
value:=0
{defer _,value=pair() or_else (_,9)}
if sum!=10||value!=9{panic("control flow")}
''','pair::proc()->(int,int,bool){return 0,0,false}\n')
        self.invoke(source)
        self.invoke(self.program('a:>1;b:=2;a,b=pair() or_else (3,4)',
            'pair::proc()->(int,int,bool){return 0,0,false}\n'),command='check',fail='immutable')

    def test_lists_inside_switch_results_keep_discard_context(self):
        self.invoke(self.program('''
_,value:=switch{case true:pair() or_else (_,7);case:0,8}
if value!=7{panic("switch fallback")}
''','pair::proc()->(int,int,bool){return 0,0,false}\n'))

    def test_cstring_literals_and_builtin_shadowing(self):
        self.invoke(self.program('''
type_of::proc(x:int)->int{return x}
expand_values::proc(x:int)->int{return x}
compress_values::proc(x:int)->int{return x}
name,n:=get() or_else ("fallback",1)
if string(name)!="fallback"||n!=1{panic("cstring fallback")}
''','get::proc()->(cstring,int,bool){return nil,0,false}\n'))

    def test_scalar_record_defaults_make_no_heap_allocations(self):
        self.invoke(self.program('''
tracker:mem.Tracking_Allocator
mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
defer mem.tracking_allocator_destroy(&tracker)
context.allocator=mem.tracking_allocator(&tracker)
a,b:=get() or_else (7,{x=9})
if a!=7||b.x!=9||len(tracker.allocation_map)!=0{panic("fallback allocated")}
''','import "core:mem"\nRow::struct{x:int}\nget::proc()->(int,Row,bool){return 0,{},false}\n'),flags=UNCHECKED)

    def test_pure_local_mutation_in_a_default_and_runtime_inputs(self):
        self.invoke(self.program('''
value:=2
if compute(value)!=8{panic("runtime pure default")}
N::#run compute(3)
if N!=10{panic("comptime pure default")}
''','''get::proc(n:int)->(int,int,bool){return n,n,false}
write::proc(destination:^int,n:int)->int{destination^=n;return n+2}
compute::fn(n:int)->int{
    local:int
    _,value:>get(n) or_else (_,write(&local,n+1))
    return local+value
}
'''))

    def test_imported_private_types_named_results_and_supported_native_vet(self):
        self.invoke(self.program('''
number,row:=data.read() or_else (7,{value=9})
if number!=7||row.value!=9{panic("imported result types")}
''','import "data"\n'),files={'data/data.odin':'''package data
@(private="file") Hidden::struct{value:int}
read::proc()->(number:int,row:Hidden,ok:bool){return 0,{},false}
'''},flags=['-vet-semicolon','-vet-when-shadowing'])

    def test_fallback_lists_inside_interpolation_and_optional_slot_defaults(self):
        source=self.program('''
value,text:=pair() or_else (one() or_else 7,f"value {one() or_else 8}")
if value!=7||text!="value 8"{panic("nested scalar defaults")}
delete(text)
''','''one::proc()->(int,bool){return 0,false}
pair::proc()->(int,string,bool){return 0,"",false}
''')
        self.invoke(source)

    def test_pure_borrowed_slice_fallback_results_become_independent_copies(self):
        source=self.program('''
frozen:>[]int{1,2}
left,right:=choose(frozen)
left[0]=7;right[0]=8
if frozen[0]!=1||left[0]!=7||right[0]!=8{panic("fallback ownership")}
delete(left);delete(right)
''','''get::proc()->([]int,[]int,bool){return nil,nil,false}
choose::fn(values:[]int)->([]int,[]int){return get() or_else (values,values)}
''')
        _,result=self.invoke(source,flags=['-debug'])
        self.assertNotIn('still allocated',result.stderr)

    def test_native_frontend_parses_lists_with_checks_disabled(self):
        with tempfile.TemporaryDirectory(prefix='thor-fallback-frontend-') as temporary:
            root=Path(temporary);binary=root/'frontend'
            result=subprocess.run(['odin','build',str(base.ROOT/'frontend'),*UNCHECKED,
                '-out:'+str(binary)],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stderr)
            source=root/'main.thor'
            source.write_text(self.program('''
_,n:=pair() or_else ((_,7))
other:=one() or_else (2)
if n!=7||other!=2{panic("parser")}
''','pair::proc()->(int,int,bool){return 0,0,false}\none::proc()->(int,bool){return 0,false}\n'))
            parsed=subprocess.run([str(binary),str(source)],capture_output=True,text=True,timeout=15)
            self.assertEqual(parsed.returncode,0,parsed.stderr)
            packet=json.loads(parsed.stdout)
            self.assertFalse(packet.get('errors'),packet.get('errors'))
            lists=[feature for feature in packet['features'] if feature['kind']=='FallbackList']
            self.assertEqual(len(lists),1,lists)
            native=source.with_suffix('.odin')
            native.write_text('package main\nread::proc()->(int,bool){return 0,false}\nmain::proc(){value:=read() or_else (7);_=value}\n')
            parsed=subprocess.run([str(binary),str(native),'--odin'],capture_output=True,text=True,timeout=15)
            self.assertEqual(parsed.returncode,0,parsed.stderr)
            self.assertFalse(any(f['kind']=='FallbackList' for f in json.loads(parsed.stdout)['features']))

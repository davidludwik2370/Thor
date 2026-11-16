"""Independently owned dynamic fn/#run results, including cached reconstruction."""
import json
import subprocess
import sys
import unittest

from tests import test_thor as base
from thorlang import values


class PureDynamicResultsTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_constant_point_loader_runtime_automatic_and_explicit_run(self):
        source=self.program('''
n:=3
runtime:=load_points(n)
automatic:=load_points(3)
explicit:=#run load_points(3)
defer delete(runtime);defer delete(automatic);defer delete(explicit)
if len(runtime)!=3||runtime[2]!=(Pos{7,8})||automatic[0]!=(Pos{1,2})||explicit[1]!=(Pos{4,5}){panic("wrong points")}
runtime[0]={9,9}
append(&automatic,{10,11});append(&explicit,{12,13})
if len(automatic)!=4||len(explicit)!=4||STARTING_POINTS[0]!=(Pos{1,2}){panic("result aliases source")}
''','''
Pos::[2]int
STARTING_POINTS::[]Pos{{1,2},{4,5},{7,8}}
load_points::fn(n:int)->[dynamic]Pos{
    points:[dynamic]Pos
    for point,i in STARTING_POINTS{if i>=n{break};append(&points,point)}
    return points
}
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_capacity_nil_zero_sized_and_distinct_results(self):
        self.invoke(self.program('''
n:=2
a:=build(n);b:=#run build(2)
defer delete(a);defer delete(b)
if len(a)!=2||cap(a)!=17||cap(b)!=17||b[1]!=8{panic("capacity changed")}
nil_value:=#run nil_result();defer delete(nil_value)
if len(nil_value)!=0||cap(nil_value)!=0||raw_data(nil_value)!=nil{panic("nil changed")}
z:=#run zeros();defer delete(z)
if len(z)!=3||cap(z)!=9{panic("zero-sized descriptor changed")}
typed:=#run distinct_result();defer delete(typed)
append(&typed,11)
if len(typed)!=2||typed[0]!=7||typed[1]!=11{panic("distinct result changed")}
''','''
build::fn(n:int)->[dynamic]int{a:=make([dynamic]int,n,17);a[1]=8;return a}
nil_result::fn()->[dynamic]int{return nil}
Empty::struct{}
zeros::fn()->[dynamic]Empty{return make([dynamic]Empty,3,9)}
Numbers::distinct [dynamic]int
distinct_result::fn()->Numbers{a:Numbers;append(&a,7);return a}
'''),flags=['--track-allocations=basic'])

    def test_nested_records_unions_repeated_aliases_and_borrowed_views(self):
        self.invoke(self.program('''
source:=[1,2]
runtime:=nested(source[:]);folded:=#run nested(INPUT)
defer pure.destroy(runtime);defer pure.destroy(folded)
if runtime.rows[0][1]!=2||folded.rows[1][0]!=1||runtime.text[0]!="hello"{panic("nested contents")}
runtime.rows[0][0]=9
if runtime.rows[1][0]!=1||source[0]!=1||raw_data(runtime.rows[0])==raw_data(runtime.rows[1]){panic("nested alias")}
union_value:=#run union_result();defer pure.destroy(union_value)
selected,ok:=union_value.([dynamic]string)
if !ok||selected[0]!="hello"||cap(selected)!=13{panic("union contents")}
''','''
import pure "thor:pure"
INPUT::[]int{1,2}
Record::struct{rows:[dynamic][]int,text:[dynamic]string}
nested::fn(input:[]int)->Record{
    rows:[dynamic][]int;append(&rows,input,input)
    text:[dynamic]string;append(&text,"hello")
    return {rows,text}
}
Choice::union{int,[dynamic]string}
union_result::fn()->Choice{v:=make([dynamic]string,1,13);v[0]="hello";return v}
'''),flags=['-debug'])

    def test_helpers_deferred_named_multi_returns_and_pure_consumer(self):
        self.invoke(self.program('''
n:=3
a,b:=make_both(n)
defer delete(a);defer delete(b)
if a[0]!=9||b[0]!=3||cap(a)!=11{panic("deferred result")}
folded:=#run make_one(3);defer delete(folded)
generic_value:=#run generic(i16,7);defer delete(generic_value)
if folded[0]!=3||TOTAL!=3||generic_value[0]!=7{panic("helper result")}
''','''
fill::proc(out:^[dynamic]int,n:int){append(out,n)}
make_one::fn(n:int)->[dynamic]int{result:[dynamic]int;fill(&result,n);return result}
make_both::fn(n:int)->(a:[dynamic]int,b:[dynamic]int){a=make([dynamic]int,1,11);a[0]=n;b=make_one(n);defer a[0]=9;return}
consumer::fn()->int{value:=make_one(3);return value[0]}
TOTAL::#run consumer()
generic::fn($E:typeid,value:E)->[dynamic]E{out:[dynamic]E;append(&out,value);return out}
'''),flags=['--track-allocations=basic'])

    def test_cache_hits_rematerialize_independent_storage_in_loops(self):
        source=self.program('''
for i in 0..<3{
    a:=#run load();b:=#run load()
    if cap(a)!=17||cap(b)!=17||a[0]!=7||raw_data(a)==raw_data(b){panic("cached alias")}
    a[0]=i
    append(&b,9)
    if b[0]!=7||len(b)!=2{panic("cached state mutated")}
    delete(a);delete(b)
}
''','load::fn()->[dynamic]int{v:=make([dynamic]int,1,17);v[0]=7;return v}\n')
        root,first=self.invoke(source,flags=['--cache-report','--track-allocations=basic'])
        report=lambda result:next(json.loads(line)['thor_cache'] for line in result.stdout.splitlines() if line.startswith('{"thor_cache"'))
        self.assertGreater(report(first)['writes'],0)
        result=subprocess.run([str(base.THOR),'run',str(root/'input/main.thor'),'--output-dir',str(root/'output'),
                               '--cache-report',*base.runtime_flags(['--track-allocations=basic'])],
                              capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(result.stderr,'')
        self.assertGreater(report(result)['hits'],0)
        packets=[json.loads(path.read_text())['value'] for path in (root/'input/.thor-cache/eval-v1').glob('*.json')]
        self.assertTrue(any(packet.get('storage')=='dynamic' and packet['capacity']==17 for packet in packets),packets)

    def test_inputs_contextless_results_and_invalid_nested_elements_stay_rejected(self):
        cases=[
            ('bad::fn(value:[dynamic]int)->int{return len(value)}','v:[dynamic]int;_=bad(v)','not public fn inputs','[dynamic]int'),
            ('bad::proc "contextless"()->[dynamic]int{return nil}','_= #run bad()','Contextless compile-time procedures cannot return dynamic arrays','[dynamic]int'),
            ('bad::fn()->[dynamic]^int{return nil}','_=bad()','Unsupported pure value type','[dynamic]^int'),
            ('bad::fn()->[dynamic]map[int]int{return nil}','_=bad()','Unsupported pure value type','[dynamic]map[int]int'),
        ]
        for declaration,body,message,annotation in cases:
            with self.subTest(declaration=declaration):
                text=self.program(body,'// 😀\n'+declaration+'\n')
                _,result=self.invoke(text,command='check',flags=['-json-errors'],fail=message)
                errors=json.loads(result.stderr)['errors']
                self.assertTrue(any(error['pos']['offset']==text.encode().index(annotation.encode()) for error in errors),errors)

    def test_dynamic_results_need_runtime_bindings_but_scalar_consumers_are_constants(self):
        for binding in ['VALUE::#run load()','VALUE::load()']:
            with self.subTest(binding=binding):
                self.invoke(self.program('_=VALUE','load::fn()->[dynamic]int{return nil}\n'+binding+'\n'),
                            command='check',fail='dynamic-array-containing')

    def test_borrowed_elements_in_local_dynamic_helpers_stay_readonly(self):
        self.invoke(self.program('source:=[1,2];_=bad(source[:])','''
change::proc(out:^[dynamic][]int){out^[0][0]=9}
bad::fn(input:[]int)->[dynamic][]int{out:[dynamic][]int;append(&out,input);change(&out);return out}
'''),command='check',fail='borrowed from an input')

    def test_dynamic_big_number_elements_keep_recursive_ownership(self):
        source=self.program('''
n:=7
runtime:=numbers(n);folded:=#run numbers(7)
defer pure.destroy(runtime);defer pure.destroy(folded)
runtime[0]+=1
if runtime[0]!=8||folded[0]!=7||folded[1]!=123456789012345678901234567890{panic("numeric result aliases")}
''','''
import pure "thor:pure"
numbers::fn(n:int)->[dynamic]Integer{
    result:[dynamic]Integer
    append(&result,Integer(n),Integer(123456789012345678901234567890))
    return result
}
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_invalid_materialized_descriptors_fail_even_with_assertions_disabled(self):
        for count,capacity in [(-1,0),(2,1),(0,sys.maxsize)]:
            for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
                with self.subTest(count=count,capacity=capacity,flags=flags):
                    self.invoke(self.program(f'_=pure.allocate_dynamic([dynamic]int,{count},{capacity})',
                        'import pure "thor:pure"\n'),flags=flags,fail='Pure result allocation size overflow')


class DynamicResultProtocolTests(unittest.TestCase):
    def test_dynamic_capacity_and_allocation_classification(self):
        packet={'kind':'sequence','storage':'dynamic','capacity':17,'nil':False,'values':[{'kind':'integer','value':'7'}]}
        values.validate(packet)
        self.assertTrue(values.has_owned(packet,slices_only=True))
        self.assertTrue(values.has_dynamic(packet))
        code=values.materialize(packet,'T','meta','pure','item_')
        self.assertIn('pure.allocate_dynamic(T,1,17,true)',code)
        self.assertIn('item_1[0]=',code)

    def test_invalid_dynamic_descriptors_are_rejected(self):
        packet={'kind':'sequence','storage':'dynamic','capacity':0,'nil':True,'values':[]}
        for bad in [{'capacity':-1},{'capacity':True},{'capacity':sys.maxsize+1},{'capacity':'2'},
                    {'capacity':2},{'nil':False,'capacity':0,'values':[{'kind':'integer','value':'7'}]}]:
            with self.subTest(bad=bad):
                with self.assertRaises(values.ValueErrorProtocol):values.validate({**packet,**bad})
        values.validate({**packet,'capacity':9,'zero_sized':True,'values':[{'kind':'record','fields':[]}]})


if __name__=='__main__':unittest.main()

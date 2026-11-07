"""Collection construction, scope, evaluation order, purity and failure paths."""
import json
import unittest
from tests import test_thor as base

class ComprehensionTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_constant_array_and_runtime_slice_are_distinct(self):
        self.invoke(self.program('''
x:>[square(a) for a in 0..<10 if a%2==0]
defer delete(x)
if EVENS!=[0,4,16,36,64] || type_of(EVENS)!=[5]int || type_of(x)!=[]int || x[4]!=64 {panic("values/types")}
''','square::proc(x:int)->int{return x*x}\nEVENS::[square(x) for x in 0..<10 if x%2==0]\n'))

    def test_explicit_collections_and_duplicates(self):
        self.invoke(self.program('''
values:=[1,2,1,3]
s:=[]int{x*2 for x in values};defer delete(s)
d:=[dynamic]int{x for x in values};defer delete(d)
u:=set[int]{x for x in values};defer delete(u)
m:=map[int]int{x=x+i for x,i in values};defer delete(m)
if len(s)!=4||s[2]!=2||len(d)!=4||len(u)!=3||m[1]!=3||m[3]!=6 {panic("collections")}
'''))

    def test_flatten_all_destinations_and_map_last_wins(self):
        self.invoke(self.program('''
rows:=[][]int{{1,2},{},{2,3}}
a:=[..row for row in rows];defer delete(a)
b:=[dynamic]int{..row for row in rows};defer delete(b)
c:=set[int]{..row for row in rows};defer delete(c)
s1:=set[int]{1,2};defer delete(s1)
s2:=set[int]{2,3};defer delete(s2)
sources:=[s1,s2]
d:=set[int]{..s for s in sources};defer delete(d)
m1:map[int]int;m1[1]=2;defer delete(m1)
m2:map[int]int;m2[1]=3;m2[2]=4;defer delete(m2)
maps:=[m1,m2]
e:=map[int]int{..m for m in maps};defer delete(e)
if len(a)!=4||a[2]!=2||len(b)!=4||len(c)!=3||len(d)!=3||e[1]!=3||e[2]!=4 {panic("flatten")}
'''))

    def test_sources_once_filter_before_output_and_key_before_value(self):
        self.invoke(self.program('''
a:=[emit(x) for x in source() if accept(x)];defer delete(a)
if sources!=1||checks!=4||outputs!=2||a[1]!=3 {panic("evaluation order")}
m:=map[int]int{key(x)=value(x) for x in [1,2]};defer delete(m)
if order!=1234 {panic("key before value")}
''','''
sources,checks,outputs,order:int
source::proc()->[4]int{sources+=1;return {0,1,2,3}}
accept::proc(x:int)->bool{checks+=1;return x%2==1}
emit::proc(x:int)->int{outputs+=1;return x}
key::proc(x:int)->int{order=order*10+(2*x-1);return x}
value::proc(x:int)->int{order=order*10+2*x;return x}
'''))

    def test_range_bounds_once_float_ranges_and_typed_bindings(self):
        self.invoke(self.program('''
x:=[v for v in low()..=high()];defer delete(x)
f:=[v*2 for v:f64 in 1..=3];defer delete(f)
a:=[i8(10),20]
y:=[i for v,(i:i8) in a];defer delete(y)
if calls!=2||len(x)!=3||x[2]!=4||type_of(f)!=[]f64||f[2]!=6||type_of(y)!=[]i8||y[1]!=1 {panic("bounds/bindings")}
''','calls:int\nlow::proc()->u8{calls+=1;return 2}\nhigh::proc()->u8{calls+=1;return 4}\n'))

    def test_outer_capture_source_shadowing_and_local_loop_scope(self):
        self.invoke(self.program('''
x:=[1,2,3];offset:=4
result:=[x+offset for x in x if x>1];defer delete(result)
if result[0]!=6||x[0]!=1 {panic("scope")}
'''))
        self.invoke(self.program('result:=[x for x in 0..<2];defer delete(result);_=x'),command='check',fail='Undeclared name: x')

    def test_map_set_string_and_enum_iteration(self):
        self.invoke(self.program('''
m:map[string]int;defer delete(m);m["abc"]=7
pairs:=[len(k)+v for k,v in m];defer delete(pairs)
s:=set[int]{3};defer delete(s)
items:=[x+1 for x in s];defer delete(items)
letters:=[r for r in "é😀"];defer delete(letters)
colors:=[c for c in Color];defer delete(colors)
if pairs[0]!=10||items[0]!=4||len(letters)!=2||letters[1]!='😀'||len(colors)!=2 {panic("iteration")}
''','Color::enum{Red,Blue}\n'))

    def test_empty_filtered_results_and_flattened_constants(self):
        self.invoke(self.program('''
a:=[x for x in 0..<0];defer delete(a)
b:=set[int]{x for x in 0..<3 if false};defer delete(b)
c:=map[int]int{x=x for x in 0..<0};defer delete(c)
if len(a)!=0||len(b)!=0||len(c)!=0||len(EMPTY)!=0||FLAT!=[1,2,3,4] {panic("empty/constants")}
''','EMPTY::[x for x in 0..<0]\nROWS::[[1,2],[3,4]]\nFLAT::[..row for row in ROWS]\n'))

    def test_fstring_elements_keep_owned_storage_and_shallow_slice_elements(self):
        self.invoke(self.program('''
texts:=[f"item {x}" for x in 0..<3]
for text in texts {if len(text)!=6 {panic("string storage")};delete(text)}
delete(texts)
rows:=[][]int{{1,2},{3,4}}
copy:=[row for row in rows]
if raw_data(copy[0])!=raw_data(rows[0]) {panic("shallow copy")}
delete(copy)
'''),flags=['-debug'])

    def test_generic_runtime_and_pure_run_comprehensions(self):
        self.invoke(self.program('''
a:=copy_values([1,2,3]);defer delete(a)
b:=#run pure_values(4);defer delete(b)
if len(a)!=3||a[2]!=3||len(b)!=4||b[3]!=9 {panic("generics/pure")}
''','''
copy_values::proc(values:$S/[$N]$T)->[]T{return [x for x in values]}
pure_values::fn(n:int)->[]int{return [x*x for x in 0..<n]}
'''))

    def test_comptime_constant_calls_obey_purity_and_reject_runtime_inputs(self):
        self.invoke(self.program('_=BAD','''
state:int
impure::proc(x:int)->int{state+=1;return x}
BAD::[impure(x) for x in 0..<3]
'''),command='check',fail='Pure')
        self.invoke(self.program('n:=3;BAD::[x for x in 0..<n];_=BAD'),command='check',fail='compile-time')

    def test_invalid_grammar_and_construction_forms(self):
        for expr,message in [
            ('[[x for x in [1]] for y in [1]]','Nested comprehensions'),
            ('[x for x in [1] for y in [2]]',"one 'for'"),
            ('[x for x in [1] if true if false',"one 'for'"),
            ('[**x for x in [[1]]]','Use'),
            ('[x,y for x in [1]]','one output'),
            ('map[int]int{x for x in [1]}','requires key = value'),
            ('[x=x for x in [1]]','Only map'),
            ('[2]int{x for x in [1]}','construct slices'),
            ('[..x for x in ["abc"]]','unpacking requires'),
        ]:
            with self.subTest(expr=expr):self.invoke(self.program('_='+expr),command='check',fail=message)

    def test_checked_and_unchecked_construction(self):
        for flags in [[],['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):
                self.invoke(self.program('''
a:=[x*2 for x in 0..<30];defer delete(a)
s:=set[int]{x for x in a};defer delete(s)
m:=map[int]int{x=x+1 for x in a};defer delete(m)
if len(a)!=30||a[29]!=58||len(s)!=30||m[58]!=59 {panic("release")}
'''),flags=flags)

    def test_contextual_enum_records_pointers_and_fixed_capacity_sources(self):
        self.invoke(self.program('''
colors:[]Color=[.Red for x in 0..<2];defer delete(colors)
records:=[]Item{{x} for x in 0..<2};defer delete(records)
fixed:[dynamic;4]int;append(&fixed,1,2,3)
copy:=[x for x in fixed];defer delete(copy)
pointer:=&fixed
other:=[x for x in pointer];defer delete(other)
if colors[1]!=.Red||records[1].value!=1||copy[2]!=3||other[0]!=1 {panic("context")}
''','Color::enum{Red,Blue}\nItem::struct{value:int}\n'))

    def test_comprehension_does_not_make_borrowed_elements_mutable_in_fn(self):
        self.invoke(self.program('x:=[][]int{{1}};bad(x)','''
bad::fn(input:[][]int){
 values:=[row for row in input]
 defer delete(values)
 values[0][0]=2
}
'''),command='check',fail='borrowed')

    def test_flatten_filter_applies_before_evaluating_the_inner_source(self):
        self.invoke(self.program('''
values:=[..part(x) for x in [1,2,3] if x==2];defer delete(values)
if calls!=1||len(values)!=2||values[1]!=3 {panic("spread filter")}
''','calls:int\npart::proc(x:int)->[2]int{calls+=1;return {x,x+1}}\n'))

    def test_allocation_failure_panics_and_releases_construction_buffers(self):
        prefix='''
import "core:mem"
import "core:fmt"
import "core:os"
import "base:runtime"
State::struct{backing:mem.Allocator,live:int,remaining:int}
state:State
deny::proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,old_memory:rawptr,old_size:int,
 loc:=#caller_location)->([]byte,mem.Allocator_Error){
 s:=cast(^State)data
 if mode==.Alloc||mode==.Alloc_Non_Zeroed||mode==.Resize||mode==.Resize_Non_Zeroed {
  if s.remaining==0 {return nil,.Out_Of_Memory}
  s.remaining-=1
 }
 data_out,err:=s.backing.procedure(s.backing.data,mode,size,alignment,old_memory,old_size,loc)
 if err==nil {
  if mode==.Free {s.live-=old_size}
  else if mode==.Alloc||mode==.Alloc_Non_Zeroed||mode==.Resize||mode==.Resize_Non_Zeroed {s.live+=len(data_out)-old_size}
 }
 return data_out,err
}
failed::proc(prefix,message:string,loc:runtime.Source_Code_Location)->! {
 if message!="Comprehension allocation failed"||state.live!=0 {fmt.eprintln(message,state.live);os.exit(1)}
 fmt.println("clean failure")
 os.exit(0)
}
'''
        for expression,budget in [('[x for x in 0..<4]',0),('[x for x in 0..<4]',1),
                                  ('[dynamic]int{x for x in 0..<100}',1),
                                  ('set[int]{x for x in 0..<100}',0),('map[int]int{x=x for x in 0..<100}',1)]:
            for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
                with self.subTest(expression=expression,budget=budget,flags=flags):
                    body=f'state=State{{backing=context.allocator,remaining={budget}}};context.allocator=mem.Allocator{{deny,&state}};context.assertion_failure_proc=failed;_={expression};panic("did not fail")'
                    self.invoke(self.program(body,prefix),flags=flags,expected='clean failure')

    def test_reference_iteration_keeps_native_addressability(self):
        self.invoke(self.program('''
values:=[1,2]
a:=[bump(&x) for &x in values];defer delete(a)
if values!=[2,3]||a[0]!=2||a[1]!=3 {panic("reference source")}
''','bump::proc(x:^int)->int{x^+=1;return x^}\n'))

    def test_strings_are_not_flattened_into_maps_or_sets(self):
        for expression,message in [('set[rune]{..s for s in ["ab"]}','Set comprehension unpacking'),
                                   ('map[rune]int{..s for s in ["ab"]}','Map comprehension unpacking')]:
            with self.subTest(expression=expression):self.invoke(self.program('_='+expression),command='check',fail=message)

    def test_compile_time_nested_records_ordinary_calls_and_cache(self):
        text=self.program('if VALUES[2].value!=4||VALUES[0].tag!="even" {panic("constant records")}','''
Item::struct{value:int,tag:string}
item::proc(x:int)->Item{return {x,"even"}}
VALUES::[item(x) for x in 0..<6 if x%2==0]
''')
        self.invoke(text)

    def test_inactive_native_when_and_lazy_calls(self):
        self.invoke(self.program('''
when false {BAD::[missing(x) for x in 0..<2];_=BAD}
values:=[x for x in 0..<2 if false];defer delete(values)
if len(values)!=0 {panic("empty")}
'''))

    def test_multiline_comments_and_filter_expression(self):
        self.invoke(self.program('''
values:=[
 x*2 for x in [1,2,3]
 if (x>1 && x<4)
]
defer delete(values)
if len(values)!=2||values[1]!=6 {panic("multiline")}
'''))

    def test_pure_local_mutation_in_output_and_borrowed_input_rejection(self):
        self.invoke(self.program('if VALUES!=[1,2,3]{panic("pure mutation")}','''
set::proc(x:^int){x^+=1}
next::fn(x:int)->int{y:=x;set(&y);return y}
VALUES::[next(x) for x in 0..<3]
'''))

    def test_zero_sized_and_distinct_elements(self):
        self.invoke(self.program('''
a:=[Empty{} for x in 0..<5];defer delete(a)
b:=[Number(x) for x in 0..<3];defer delete(b)
if len(a)!=5||len(b)!=3||type_of(b)!=[]Number||b[2]!=2 {panic("element types")}
''','Empty::struct{}\nNumber::distinct int\n'))

    def test_enumerated_array_indices_and_generic_native_index_types(self):
        self.invoke(self.program('''
values:[Color]int
indices:=[i for x,i in values];defer delete(indices)
other:=indices_of(values);defer delete(other)
if type_of(indices)!=[]Color||indices[1]!=.Blue||type_of(other)!=[]Color {panic("enum indices")}
''','''
Color::enum{Red,Blue}
indices_of::proc(values:$A/[$N]$E)->[]Color{
 result:=[i for x,i in values]
 return result
}
'''))

    def test_package_runtime_initializers_and_anonymous_owned_arguments(self):
        self.invoke(self.program('if VALUES[2]!=4||len(TEXTS)!=2 {panic("globals")};backing:=runtime.default_context().allocator;delete(VALUES,backing);for text in TEXTS {delete(text,backing)};delete(TEXTS,backing)','''
import "base:runtime"
VALUES:=[x*x for x in 0..<3]
TEXTS:=[f"value {x}" for x in 0..<2]
'''),flags=['-debug'])
        self.invoke(self.program('consume([x for x in 0..<3])','consume::proc(values:[]int){if values[2]!=2{panic("anonymous")};delete(values)}\n'),flags=['-debug'])

    def test_silent_nil_allocator_is_a_construction_failure(self):
        for expression in ['[x for x in 0..<3]','set[int]{x for x in 0..<3}','map[int]int{x=x for x in 0..<3}']:
            with self.subTest(expression=expression):
                self.invoke(self.program('context.allocator=mem.nil_allocator();_='+expression,'import "core:mem"\n'),fail='Comprehension allocation failed',flags=['-o:speed','-disable-assert'])

    def test_distinct_containers_and_mixed_untyped_range_bounds(self):
        self.invoke(self.program('''
a:=Numbers{x for x in 0..<3};defer delete(a)
b:=Growing{x for x in 0..<3};defer delete(b)
hi:u8=3
c:=[1+x for x in 0..<hi];defer delete(c)
if type_of(a)!=Numbers||type_of(b)!=Growing||type_of(c)!=[]u8||c[2]!=3 {panic("native identities")}
''','Numbers::distinct []int\nGrowing::distinct [dynamic]int\n'))

    def test_native_parser_mode_and_original_expression_roles(self):
        from thorlang.compiler import Source,ThorError
        from thorlang.driver import frontend_binary
        frontend,_=frontend_binary('odin')
        text=self.program('items:=[x+1 for x in 0..<3 if x>0];defer delete(items)')
        parsed=Source(base.ROOT/'comp-parser.thor',frontend,raw=text.encode(),cache=False)
        comp=next(n for n in parsed.nodes if n.kind=='Comprehension')
        self.assertEqual(comp.role('output').text,'x+1')
        self.assertEqual(comp.role('expr').text,'0..<3')
        self.assertEqual(comp.role('cond').text,'x>0')
        with self.assertRaises(ThorError):Source(base.ROOT/'comp-parser.odin',frontend,thor=False,raw=text.encode(),cache=False)

    def test_generic_compile_time_lengths_are_specialized_independently(self):
        self.invoke(self.program('if total(3)!=6||total(5)!=15 {panic("specialized constants")}','''
total::proc($N:int)->int{
 VALUES::[x+1 for x in 0..<N]
 result:=0
 for value in VALUES {result+=value}
 return result
}
'''))

    def test_compile_time_contextual_enum_array(self):
        self.invoke(self.program('if (COLORS!=[2]Color{.Red,.Red}){panic("constant context")}','Color::enum{Red,Blue}\nCOLORS:[2]Color:[.Red for x in 0..<2]\n'))

    def test_logical_number_elements_follow_direct_copy_ownership(self):
        self.invoke(self.program('''
a:=[Integer(x) for x in 0..<3]
b:=[x for x in a]
for _,i in a {delete(a[i])}
delete(a)
if b[2]!=2 {panic("Integer clone")}
for _,i in b {delete(b[i])}
delete(b)
r:=[]Rational{Rational(x,2) for x in 1..=3}
if r[2]!=Rational(3,2) {panic("Rational result")}
for _,i in r {delete(r[i])}
delete(r)
'''),flags=['-debug'])

    def test_anonymous_map_and_set_comprehensions_do_not_bypass_fn_rules(self):
        for expression in ['map[int]int{x=x for x in 0..<2}','set[int]{x for x in 0..<2}']:
            with self.subTest(expression=expression):
                self.invoke(self.program('_=bad()','bad::fn()->int{return len('+expression+')}\n'),command='check',fail='Pure local storage')

    def test_multiple_result_iterators_have_a_source_diagnostic(self):
        self.invoke(self.program('_=[x for x in step()]','step::proc()->(int,bool){return 1,false}\n'),command='check',fail='iterator procedures with multiple results')

    def test_or_else_unwraps_output_once_with_lazy_fallback(self):
        text=self.program('''
values:=[-1,0,1,2]
x:=[my_proc(a) or_else fallback(a) for a in values]
defer delete(x)
if len(x)!=4||x[0]!=2||x[1]!=2||x[2]!=10||x[3]!=20||calls!=4||fallbacks!=2 {panic("optional output")}
empty:=[my_proc(a) or_else fallback(a) for a in values if false]
defer delete(empty)
if len(empty)!=0||calls!=4||fallbacks!=2 {panic("filtered calls")}
''','''
calls,fallbacks:int
my_proc::proc(a:int)->(int,bool){calls+=1;return a*10,a>0}
fallback::proc(a:int)->int{fallbacks+=1;return 2}
''')
        for flags in [[],['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):self.invoke(text,flags=flags)

    def test_or_else_nil_error_generic_and_grouped_calls(self):
        self.invoke(self.program('''
values:=[i8(0),1,2]
x:=[(get(a)) or_else 2 for a in values];defer delete(x)
if type_of(x)!=[]i8||x[0]!=2||x[1]!=1||x[2]!=2 {panic("generic bool status")}
y:=[parse(a) or_else 2 for a in values];defer delete(y)
if type_of(y)!=[]int||y[0]!=2||y[1]!=10 {panic("error status")}
z:=[pointer_error(a) or_else 2 for a in values];defer delete(z)
if z[0]!=2||z[1]!=10 {panic("pointer error status")}
''','''
Failure::enum{Invalid}
Status::union{Failure}
failure:Failure
pointer_error::proc(a:i8)->(int,^Failure){if a==0{return 0,&failure};return int(a)*10,nil}
get::proc(a:$T)->(T,bool) #optional_ok{return a,a>0}
parse::proc(a:i8)->(int,Status){if a==0{return 0,Failure.Invalid};return int(a)*10,nil}
'''))

    def test_or_else_in_typed_outputs_keys_filters_and_flattening(self):
        self.invoke(self.program('''
values:=[-1,0,1,2]
a:=[]int{get(x) or_else 2 for x in values};defer delete(a)
b:=[dynamic]int{get(x) or_else 2 for x in values};defer delete(b)
c:=set[int]{get(x) or_else 2 for x in values};defer delete(c)
d:=map[int]int{get(x) or_else 2=x for x in values};defer delete(d)
e:=[x for x in values if accept(x) or_else false];defer delete(e)
f:=[..pair(x) or_else [7,8] for x in values];defer delete(f)
if a[0]!=2||b[3]!=2||len(c)!=2||d[2]!=2||len(e)!=2||e[0]!=1||len(f)!=8||f[0]!=7||f[4]!=1 {panic("optional combinations")}
''','''
get::proc(x:int)->(int,bool){return x,x>0}
accept::proc(x:int)->(bool,bool){return x>0,x>0}
pair::proc(x:int)->([2]int,bool){return {x,x+1},x>0}
'''))

    def test_or_else_in_compile_time_comprehensions_and_pure_results(self):
        self.invoke(self.program('''
if VALUES!=[2,2,10,20]||type_of(VALUES)!=[4]int {panic("constant optional")}
runtime_input:=[-1,0,1,2]
a:=collect(runtime_input);defer delete(a)
b:=#run collect([-1,0,1,2]);defer delete(b)
if a[2]!=10||b[0]!=2 {panic("pure optional")}
''','''
get::proc(x:int)->(int,bool){return x*10,x>0}
VALUES::[get(a) or_else 2 for a in [-1,0,1,2]]
collect::fn(values:[4]int)->[]int{return [get(a) or_else 2 for a in values]}
'''),flags=['-debug'])

    def test_or_else_native_library_and_collection_source(self):
        self.invoke(self.program('''
words:=["7","invalid","12"]
x:=[strconv.parse_int(word) or_else 2 for word in words];defer delete(x)
y:=[v*2 for v in source(false) or_else [1,2]];defer delete(y)
if x[0]!=7||x[1]!=2||x[2]!=12||y[0]!=2||y[1]!=4 {panic("native optional")}
''','import "core:strconv"\nsource::proc(ok:bool)->([2]int,bool){return {3,4},ok}\n'))

    def test_or_else_map_lookup_and_optional_fallback_call(self):
        self.invoke(self.program('''
values:=[0,1,2]
table:map[int]int;table[1]=7;defer delete(table)
a:=[table[x] or_else 2 for x in values];defer delete(a)
b:=[get(x) or_else backup(x) for x in values];defer delete(b)
c:=[(get(x) or_else 2)+1 for x in values];defer delete(c)
if a[0]!=2||a[1]!=7||b[0]!=9||b[1]!=1||c[0]!=3||c[2]!=3 {panic("optional contexts")}
''','get::proc(x:int)->(int,bool){return x,x>0}\nbackup::proc(x:int)->(int,bool) #optional_ok{return 9,true}\n'))

    def test_optional_values_inside_helpers_keep_runtime_calls_and_constant_types(self):
        text=self.program('''
x:=[(get(a) or_else 2)**2 for a in [0,1,2]];defer delete(x)
if calls!=3||x[0]!=4||x[1]!=100||x[2]!=400 {panic("optional power")}
y:=[get(a) or_panic for a in [1,2]];defer delete(y)
if calls!=5||y[0]!=10||y[1]!=20 {panic("optional panic")}
z:=[switch {case true:get(a) or_else 2;case:3} for a in [0,1,2]];defer delete(z)
if calls!=8||z[0]!=2||z[1]!=10||z[2]!=20 {panic("optional switch")}
''','calls:int\nget::proc(x:int)->(int,bool){calls+=1;return x*10,x>0}\n')
        for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):self.invoke(text,flags=flags)
        self.invoke(self.program('if VALUES!=[4,100,400]{panic("constant optional power")}','''
get::proc(x:int)->(int,bool){return x*10,x>0}
VALUES::[(get(a) or_else 2)**2 for a in [0,1,2]]
'''))

    def test_or_else_fallback_error_points_to_original_expression(self):
        text=self.program('values:=[1,2];x:=[get(a) or_else "wrong" for a in values];_=x',
                          'get::proc(a:int)->(int,bool){return a,true}\n')
        _,result=self.invoke(text,command='check',flags=['--diagnostic-format=json'],fail='wrong')
        errors=json.loads(result.stdout or result.stderr)['errors']
        failures=[e for e in errors if 'wrong' in str(e['msgs'])]
        self.assertTrue(failures,errors)
        self.assertTrue(any(e['pos']['offset']==text.index('"wrong"') for e in failures),failures)

    def test_original_diagnostics(self):
        text=self.program('values:=[1,2]\nx:=[missing(v) for v in values];_=x')
        _,result=self.invoke(text,command='check',flags=['--diagnostic-format=json'],fail='Undeclared name: missing')
        errors=json.loads(result.stdout or result.stderr)['errors']
        error=next(e for e in errors if 'Undeclared name: missing' in str(e['msgs']))
        self.assertEqual(error['pos']['offset'],text.index('missing'))
        self.assertNotIn('__thor_',result.stdout)

if __name__=='__main__':unittest.main()

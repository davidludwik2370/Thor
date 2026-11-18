"""Call-result types and parallel mapping, checked by the native Odin backend."""
import json
import unittest
from tests import test_thor as base

UNCHECKED = ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']


class ParallelMapTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_result_query_preserves_names_types_and_never_calls_the_function(self):
        source='''package main
import ti "thor:intrinsics"
calls: int
effect :: proc()->(number:int, text:string){calls+=1;return 7,"seven"}
identity :: proc(x:$T)->T{return x}
Named :: ti.result_type_of(effect())
Scalar :: ti.result_type_of(identity(i16(7)))
Direct :: ti.result_type_of((proc(x:int)->int{return x})(2))
query_alias :: ti.result_type_of
Unnamed :: query_alias(pair())
pair :: proc()->(int,int){calls+=1;return 1,2}
main::proc(){
    item:Named={7,"seven"}
    value:Scalar=8
    direct:Direct=9
    pair_value:Unnamed={3,4}
    if calls!=0 || item.number!=7 || item.text!="seven" || value!=8 || direct!=9 {panic("query ran code or lost types")}
    if pair_value.v0!=3 || pair_value.v1!=4 {panic("unnamed result fields")}
    Local :: ti.result_type_of(effect())
    local:Local={8,"eight"}
    if local.number!=8 || calls!=0 {panic("local query")}
}
'''
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_polymorphic_callbacks_named_results_overrides_and_source_order(self):
        source='''package main
import "thor:basic"
calculate :: proc(a,b:$T)->(sum,product:T){return a+b,a*b}
Job :: struct{left,right:int}
Override :: struct{added,multiplied:int}
calls:int
source_jobs: [2]Job = {{2,3},{4,5}}
jobs_once :: proc()->[]Job{calls=calls*10+1;return source_jobs[:]}
workers_once :: proc()->int{calls=calls*10+2;return 2}
main::proc(){
    jobs:=[]Job{{2,3},{4,5}}
    for workers in ([]int{1,2,20}) {
        output:=basic.pmap(jobs,calculate,workers=workers)
        if len(output)!=2 || output[0].sum!=5 || output[1].product!=20 {panic("named generic results")}
        delete(output)
    }
    output:=basic.pmap(jobs,calculate,result_type=Override,workers=2)
    defer delete(output)
    if output[0].added!=5 || output[1].multiplied!=20 {panic("override names")}
    map_alias :: basic.pmap
    named:=map_alias(workers=workers_once(),calculate=calculate,jobs=jobs_once())
    defer delete(named)
    if calls!=21 || named[1].sum!=9 {panic("named argument order or duplicate evaluation")}
}
'''
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_scalar_struct_and_unnamed_multiple_results(self):
        source='''package main
import "thor:basic"
Job::struct{x:int}
Item::struct{value:int,label:string}
single::fn(x:int)->int{return x*3}
record::proc(x:int)->Item{return {x,"item"}}
pair::proc(x:int)->(int,int){return x,x+1}
main::proc(){
    jobs:=[]Job{{1},{2},{3}}
    scalar:=basic.pmap(jobs,single,workers=2)
    defer delete(scalar)
    items:=basic.pmap(jobs,record,workers=2)
    defer delete(items)
    pairs:=basic.pmap(jobs,pair,workers=2)
    defer delete(pairs)
    if scalar[2]!=9 || items[1].value!=2 || items[1].label!="item" {panic("single-result shape")}
    if pairs[0].v0!=1 || pairs[2].v1!=4 {panic("multiple integer results must remain structs")}
}
'''
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_no_results_join_and_empty_inputs(self):
        source='''package main
import "thor:basic"
Job::struct{number:int,destination:^int}
set::proc(number:$T,destination:^T){destination^=number*2}
main::proc(){
    values:[32]int
    jobs:[32]Job
    for i in 0..<len(jobs) {jobs[i]={i,&values[i]}}
    basic.pmap(jobs[:],set,workers=4)
    for value,i in values {if value!=i*2 {panic("returned before all jobs finished")}}
    basic.pmap([]Job{},set,workers=4)
}
'''
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)
        invalid=source.replace('    basic.pmap(jobs[:],set,workers=4)','    output:=basic.pmap(jobs[:],set,workers=4)\n    _=output')
        self.invoke(invalid,command='check',fail='does not return a value')

    def test_imports_aliases_and_procedure_variables(self):
        imports=[('import "thor:basic"','basic.pmap'),('import b "thor:basic"','b.pmap'),
                 ('import _ "thor:basic" / pmap','pmap'),('import "thor:parallel"','parallel.pmap')]
        for declaration,name in imports:
            with self.subTest(declaration=declaration):
                self.invoke('''package main
'''+declaration+'''
Job::struct{x:int}
double::proc(x:int)->(answer:int,valid:bool){return x*2,true}
main::proc(){
    callback:proc(int)->(number:int,ok:bool)=double
    output:=MAP([]Job{{3}},callback)
    defer delete(output)
    if output[0].number!=6 || !output[0].ok {panic("procedure variable signature")}
}
'''.replace('MAP',name))

    def test_imported_polymorphic_and_standard_library_callbacks(self):
        source='''package main
import "thor:basic"
import "callbacks"
import "core:math"
main::proc(){
    numbers:=basic.pmap([]struct{x:f64}{{4},{9}},math.sqrt)
    defer delete(numbers)
    if numbers[0]!=2 || numbers[1]!=3 {panic("standard library overload")}
    rows:=basic.pmap([]struct{x:i32}{{7},{8}},callbacks.calculate)
    defer delete(rows)
    if rows[0].number!=7 || !rows[1].valid {panic("imported generic callback")}
}
'''
        self.invoke(source,files={'callbacks/callbacks.thor':'package callbacks\ncalculate::proc(x:$T)->(number:T,valid:bool){return x,true}\n'})

    def test_native_parameter_directives_still_control_call_compatibility(self):
        self.invoke('''package main
import "thor:basic"
read::proc(#any_int value:int)->int{return value+1}
main::proc(){
    rows:=basic.pmap([]struct{x:i8}{{3}},read)
    defer delete(rows)
    if rows[0]!=4 {panic("native parameter directive")}
}
''')

    def test_inline_callbacks_infer_without_annotations(self):
        source='''package main
import "thor:basic"
main::proc(){
    jobs:=[]struct{x:int}{{2},{3}}
    values:=basic.pmap(jobs,proc(x:int)->int{return x*2})
    defer delete(values)
    rows:=basic.pmap(jobs,proc(x:$T)->(value:T,ok:bool){return x*3,true})
    defer delete(rows)
    if values[1]!=6 || rows[0].value!=6 || !rows[1].ok {panic("inline callback inference")}
}
'''
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_type_query_errors_have_original_locations(self):
        prefix='''import ti "thor:intrinsics"
void::proc(){}
'''
        for expression,message in [('ti.result_type_of(1)','call expression'),
                                   ('ti.result_type_of(void())','no return values'),
                                   ('ti.result_type_of()','call expression')]:
            with self.subTest(expression=expression):
                source=self.program('/* 😀 */ R::'+expression+'\n_=R',prefix)
                root,result=self.invoke(source,command='check',fail=message,flags=['--diagnostic-format=json'])
                error=json.loads(result.stderr)['errors'][0]
                self.assertEqual(error['pos']['file'],str((root/'input/main.thor').resolve()))
                self.assertEqual(error['pos']['offset'],source.encode().index(expression.encode()))
                self.assertNotIn('Thor expression','\n'.join(error['msgs']))

    def test_queries_are_unevaluated_in_pure_code_and_keep_native_type_names(self):
        source='''package main
import ti "thor:intrinsics"
import "thor:basic"
calls:int
effect::proc(x:int)->(number:int,ok:bool){calls+=1;return x,true}
Record::struct{x:int}
Choice::enum{a,b}
schema::proc()->(record:Record,choice:Choice){calls+=1;return {3},.b}
schema_size::fn()->int{Shape::ti.result_type_of(schema());return size_of(Shape)}
forever::fn()->int{for {};return 0}
get::fn(x:int)->int{
    Ignored::ti.result_type_of(effect(x))
    return size_of(Ignored)
}
Never::ti.result_type_of(forever())
query::ti.result_type_of
Named::query(effect(x=3))
main::proc(){
    bytes::#run get(2)
    schema_bytes::#run schema_size()
    n:Never=7
    value:Named={3,true}
    if calls!=0 || bytes==0 || schema_bytes==0 || n!=7 || value.number!=3 {panic("unevaluated query")}
    result_type_of::proc(x:int)->int{return x+1}
    if result_type_of(2)!=3 {panic("ordinary same-named procedure")}
}
'''
        for flags in (['--run-timeout','1'],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_compile_time_queries_keep_transitive_result_type_dependencies(self):
        source='''package main
import ti "thor:intrinsics"
import "callbacks"
schema_size::fn()->int{
    Shape::ti.result_type_of(callbacks.effect())
    return size_of(Shape)
}
main::proc(){
    bytes::#run schema_size()
    if bytes==0 || callbacks.calls!=0 {panic("transitive type query evaluated the callback")}
}
'''
        files={
            'records/records.thor':'package records\nRecord::struct{number:int}\n',
            'choices/choices.thor':'package choices\nChoice::enum{first,second}\n',
            'callbacks/callbacks.thor':'''package callbacks
import "../records"
import "../choices"
calls:int
effect::proc()->(record:records.Record,choice:choices.Choice){calls+=1;return {7},.second}
''',
        }
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,files=files,flags=flags)

    def test_query_defaults_work_in_ordinary_library_functions(self):
        library='''package mapping
import ti "thor:intrinsics"
first::proc(jobs:[]$J,callback:$F,$Result:typeid=ti.result_type_of(callback(**J{})))->Result{
    return compress_values(callback(**jobs[0]))
}
'''
        source='''package main
import "mapping"
Job::struct{number:int}
calculate::proc(x:$T)->(number:T,ok:bool){return x*2,true}
main::proc(){
    result:=mapping.first([]Job{{3}},calculate)
    if result.number!=6 || !result.ok {panic("ordinary dependent default")}
}
'''
        self.invoke(source,files={'mapping/mapping.thor':library})

    def test_mapping_errors_reject_incompatible_jobs_and_result_types(self):
        prefix='''import "thor:basic"
Job::struct{x:int}
pair::proc(x:int)->(a,b:int){return x,x}
void::proc(x:int){}
'''
        for expression in ['basic.pmap([]Job{{1}},pair,result_type=struct{a:int})',
                           'basic.pmap([]Job{{1}},pair,result_type=struct{a:string,b:int})',
                           'basic.pmap([]Job{{1}},void,result_type=struct{a:int})',
                           'basic.pmap([]struct{x:string}{{"one"}},pair)']:
            with self.subTest(expression=expression):
                self.invoke(self.program('_='+expression,prefix),command='check',fail='Error:')

    def test_ordering_bounded_workers_nested_mapping_and_read_only_jobs(self):
        source='''package main
import "thor:basic"
import "core:sync"
Job::struct{x:int}
identify::proc(x:int)->(number,thread_id:int){return x*3,sync.current_thread_id()}
nested::proc(x:int)->int{
    output:=basic.pmap([]Job{{x},{x+1}},identify,workers=8)
    defer delete(output)
    if output[0].thread_id!=output[1].thread_id {panic("nested pmap created extra workers")}
    return output[0].number+output[1].number
}
main::proc(){
    jobs:[128]Job
    for &job,i in jobs {job={i}}
    frozen :> jobs[:]
    outputs:=basic.pmap(frozen,identify,workers=4)
    defer delete(outputs)
    threads:=make(map[int]bool)
    defer delete(threads)
    for row,i in outputs {if row.number!=i*3 {panic("order or missing job")};threads[row.thread_id]=true}
    if len(threads)!=4 {panic("worker bound or caller participation")}
    mapped:=basic.pmap(frozen,nested,workers=3)
    defer delete(mapped)
    for value,i in mapped {if value!=6*i+3 {panic("nested result")}}
    nil_jobs:[]Job
    empty:=basic.pmap(nil_jobs,identify)
    defer delete(empty)
    if len(empty)!=0 {panic("nil input")}
}
'''
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_callback_allocations_context_and_scoped_cleanup(self):
        source='''package main
import "thor:basic"
import "core:mem"
import "base:runtime"
Job::struct{x:int}
allocate::proc(x:int)->[]int{
    scratch:=make([]int,8,context.temp_allocator)
    scratch[0]=x
    owned:=make([]int,2)
    owned[0],owned[1]=scratch[0],x+1
    return owned
}
main::proc(){
    tracker:mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker,context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    allocator:=mem.tracking_allocator(&tracker)
    results:=basic.pmap([]Job{{1},{2},{3},{4}},allocate,workers=3,allocator=allocator)
    for row,i in results {if row[0]!=i+1 || row[1]!=i+2 {panic("worker allocation lifetime")};delete(row,runtime.heap_allocator())}
    delete(results,allocator)
    if len(tracker.allocation_map)!=0 {panic("scheduling/output allocation leak")}
}
'''
        for flags in ([],UNCHECKED,['-debug']):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_big_number_results_keep_types_and_own_worker_allocations(self):
        source='''package main
import "thor:basic"
Job::struct{x:int}
calculate::proc(x:int)->(whole:Integer,fraction:Rational){
    return Integer(x)*100000000000000000000000,Rational(x,3)
}
main::proc(){
    rows:=basic.pmap([]Job{{1},{2},{3}},calculate,workers=2)
    defer delete(rows)
    for &row,i in rows {
        if row.whole!=Integer(i+1)*100000000000000000000000 || row.fraction!=Rational(i+1,3) {panic("big result shape")}
        delete(row.whole)
        delete(row.fraction)
    }
}
'''
        for flags in ([],UNCHECKED,['-debug']):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

    def test_scheduling_allocation_failure_falls_back_and_restores_context(self):
        source='''package main
import "thor:basic"
import "core:mem"
State::struct{backing:mem.Allocator,remaining:int}
limited::proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,
    old:rawptr,old_size:int,loc:=#caller_location)->([]byte,mem.Allocator_Error){
    state:=(^State)(data)
    if mode==.Alloc || mode==.Alloc_Non_Zeroed || mode==.Resize || mode==.Resize_Non_Zeroed {
        if state.remaining==0 {return nil,.Out_Of_Memory}
        state.remaining-=1
    }
    return state.backing.procedure(state.backing.data,mode,size,alignment,old,old_size,loc)
}
Job::struct{x:int}
double::proc(x:int)->int{return x*2}
main::proc(){
    original:=context.allocator
    for limit in 1..=3 {
        state:=State{original,limit}
        allocator:=mem.Allocator{limited,&state}
        values:=basic.pmap([]Job{{1},{2},{3},{4}},double,workers=4,allocator=allocator)
        for value,i in values {if value!=(i+1)*2 {panic("allocation fallback")}}
        delete(values,allocator)
        if context.allocator.data!=original.data || context.allocator.procedure!=original.procedure {panic("allocator not restored")}
    }
}
'''
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_read_only_nested_slices_reject_mutating_callbacks(self):
        source='''package main
import "thor:basic"
Job::struct{values:[]int}
mutate::proc(values:[]int){values[0]=7}
main::proc(){
    values:=[]int{1,2}
    jobs :> []Job{{values}}
    basic.pmap(jobs,mutate)
}
'''
        self.invoke(source,command='check',fail='read-only')
        readonly=source.replace('mutate::proc(values:[]int){values[0]=7}',
            'mutate::proc(values:[]int)->int{return values[0]}').replace('    basic.pmap(jobs,mutate)',
            '    rows:=basic.pmap(jobs,mutate)\n    defer delete(rows)\n    if rows[0]!=1 {panic("read-only nested input")}')
        # Existing indirect-callback contracts are conservative even for a
        # reader. A readonly outer job slice with scalar fields is supported;
        # slices carried inside jobs need a writable view for opaque callbacks.
        self.invoke(readonly,command='check',fail='read-only')

    def test_invalid_worker_count_reports_original_call_site(self):
        source='''package main
import "thor:basic"
Job::struct{x:int}
double::proc(x:int)->int{return x*2}
main::proc(){
    rows:=basic.pmap([]Job{{1}},double,workers=-1)
    delete(rows)
}
'''
        _,result=self.invoke(source,fail='pmap: workers must be nonnegative')
        self.assertIn('main.thor(6:',result.stderr)

    def test_no_arguments_callback_defaults_and_overloads(self):
        source='''package main
import "thor:basic"
Job::struct{}
constant::proc()->int{return 42}
integer::proc(x:int)->(number:int,ok:bool){return x,true}
floating::proc(x:f64)->f64{return x*2}
overload::proc{integer,floating}
with_default::proc(x:$T,amount:int=3)->T{return x+T(amount)}
sum::proc(values:..int)->int{n:=0;for value in values {n+=value};return n}
main::proc(){
    constants:=basic.pmap([]Job{{},{},{}},constant,workers=2)
    defer delete(constants)
    if len(constants)!=3 || constants[2]!=42 {panic("empty struct job")}
    numbers:=basic.pmap([]struct{x:int}{{1},{2}},overload)
    defer delete(numbers)
    if numbers[1].number!=2 || !numbers[0].ok {panic("callback overload")}
    defaults:=basic.pmap([]struct{x:i16}{{4}},with_default)
    defer delete(defaults)
    if defaults[0]!=7 {panic("callback default parameter")}
    totals:=basic.pmap([]struct{a,b,c:int}{{1,2,3}},sum)
    defer delete(totals)
    if totals[0]!=6 {panic("variadic callback")}
}
'''
        for flags in ([],UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

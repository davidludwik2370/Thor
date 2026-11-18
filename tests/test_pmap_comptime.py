"""Compile-time mapping shares the runtime scheduler and ordinary pure checks."""
import unittest
from tests import test_pmap as mapping


class CompileTimeParallelMapTests(unittest.TestCase):
    invoke=mapping.ParallelMapTests.invoke
    program=mapping.ParallelMapTests.program

    def test_enclosing_fn_evaluates_automatically_except_optional_debug_calls(self):
        from tests.test_t21_t22 import CacheTests
        prefix='''import "thor:basic"
square::proc(x:int)->int{return x*x}
compute::fn(n:int)->int{
    rows:=basic.pmap([]int{n,n+1},square,workers=2)
    return rows[0]+rows[1]
}
'''
        for declaration,flags,evaluated in [
            ('value:=compute(3)',[],True),
            ('value:=compute(3)',['-debug'],False),
            ('value:=#run compute(3)',['-debug'],True),
            ('value::compute(3)',['-debug'],True),
        ]:
            with self.subTest(declaration=declaration,flags=flags):
                _,result=self.invoke(self.program(declaration+';if value!=25{panic("automatic mapping")}',prefix),flags=[*flags,'--cache-report'])
                self.assertEqual(CacheTests.report(self,result)['writes']>0,evaluated)
                self.assertNotIn('may keep a reference to the stack memory',result.stderr)
                self.assertNotIn('still allocated',result.stderr)

    def test_scalar_and_polymorphic_multi_result_callbacks(self):
        source='''package main
import "thor:basic"
Job::struct{a,b:int}
scalar::fn(a,b:int)->int{return a+b}
pair::proc(a,b:$T)->(sum,product:T){return a+b,a*b}
main::proc(){
    rows:=#run basic.pmap([]Job{{2,3},{4,5}},scalar,workers=2)
    defer delete(rows)
    pairs:=#run basic.pmap([]Job{{2,3},{4,5}},pair,workers=2)
    defer delete(pairs)
    if rows[0]!=5 || rows[1]!=9 || pairs[0].sum!=5 || pairs[1].product!=20 {panic("comptime mapping")}
}
'''
        for flags in ([],mapping.UNCHECKED,['-debug']):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

    def test_impure_callbacks_are_rejected_at_the_mapping_call(self):
        for callback in ['calls+=1;return x','basic.print(x);return x']:
            with self.subTest(callback=callback):
                source='''package main
import "thor:basic"
calls:int
calculate::proc(x:int)->int{BODY}
main::proc(){rows:=#run basic.pmap([]int{2},calculate);delete(rows)}
'''.replace('BODY',callback)
                _,result=self.invoke(source,command='check',fail='Pure')
                self.assertIn('main.thor(5:',result.stderr)

    def test_callbacks_may_mutate_storage_owned_by_the_surrounding_fn(self):
        source='''package main
import "thor:basic"
Job::struct{destination:^int,value:int}
set::proc(destination:^int,value:int){destination^=value}
compute::fn()->int{
    values:[2]int
    jobs:=[]Job{{&values[0],7},{&values[1],9}}
    basic.pmap(jobs,set,workers=2)
    return values[0]+values[1]
}
main::proc(){
    n::#run compute()
    if n!=16 {panic("owned callback mutation")}
}
'''
        for flags in ([],mapping.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_callbacks_cannot_mutate_storage_borrowed_by_a_fn(self):
        source='''package main
import "thor:basic"
Job::struct{destination:^int,value:int}
set::proc(destination:^int,value:int){destination^=value}
compute::fn(values:[]int)->int{
    jobs:=[]Job{{&values[0],7}}
    basic.pmap(jobs,set)
    return values[0]
}
main::proc(){n::#run compute([]int{1});_=n}
'''
        self.invoke(source,command='check',fail='borrowed')

    def test_runtime_fn_results_and_out_parameters_keep_worker_allocations_alive(self):
        source='''package main
import "thor:basic"
import "core:mem"
Job::struct{x:int}
allocate::proc(x:int)->[]int{result:=make([]int,2);result[0],result[1]=x,x+1;return result}
Out::struct{destination:^[]int,value:int}
store::proc(destination:^[]int,value:int){destination^=make([]int,1);destination^[0]=value}
compute::fn(x:int)->[][]int{
    rows:=basic.pmap([]int{x,x+1},allocate,workers=2)
    buffer:[]int
    basic.pmap([]Out{{&buffer,x+2}},store,workers=2)
    rows[0][1]+=buffer[0]
    return rows
}
main::proc(){
    tracker:mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    context.allocator=mem.tracking_allocator(&tracker)
    x:=3
    rows:=compute(x)
    if rows[0][0]!=3 || rows[0][1]!=9 || rows[1][0]!=4 {panic("worker lifetime")}
    for row in rows {delete(row)}
    delete(rows)
    if len(tracker.allocation_map)!=0 || len(tracker.bad_free_array)!=0 {panic("pure pmap lifetime leak")}
}
'''
        for flags in ([],mapping.UNCHECKED,['-debug']):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_explicit_free_across_worker_and_caller_scratch(self):
        source='''package main
import "thor:basic"
allocate::proc(x:int)->[]int{result:=make([]int,2);result[0]=x;return result}
release::proc(values:[]int){delete(values)}
compute::fn(x:int)->int{
    rows:=basic.pmap([]int{x,x+1},allocate,workers=2)
    result:=rows[0][0]+rows[1][0]
    for row in rows {delete(row)}
    jobs:[16][]int
    for &job in jobs {job=make([]int,2)}
    basic.pmap(jobs[:],release,workers=4)
    delete(rows)
    return result
}
main::proc(){
    x:=3
    if compute(x)!=7 {panic("runtime worker cleanup")}
    result::#run compute(5)
    if result!=11 {panic("comptime worker cleanup")}
}
'''
        for flags in (['-debug'],mapping.UNCHECKED):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

    def test_inline_alias_overload_and_named_result_override(self):
        source='''package main
import b "thor:basic"
Job::struct{x:int}
first::proc(x:int)->(number:int,ok:bool){return x*2,true}
other::proc(x:f64)->f64{return x}
calculate::proc{first,other}
Result::struct{value:int,valid:bool}
main::proc(){
    mapper::b.pmap
    rows:=#run mapper([]int{3,4},calculate,result_type=Result,workers=2)
    defer delete(rows)
    direct:=#run mapper([]int{5},proc(x:$T)->T{return x*3})
    defer delete(direct)
    if rows[0].value!=6 || !rows[1].valid || direct[0]!=15 {panic("comptime callback inference")}
}
'''
        self.invoke(source)

    def test_callback_defaults_are_checked_only_when_used(self):
        source='''package main
import "thor:basic"
calculate::proc(x:int,amount:=context.user_index)->int{return x+amount}
main::proc(){rows:=#run basic.pmap([]struct{x,amount:int}{{2,4}},calculate);defer delete(rows);if rows[0]!=6{panic("overridden default")}}
'''
        self.invoke(source)
        self.invoke(source.replace('[]struct{x,amount:int}{{2,4}}','[]struct{x:int}{{2}}'),command='check',fail='Pure')

    def test_the_same_callback_can_be_called_directly_and_through_pmap(self):
        source='''package main
import "thor:basic"
Job::struct{destination:^int,value:int}
set::proc(destination:^int,value:int){destination^=value}
compute::fn()->int{
    a,b:int
    set(&a,3)
    basic.pmap([]Job{{&b,5}},set)
    return a+b
}
main::proc(){n::#run compute();if n!=8{panic("mixed checked callback")}}
'''
        self.invoke(source)

    def test_owned_results_big_numbers_and_nested_mapping(self):
        source='''package main
import "thor:basic"
Job::struct{x:int}
twice::proc(x:$T)->T{return x*2}
calculate::proc(x:int)->(whole:Integer,fraction:Rational,text:string,values:[]int){
    nested:=basic.pmap([]int{x,x+1},twice,workers=4)
    return Integer(x)*100000000000000000000000,Rational(x,3),f"value {x}",nested
}
main::proc(){
    rows:=#run basic.pmap([]int{1,2},calculate,workers=2)
    for &row,i in rows {
        if row.whole!=Integer(i+1)*100000000000000000000000 || row.fraction!=Rational(i+1,3) || row.values[1]!=2*(i+2) {panic("owned materialization")}
        delete(row.whole);delete(row.fraction);delete(row.text);delete(row.values)
    }
    delete(rows)
}
'''
        for flags in ([],mapping.UNCHECKED,['-debug']):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

    def test_zero_argument_empty_and_standard_library_callbacks(self):
        self.invoke('''package main
import "thor:basic"
import "core:math"
constant::proc()->int{return 7}
main::proc(){
    roots:=#run basic.pmap([]f64{4,9},math.sqrt)
    defer delete(roots)
    values:=#run basic.pmap([]struct{}{{},{}},constant)
    defer delete(values)
    empty:=#run basic.pmap([]struct{}{},constant)
    defer delete(empty)
    if roots[1]!=3 || values[0]!=7 || len(empty)!=0 {panic("empty/native mapping")}
}
''')

    def test_runtime_callbacks_and_explicit_allocators_are_rejected(self):
        prefix='''import "thor:basic"
calculate::proc(x:int)->int{return x*2}
'''
        for body,message in [
            ('callback:=calculate;rows:=#run basic.pmap([]int{1},callback);delete(rows)','compile-time known'),
            ('rows:=#run basic.pmap([]int{1},calculate,allocator=context.allocator);delete(rows)','allocator'),
            ('rows:=#run basic.pmap([]int{1},calculate,worker_allocator=context.allocator);delete(rows)','allocator'),
        ]:
            with self.subTest(body=body):self.invoke(self.program(body,prefix),command='check',fail=message)

    def test_selected_generic_branch_and_callback_transitive_effects(self):
        source='''package main
import "thor:basic"
calculate::proc(x:$T)->T{when T==int{return x*2}else{basic.print(x);return x}}
main::proc(){rows:=#run basic.pmap([]int{2},calculate);defer delete(rows);if rows[0]!=4{panic("generic branch")}}
'''
        self.invoke(source)
        self.invoke(source.replace('[]int{2}','[]f64{2}'),command='check',fail='I/O')

    def test_positional_options_and_unrelated_mapping_name(self):
        source='''package main
import "thor:basic"
set::proc(destination:^int){destination^=7}
square::proc(x:int)->int{return x*x}
pmap::proc(x:int)->int{return x+1}
compute::fn()->int{
    value:int
    basic.pmap([]^int{&value},set,1)
    rows:=basic.pmap([]int{3},square,int,2)
    return value+rows[0]+pmap(1)
}
main::proc(){value::#run compute();if value!=18{panic("positional options")}}
'''
        self.invoke(source)
        self.invoke(source.replace('},set,1)' ,'},set,1,context.allocator)'),command='check',fail='allocator')

    def test_negative_workers_and_timeout_report_the_original_request(self):
        self.invoke(self.program('rows:=#run basic.pmap([]int{2},calculate,workers=-1);delete(rows)',
            'import "thor:basic"\ncalculate::proc(x:int)->int{return x}\n'),command='check',fail='workers must be nonnegative')
        self.invoke(self.program('rows:=#run basic.pmap([]int{2},calculate);delete(rows)',
            'import "thor:basic"\ncalculate::proc(x:int)->int{for {};return x}\n'),command='check',flags=['--run-timeout','1'],fail='timeout')

    def test_cached_results_are_reused_and_callback_edits_invalidate_them(self):
        from tests.test_t21_t22 import CacheTests
        source='''package main
import "thor:basic"
calculate::proc(x:int)->int{return x*2}
main::proc(){
    rows:=#run basic.pmap([]int{3},calculate)
    defer delete(rows)
    if rows[0]!=6 {panic("cached mapping")}
}
'''
        root,first=self.invoke(source,flags=['--cache-report'],directory=True)
        self.assertGreaterEqual(CacheTests.report(self,first)['writes'],1)
        warm=CacheTests.again(self,root)
        self.assertGreaterEqual(CacheTests.report(self,warm)['hits'],1)
        (root/'input/main.thor').write_text(source.replace('return x*2','return x*3').replace('rows[0]!=6','rows[0]!=9'))
        changed=CacheTests.again(self,root)
        self.assertGreaterEqual(CacheTests.report(self,changed)['writes'],1)

    def test_explicit_delete_of_nonnumeric_fields_preserves_native_behavior(self):
        source='''package main
import "thor:basic"
calculate::proc(x:int)->(whole:Integer,text:string,values:[]int){
    return Integer(x),f"value {x}",make([]int,2)
}
main::proc(){
    rows:=#run basic.pmap([]int{1,2},calculate)
    for row in rows {delete(row.text);delete(row.values)}
    for &row in rows {delete(row.whole)}
    delete(rows)
}
'''
        for flags in (['-debug'],mapping.UNCHECKED):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

"""Conservative allocation contracts, including generated and indirect effects."""
from tests.test_rust_compiler import compiler_engines
import json
import os
import subprocess
import unittest
from tests import test_rust_compiler as base


class NoAllocTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke
    compare=base.RustCompilerTests.compare

    def check(self,body,*,declarations='',fail=None,header='',flags=(),command='check'):
        return self.compare(header+'package main\n'+declarations+'\n@(no_alloc)\nchecked::proc(){'+body+'}\nmain::proc(){checked()}\n',
                            command=command,flags=['-thread-count:1',*flags],fail=fail)

    def test_scalar_fixed_storage_recursion_and_aliases(self):
        text='''package main
import "core:fmt"
Pair::struct{x,y:int}
step::proc(values:[]int,amount:int){for &value in values {value+=amount}}
recur::proc(n:int)->int{if n<=0 {return 0};return n+recur(n-1)}
@(no_alloc)
calculate::proc()->int{
 a:=[1,2,3];alias::step;alias(a[:],2)
 b:=Pair{1,2}+{3,4};b*= {2,3}
 return recur(3)+a[2]+b.x+b.y
}
main::proc(){if calculate()!=37 {panic("bad result")};fmt.println("ok")}
'''
        for flags in [[],base.UNCHECKED,['-debug']]:
            with self.subTest(flags=flags):self.compare(text,flags=['-thread-count:1',*flags],expected='ok')

    def test_successful_paths_make_zero_runtime_allocations(self):
        text='''package main
import "core:mem"
import "core:fmt"
Pair::struct{a,b:int}
@(no_alloc)
build_pair::fn(value:int)->Pair{return {value,value+1}}
@(no_alloc)
sum::fn(values:[]int)->(result:int){for value in values {result+=value};return}
@(no_alloc)
calculate::proc()->int{
 values:=[1,2,3]
 for &value in values {value*=2}
 pair:=#runtime build_pair(3)
 return sum(values[:])+pair.a+pair.b
}
main::proc(){
 backing:=context.allocator
 temp:=context.temp_allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 context.allocator=mem.tracking_allocator(&tracker)
 context.temp_allocator=context.allocator
 result:=calculate()
 count:=tracker.total_allocation_count
 context.allocator=backing
 context.temp_allocator=temp
 mem.tracking_allocator_destroy(&tracker)
 if result!=19 || count!=0 {panic("no_alloc runtime proof failed")}
 fmt.println("zero allocations")
}
'''
        for flags in [[],base.UNCHECKED,['-debug','--track-allocations=off','--no-sanitize']]:
            with self.subTest(flags=flags):self.compare(text,flags=['-thread-count:1',*flags],expected='zero allocations')

    def test_allocating_builtins_even_zero_size_or_spare_capacity(self):
        for expr in ['make([]int,0)','new(int)','make([dynamic]int,0,16)','make(map[int]int)',
                     'append(&values,1)','reserve(&values,1)','resize(&values,1)','delete(values)']:
            with self.subTest(expr=expr):
                self.check('values:[dynamic]int;_ = values;'+expr,fail='no_alloc:')

    def test_transitive_and_recursive_failures_are_not_hidden_by_cycles(self):
        self.check('a(2)',declarations='''
a::proc(n:int){if n>0 {b(n-1)}}
b::proc(n:int){a(n);value:=make([]int,2);_=value}
''',fail="builtin 'make'")

    def test_constant_aliases_and_shadowed_builtin_names(self):
        self.check('alias()',declarations='allocate::proc(){_=make([]int,1)}\nalias::allocate',fail="builtin 'make'")
        self.check('make()',declarations='make::proc(){_=new(int)}',fail='no_alloc:')
        self.check('make()',declarations='make::proc(){}')

    def test_indirect_and_foreign_calls_are_unverified(self):
        for body,decl in [
            ('external()','foreign import libc "system:c"\nforeign libc {external::proc() ---}'),
        ]:
            with self.subTest(body=body):self.check(body,declarations=decl,fail='no_alloc:')

    def test_known_callback_values_and_forwarding(self):
        self.check('callback:=plain;callback();invoke(plain)',declarations='plain::proc(){}\ninvoke::proc(callback:proc()){callback()}')
        self.check('invoke(allocate)',declarations='allocate::proc(){_=make([]int,1)}\ninvoke::proc(callback:proc()){callback()}',fail="builtin 'make'")

    def test_callback_defaults_and_names_come_from_declared_callable(self):
        decl='a::proc(flag:bool=true){}\nb::proc(other:bool=false){if other {_=make([]int,1)}}'
        self.check('callback:=a;callback=b;callback()',declarations=decl,fail="builtin 'make'")
        self.check('callback:=a;callback=b;callback(flag=false)',declarations=decl)
        self.check('invoke(b)',declarations=decl+'\ninvoke::proc(callback:proc(yes:bool=true)){callback()}',fail="builtin 'make'")
        self.check('invoke(b)',declarations=decl+'\ninvoke::proc(callback:proc(yes:bool=true)){callback(yes=false)}')

    def test_callback_type_cannot_hide_deferred_metadata(self):
        self.check('callback:=original;callback=other;callback()',declarations='''
after::proc(){_=make([]int,1)}
@(deferred_none=after)
original::proc(){}
other::proc(){}
''',fail='no_alloc:')

    def test_nonallocating_generic_helper_and_overload_selection(self):
        self.check('values:=[1,2];_=count(values[:]);_=choose(1)',declarations='''
count::proc(values:[]$T)->int{return len(values)}
number::proc(x:int)->int{return x+1}
text::proc(x:string)->int{_=make([]int,1);return len(x)}
choose::proc{number,text}
''')
        self.check('_=choose("a")',declarations='''
number::proc(x:int)->int{return x+1}
text::proc(x:string)->int{_=make([]int,1);return len(x)}
choose::proc{number,text}
''',fail="builtin 'make'")

    def test_generated_runtime_allocations(self):
        for body in ['value:=7;text:=f"{value}";_=text',
                     'values:=[1,2];result:=[x*2 for x in values];_=result',
                     'values:=map[int]int{1=2};_=values',
                     'values:=[dynamic]int{1,2};_=values',
                     'values:=set[int]{1,2};_=values']:
            with self.subTest(body=body):self.check(body,fail='no_alloc:')

    def test_map_insertion_and_indirect_aliases(self):
        for write in ['values[1]=2','(values)[1]=2','alias:=values;alias[1]=2']:
            with self.subTest(write=write):
                self.check('write(nil)',declarations='write::proc(values:map[int]int){'+write+'}',fail='map writes may grow storage')

    def test_both_runtime_branches_but_only_active_when_branch(self):
        self.check('if flag {_=make([]int,1)}',declarations='flag:bool',fail="builtin 'make'")
        self.check('if false {_=make([]int,1)}')
        self.check('when false {_=make([]int,1)} else {value:=2;_=value}')
        self.check('when #config(ALLOCATE,false) {_=make([]int,1)}')
        self.check('value:=(make([]int,1) when false else []int{2});_=value')
        self.check('when #config(ALLOCATE,false) {_=make([]int,1)}',flags=['-define:ALLOCATE=true'],fail="builtin 'make'")

    def test_fn_scalar_results_and_owned_results(self):
        self.check('_=twice(7)',declarations='twice::fn(x:int)->int{return x*2}')
        self.check('values:=[1,2];_=view(values[:])',declarations='view::fn(values:[]int)->[]int{return values}',fail='ownership promotion may allocate')
        self.check('_=#runtime text()',declarations='text::fn()->string{return "hello"}',fail='ownership promotion may allocate')
        self.check('a:=[1];b:=[2];values:=soa_zip(a=a[:],b=b[:]);_=#runtime identity(values)',
                   declarations='identity::fn(value:$T)->T{return value}',fail='ownership promotion may allocate')

    def test_scalar_run_and_type_queries_do_not_reject_compiler_work(self):
        self.check('value:=#run work();_=value;_=size_of(make([]int,1))',declarations='''
work::fn()->int{values:=make([]int,2);values[0]=7;return values[0]}
''')
        self.check('value:=#runtime work();_=value',declarations='work::fn()->int{values:=make([]int,2);return len(values)}',fail="builtin 'make'")

    def test_run_owned_result_materialization(self):
        self.check('values:=#run work();_=values',declarations='work::fn()->[]int{return []int{1,2}}',fail='materializing this compile-time result may allocate')
        self.check('value:=#run work();_=value',declarations='work::fn()->string{return "hello"}',fail='materializing this compile-time result may allocate')
        self.check('TEXT::#run work();_=TEXT',declarations='work::fn()->string{return "hello"}')

    def test_file_policy_procedures_initializers_and_other_files(self):
        self.compare('''#+vet no_alloc
package main
safe::proc(x:int)->int{return x+1}
main::proc(){_=safe(2)}
''',files={'other.thor':'package main\nunused::proc(){_=make([]int,1)}\n'},directory=True,command='check',flags=['-thread-count:1'])
        for decl in ['unused::proc(){_=make([]int,1)}','value:=make([]int,1)','value:>make([]int,1)',
                     'import * "thor:basic"\nvalue:Integer=1']:
            with self.subTest(decl=decl):
                self.compare('#+vet no_alloc\npackage main\n'+decl+'\nmain::proc(){}',command='check',flags=['-thread-count:1'],fail='no_alloc:')

    def test_mixed_native_tags_comments_and_crlf(self):
        self.compare('''#+vet no_alloc, semicolon
package main
// #+vet no_alloc
main::proc(){value:=7;_=value}
'''.replace('\n','\r\n'),command='check',flags=['-thread-count:1'])
        self.compare('package main\n// @(no_alloc)\ntext::"#+vet no_alloc"\nmain::proc(){_=make([]int,1);_=text}',command='check',flags=['-thread-count:1'])
        self.compare('#+vet no_alloc\n#+vet no_alloc\npackage main\nmain::proc(){}',command='check',flags=['-thread-count:1'])

    def test_invalid_annotations_and_no_foreign_promises(self):
        for decl,problem in [
            ('@(no_alloc=true)\nf::proc(){}','takes no value'),
            ('@(no_alloc,no_alloc)\nf::proc(){}','Duplicate @(no_alloc)'),
            ('@(no_alloc)\n@(no_alloc)\nf::proc(){}','Duplicate @(no_alloc)'),
            ('@(no_alloc)\nvalue:=7','requires a procedure'),
            ('@(no_alloc)\nCallback::#type proc()','requires a procedure'),
            ('plain::proc(){}\n@(no_alloc)\nalias::plain','requires a procedure'),
            ('foreign import libc "system:c"\nforeign libc {@(no_alloc)\nf::proc() ---}','foreign or bodyless'),
        ]:
            with self.subTest(decl=decl):self.compare('package main\n'+decl+'\nmain::proc(){}',command='check',flags=['-thread-count:1'],fail=problem)

    def test_sdk_direct_calls_and_aliases(self):
        self.check('_=alias(1.5)',declarations='import "core:math"\nalias::math.sqrt')
        self.check('_=alias("hello")',declarations='import "core:strings"\nalias::strings.clone',fail='no_alloc:')

    def test_unused_attribute_cannot_suppress_contract(self):
        self.compare('package main\n@(unused,no_alloc,vet_ignore="pure")\nvalue::proc(){_=make([]int,1)}\nmain::proc(){}',command='check',flags=['-thread-count:1'],fail='no_alloc:')

    def test_contract_errors_survive_unchecked_and_warning_flags(self):
        for flags in [base.UNCHECKED,['-ignore-warnings'],['-warnings-as-errors']]:
            with self.subTest(flags=flags):self.check('_=make([]int,1)',flags=flags,command='build',fail='no_alloc:')

    def test_shorthand_and_native_attributes_keep_their_meaning(self):
        self.compare('package main\n@(no_alloc,private="file")\nvalue::proc()->int{return 7}\n@no_alloc\nmain::proc(){_=value()}',
                     command='check',flags=['-thread-count:1'])
        self.compare('package main\n@no_alloc\nmain::proc(){_=make([]int,1)}',command='check',flags=['-thread-count:1'],fail='no_alloc:')

    def test_defer_and_nested_functions(self):
        self.check('defer allocate()',declarations='allocate::proc(){_=make([]int,1)}',fail="builtin 'make'")
        self.check('inner::proc(){_=make([]int,1)};_=inner')
        self.check('inner::proc(){_=make([]int,1)};inner()',fail="builtin 'make'")

    def test_defaults_only_when_omitted(self):
        self.check('_=read(4)',declarations='read::proc(value:int=7)->int{return value}')
        self.check('_=read()',declarations='read::proc(value:int=7)->int{return value}')

    def test_fallback_lists_and_switch_arms(self):
        self.check('_,value:=read() or_else (_,2);_=value',declarations='read::proc()->(int,int,bool){return 1,2,true}')
        self.check('value:=1;result:=switch value {case 1: 7;case: 9};_=result')
        self.check('value:=1;result:=switch value {case 1: len(make([]int,2));case: 9};_=result',fail="builtin 'make'")
        self.check('_,value:=read() or_else (_,allocate());_=value',declarations='read::proc()->(int,int,bool){return 1,2,true}\nallocate::proc()->int{_=make([]int,1);return 0}',fail="builtin 'make'")

    def test_elided_record_storage_is_inferred_but_not_assumed(self):
        self.check('data:Record={1,2};_=data;_=build()',declarations='Record::struct{x,y:int}\nbuild::proc()->Record{return {3,4}}')
        for typ,body in [('[dynamic]int','{1,2}'),('map[int]int','{1=2}')]:
            with self.subTest(typ=typ):
                self.check('value:Record={values='+body+'};_=value',declarations='Record::struct{values:'+typ+'}',fail='no_alloc:')

    def test_pmap_and_allocation_error_paths_cannot_hide_effects(self):
        self.check('values:=[1,2];result:=pmap(values[:],double);_=result',declarations='import * "thor:basic"\ndouble::proc(x:int)->int{return x*2}',fail='no_alloc:')
        self.check('value:=read() or_panic;_=value',declarations='read::proc()->(int,bool){return 1,true}')

    def test_runtime_marker_and_uninitialized_stack_storage(self):
        self.check('values:[2]int=---;values[0]=1;values[1]=2;_=#runtime double(values[0])',declarations='double::fn(x:int)->int{return x*2}')

    def test_compile_time_comprehension_has_no_runtime_storage(self):
        self.check('VALUES::[x*2 for x in 0..<3];_=VALUES')

    def test_named_arguments_metadata_and_builtin_aliases(self):
        self.check('values:=[1,2];_=reader(values=values[:]);_=length(values[:]);_=raw_data(values[:])',declarations='''
reader::proc(#ro values:[]int,expression:>#caller_expression(values))->int{return len(values)}
length::len
''')

    def test_named_and_pointer_map_writes_are_rejected(self):
        for typ,body in [('Mapping','values[1]=2'),('^Mapping','values[1]=2'),
                         ('Container','values.mapping[1]=2')]:
            with self.subTest(typ=typ):
                self.check('values:'+typ+';write(values)',declarations='''
Mapping::distinct map[int]int
Container::struct{mapping:Mapping}
write::proc(values:'''+typ+'){'+body+'}',fail='map writes may grow storage')

    def test_runtime_side_effects_without_allocation_are_allowed(self):
        self.check('counter+=1;data:=[1,2];copy(data[:],[]int{3,4});_=data',declarations='counter:int')

    def test_other_file_calls_cannot_hide_allocation(self):
        self.compare('package main\n@(no_alloc)\nmain::proc(){helper()}',files={'helper.thor':'package main\nhelper::proc(){_=make([]int,2)}'},
                     directory=True,command='check',flags=['-thread-count:1'],fail="builtin 'make'")

    def test_imported_package_call_cannot_hide_allocation(self):
        self.compare('package main\nimport "helper"\n@(no_alloc)\nmain::proc(){helper.work()}',
                     files={'helper/lib.thor':'package helper\nwork::proc(){_=make([]int,2)}'},directory=True,command='check',flags=['-thread-count:1'],fail="builtin 'make'")

    def test_annotation_on_fn_is_checked_even_when_unused(self):
        self.compare('package main\n@(no_alloc)\nunused::fn()->int{values:=make([]int,1);return len(values)}\nmain::proc(){}',
                     command='check',flags=['-thread-count:1'],fail="builtin 'make'")

    def test_fn_aggregate_ownership_and_big_number_storage(self):
        self.check('value:Holder;_=wrap(value)',declarations='''
Holder::struct{values:[]int}
wrap::fn(value:Holder)->Holder{return value}
''',fail='ownership promotion may allocate')
        self.check('value:Integer;value+=1;_=value',declarations='import * "thor:basic"',fail='Integer/Rational')

    def test_implicit_logical_number_initialization_cannot_bypass_the_contract(self):
        for body in ['value:Integer=1','value:Rational=1','value:Integer','value:Rational',
                     'values:=[]Integer{1,2};_=len(values)',
                     'values:=[]Rational{1,2};_=len(values)']:
            with self.subTest(body=body):
                self.check(body,declarations='import * "thor:basic"\nhelper::proc(value:Integer){}',fail='Integer/Rational')
        self.check('helper(1)',declarations='import * "thor:basic"\nhelper::proc(value:Integer){}',fail='no_alloc:')
        self.check('value:=Record{1};_=value',declarations='import * "thor:basic"\nRecord::struct{value:Integer}',fail='Integer/Rational')

    def test_borrowed_string_results_remain_allocation_free(self):
        self.check('_=read("hello")',declarations='read::proc(value:string)->string{return value}')

    def test_discarded_logical_numeric_builtin_results_are_still_owned(self):
        for expr in ['min(..values)','max(..values)','alias(..values)']:
            with self.subTest(expr=expr):
                self.check('probe(nil)',declarations='import * "thor:basic"\nalias::min\nprobe::proc(values:[]Integer){_,_='+expr+'}',fail='Integer/Rational builtin results')
        # Native copy is shallow even for logical numbers; headers and lengths
        # do not acquire logical-number ownership just by being inspected.
        self.check('probe(nil,nil)',declarations='import * "thor:basic"\nprobe::proc(a,b:[]Integer){_=copy(a,b);_=len(a)}')
        self.check('read(nil)',declarations='read::proc(values:[]int){_,_=min(..values)}')

    def test_checked_bounds_and_type_assertions_keep_native_trap_policy(self):
        self.check('data:=[1,2];_=read(data[:]);value:any=7;_=read_any(value)',declarations='''
read::proc(values:[]int)->int{return values[0]}
read_any::proc(value:any)->int{return value.(int)}
''')
        self.check('panic("explicit")')
        self.check('unreachable()')

    def test_struct_slice_iterator_index_is_a_scalar(self):
        self.compare('''package main
import "core:fmt"
Row::struct{value:int}
@(no_alloc)
total::proc(rows:[]Row)->(result:int){for row,i in rows {result+=row.value+i};return}
main::proc(){rows:=[2]Row{{1},{2}};if total(rows[:])!=4 {panic("index type")};fmt.println("4")}
''',expected='4',flags=['-thread-count:1'])

    def test_generic_annotation_requires_proof_without_type_assumptions(self):
        self.compare('package main\n@(no_alloc)\nidentity::proc(value:$T)->T{return value}\nmain::proc(){_=identity(1)}',
                     command='check',flags=['-thread-count:1'],fail='generic value copies')
        self.compare('package main\n@(no_alloc)\nconstruct::proc($T:typeid)->[1]T{return {1}}\nmain::proc(){_=construct(int)}',
                     command='check',flags=['-thread-count:1'],fail='generic value copies')
        self.check('value:=square(3);_=value',declarations='square::proc(value:$T)->T{return value*value}')

    def test_allocation_free_generic_slice_annotation(self):
        self.compare('package main\n@(no_alloc)\ncount::proc(values:[]$T)->int{return len(values)}\nmain::proc(){a:=[1,2];_=count(a[:])}',
                     command='check',flags=['-thread-count:1'])

    def test_unbound_generics_cannot_hide_owned_storage_or_discarded_results(self):
        for declaration in ['probe::proc(value:$T){}',
                            'probe::proc($T:typeid){value:T}',
                            'probe::proc($T:typeid){_=T(1)}',
                            'probe::proc($T:typeid){_=cast(T)1}',
                            'probe::proc($T:typeid){_=T{}}',
                            'probe::proc(values:[]$T){_,_=min(..values)}',
                            'probe::proc(values:[]$T){_,_=max(..values)}']:
            with self.subTest(declaration=declaration):
                self.compare('package main\n@(no_alloc)\n'+declaration+'\nmain::proc(){}',
                             command='check',flags=['-thread-count:1'],fail='no_alloc:')
        self.compare('package main\nCell::struct($T:typeid){value:T}\n@(no_alloc)\nprobe::proc(cell:^Cell($T)){_=cell.value}\nmain::proc(){}',
                     command='check',flags=['-thread-count:1'],fail='no_alloc:')

    def test_constant_specializations_and_lexical_shadowing(self):
        decl='choose::proc($allocate:bool){when allocate {_=make([]int,1)}}\nforward::proc($flag:bool){choose(flag)}'
        self.check('forward(false)',declarations=decl)
        self.check('forward(true)',declarations=decl,fail="builtin 'make'")
        self.check('helper(int)',declarations='import "base:intrinsics"\nhelper::proc($T:typeid){{T::[]int;when intrinsics.type_is_slice(T){_=make([]int,1)}}}',fail="builtin 'make'")
        self.check('helper(false)',declarations='helper::proc($flag:bool){{flag::true;when flag {_=make([]int,1)}}}',fail="builtin 'make'")

    def test_expanding_generic_recursion_has_a_bounded_diagnostic(self):
        self.check('expand(int)',declarations='expand::proc($T:typeid){expand([1]T)}',fail='cannot prove the allocation effects of expanding generic recursion')

    def test_file_policy_checks_nested_and_anonymous_procedures(self):
        self.compare('#+vet no_alloc\npackage main\nmain::proc(){callback:=proc(){_=make([]int,1)};_=callback}',
                     command='check',flags=['-thread-count:1'],fail="builtin 'make'")
        self.compare('#+vet no_alloc\npackage main\nwhen false {value:=make([]int,1);bad::proc(){_=new(int)}}\nmain::proc(){}',
                     command='check',flags=['-thread-count:1'])
        self.compare('#+vet no_alloc\npackage main\nwhen #config(ACTIVE,false) {value:=make([]int,1);bad::proc(){_=new(int)}}\nmain::proc(){}',
                     command='check',flags=['-thread-count:1'])

    def test_deferred_attribute_effects_are_not_assumed_safe(self):
        self.check('work()',declarations='''
after::proc(){_=make([]int,1)}
@(deferred_none=after)
work::proc(){}
''',fail='no_alloc:')

    def test_repeated_calls_share_proof(self):
        self.source.write_text('package main\nhelper::proc(value:int)->int{return value+1}\n@(no_alloc)\nmain::proc(){'+
                               ''.join('_=helper(1);' for _ in range(200))+'}\n')
        profile=self.root/'profile.json'
        result=self.invoke('rust','check',env={**os.environ,'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode,0,result.stderr)
        info=json.loads(profile.read_text())
        self.assertEqual(info['counters']['no_alloc.procedures'],2,info)

    def test_file_policy_universal_proofs_subsume_call_contexts(self):
        self.source.write_text('\n'.join(['#+vet no_alloc','package main',
            'leaf::proc(value:int)->int{return value+1}',
            'forward::proc(value:int)->int{return leaf(value)}',
            'main::proc(){'+''.join('_=forward('+str(i)+');' for i in range(200))+'}']))
        profile=self.root/'universal.json'
        result=self.invoke('rust','check',env={**os.environ,'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode,0,result.stderr)
        info=json.loads(profile.read_text())
        self.assertEqual(info['counters']['no_alloc.procedures'],3,info)

    def test_profile_is_gated_for_unannotated_projects(self):
        self.source.write_text('package main\n// A comment mentioning @(no_alloc) does not opt in.\nmain::proc(){values:=make([]int,3);_=values}\n')
        profile=self.root/'profile.json'
        result=self.invoke('rust','check',env={**os.environ,'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode,0,result.stderr)
        info=json.loads(profile.read_text())
        self.assertNotIn('no_alloc.procedures',info.get('counters',{}))
        self.assertNotIn('analysis.no_alloc',info.get('stages',{}))

    def test_vet_apply_preserves_and_rechecks_contracts(self):
        original='package main\n@(no_alloc)\nnext::proc(value:int)->int{return value+1}\nmain::proc(){_=next(7)}\n'
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.source.write_text(original)
                result=self.invoke(engine,'vet',flags=['--apply','-thread-count:1'])
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                changed=self.source.read_text()
                self.assertIn('@(no_alloc)',changed)
                self.assertIn('next::fn(',changed)
                result=self.invoke(engine,'check',flags=['-thread-count:1'])
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_native_compatibility_view_consumes_only_custom_contract(self):
        self.compare('#+vet no_alloc\npackage main\n@(no_alloc)\nmain::proc(){}',
                     command='check',flags=['--odin-compatible','-vet-semicolon','-thread-count:1'])

    def test_sdk_allocation_does_not_disappear_behind_a_native_helper(self):
        self.check('_=strings.clone("hello")',declarations='import "core:strings"',fail='no_alloc:')


if __name__=='__main__':unittest.main()

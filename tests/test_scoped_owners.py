"""Shallow lexical ownership, allocator identity, and lifetime diagnostics."""
import json
import unittest
from tests import test_rust_compiler as base

class ScopedOwnerTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke
    compare=base.RustCompilerTests.compare

    def checked(self,source,expected=''):
        for flags in [[],base.UNCHECKED]:
            with self.subTest(flags=flags):
                self.compare(source,flags=['-thread-count:1',*flags],expected=expected)

    def test_all_spacing_forms_and_lexical_cleanup(self):
        self.checked('''package main
import "core:mem"
import "core:fmt"
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 {
  a~ := make([]int,1)
  b ~:= make([]int,2)
  c~ :> make([]int,3)
  d ~:> make([]int,4)
  if len(a)+len(b)+len(c)+len(d)!=10 {panic("spacing")}
  if tracker.current_memory_allocated==0 {panic("allocation missing")}
 }
 if tracker.current_memory_allocated!=0 || tracker.total_allocation_count!=4 {panic("leak or registration allocation")}
 fmt.println("ok")
}
''','ok')

    def test_final_headers_custom_allocators_and_shallow_cleanup(self):
        self.checked('''package main
import "core:mem"
import "core:strings"
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 allocator:=mem.tracking_allocator(&tracker)
 context.allocator=allocator
 inner:=make([]int,2)
 {
  p~:=new(int)
  p^=7
  outer~:=make([][]int,1)
  outer[0]=inner
  growing~:=make([dynamic]int)
  for i in 0..<1000 {append(&growing,i)}
  table~:=make(map[int]int)
  for i in 0..<100 {table[i]=i}
  text~:>strings.clone("hello",allocator=allocator)
  if p^!=7 || len(growing)!=1000 || len(table)!=100 || text!="hello" {panic("values")}
  // Slices/pointers keep their construction allocator; containers own theirs.
  context.allocator=backing
 }
 if tracker.current_memory_allocated!=2*size_of(int) {panic("shallow/allocator cleanup")}
 delete(inner,allocator)
 if tracker.current_memory_allocated!=0 {panic("leak")}
}
''')

    def test_early_return_break_continue_and_defer_order(self):
        self.checked('''package main
import "core:mem"
import "core:fmt"
work::proc()->int{
 nums~:=make([]int,1)
 defer {if nums[0]!=9 {panic("defer order")}}
 nums[0]=9
 return nums[0]
}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 for i in 0..<5 {
  nums~:=make([]int,5)
  nums[0]=i
  if i<3 {continue}
  break
 }
 fmt.println(work())
 if tracker.current_memory_allocated!=0 {panic("early exit leak")}
}
''','9')

    def test_reject_returns_through_aliases_views_and_aggregates(self):
        cases=[
            ('[]int','return nums'),
            ('[]int','alias:=nums;return alias'),
            ('[]int','return nums[1:]'),
            ('^int','return &nums[0]'),
            ('Box','return Box{nums}'),
            ('[]int','return identity(nums)'),
            ('[]int','return true ? nums : nil'),
        ]
        for typ,tail in cases:
            with self.subTest(tail=tail):
                self.compare('''package main
Box::struct{values:[]int}
identity::proc(values:[]int)->[]int{return values}
bad::proc()->'''+typ+'{nums~:=make([]int,2);'+tail+'}\nmain::proc(){}\n',command='check',flags=['-thread-count:1'],fail='cannot escape its scope')

    def test_pointer_and_named_result_cannot_escape(self):
        for body in ['bad::proc()->^int{p~:=new(int);return p}',
                     'bad::proc()->(result:[]int){nums~:=make([]int,2);result=nums;return}']:
            self.compare('package main\n'+body+'\nmain::proc(){}\n',command='check',fail='cannot escape its scope')

    def test_independent_copies_and_scalar_loads_can_return(self):
        self.checked('''package main
import "core:slice"
import "core:fmt"
Record::struct{n:int}
scalar::proc()->int {nums~:=make([]int,1);nums[0]=7;return nums[0]}
record::proc()->Record {p~:=new(Record);p.n=8;return p^}
copy::proc()->[]int {nums~:=make([]int,1);nums[0]=9;return slice.clone(nums)}
main::proc(){nums~:=copy();fmt.println(scalar(),record().n,nums[0])}
''','7 8 9')

    def test_invalid_scoped_declarations(self):
        for source,problem in [
            ('nums~:=make([]int,1)\nmain::proc(){}','only allowed on local'),
            ('main::proc(){nums~::3}','requires'),
            ('main::proc(){_~:=make([]int,1)}','named owner'),
            ('main::proc(){nums~:=3;_=nums}','supported owning'),
            ('main::proc(){s~:>"literal";_=s}','independent owned storage'),
            ('main::proc(){x:=3;p~:=&x;_=p}','independent owned storage'),
            ('main::proc(){a:=make([]int,1);defer delete(a);b~:=a;_=b}','independent owned storage'),
            ('main::proc(){a~:=[]int{1,2};_=a}',"slice literal"),
            ('main::proc(){if true do a~:=make([]int,1)}','braced scope'),
        ]:
            with self.subTest(source=source):
                self.compare('package main\n'+source+'\n',command='check',fail=problem)

    def test_xor_and_complement_keep_odin_semantics(self):
        self.checked('''package main
import "core:fmt"
main::proc(){a,b:=3,5;fmt.println(a~b,~u8(0))}
''','6 255')

    def test_multiple_bindings_can_mark_only_the_owner(self):
        self.checked('''package main
import "core:fmt"
allocate::proc()->([]int,bool){return make([]int,2),true}
main::proc(){values~,ok:=allocate();fmt.println(len(values),ok)}
''','2 true')

    def test_no_alloc_checks_implicit_cleanup(self):
        self.compare('''package main
@(no_alloc)
main::proc(){nums~:=make([]int,0);_=nums}
''',command='check',fail='implicit scoped cleanup')

    def test_constructor_allocators_are_evaluated_once(self):
        self.checked('''package main
import "core:mem"
calls:int
pick::proc(allocator:mem.Allocator)->mem.Allocator{calls+=1;return allocator}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 allocator:=mem.tracking_allocator(&tracker)
 {
  a~:=make([]int,2,pick(allocator))
  b~:=make([]int,3,allocator=pick(allocator))
  c~:=new(int,pick(allocator))
  c^=len(a)+len(b)
  if c^!=5 || calls!=3 {panic("allocator evaluation")}
 }
 if tracker.current_memory_allocated!=0 {panic("custom allocator leak")}
}
''')

    def test_comprehensions_sets_and_numbers(self):
        self.checked('''package main
import "core:mem"
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 {
  values~:>[x*x for x in 0..<10]
  unique~:=set[int]{1,2,3}
  number~:=Integer(123456789012345678901234567890)
  fraction~:>Rational(3/7)
  if len(values)!=10 || len(unique)!=3 || number<=0 || fraction<=0 {panic("owner types")}
 }
 if tracker.current_memory_allocated!=0 {panic("collection/number leak")}
}
''')

    def test_scoped_temporary_inside_comptime_function(self):
        source='''package main
import "core:fmt"
sum::fn(n:int)->int{nums~:=make([]int,n);for &v,i in nums {v=i};total:=0;for v in nums {total+=v};return total}
VALUE::#run sum(5)
main::proc(){fmt.println(VALUE,#runtime sum(4))}
'''
        for mode in ['required','normal','aggressive','override']:
            self.compare(source,flags=['--comptime='+mode,'-thread-count:1'],expected='10 6')

    def test_user_clone_name_is_not_assumed_independent(self):
        self.compare('''package main
clone::proc(values:[]int)->[]int{return values}
bad::proc()->[]int{values~:=make([]int,2);return clone(values)}
main::proc(){}
''',command='check',fail='cannot escape its scope')

    def test_return_length_and_owned_clone_alias(self):
        self.checked('''package main
import "core:slice"
import "core:fmt"
duplicate::slice.clone
count::proc()->int{values~:=make([]int,2);return len(values)}
copy::proc()->[]int{values~:=make([]int,3);return duplicate(values)}
main::proc(){values~:=copy();fmt.println(count(),len(values))}
''','2 3')

    def test_pmap_scalar_result_is_independent_of_scoped_jobs(self):
        self.checked('''package main
import basic "thor:basic"
import "core:fmt"
same::proc(x:int)->int{return x}
produce::proc()->[]int{jobs~:=make([]int,2);jobs[0]=7;return basic.pmap(jobs,same)}
main::proc(){out~:=produce();fmt.println(out[0])}
''','7')

    def test_pmap_borrowed_elements_cannot_escape_scoped_input(self):
        self.compare('''package main
import basic "thor:basic"
borrow::proc(x:[]int)->[]int{return x}
bad::proc()->[][]int{v~:=make([]int,2);jobs:=[1][]int{v};return basic.pmap(jobs[:],borrow)}
main::proc(){}
''',command='check',fail='cannot escape its scope')

    def test_lifo_cleanup_and_unused_owner(self):
        self.checked('''package main
import "core:mem"
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 {
  first~:=make([]int,1)
  defer {if tracker.current_memory_allocated!=size_of(int){panic("LIFO")}}
  second~:=make([]int,2)
 }
 if tracker.current_memory_allocated!=0 {panic("unused owner leak")}
}
''')

    def test_generic_owner_and_factory_alias(self):
        self.checked('''package main
import "core:fmt"
allocate::new
get::proc($T:typeid,value:T)->T{local~:=allocate(T);local^=value;return local^}
main::proc(){fmt.println(get(int,7),get(f64,2.5))}
''','7 2.5')

    def test_aggregate_assignment_and_conditional_escape(self):
        for tail,typ in [
            ('box:Box;box.values=values;return box','Box'),
            ('box~:=new(Box);box.values=values;return box^','Box'),
            ('box~:=new(Box);alias:=box;alias.values=values;return box^','Box'),
            ('return switch{case true:values;case:nil}','[]int'),
            ('return [values for i in 0..<1]','[][]int'),
            ('alias:[]int;for i in 0..<1{alias=values};return alias','[]int'),
        ]:
            with self.subTest(tail=tail):
                self.compare('package main\nBox::struct{values:[]int}\nbad::proc()->'+typ+'{values~:=make([]int,2);'+tail+'}\nmain::proc(){}\n',command='check',fail='cannot escape its scope')

    def test_pointee_fields_are_not_deep_freed(self):
        self.checked('''package main
import "core:fmt"
Box::struct{values:[]int}
get::proc()->[]int{box~:=new(Box);box.values=make([]int,2);box.values[0]=7;return box.values[:]}
main::proc(){values~:=get();fmt.println(values[0])}
''','7')

    def test_allocator_capture_preserves_argument_order_and_multiple_results(self):
        self.checked('''package main
import "core:mem"
order:int
length::proc()->int{order=order*10+1;return 2}
pick::proc(a:mem.Allocator)->mem.Allocator{order=order*10+2;return a}
pair::proc(allocator:=context.allocator)->([]int,[]int){return make([]int,1,allocator),make([]int,2,allocator)}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 allocator:=mem.tracking_allocator(&tracker)
 {
  values~:=make([]int,length(),pick(allocator))
  if order!=12 || len(values)!=2 {panic("argument order")}
  a~,b~:=pair(allocator=allocator)
  if len(a)+len(b)!=3 {panic("multi results")}
 }
 if tracker.current_memory_allocated!=0 {panic("custom allocator leak")}
}
''')

    def test_fixed_array_value_copy_and_borrowed_view(self):
        self.checked('''package main
import "core:fmt"
get::proc()->[2]int{values~:=make([][2]int,1);values[0]={7,8};return values[0]}
main::proc(){fmt.println(get())}
''','[7, 8]')
        self.compare('''package main
bad::proc()->[]int{values~:=make([][2]int,1);return values[0][:]}
main::proc(){}
''',command='check',fail='cannot escape its scope')

    def test_vet_apply_preserves_marker(self):
        self.source.write_text('''package main
import "core:fmt"
main::proc(){values~:=make([]int,2);fmt.println(len(values))}
''')
        result=self.invoke('rust','vet',flags=['--apply','-thread-count:1'])
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('values~:>',self.source.read_text().replace(' ',''))
        result=self.invoke('rust','run',flags=['-thread-count:1'])
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(result.stdout.strip(),'2')

    def test_allocated_run_results_fstrings_and_optional_construction(self):
        self.checked('''package main
import "core:mem"
create::fn()->[]int{return []int{1,2,3}}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 {
  values~:>#run create()
  text~:=f"count {len(values)}"
  other~:=make([]int,2) or_panic
  more~,err:=make([]int,3)
  if err!=nil || len(more)!=3 || len(other)!=2 || text!="count 3" {panic("owned results")}
 }
 if tracker.current_memory_allocated!=0 {panic("result/format leak")}
}
''')

    def test_inactive_compile_time_branch_is_not_a_lifetime_error(self):
        self.checked('''package main
main::proc(){when false {values~:=3;_=values}}
''')

    def test_deferred_result_cannot_be_owned_twice(self):
        self.compare('''package main
cleanup::proc(values:[]int){delete(values)}
@(deferred_out=cleanup)
allocate::proc()->[]int{return make([]int,2)}
main::proc(){values~:=allocate();_=values}
''',command='check',fail='already has deferred-result cleanup')

    def test_multiple_result_aliases_and_unknown_callbacks(self):
        for setup,tail in [
            ('pair::proc(n:int,v:[]int)->(int,[]int){return n,v}', '_,alias:=pair(7,values);return alias'),
            ('pair::proc(n:int,v:[]int)->(int,[]int){return n,v}', '_,alias:=pair(7,values) or_else (_,nil);return alias'),
        ]:
            # The optional fallback requires a bool/error final result.
            if 'or_else' in tail:setup=setup.replace('(int,[]int)','(int,[]int,bool)').replace('return n,v','return n,v,true')
            with self.subTest(tail=tail):
                self.compare('package main\n'+setup+'\nbad::proc()->[]int{values~:=make([]int,1);'+tail+'}\nmain::proc(){}\n',command='check',fail='cannot escape its scope')

    def test_conditional_fallback_and_cast_capture_the_selected_allocator(self):
        self.checked('''package main
import "core:mem"
import "core:strings"
fail::proc(allocator:mem.Allocator)->([]int,bool){return nil,false}
main::proc(){
 backing:=context.allocator
 a,b:mem.Tracking_Allocator
 mem.tracking_allocator_init(&a,backing,backing)
 mem.tracking_allocator_init(&b,backing,backing)
 defer mem.tracking_allocator_destroy(&a)
 defer mem.tracking_allocator_destroy(&b)
 first,second:=mem.tracking_allocator(&a),mem.tracking_allocator(&b)
 for choose in [true,false] {
  {
   values~:=choose ? make([]int,1,allocator=first) : make([]int,2,allocator=second)
   text~:=choose ? strings.clone("a",allocator=first) : strings.clone("b",allocator=second)
   pointer~:=choose ? new(int,allocator=first) : new(int,allocator=second)
   casted~:=cast(^int)new(int,allocator=second)
   fallback~:=fail(first) or_else make([]int,3,allocator=second)
   selected~:=switch {case choose:make([]int,4,allocator=first);case:make([]int,5,allocator=second)}
   anonymous~:=make([]int,1,allocator=(proc(a:mem.Allocator)->mem.Allocator{selected:=switch{case true:a;case:a};return selected})(first))
   if len(values)==0 || len(text)==0 || pointer==nil || casted==nil || len(fallback)!=3 || len(selected)<4 || len(anonymous)!=1 {panic("values")}
  }
  if a.current_memory_allocated!=0 || b.current_memory_allocated!=0 {panic("conditional allocator leak")}
 }
}
''')

    def test_mixed_allocator_branches_are_rejected_for_plain_headers(self):
        self.compare('''package main
main::proc(){values~:=true ? make([]int,1,allocator=context.allocator) : make([]int,2);_=values}
''',command='check',fail='branches must all specify an explicit allocator')
        self.checked('''package main
main::proc(){
 values~:=true ? make(map[int]int,allocator=context.allocator) : make(map[int]int)
 values[0]=7
 if values[0]!=7 {panic("map")}
}
''')

    def test_casts_cannot_hide_borrowed_initializers(self):
        for body in [
            'n:=7;p~:=cast(^int)&n;_=p',
            'Slice::[]int;a:=make(Slice,2);defer delete(a);b~:=Slice(a);_=b',
        ]:
            self.compare('package main\nmain::proc(){'+body+'}\n',command='check',fail='independent owned storage')

    def test_new_named_location_is_not_mistaken_for_an_allocator(self):
        self.checked('''package main
main::proc(){p~:=new(int,loc=#location());p^=7;if p^!=7{panic("value")}}
''')

    def test_known_fn_strings_and_caller_metadata_keep_ownership_and_text(self):
        self.checked('''package main
import "core:mem"
import "core:strings"
word::fn()->string{return "hello"}
pick::proc(a:mem.Allocator)->mem.Allocator{return a}
construct::proc(allocator:mem.Allocator,expression:>#caller_expression(allocator))->string{
 if expression!="pick(allocator)" {panic("caller expression changed")}
 return strings.clone("owned",allocator=allocator)
}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 allocator:=mem.tracking_allocator(&tracker)
 context.allocator=allocator
 {
  automatic~:>word()
  explicit~:>#run word()
  metadata~:=construct(pick(allocator))
  if automatic!="hello" || explicit!="hello" || metadata!="owned" {panic("values")}
 }
 if tracker.current_memory_allocated!=0 {panic("string leak")}
}
''')

    def test_unknown_callback_results_keep_each_position(self):
        self.compare('''package main
bad::proc(callback:proc(int,[]int)->(int,[]int))->[]int{
 values~:=make([]int,1)
 _,alias:=callback(7,values)
 return alias
}
main::proc(){}
''',command='check',fail='cannot escape its scope')
        self.compare('''package main
good::proc(callback:proc(int,[]int)->(int,[]int))->int{
 values~:=make([]int,1)
 result,_:=callback(7,values)
 return result
}
main::proc(){}
''',command='check')

    def test_native_vet_sees_original_scoped_source(self):
        self.compare('''package main
import "core:fmt"
main::proc(){
 values~:=make([]int,2)
 fmt.println(len(values))
}
''',command='check',flags=['-vet','-thread-count:1'])

    def test_set_backing_table_cannot_escape(self):
        self.compare('''package main
bad::proc()->map[int]struct{}{values~:=set[int]{1,2};return values.table}
main::proc(){}
''',command='check',fail='cannot escape its scope')

    def test_recursive_alias_graph_analysis_stays_bounded(self):
        locals_=';'.join('v'+str(i)+':=0;_=v'+str(i) for i in range(100))
        self.compare('''package main
Node::struct{a,b:^Node}
main::proc(){node~:=new(Node);'''+locals_+''';for i in 0..<100{node.a=node;node.b=node}}
''',command='check',fail='alias graph')

if __name__=='__main__':unittest.main()

"""Native cleanup patterns from the project audit, with release-safe oracles."""
import unittest
from tests import test_scoped_owners as scoped


class LifetimeCleanupRuntimeTests(unittest.TestCase):
    setUpClass = classmethod(scoped.ScopedOwnerTests.setUpClass.__func__)
    setUp = scoped.ScopedOwnerTests.setUp
    invoke = scoped.ScopedOwnerTests.invoke
    compare = scoped.ScopedOwnerTests.compare
    checked = scoped.ScopedOwnerTests.checked

    def test_allocated_builder_result_callers_release_both_report_paths(self):
        self.checked('''package main
import "core:mem"
import "core:fmt"
import "core:strings"
comma_sep::proc(n:int)->string{
 builder:strings.Builder
 number:=fmt.aprint(n);defer delete(number)
 end:=len(number)-1
 j:=2*len(number)%3
 for r,i in number {
  strings.write_rune(&builder,r)
  if j>1 && i!=end {strings.write_rune(&builder,',');j=0}else{j+=1}
 }
 return strings.to_string(builder)
}
report::proc(failed:bool){
 total:=comma_sep(1234);defer delete(total)
 if total!="1,234" {panic("number format")}
 if failed {count:=comma_sep(12);defer delete(count);if count!="12" {panic("failed count")}}
}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 report(false);report(true)
 if tracker.current_memory_allocated!=0 || len(tracker.bad_free_array)!=0 {panic("report string leak")}
}
''')

    def test_final_headers_and_undo_redo_transfers_release_once(self):
        self.checked('''package main
import "core:mem"
Action::struct{points:[dynamic]int}
clear_actions::proc(actions:^[dynamic]Action){
 for action in actions^ {delete(action.points)}
 clear(actions)
}
exercise::proc(){
 points:=make([dynamic]int,2)
 circles:=make([dynamic]int,3)
 offsets:=make([dynamic]int,4)
 defer {delete(points);delete(circles);delete(offsets)}
 // Cleanup must observe replacement headers, as in reset in Circles on Dots.
 delete(points);points=make([dynamic]int,5)
 delete(circles);circles=make([dynamic]int,6)
 actions,redo:[dynamic]Action
 defer {clear_actions(&actions);clear_actions(&redo);delete(actions);delete(redo)}
 for n in 1..=3 {
  stroke:=Action{make([dynamic]int,n)}
  append(&actions,stroke)
 }
 undo:=pop(&actions);append(&redo,undo)
 restored:=pop(&redo);append(&actions,restored)
 undo_again:=pop(&actions);append(&redo,undo_again)
 if len(actions)!=2 || len(redo)!=1 {panic("history transfer")}
}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 exercise()
 if tracker.current_memory_allocated!=0 || len(tracker.bad_free_array)!=0 {panic("cleanup leak or duplicate free")}
}
''')

    def test_join_destroy_and_pmap_result_cleanup(self):
        self.checked('''package main
import "core:mem"
import "core:thread"
import basic "thor:basic"
Job::struct{n,result:int}
work::proc(task:thread.Task){job:=cast(^Job)task.data;job.result=job.n*2}
twice::proc(n:int)->int{return n*2}
exercise::proc(){
 jobs:=make([]Job,4);defer delete(jobs)
 pool:thread.Pool
 thread.pool_init(&pool,context.allocator,2)
 defer thread.pool_destroy(&pool)
 thread.pool_start(&pool)
 for &job,i in jobs {job.n=i;thread.pool_add_task(&pool,context.allocator,work,&job)}
 thread.pool_finish(&pool)
 for job in jobs {if job.result!=2*job.n {panic("worker not joined")}}
 inputs:=[i for i in 0..<10];defer delete(inputs)
 outputs:=basic.pmap(inputs,twice,workers=2);defer delete(outputs)
 if basic.sum(outputs)!=90 {panic("pmap result")}
}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 exercise()
 if tracker.current_memory_allocated!=0 || len(tracker.bad_free_array)!=0 {panic("pool or map leak")}
}
''')

    def test_fixed_builder_and_iterators_borrow_without_allocating(self):
        self.checked('''package main
import "core:mem"
import "core:fmt"
import "core:strings"
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 buf:[64]byte
 builder:=strings.builder_from_bytes(buf[:])
 text:=fmt.sbprintf(&builder,"%s %d","hello",7)
 if text!="hello 7" {panic("builder output")}
 remaining:=text
 count:=0
 for token in strings.fields_iterator(&remaining) {if len(token)==0 {panic("empty token")};count+=1}
 if count!=2 || tracker.total_allocation_count!=0 {panic("borrowed I/O allocated")}
}
''')


if __name__ == '__main__':
    unittest.main()

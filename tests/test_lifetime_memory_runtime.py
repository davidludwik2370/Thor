"""Positive native oracles run with checked and unchecked release settings."""
import unittest
from tests import test_scoped_owners as base
class MemoryRuntimeTests(unittest.TestCase):
    setUpClass=classmethod(base.ScopedOwnerTests.setUpClass.__func__)
    setUp=base.ScopedOwnerTests.setUp
    invoke=base.ScopedOwnerTests.invoke
    compare=base.ScopedOwnerTests.compare
    checked=base.ScopedOwnerTests.checked

    def test_pool_callbacks_finish_before_borrowed_jobs_are_released(self):
        self.checked('''package main
import "core:thread"
job::proc(t:thread.Task){p:=(^int)(t.data);p^+=7}
main::proc(){
 values:=make([]int,8)
 defer delete(values)
 pool:thread.Pool
 thread.pool_init(&pool,context.allocator,2)
 defer thread.pool_destroy(&pool)
 thread.pool_start(&pool)
 for &v in values{thread.pool_add_task(&pool,context.allocator,job,&v)}
 thread.pool_finish(&pool)
 for v in values{if v!=7{panic("worker result")}}
}
''')

    def test_byte_copy_and_zero_termination(self):
        self.checked('''package main
import "core:mem"
main::proc(){
 source:=[4]u8{65,66,67,0}
 target:[4]u8
 mem.copy(raw_data(target[:]),raw_data(source[:]),4)
 text:=cstring(raw_data(target[:]))
 if len(text)!=3 || string(text)!="ABC"{panic("copied text")}
 mem.zero(raw_data(target[:]),4)
 if len(text)!=0{panic("zeroed text")}
}
''')

    def test_empty_slice_literal_has_native_nil_header(self):
        self.checked('''package main
empty::proc()->[]int{return []int{}}
main::proc(){v:=empty();if len(v)!=0||raw_data(v)!=nil{panic("empty header")}}
''')

    def test_native_when_keeps_locals_and_defers_until_enclosing_exit(self):
        self.checked('''package main
released:int
finish::proc(p:^int){released+=p^;free(p)}
main::proc(){
 {when true {p:=new(int);p^=7;defer finish(p)} else {p:=new(int);p^=9;defer finish(p)}
  if released!=0||p^!=7{panic("when exited early")}}
 if released!=7{panic("when defer")}
}
''')

    def test_raw_partial_writes_and_size_preserving_views(self):
        self.checked('''package main
import "core:mem"
main::proc(){
 bytes,_:=mem.alloc_bytes_non_zeroed(16)
 defer delete(bytes)
 values:=([2]u64){}
 values={7,9}
 mem.copy(raw_data(bytes),raw_data(values[:]),16)
 view:=mem.slice_ptr((^u64)(raw_data(bytes)),2)
 if view[0]!=7||view[1]!=9{panic("raw copy")}
}
''')

    def test_uninitialized_byte_transport_can_be_initialized_before_use(self):
        self.checked('''package main
import "core:mem"
main::proc(){
 source,_:=mem.alloc_bytes_non_zeroed(8)
 defer delete(source)
 destination:=make([]u8,8)
 defer delete(destination)
 mem.copy(raw_data(destination),raw_data(source),8)
 mem.zero(raw_data(destination),8)
 if ((^u64)(raw_data(destination)))^!=0{panic("overwrite after copy")}
}
''')

    def test_native_thread_destroy_completes_borrowed_job(self):
        self.checked('''package main
import "core:thread"
job::proc(p:rawptr){value:=(^int)(p);value^+=7}
main::proc(){
 value:=3
 worker:=thread.create_and_start_with_data(&value,job)
 thread.destroy(worker)
 if value!=10{panic("native worker")}
}
''')

    def test_temporary_reset_keeps_owned_copy_and_new_generation(self):
        self.checked('''package main
import "core:fmt"
import "core:strings"
main::proc(){
 original:=fmt.tprint(17)
 owned:=strings.clone(original)
 defer delete(owned)
 free_all(context.temp_allocator)
 next:=fmt.tprint(23)
 if owned!="17"||next!="23"{panic("temporary copy")}
}
''')

    def test_deferred_allocator_context_matches_native_odin(self):
        self.checked('''package main
import "base:runtime"
released:uintptr
allocate::proc(data:rawptr,mode:runtime.Allocator_Mode,size,alignment:int,old:rawptr,old_size:int,loc:runtime.Source_Code_Location)->([]byte,runtime.Allocator_Error){
 if mode==.Free{released=uintptr(data)}
 heap:=runtime.heap_allocator()
 return heap.procedure(heap.data,mode,size,alignment,old,old_size,loc)
}
main::proc(){
 {context.allocator={allocate,rawptr(uintptr(1))}
  p:=new(int)
  defer free(p)
  context.allocator={allocate,rawptr(uintptr(2))}}
 if released!=1{panic("defer allocator context")}
}
''')

if __name__=='__main__':unittest.main()

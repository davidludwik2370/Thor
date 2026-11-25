"""Native positive oracles, with checks that survive unchecked release flags."""
import unittest
from tests import test_scoped_owners as base

class LifetimeExtensionsRuntimeTests(unittest.TestCase):
    setUpClass=classmethod(base.ScopedOwnerTests.setUpClass.__func__)
    setUp=base.ScopedOwnerTests.setUp
    invoke=base.ScopedOwnerTests.invoke
    compare=base.ScopedOwnerTests.compare
    checked=base.ScopedOwnerTests.checked

    def test_empty_and_growing_builder_allocation_and_transfer(self):
        self.checked('''package main
import "core:mem"
import "core:strings"
text::proc(n:int)->string{b:strings.Builder;for _ in 0..<n{strings.write_rune(&b,'a')};return strings.to_string(b)}
exercise::proc(){
 b:=strings.builder_make(context.allocator)
 strings.builder_destroy(&b)
 grown:=strings.builder_make(0,8,context.allocator)
 for _ in 0..<20{strings.write_string(&grown,"abc")}
 result:=strings.to_string(grown)
 if len(result)!=60{panic("builder count")}
 strings.builder_destroy(&grown)
 value:=text(12)
 if value!="aaaaaaaaaaaa"{panic("builder transfer")}
 delete(value)
}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 empty:=strings.builder_make(context.allocator)
 strings.write_string(&empty,"")
 if tracker.total_allocation_count!=0{panic("empty builder allocated")}
 strings.builder_destroy(&empty)
 exercise()
 if tracker.current_memory_allocated!=0 || len(tracker.bad_free_array)!=0{panic("builder cleanup")}
}
''')

    def test_writer_and_scalar_global_effects_keep_native_behavior(self):
        self.checked('''package main
import "core:mem"
count:int
fill::proc(v:[]int){for &n in v{n+=1};count+=1}
main::proc(){v:=make([]int,10);defer delete(v);for _ in 0..<100{fill(v)};for n in v{if n!=100{panic("writer")}};if count!=100{panic("global effect")}}
''')

    def test_generic_static_values_are_persistent_and_distinct(self):
        self.checked('''package main
next::proc(n:$T)->T{@static value:T;value+=n;return value}
by_value::proc($N:int)->int{@static value:int;value+=N;return value}
main::proc(){
 if next(1)!=1 || next(f64(2))!=2 || next(1)!=2 || next(f64(2))!=4{panic("generic statics")}
 if by_value(1)!=1 || by_value(2)!=2 || by_value(1)!=2 || by_value(2)!=4{panic("generic value statics")}
}
''')

if __name__=='__main__':unittest.main()

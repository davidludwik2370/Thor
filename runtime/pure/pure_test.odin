#+test
package thor_pure

import "core:mem"
import "core:testing"

@(test)
scratch_frees_live_buffers_and_handles_explicit_deletion :: proc(t:^testing.T) {
    backing:=context.allocator
    outer:mem.Tracking_Allocator
    mem.tracking_allocator_init(&outer,context.allocator,context.allocator)
    defer mem.tracking_allocator_destroy(&outer)
    context.allocator=mem.tracking_allocator(&outer)
    {
        scratch:Scratch
        scratch_init(&scratch)
        context.allocator=scratch_allocator(&scratch)
        a:=make([]int,10);a[0]=7
        b:=make([]u8,32);delete(b)
        {
            nested:Scratch
            scratch_init(&nested)
            context.allocator=scratch_allocator(&nested)
            _=make([]int,5)
            context.allocator=scratch_allocator(&scratch)
            scratch_destroy(&nested)
        }
        context.allocator=mem.tracking_allocator(&outer)
        scratch_destroy(&scratch)
    }
    context.allocator=backing
    testing.expect(t,len(outer.allocation_map)==0)
    testing.expect(t,len(outer.bad_free_array)==0)
}

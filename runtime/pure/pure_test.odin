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

@(test)
deep_results_use_explicit_allocator_and_release_every_occurrence :: proc(t:^testing.T) {
    tracker:mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    allocator:=mem.tracking_allocator(&tracker)
    Record::struct{rows:[2][]string,name:string}
    input:=[2]string{"one","two"}
    original:=Record{[2][]string{input[:],input[:]},"name"}
    result:=clone(original,allocator)
    testing.expect(t,raw_data(result.rows[0])!=raw_data(result.rows[1]))
    testing.expect(t,raw_data(result.rows[0][0])!=raw_data(result.rows[1][0]))
    testing.expect(t,len(tracker.allocation_map)==7)
    destroy(result,allocator)
    testing.expect(t,len(tracker.allocation_map)==0)
    testing.expect(t,len(tracker.bad_free_array)==0)
}

@(test)
tagged_unions_enum_arrays_and_nil_values_clone_recursively :: proc(t:^testing.T) {
    Key::enum{First,Second}
    Variant::union{int,[Key]string}
    input:Variant=[Key]string{.First="first",.Second="second"}
    result:=clone(input)
    array,ok:=result.([Key]string)
    testing.expect(t,ok&&array[.Second]=="second")
    destroy(result)
    empty:Variant
    destroy(clone(empty))
    nil_slice:[]string
    copied:=clone(nil_slice)
    testing.expect(t,raw_data(copied)==nil)
    destroy(copied)
}

@(test)
empty_non_nil_and_zero_sized_slices_keep_descriptor_semantics :: proc(t:^testing.T) {
    input:=[1]int{1}
    empty:=clone(input[:0])
    testing.expect(t,len(empty)==0&&raw_data(empty)!=nil)
    destroy(empty)
    Empty::struct{}
    zero:=allocate_slice([]Empty,3)
    result:=clone(zero)
    testing.expect(t,len(result)==3&&raw_data(result)!=nil)
    destroy(result);destroy(zero)
}

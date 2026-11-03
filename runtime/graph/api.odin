// Deep copies of supported, stable data graphs. No compiler hooks or ownership
// registry: keep allocation boundaries represented until explicit deepfree.
package thor_graph

import "base:intrinsics"
import "base:runtime"

Error :: enum {
    None,
    Unsupported_Type,
    Unsupported_Reference,
    Invalid_Data,
    Invalid_Allocator,
    Allocation_Failed,
    Size_Overflow,
    Free_Failed,
}

// Clone the graph reachable from source into independent heap storage. Shared
// objects, cycles and overlapping ordinary storage retain their relationships.
// source may point to stack/static/heap data; it must remain valid and stable.
// Temporary traversal storage uses scratch_allocator and is released on return.
deepclone :: proc(source:^$T, allocator:=context.allocator,
                  scratch_allocator:=context.allocator, loc:=#caller_location) -> (result:^T, err:Error) {
    when T==rawptr || T==any || intrinsics.type_is_multi_pointer(T) || intrinsics.type_is_proc(T) {
        #panic("deepclone does not support raw pointers, unbounded multi-pointers, any or procedure values")
    }
    if source==nil {return}
    state:State
    state_init(&state,allocator,scratch_allocator,loc)
    defer state_destroy(&state)
    if !validate_schema(&state,type_info_of(T)) {return nil,state.error}
    add_region(&state,source,size_of(T),align_of(T),size_of(T))
    enqueue(&state,source,type_info_of(T))
    discover(&state)
    organize(&state)
    if state.error!=.None {return nil,state.error}
    allocate_groups(&state)
    build_maps(&state)
    patch_graph(&state)
    if state.error==.None {result=cast(^T)translate(&state,uintptr(source),size_of(T),true)}
    if state.error!=.None {
        rollback(&state)
        return nil,state.error
    }
    return result,.None
}

// Free a clone-owned graph once, using the allocator passed to deepclone.
// Discover the complete graph before freeing anything. Do not discard owning
// headers, attach borrowed storage, or independently free shared subobjects.
// Validation/discovery failure frees nothing. A failing allocator can make the
// freeing phase partial; the first failure is returned and cleanup continues.
deepfree :: proc(root:^$T, allocator:=context.allocator,
                 scratch_allocator:=context.allocator, loc:=#caller_location) -> Error {
    when T==rawptr || T==any || intrinsics.type_is_multi_pointer(T) || intrinsics.type_is_proc(T) {
        #panic("deepfree does not support raw pointers, unbounded multi-pointers, any or procedure values")
    }
    if root==nil {return .None}
    state:State
    state_init(&state,allocator,scratch_allocator,loc)
    defer state_destroy(&state)
    state.freeing=true
    if !validate_schema(&state,type_info_of(T)) {return state.error}
    add_region(&state,root,size_of(T),align_of(T),size_of(T))
    enqueue(&state,root,type_info_of(T))
    discover(&state)
    organize(&state)
    if state.error!=.None {return state.error}
    return release_graph(&state)
}

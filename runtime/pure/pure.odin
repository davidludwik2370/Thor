// Compiler-controlled validation and scratch storage. Not a user-call whitelist.
package thor_pure

import "base:intrinsics"
import "core:mem"

Scratch :: struct { tracker: mem.Tracking_Allocator }
scratch_init :: proc(s:^Scratch) {
    mem.tracking_allocator_init(&s.tracker,context.allocator,context.allocator)
}
scratch_allocator :: proc(s:^Scratch)->mem.Allocator {return {procedure=scratch_allocate,data=s}}
scratch_allocate :: proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,
                         old_memory:rawptr,old_size:int,loc:=#caller_location)->([]byte,mem.Allocator_Error) {
    scratch:=cast(^Scratch)data
    result,err:=mem.tracking_allocator_proc(&scratch.tracker,mode,size,alignment,old_memory,old_size,loc)
    #partial switch mode {
    case .Alloc,.Alloc_Non_Zeroed,.Resize,.Resize_Non_Zeroed:
        if err!=.None {panic("Pure scratch allocation failed")}
    }
    return result,err
}
scratch_destroy :: proc(s:^Scratch) {
    // The metadata map uses the saved backing allocator and must outlive these
    // frees. Free directly through backing so iteration does not alter the map.
    for pointer in s.tracker.allocation_map {free(pointer,s.tracker.backing)}
    mem.tracking_allocator_destroy(&s.tracker)
}

validate :: proc "contextless"($T:typeid,$Result:bool,$Depth:int) {
    when Depth>24 {#panic("Pure functions do not support recursive reference-containing types")}
    else when intrinsics.type_is_boolean(T)||intrinsics.type_is_numeric(T)||intrinsics.type_is_enum(T)||intrinsics.type_is_bit_set(T) {}
    else when intrinsics.type_is_string(T) {
        when Result {#panic("Allocation-bearing pure results are deferred; return a scalar or pointer-free fixed aggregate")}
    } else when intrinsics.type_is_slice(T) {
        when Result {#panic("Allocation-bearing pure results are deferred; return a scalar or pointer-free fixed aggregate")}
        else {validate(intrinsics.type_elem_type(T),false,Depth+1)}
    } else when intrinsics.type_is_array(T)||intrinsics.type_is_enumerated_array(T) {
        validate(intrinsics.type_elem_type(T),Result,Depth+1)
    } else when intrinsics.type_is_struct(T) {
        #panic("Pure record fields require a resolved compiler schema")
    } else when intrinsics.type_is_union(T) {
        validate_union(T,Result,Depth,0)
    } else {#panic("Unsupported pure value type: pointers, handles, maps, dynamic arrays, any and procedure values are not pure inputs/results")}
}

validate_union :: proc "contextless"($T:typeid,$Result:bool,$Depth:int,$Index:int) {
    when Index<intrinsics.type_union_variant_count(T) {
        validate(intrinsics.type_variant_type_of(T,Index),Result,Depth+1)
        validate_union(T,Result,Depth,Index+1)
    }
}

// Copying a slice creates fresh outer storage; its elements retain ordinary
// shallow values. The frontend propagates borrowed nested elements separately.
clone_slice :: proc(values:[]$T)->[]T {
    result:=make([]T,len(values));copy(result,values);return result
}

validate_format :: proc "contextless"($T:typeid) {
    when !(intrinsics.type_is_boolean(T)||intrinsics.type_is_numeric(T)||intrinsics.type_is_enum(T)||intrinsics.type_is_string(T)) {
        #panic("Pure formatting supports deterministic scalar/string values only; custom record formatters are not eligible")
    }
}

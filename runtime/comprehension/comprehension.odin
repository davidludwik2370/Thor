// Checked construction primitives. Element copies retain ordinary ownership;
// only the compiler's outer construction buffers are released on failure.
package thor_comprehension

import "base:intrinsics"
import "base:runtime"
import "thor:sets"

first_sequence :: proc "contextless"(value:$T)->intrinsics.type_elem_type(T) where intrinsics.type_is_array(T)||intrinsics.type_is_slice(T)||intrinsics.type_is_dynamic_array(T)||intrinsics.type_is_fixed_capacity_dynamic_array(T) {return {}}
first_map :: proc "contextless"(value:$S/map[$K]$V)->K {return {}}
first_set :: proc "contextless"(value:sets.Set($T))->T {return {}}
first_string :: proc "contextless"(value:$S/string)->rune {return {}}
first_pointer :: proc "contextless"(value:^$T)->intrinsics.type_elem_type(T) {return {}}
first :: proc{first_sequence,first_map,first_set,first_string,first_pointer}

second_map :: proc "contextless"(value:$S/map[$K]$V)->V {return {}}
second_native :: proc "contextless"(value:$S)->int where !intrinsics.type_is_map(S) {return 0}
second :: proc{second_map,second_native}
second_enum :: proc "contextless"(value:[$I]$T)->I {return {}}

// A few Odin allocators return (nil,nil) to refuse storage. Normalize this at
// the allocation boundary before native map/dynamic-array code uses the bytes.
// Adapters are stack-local and never escape in a returned container's header.
checked_allocate :: proc(data:rawptr,mode:runtime.Allocator_Mode,size,alignment:int,
                         old_memory:rawptr,old_size:int,loc:=#caller_location)->([]byte,runtime.Allocator_Error) {
    backing:=cast(^runtime.Allocator)data
    result,err:=backing.procedure(backing.data,mode,size,alignment,old_memory,old_size,loc)
    allocating:=mode==.Alloc||mode==.Alloc_Non_Zeroed||mode==.Resize||mode==.Resize_Non_Zeroed
    if allocating&&size>0&&raw_data(result)==nil&&err==nil {err=.Out_Of_Memory}
    return result,err
}

append_one :: proc(target:^[dynamic]$T,value:T,loc:=#caller_location) {
    backing:=target.allocator
    if backing.procedure==nil {backing=context.allocator}
    target.allocator={checked_allocate,&backing}
    count,err:=append(target,value)
    target.allocator=backing
    if err!=nil||count!=1 {delete(target^);target^={};panic("Comprehension allocation failed",loc)}
}

finish_slice :: proc(target:^[dynamic]$T,loc:=#caller_location)->[]T {
    backing:=context.allocator
    result,err:=make([]T,len(target^),runtime.Allocator{checked_allocate,&backing})
    if err!=nil||len(result)!=len(target^) {
        delete(result);delete(target^);target^={}
        panic("Comprehension allocation failed",loc)
    }
    copy(result,target^[:])
    delete(target^);target^={}
    return result
}

map_insert :: proc(target:^map[$K]$V,key:K,value:V,loc:=#caller_location) {
    // Native assignment discards allocation errors. map_entry exposes them,
    // including growth at the map's load threshold, rather than its raw cap.
    backing:=target^.allocator
    if backing.procedure==nil {backing=context.allocator}
    target^.allocator={checked_allocate,&backing}
    _,destination,_,err:=map_entry(target,key,loc)
    target^.allocator=backing
    if err!=nil||destination==nil {delete(target^);target^=nil;panic("Comprehension allocation failed",loc)}
    destination^=value
}

set_insert :: proc(target:^sets.Set($T),value:T,loc:=#caller_location) {
    map_insert(&target.table,value,struct{}{},loc)
}

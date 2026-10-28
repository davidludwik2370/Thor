// Compiler-controlled validation and scratch storage. Not a user-call whitelist.
package thor_pure

import "base:intrinsics"
import "base:runtime"

// Compiler scratch must not import core:mem/core:reflect: checked SDK bodies
// can belong to those packages or their dependencies. Keep tracking local to
// one pure call and allocate the bookkeeping through the saved backing allocator.
Scratch :: struct {
    backing: runtime.Allocator,
    allocations: map[rawptr]Scratch_Entry,
}
Scratch_Entry :: struct {size,alignment:int}
scratch_init :: proc(s:^Scratch) {
    s.backing=context.allocator
    s.allocations.allocator=s.backing
}
scratch_allocator :: proc(s:^Scratch)->runtime.Allocator {return {procedure=scratch_allocate,data=s}}
scratch_allocate :: proc(data:rawptr,mode:runtime.Allocator_Mode,size,alignment:int,
                         old_memory:rawptr,old_size:int,loc:=#caller_location)->([]byte,runtime.Allocator_Error) {
    s:=cast(^Scratch)data
    #partial switch mode {
    case .Query_Features:
        if old_memory!=nil {(cast(^runtime.Allocator_Mode_Set)old_memory)^={.Alloc,.Alloc_Non_Zeroed,.Free,.Free_All,.Resize,.Resize_Non_Zeroed,.Query_Features,.Query_Info}}
        return nil,.None
    case .Query_Info:
        query:=cast(^runtime.Allocator_Query_Info)old_memory
        if query!=nil {
            if entry,ok:=s.allocations[query.pointer];ok {query.size=entry.size;query.alignment=entry.alignment}
            query.pointer=nil
        }
        return nil,.None
    case .Free_All:
        for pointer in s.allocations {runtime.mem_free(pointer,s.backing)}
        clear(&s.allocations)
        return nil,.None
    }
    if old_memory!=nil && (mode==.Free||mode==.Resize||mode==.Resize_Non_Zeroed) && old_memory not_in s.allocations {
        panic("Pure scratch received an invalid or already freed pointer",loc)
    }
    if s.backing.procedure==nil {panic("Pure scratch allocation failed",loc)}
    result,err:=s.backing.procedure(s.backing.data,mode,size,alignment,old_memory,old_size,loc)
    #partial switch mode {
    case .Alloc,.Alloc_Non_Zeroed,.Resize,.Resize_Non_Zeroed:
        if err!=.None||(size>0&&raw_data(result)==nil) {panic("Pure scratch allocation failed",loc)}
        if (mode==.Resize||mode==.Resize_Non_Zeroed) && old_memory!=raw_data(result) {delete_key(&s.allocations,old_memory)}
        if raw_data(result)!=nil {s.allocations[raw_data(result)]={size,alignment}}
    case .Free:
        if err==.None {delete_key(&s.allocations,old_memory)}
    }
    return result,err
}
scratch_destroy :: proc(s:^Scratch) {
    for pointer in s.allocations {runtime.mem_free(pointer,s.backing)}
    delete(s.allocations)
    s^={}
}

validate :: proc "contextless"($T:typeid,$Result:bool,$Depth:int) {
    when Depth>24 {#panic("Pure functions do not support recursive reference-containing types")}
    else when intrinsics.type_is_boolean(T)||intrinsics.type_is_numeric(T)||intrinsics.type_is_enum(T)||intrinsics.type_is_bit_set(T) {}
    else when intrinsics.type_is_string(T) {
        when Result {#panic("Contextless compile-time procedures cannot return strings/slices; use fn or a contextful proc")}
    } else when intrinsics.type_is_slice(T) {
        when Result {#panic("Contextless compile-time procedures cannot return strings/slices; use fn or a contextful proc")}
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

// Results have tree ownership. Every string/slice occurrence receives its own
// storage, including input views and repeated aliases. Padding is copied only
// within this process; no address is serialized by the compile-time protocol.
clone :: proc(value:$T,allocator:=context.allocator)->T {
    when intrinsics.type_is_boolean(T)||intrinsics.type_is_numeric(T)||intrinsics.type_is_enum(T)||intrinsics.type_is_bit_set(T) {
        return value
    } else {
        result:=value
        clone_fields(any(&result),any(value),allocator,0)
        // All references now own fresh storage. Make that promise explicit to
        // Odin's conservative escape checker at this trusted boundary.
        return transmute(T)result
    }
}

allocate_slice :: proc($T:typeid/[]$E,count:int,nonnull:bool=true)->T {
    assert(count>=0&&(size_of(E)==0||count<=max(int)/max(1,size_of(E))),"Pure result allocation size overflow")
    if !nonnull {
        assert(count==0||size_of(E)==0,"Null slice storage requires zero-sized elements")
        return T((cast([^]E)nil)[:count])
    }
    storage,err:=runtime.mem_alloc_bytes(max(1,count*size_of(E)),align_of(E))
    if err!=.None||raw_data(storage)==nil {panic("Pure result allocation failed")}
    return T((cast([^]E)raw_data(storage))[:count])
}

// dst carries a pointer so its type and address remain explicit in this
// trusted runtime. User fn code cannot call or expose these pointer operations.
clone_fields :: proc(dst:any,src:any,allocator:runtime.Allocator,depth:int) {
    pointer_info:=runtime.type_info_base(type_info_of(dst.id)).variant.(runtime.Type_Info_Pointer)
    clone_at((cast(^rawptr)dst.data)^,src.data,pointer_info.elem,allocator,depth)
}

offset :: proc "contextless"(pointer:rawptr,amount:int)->rawptr {return rawptr(uintptr(pointer)+uintptr(amount))}

clone_at :: proc(dst,src:rawptr,info:^runtime.Type_Info,allocator:runtime.Allocator,depth:int) {
    if depth>24 {panic("Pure result nesting limit exceeded")}
    info:=runtime.type_info_base(info)
    #partial switch item in info.variant {
    case runtime.Type_Info_Boolean,runtime.Type_Info_Integer,runtime.Type_Info_Rune,runtime.Type_Info_Float,
         runtime.Type_Info_Complex,runtime.Type_Info_Quaternion,runtime.Type_Info_Enum,runtime.Type_Info_Bit_Set:
        runtime.mem_copy_non_overlapping(dst,src,info.size)
    case runtime.Type_Info_String:
        if item.is_cstring||item.encoding!=.UTF_8 {panic("Unsupported pure string result")}
        value:=(cast(^string)src)^
        result:string
        if len(value)>0 {
            data,err:=runtime.mem_alloc_bytes(len(value),1,allocator)
            if err!=.None||len(data)<len(value)||raw_data(data)==nil {panic("Pure result allocation failed")}
            runtime.mem_copy_non_overlapping(raw_data(data),raw_data(value),len(value))
            result=string(data)
        }
        (cast(^string)dst)^=result
    case runtime.Type_Info_Slice:
        value:=(cast(^runtime.Raw_Slice)src)^
        result:=runtime.Raw_Slice{len=value.len}
        if value.data==nil&&value.len!=0&&item.elem_size!=0 {panic("Invalid pure slice result")}
        if value.data!=nil {
            if value.len<0||value.len>max(int)/max(1,item.elem_size) {panic("Pure result allocation size overflow")}
            data,err:=runtime.mem_alloc_bytes(max(1,value.len*item.elem_size),item.elem.align,allocator)
            if err!=.None||raw_data(data)==nil {panic("Pure result allocation failed")}
            result={raw_data(data),value.len}
            for i in 0..<value.len {clone_at(offset(result.data,i*item.elem_size),offset(value.data,i*item.elem_size),item.elem,allocator,depth+1)}
        }
        (cast(^runtime.Raw_Slice)dst)^=result
    case runtime.Type_Info_Array:
        for i in 0..<item.count {clone_at(offset(dst,i*item.elem_size),offset(src,i*item.elem_size),item.elem,allocator,depth+1)}
    case runtime.Type_Info_Enumerated_Array:
        for i in 0..<item.count {clone_at(offset(dst,i*item.elem_size),offset(src,i*item.elem_size),item.elem,allocator,depth+1)}
    case runtime.Type_Info_Struct:
        if .raw_union in item.flags {panic("Raw unions cannot be pure results")}
        for i in 0..<int(item.field_count) {clone_at(offset(dst,int(item.offsets[i])),offset(src,int(item.offsets[i])),item.types[i],allocator,depth+1)}
    case runtime.Type_Info_Union:
        runtime.mem_copy_non_overlapping(dst,src,info.size)
        variant:=union_variant(src,item)
        if variant!=nil {clone_at(dst,src,variant,allocator,depth+1)}
    case:panic("Unsupported pure result type")
    }
}

// Call once for an independently owned fn/#run result. Ordinary assignments
// alias its buffers; destroying two aliases is a programmer ownership error.
destroy :: proc(value:$T,allocator:=context.allocator) {destroy_at(any(value).data,type_info_of(T),allocator,0)}

destroy_at :: proc(src:rawptr,info:^runtime.Type_Info,allocator:runtime.Allocator,depth:int) {
    if depth>24 {panic("Pure result nesting limit exceeded")}
    info:=runtime.type_info_base(info)
    #partial switch item in info.variant {
    case runtime.Type_Info_Boolean,runtime.Type_Info_Integer,runtime.Type_Info_Rune,runtime.Type_Info_Float,
         runtime.Type_Info_Complex,runtime.Type_Info_Quaternion,runtime.Type_Info_Enum,runtime.Type_Info_Bit_Set:
    case runtime.Type_Info_String:
        value:=(cast(^string)src)^;if len(value)>0 {delete(value,allocator)}
    case runtime.Type_Info_Slice:
        value:=(cast(^runtime.Raw_Slice)src)^
        if value.data!=nil {
            for i in 0..<value.len {destroy_at(offset(value.data,i*item.elem_size),item.elem,allocator,depth+1)}
            runtime.mem_free(value.data,allocator)
        }
    case runtime.Type_Info_Array:
        for i in 0..<item.count {destroy_at(offset(src,i*item.elem_size),item.elem,allocator,depth+1)}
    case runtime.Type_Info_Enumerated_Array:
        for i in 0..<item.count {destroy_at(offset(src,i*item.elem_size),item.elem,allocator,depth+1)}
    case runtime.Type_Info_Struct:
        if .raw_union in item.flags {panic("Raw unions cannot be pure results")}
        for i in 0..<int(item.field_count) {destroy_at(offset(src,int(item.offsets[i])),item.types[i],allocator,depth+1)}
    case runtime.Type_Info_Union:
        variant:=union_variant(src,item)
        if variant!=nil {destroy_at(src,variant,allocator,depth+1)}
    case:panic("Unsupported pure result type")
    }
}

// Pure unions cannot contain pointer representations, including pointer-only
// Maybe unions. Their native integer tag selects the recursively owned payload.
union_variant :: proc(value:rawptr,info:runtime.Type_Info_Union)->^runtime.Type_Info {
    if len(info.variants)==0 {return nil}
    assert(info.tag_type!=nil,"Unsupported pure union representation")
    pointer:=offset(value,int(info.tag_offset))
    tag:u64
    switch info.tag_type.size {
    case 1:tag=u64((cast(^u8)pointer)^)
    case 2:tag=u64((cast(^u16)pointer)^)
    case 4:tag=u64((cast(^u32)pointer)^)
    case 8:tag=(cast(^u64)pointer)^
    case:panic("Unsupported pure union tag")
    }
    if !info.no_nil {if tag==0{return nil};tag-=1}
    assert(tag<u64(len(info.variants)),"Invalid pure union tag")
    return info.variants[int(tag)]
}

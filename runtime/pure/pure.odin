// Compiler-controlled validation and scratch storage. Not a user-call whitelist.
package thor_pure

import "base:intrinsics"
import "base:runtime"
import "thor:typeinfo"

// Compiler scratch must not import core:mem/core:reflect: checked SDK bodies
// can belong to those packages or their dependencies. Keep tracking local to
// one pure call and allocate the bookkeeping through the saved backing allocator.
Scratch :: struct {
    backing: runtime.Allocator,
    allocations: map[rawptr]Scratch_Entry,
    children, next, parent: ^Scratch,
    // Only a tree used by parallel pure calls needs synchronized bookkeeping.
    gate: ^Scratch,
    locked: u32,
}
Scratch_Entry :: struct {size,alignment:int}
scratch_init :: #force_inline proc(s:^Scratch) {
    s.backing=context.allocator
    s.allocations.allocator=s.backing
}
scratch_allocator :: #force_inline proc(s:^Scratch)->runtime.Allocator {return {procedure=scratch_allocate,data=s}}
// Worker scratch arenas allocate independently on the heap. A shared gate also
// permits explicit delete/resize across the caller/worker ownership boundary.
@(private="file")
scratch_lock :: proc(s:^Scratch) {
    for intrinsics.atomic_exchange_explicit(&s.locked,1,.Acquire)!=0 {intrinsics.cpu_relax()}
}
@(private="file")
scratch_unlock :: proc(s:^Scratch) {intrinsics.atomic_store_explicit(&s.locked,0,.Release)}
@(private="file")
scratch_find :: proc(root:^Scratch,pointer:rawptr)->^Scratch {
    node:=root
    for {
        if pointer in node.allocations {return node}
        if node.children!=nil {node=node.children;continue}
        for node!=root && node.next==nil {node=node.parent}
        if node==root {return nil}
        node=node.next
    }
}
scratch_parent :: #force_inline proc(allocator:runtime.Allocator)->^Scratch {
    if allocator.procedure==scratch_allocate {return cast(^Scratch)allocator.data}
    return nil
}
// The caller retains this parent until scratch_destroy; the child cannot escape
// that lifetime. rawptr makes this deliberate internal lifetime link explicit.
scratch_child :: proc(parent_data:rawptr)->^Scratch {
    parent:=cast(^Scratch)parent_data
    heap:=runtime.heap_allocator()
    data,err:=runtime.mem_alloc_bytes(size_of(Scratch),align_of(Scratch),heap)
    if err!=.None||raw_data(data)==nil {panic("Pure worker scratch allocation failed")}
    child:=cast(^Scratch)raw_data(data)
    if parent.gate==nil {parent.gate=parent}
    gate:=parent.gate
    scratch_lock(gate)
    defer scratch_unlock(gate)
    child^={backing=heap,next=parent.children,parent=parent,gate=gate}
    child.allocations.allocator=heap
    parent.children=child
    return child
}
@(private="file")
scratch_release_children :: proc(s:^Scratch) {
    pending:=s.children
    s.children=nil
    for pending!=nil {
        child:=pending
        pending=child.next
        if child.children!=nil {
            tail:=child.children
            for tail.next!=nil {tail=tail.next}
            tail.next=pending
            pending=child.children
        }
        for pointer in child.allocations {runtime.mem_free(pointer,child.backing)}
        if cap(child.allocations)>0 {delete(child.allocations)}
        runtime.mem_free(child,runtime.heap_allocator())
    }
}
scratch_allocate :: proc(data:rawptr,mode:runtime.Allocator_Mode,size,alignment:int,
                         old_memory:rawptr,old_size:int,loc:=#caller_location)->([]byte,runtime.Allocator_Error) {
    s:=cast(^Scratch)data
    gate:=s.gate
    if gate!=nil {scratch_lock(gate)}
    defer {if gate!=nil {scratch_unlock(gate)}}
    // Slice and string descriptors carry no allocator. The caller's default
    // allocator must still be able to release storage produced by a worker.
    if old_memory!=nil && (mode==.Free||mode==.Resize||mode==.Resize_Non_Zeroed) && old_memory not_in s.allocations && gate!=nil {
        if owner:=scratch_find(gate,old_memory);owner!=nil {s=owner}
    }
    #partial switch mode {
    case .Query_Features:
        if old_memory!=nil {(cast(^runtime.Allocator_Mode_Set)old_memory)^={.Alloc,.Alloc_Non_Zeroed,.Free,.Free_All,.Resize,.Resize_Non_Zeroed,.Query_Features,.Query_Info}}
        return nil,.None
    case .Query_Info:
        query:=cast(^runtime.Allocator_Query_Info)old_memory
        if query!=nil {
            if gate!=nil {if owner:=scratch_find(gate,query.pointer);owner!=nil {s=owner}}
            if entry,ok:=s.allocations[query.pointer];ok {query.size=entry.size;query.alignment=entry.alignment}
            query.pointer=nil
        }
        return nil,.None
    case .Free_All:
        scratch_release_children(s)
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
scratch_destroy :: #force_inline proc(s:^Scratch) {
    if s.children!=nil {scratch_release_children(s)}
    // No map storage exists until the first tracked allocation. Avoid the
    // generic map destructor on allocation-free pure calls. A zero length with
    // retained capacity still needs the normal cleanup below.
    if cap(s.allocations)==0 {s^={};return}
    for pointer in s.allocations {runtime.mem_free(pointer,s.backing)}
    delete(s.allocations)
    s^={}
}

// Only queried via type_of. The marker length distinguishes a normal
// record from fixed, sliced and dynamic SOA; its element is the logical row.
soa_shape :: proc{soa_fixed_shape,soa_slice_shape,soa_dynamic_shape,ordinary_shape}
soa_fixed_shape :: proc "contextless"($T:typeid/#soa[$N]$E)->[1]E {return {}}
soa_slice_shape :: proc "contextless"($T:typeid/#soa[]$E)->[2]E {return {}}
soa_dynamic_shape :: proc "contextless"($T:typeid/#soa[dynamic]$E)->[3]E {return {}}
ordinary_shape :: proc "contextless"($T:typeid)->[0]bool {return {}}

validate :: proc "contextless"($T:typeid,$Result:bool,$Depth:int,$Site:string) {
    when Depth>24 {#panic("Pure functions do not support recursive reference-containing types or value nesting deeper than 24 levels"+Site)}
    else when intrinsics.type_is_boolean(T)||intrinsics.type_is_numeric(T)||intrinsics.type_is_enum(T)||intrinsics.type_is_bit_set(T) {}
    else when intrinsics.type_is_cstring(T)||intrinsics.type_is_cstring16(T)||intrinsics.type_is_string16(T) {
        #panic("Unsupported pure value type: cstring, cstring16 and string16 are not supported; use UTF-8 string values"+Site)
    }
    else when intrinsics.type_is_string(T) {
        when Result {#panic("Contextless compile-time procedures cannot return strings/slices; use fn or a contextful proc"+Site)}
    } else when intrinsics.type_is_slice(T) {
        when Result {#panic("Contextless compile-time procedures cannot return strings/slices; use fn or a contextful proc"+Site)}
        else {validate(intrinsics.type_elem_type(T),false,Depth+1,Site)}
    } else when intrinsics.type_is_array(T)||intrinsics.type_is_enumerated_array(T) {
        validate(intrinsics.type_elem_type(T),Result,Depth+1,Site)
    } else when intrinsics.type_is_struct(T) {
        #panic("Pure record fields require a resolved compiler schema"+Site)
    } else when intrinsics.type_is_union(T) {
        validate_union(T,Result,Depth,0,Site)
    } else when intrinsics.type_is_dynamic_array(T) {
        #panic("Unsupported pure value type: dynamic arrays are not public fn inputs; pass a slice view instead"+Site)
    } else {#panic("Unsupported pure value type: pointers, handles, maps, any and procedure values are not pure inputs/results"+Site)}
}

validate_union :: proc "contextless"($T:typeid,$Result:bool,$Depth:int,$Index:int,$Site:string) {
    when Index<intrinsics.type_union_variant_count(T) {
        validate(intrinsics.type_variant_type_of(T,Index),Result,Depth+1,Site)
        validate_union(T,Result,Depth,Index+1,Site)
    }
}

// Used only beneath type_of by the compiler. Checking the concrete target here
// also covers type parameters captured from an enclosing ordinary procedure.
// The real conversion remains a native expression, evaluated exactly once.
conversion_type :: proc "contextless"($T:typeid,$Site:string)->T {
    when intrinsics.type_is_pointer(T)||intrinsics.type_is_multi_pointer(T)||
         intrinsics.type_is_proc(T)||intrinsics.type_is_map(T)||
         intrinsics.type_is_cstring(T)||intrinsics.type_is_cstring16(T)||
         intrinsics.type_is_string16(T)||T==rawptr||T==any||T==typeid {
        #panic("Pure code cannot convert values to pointers, procedure values, any or runtime type identities."+Site)
    }
    return {}
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

// Results have tree ownership. Every string/slice/dynamic-array occurrence owns
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

// Scratch allocates payloads through its backing allocator. Releasing the
// tracking entry transfers an entire allocation without moving its bytes.
take_result_storage :: proc(s:^Scratch, pointer:rawptr, bytes:int, allocator:runtime.Allocator)->bool {
    if s==nil || s.backing!=allocator || pointer==nil {return false}
    if s.gate!=nil {scratch_lock(s.gate)}
    defer {if s.gate!=nil {scratch_unlock(s.gate)}}
    entry,ok:=s.allocations[pointer]
    if !ok || entry.size!=bytes {return false}
    delete_key(&s.allocations,pointer)
    return true
}

promote :: proc(value:$T, allocator:runtime.Allocator)->T {
    when intrinsics.type_is_boolean(T)||intrinsics.type_is_numeric(T)||intrinsics.type_is_enum(T)||intrinsics.type_is_bit_set(T) {
        return value
    } else {
        result:=value
        ptr:=any(&result)
        info:=runtime.type_info_base(type_info_of(ptr.id)).variant.(typeinfo.Type_Info_Pointer)
        clone_at((cast(^rawptr)ptr.data)^,any(value).data,info.elem,allocator,0,scratch_parent(context.allocator))
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

// A promoted dynamic header must use the caller's allocator, never a scratch
// allocator whose bookkeeping is destroyed when its pure invocation returns.
allocate_dynamic :: proc($T:typeid/[dynamic]$E,count,capacity:int,nonnull:bool=true)->T {
    header:=allocate_dynamic_storage(count,capacity,size_of(E),align_of(E),nonnull,context.allocator)
    return transmute(T)header
}

allocate_dynamic_storage :: proc(count,capacity,elem_size,alignment:int,nonnull:bool,allocator:runtime.Allocator)->runtime.Raw_Dynamic_Array {
    if count<0||capacity<count||elem_size<0||capacity>max(int)/max(1,elem_size) {panic("Pure result allocation size overflow")}
    result:=runtime.Raw_Dynamic_Array{len=count,cap=capacity,allocator=allocator}
    if !nonnull {
        if capacity!=0&&elem_size!=0 {panic("Null dynamic-array storage requires zero capacity or zero-sized elements")}
        return result
    }
    amount:=max(1,capacity*elem_size)
    storage,err:=runtime.mem_alloc_bytes(amount,alignment,allocator)
    if err!=.None||len(storage)<amount||raw_data(storage)==nil {panic("Pure result allocation failed")}
    result.data=raw_data(storage)
    return result
}

// dst carries a pointer so its type and address remain explicit in this
// trusted runtime. User fn code cannot call or expose these pointer operations.
clone_fields :: proc(dst:any,src:any,allocator:runtime.Allocator,depth:int) {
    pointer_info:=runtime.type_info_base(type_info_of(dst.id)).variant.(typeinfo.Type_Info_Pointer)
    clone_at((cast(^rawptr)dst.data)^,src.data,pointer_info.elem,allocator,depth)
}

offset :: proc "contextless"(pointer:rawptr,amount:int)->rawptr {return rawptr(uintptr(pointer)+uintptr(amount))}

// Moving a scalar column or slice already in its final allocation is O(1).
clone_elements :: proc(dst,src:rawptr,elem:^runtime.Type_Info,count:int,allocator:runtime.Allocator,depth:int,scratch:^Scratch) {
    if count==0 || elem.size==0 {return}
    #partial switch _ in runtime.type_info_base(elem).variant {
    case typeinfo.Type_Info_Boolean,typeinfo.Type_Info_Integer,typeinfo.Type_Info_Rune,typeinfo.Type_Info_Float,
         typeinfo.Type_Info_Complex,typeinfo.Type_Info_Quaternion,typeinfo.Type_Info_Enum,typeinfo.Type_Info_Bit_Set,typeinfo.Type_Info_Procedure:
        if dst!=src {runtime.mem_copy_non_overlapping(dst,src,count*elem.size)}
        return
    }
    for i in 0..<count {
        clone_at(offset(dst,i*elem.size),offset(src,i*elem.size),elem,allocator,depth,scratch)
    }
}

clone_at :: proc(dst,src:rawptr,info:^runtime.Type_Info,allocator:runtime.Allocator,depth:int,scratch:^Scratch=nil) {
    if depth>24 {panic("Pure result nesting limit exceeded")}
    for item in compiler_logical_values {
        if item.id==info.id && item.clone!=nil {
            if scratch!=nil {item.promote(dst,src,allocator,scratch)} else {item.clone(dst,src,allocator)}
            return
        }
    }
    info:=runtime.type_info_base(info)
    #partial switch item in info.variant {
    case typeinfo.Type_Info_Boolean,typeinfo.Type_Info_Integer,typeinfo.Type_Info_Rune,typeinfo.Type_Info_Float,
         typeinfo.Type_Info_Complex,typeinfo.Type_Info_Quaternion,typeinfo.Type_Info_Enum,typeinfo.Type_Info_Bit_Set,typeinfo.Type_Info_Procedure:
        if dst!=src {runtime.mem_copy_non_overlapping(dst,src,info.size)}
    case typeinfo.Type_Info_String:
        if item.is_cstring||item.encoding!=.UTF_8 {panic("Unsupported pure string result")}
        value:=(cast(^string)src)^
        result:string
        if take_result_storage(scratch,raw_data(value),len(value),allocator) {result=value}
        else if len(value)>0 {
            data,err:=runtime.mem_alloc_bytes(len(value),1,allocator)
            if err!=.None||len(data)<len(value)||raw_data(data)==nil {panic("Pure result allocation failed")}
            runtime.mem_copy_non_overlapping(raw_data(data),raw_data(value),len(value))
            result=string(data)
        }
        (cast(^string)dst)^=result
    case typeinfo.Type_Info_Slice:
        value:=(cast(^runtime.Raw_Slice)src)^
        result:=runtime.Raw_Slice{len=value.len}
        if value.data==nil&&value.len!=0&&item.elem_size!=0 {panic("Invalid pure slice result")}
        if value.data!=nil {
            if value.len<0||value.len>max(int)/max(1,item.elem_size) {panic("Pure result allocation size overflow")}
            if take_result_storage(scratch,value.data,value.len*item.elem_size,allocator) {result=value}
            else {
                data,err:=runtime.mem_alloc_bytes(max(1,value.len*item.elem_size),item.elem.align,allocator)
                if err!=.None||raw_data(data)==nil {panic("Pure result allocation failed")}
                result={raw_data(data),value.len}
            }
            clone_elements(result.data,value.data,item.elem,value.len,allocator,depth+1,scratch)
        }
        (cast(^runtime.Raw_Slice)dst)^=result
    case typeinfo.Type_Info_Dynamic_Array:
        value:=(cast(^runtime.Raw_Dynamic_Array)src)^
        result:=value
        if value.allocator==context.allocator && take_result_storage(scratch,value.data,value.cap*item.elem_size,allocator) {
            result.allocator=allocator
        } else {result=allocate_dynamic_storage(value.len,value.cap,item.elem_size,item.elem.align,value.data!=nil,allocator)}
        if value.data!=nil {
            clone_elements(result.data,value.data,item.elem,value.len,allocator,depth+1,scratch)
        }
        (cast(^runtime.Raw_Dynamic_Array)dst)^=result
    case typeinfo.Type_Info_Array:
        clone_elements(dst,src,item.elem,item.count,allocator,depth+1,scratch)
    case typeinfo.Type_Info_Enumerated_Array:
        clone_elements(dst,src,item.elem,item.count,allocator,depth+1,scratch)
    case typeinfo.Type_Info_Struct:
        if item.soa_kind==.Slice || item.soa_kind==.Dynamic {
            clone_soa(dst,src,info,item,allocator,depth,scratch)
            return
        }
        if .raw_union in item.flags {panic("Raw unions cannot be pure results")}
        for i in 0..<int(item.field_count) {
            if item.names[i]=="_" {continue}
            clone_at(offset(dst,int(item.offsets[i])),offset(src,int(item.offsets[i])),item.types[i],allocator,depth+1,scratch)
        }
    case typeinfo.Type_Info_Union:
        if dst!=src {runtime.mem_copy_non_overlapping(dst,src,info.size)}
        variant:=union_variant(src,item)
        if variant!=nil {clone_at(dst,src,variant,allocator,depth+1,scratch)}
    case:panic("Unsupported pure result type")
    }
}

// Allocate all columns as a single block, using the native SOA alignment
// between columns. Dynamic headers retain capacity and the caller allocator.
clone_soa :: proc(dst,src:rawptr,info:^runtime.Type_Info,s:typeinfo.Type_Info_Struct,
                  allocator:runtime.Allocator,depth:int,scratch:^Scratch) {
    columns:=typeinfo.soa_columns(s)
    count:=typeinfo.soa_length(s,src)
    capacity:=count
    if s.soa_kind==.Dynamic {capacity=(cast(^int)offset(src,int(s.offsets[columns+1])))^}
    if count<0 || capacity<count {panic("Invalid pure SOA result")}
    align:=s.soa_base_type.align
    total:=0
    for col in 0..<columns {
        _,elem:=typeinfo.soa_column(s,src,col)
        if capacity>(max(int)-total-(align-1))/max(1,elem.size) {panic("Pure SOA result size overflow")}
        total=runtime.align_forward_int(total+elem.size*capacity,align)
    }
    block:rawptr
    if total>0 {
        first,_:=typeinfo.soa_column(s,src,0)
        if take_result_storage(scratch,first,total,allocator) {block=first}
        else {
            bytes,err:=runtime.mem_alloc_bytes(total,align,allocator)
            if err!=.None || raw_data(bytes)==nil {panic("Pure SOA result allocation failed")}
            block=raw_data(bytes)
        }
    }
    if dst!=src {runtime.mem_copy_non_overlapping(dst,src,info.size)}
    pos:=0
    for col in 0..<columns {
        source,elem:=typeinfo.soa_column(s,src,col)
        target:=offset(block,pos)
        (cast(^rawptr)offset(dst,int(s.offsets[col])))^=target
        if elem.size>0 && s.names[col]!="_" {
            clone_elements(target,source,elem,count,allocator,depth+1,scratch)
        }
        pos=runtime.align_forward_int(pos+elem.size*capacity,align)
    }
    if s.soa_kind==.Dynamic {(cast(^runtime.Allocator)offset(dst,int(s.offsets[columns+2])))^=allocator}
}

// Call once for an independently owned fn/#run result. Ordinary assignments
// alias its buffers; destroying two aliases is a programmer ownership error.
destroy :: proc(value:$T,allocator:=context.allocator) {destroy_at(any(value).data,type_info_of(T),allocator,0)}

destroy_at :: proc(src:rawptr,info:^runtime.Type_Info,allocator:runtime.Allocator,depth:int) {
    if depth>24 {panic("Pure result nesting limit exceeded")}
    for item in compiler_logical_values {if item.id==info.id && item.destroy!=nil {item.destroy(src);return}}
    info:=runtime.type_info_base(info)
    #partial switch item in info.variant {
    case typeinfo.Type_Info_Boolean,typeinfo.Type_Info_Integer,typeinfo.Type_Info_Rune,typeinfo.Type_Info_Float,
         typeinfo.Type_Info_Complex,typeinfo.Type_Info_Quaternion,typeinfo.Type_Info_Enum,typeinfo.Type_Info_Bit_Set,typeinfo.Type_Info_Procedure:
    case typeinfo.Type_Info_String:
        value:=(cast(^string)src)^;if len(value)>0 {delete(value,allocator)}
    case typeinfo.Type_Info_Slice:
        value:=(cast(^runtime.Raw_Slice)src)^
        if value.data!=nil {
            for i in 0..<value.len {destroy_at(offset(value.data,i*item.elem_size),item.elem,allocator,depth+1)}
            runtime.mem_free(value.data,allocator)
        }
    case typeinfo.Type_Info_Dynamic_Array:
        value:=(cast(^runtime.Raw_Dynamic_Array)src)^
        if value.data!=nil {
            for i in 0..<value.len {destroy_at(offset(value.data,i*item.elem_size),item.elem,value.allocator,depth+1)}
            runtime.mem_free(value.data,value.allocator)
        }
    case typeinfo.Type_Info_Array:
        for i in 0..<item.count {destroy_at(offset(src,i*item.elem_size),item.elem,allocator,depth+1)}
    case typeinfo.Type_Info_Enumerated_Array:
        for i in 0..<item.count {destroy_at(offset(src,i*item.elem_size),item.elem,allocator,depth+1)}
    case typeinfo.Type_Info_Struct:
        if item.soa_kind==.Slice || item.soa_kind==.Dynamic {
            columns:=typeinfo.soa_columns(item)
            count:=typeinfo.soa_length(item,src)
            backing:=allocator
            if item.soa_kind==.Dynamic {backing=(cast(^runtime.Allocator)offset(src,int(item.offsets[columns+2])))^}
            for col in 0..<columns {
                data,elem:=typeinfo.soa_column(item,src,col)
                if item.names[col]=="_" || elem.size==0 {continue}
                for i in 0..<count {destroy_at(offset(data,i*elem.size),elem,backing,depth+1)}
            }
            if columns>0 {pointer,_:=typeinfo.soa_column(item,src,0); if pointer!=nil {runtime.mem_free(pointer,backing)}}
            return
        }
        if .raw_union in item.flags {panic("Raw unions cannot be pure results")}
        for i in 0..<int(item.field_count) {
            if item.names[i]=="_" {continue}
            destroy_at(offset(src,int(item.offsets[i])),item.types[i],allocator,depth+1)
        }
    case typeinfo.Type_Info_Union:
        variant:=union_variant(src,item)
        if variant!=nil {destroy_at(src,variant,allocator,depth+1)}
    case:panic("Unsupported pure result type")
    }
}

// Pure unions cannot contain pointer representations, including pointer-only
// Maybe unions. Their native integer tag selects the recursively owned payload.
union_variant :: proc(value:rawptr,info:typeinfo.Type_Info_Union)->^runtime.Type_Info {
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

// Logical numeric hooks keep this base package independent of math/big and its
// SDK dependencies. Registration happens once at init, without allocating.
Compiler_Logical_Value :: struct {
    id:typeid,
    clone:proc(dst,src:rawptr,allocator:runtime.Allocator),
    destroy:proc(src:rawptr),
    promote:proc(dst,src:rawptr,allocator:runtime.Allocator,scratch:^Scratch),
}
@(private="package") compiler_logical_values:[2]Compiler_Logical_Value
compiler_register_logical :: proc "contextless"(index:int,value:Compiler_Logical_Value) {
    assert_contextless(index>=0 && index<len(compiler_logical_values))
    assert_contextless(compiler_logical_values[index].id==nil || compiler_logical_values[index].id==value.id)
    compiler_logical_values[index]=value
}

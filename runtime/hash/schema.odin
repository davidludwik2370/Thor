#+private
package thor_hash

import "base:runtime"
import "core:os"
import "core:net"
import "core:sync"
import "thor:big"

resource :: proc(info:^runtime.Type_Info)->bool {
    return info.id==typeid_of(runtime.Allocator)||info.id==typeid_of(runtime.Random_Generator)||
        info.id==typeid_of(os.File)||info.id==typeid_of(net.Socket)||
        info.id==typeid_of(sync.Mutex)||info.id==typeid_of(sync.RW_Mutex)||
        info.id==typeid_of(sync.Recursive_Mutex)||info.id==typeid_of(sync.Atomic_Mutex)||
        info.id==typeid_of(sync.Atomic_RW_Mutex)||info.id==typeid_of(sync.Atomic_Recursive_Mutex)||
        info.id==typeid_of(sync.Barrier)||info.id==typeid_of(sync.Ticket_Mutex)||
        info.id==typeid_of(sync.Cond)||info.id==typeid_of(sync.Sema)||info.id==typeid_of(sync.Futex)||
        info.id==typeid_of(sync.Atomic_Cond)||info.id==typeid_of(sync.Atomic_Sema)||
        info.id==typeid_of(sync.Wait_Group)||info.id==typeid_of(sync.Auto_Reset_Event)||
        info.id==typeid_of(sync.Benaphore)||info.id==typeid_of(sync.Recursive_Benaphore)||
        info.id==typeid_of(sync.Once)||info.id==typeid_of(sync.Parker)||info.id==typeid_of(sync.One_Shot_Event)
}
// Inspect named wrappers before dropping them: resource aliases/distinct types
// must not turn into apparently ordinary integers, and big values have semantics
// independent of their internal dynamic-array storage.
base :: proc(info:^runtime.Type_Info)->^runtime.Type_Info {
    info:=info
    for info!=nil {
        item,ok:=info.variant.(runtime.Type_Info_Named)
        if !ok {return info}
        if resource(info)||info.id==typeid_of(big.Integer)||info.id==typeid_of(big.Rational) {return info}
        info=item.base
    }
    return nil
}
validate :: proc(s:^State,info:^runtime.Type_Info) {
    if s.failure.error!=.None {return}
    info:=base(info)
    if info==nil {fail(s,.Unsupported_Type);return}
    if info.id==typeid_of(big.Integer)||info.id==typeid_of(big.Rational) {return}
    if _,named:=info.variant.(runtime.Type_Info_Named);named {fail(s,.Unsupported_Type,info);return}
    for id in s.schemas[:s.schema_count] {if id==info.id {return}}
    if s.schema_count==MAX_DEPTH {fail(s,.Too_Deep,info);return}
    s.schemas[s.schema_count]=info.id;s.schema_count+=1
    defer s.schema_count-=1
    #partial switch item in info.variant {
    case runtime.Type_Info_Boolean,runtime.Type_Info_Integer,runtime.Type_Info_Rune,runtime.Type_Info_Float,runtime.Type_Info_Bit_Set:
        if info.size<1||info.size>16 {fail(s,.Unsupported_Type,info)}
    case runtime.Type_Info_Complex,runtime.Type_Info_Quaternion:
        if info.size<4||info.size>32 {fail(s,.Unsupported_Type,info)}
    case runtime.Type_Info_Type_Id,runtime.Type_Info_Pointer,runtime.Type_Info_Multi_Pointer,
         runtime.Type_Info_String,runtime.Type_Info_Any:
    case runtime.Type_Info_Enum:validate(s,item.base)
    case runtime.Type_Info_Slice:validate(s,item.elem)
    case runtime.Type_Info_Dynamic_Array:validate(s,item.elem)
    case runtime.Type_Info_Fixed_Capacity_Dynamic_Array:validate(s,item.elem)
    case runtime.Type_Info_Array:validate(s,item.elem)
    case runtime.Type_Info_Simd_Vector:validate(s,item.elem)
    case runtime.Type_Info_Enumerated_Array:
        validate(s,item.elem)
    case runtime.Type_Info_Matrix:validate(s,item.elem)
    case runtime.Type_Info_Struct:
        if .raw_union in item.flags||item.soa_kind!=.None {fail(s,.Unsupported_Type,info);return}
        for i in 0..<int(item.field_count) {
            if !push_path(s,Path{name=item.names[i]}) {return}
            validate(s,item.types[i]);s.depth-=1
            if s.failure.error!=.None {return}
        }
    case runtime.Type_Info_Union:
        for variant in item.variants {validate(s,variant);if s.failure.error!=.None {return}}
    case runtime.Type_Info_Bit_Field:
        if item.backing_type.size>16 {fail(s,.Unsupported_Type,info);return}
        for i in 0..<item.field_count {validate(s,item.types[i])}
    case:fail(s,.Unsupported_Type,info)
    }
}
byte_element :: proc(info:^runtime.Type_Info)->bool {
    info:=base(info)
    if info==nil {return false}
    item,ok:=info.variant.(runtime.Type_Info_Integer)
    return ok&&!item.signed&&info.size==1
}

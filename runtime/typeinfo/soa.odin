package thor_typeinfo

import "base:runtime"

// SOA descriptors store one column per logical field. Slice and dynamic
// descriptors append a footer after those columns; fixed arrays store them inline.
soa_columns :: proc "contextless"(s:Type_Info_Struct)->int {
    #partial switch s.soa_kind {
    case .Slice: return int(s.field_count)-1
    case .Dynamic: return int(s.field_count)-3
    }
    return int(s.field_count)
}

soa_length :: proc "contextless"(s:Type_Info_Struct, data:rawptr)->int {
    if s.soa_kind==.Fixed {return int(s.soa_len)}
    return (cast(^int)(uintptr(data)+s.offsets[soa_columns(s)]))^
}

soa_column :: proc "contextless"(s:Type_Info_Struct, data:rawptr, column:int)->(rawptr,^runtime.Type_Info) {
    slot:=rawptr(uintptr(data)+s.offsets[column])
    info:=runtime.type_info_base(s.types[column])
    if s.soa_kind==.Fixed {return slot,info.variant.(Type_Info_Array).elem}
    return (cast(^rawptr)slot)^,info.variant.(Type_Info_Multi_Pointer).elem
}

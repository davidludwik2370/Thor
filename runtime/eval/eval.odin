// Versioned, contents-only protocol for native compile-time helper processes.
package thor_eval

import "base:intrinsics"
import "base:runtime"
import "thor:typeinfo"
import "core:fmt"
import "core:reflect"
import "core:strings"
import big "thor:big"

hex :: proc(builder:^strings.Builder,bytes:[]u8) {
    digits:="0123456789abcdef"
    for byte in bytes {
        strings.write_byte(builder,digits[byte>>4])
        strings.write_byte(builder,digits[byte&15])
    }
}

// Metadata remains textual type identity, never a process address/typeid.
emit_specialized :: proc(value:$T,names:[]string) {
    builder:strings.Builder
    strings.builder_init(&builder)
    defer strings.builder_destroy(&builder)
    strings.write_string(&builder,`{"thor_value":1,"value":{"kind":"specialized","types":[`)
    for name,i in names {
        if i>0 {strings.write_byte(&builder,',')}
        write(&builder,name,0)
    }
    strings.write_string(&builder,`],"value":`)
    write(&builder,value,0)
    strings.write_string(&builder,`}}`)
    print_packet(builder)
}

write :: proc(builder:^strings.Builder,value:$T,$Depth:int) {
    when Depth>32 {panic("Compile-time value nesting limit exceeded")}
    else when T==big.Integer || T==big.Rational {
        write_big(builder,value)
    } else when intrinsics.type_is_boolean(T) {
        strings.write_string(builder,`{"kind":"bool","value":`)
        fmt.sbprintf(builder,"%t",bool(value));strings.write_byte(builder,'}')
    } else when intrinsics.type_is_integer(T) {
        strings.write_string(builder,`{"kind":"integer","value":"`)
        fmt.sbprintf(builder,"%d",value);strings.write_string(builder,`"}`)
    } else when intrinsics.type_is_enum(T)||intrinsics.type_is_bit_set(T) {
        write_dynamic(builder,any(value),Depth)
    } else when intrinsics.type_is_float(T) {
        kind:="float"
        strings.write_string(builder,`{"kind":"`);strings.write_string(builder,kind)
        strings.write_string(builder,`","bytes":"`)
        bytes:=transmute([size_of(T)]u8)value
        hex(builder,bytes[:]);strings.write_string(builder,`","byte_order":"`)
        order:="big" when intrinsics.type_is_endian_big(T)||(intrinsics.type_is_endian_platform(T)&&ODIN_ENDIAN==.Big) else "little"
        strings.write_string(builder,order);strings.write_string(builder,`"}`)
    } else when intrinsics.type_is_complex(T) {
        strings.write_string(builder,`{"kind":"complex","values":[`)
        write(builder,real(value),Depth+1);strings.write_byte(builder,',')
        write(builder,imag(value),Depth+1);strings.write_string(builder,` ]}`)
    } else when intrinsics.type_is_quaternion(T) {
        strings.write_string(builder,`{"kind":"quaternion","values":[`)
        write(builder,real(value),Depth+1);strings.write_byte(builder,',')
        write(builder,imag(value),Depth+1);strings.write_byte(builder,',')
        write(builder,jmag(value),Depth+1);strings.write_byte(builder,',')
        write(builder,kmag(value),Depth+1);strings.write_string(builder,` ]}`)
    } else when intrinsics.type_is_string(T) {
        strings.write_string(builder,`{"kind":"string","bytes":"`)
        hex(builder,transmute([]u8)value);strings.write_string(builder,`"}`)
    } else when intrinsics.type_is_enumerated_array(T) {
        write_dynamic(builder,any(value),Depth)
    } else when intrinsics.type_is_dynamic_array(T) {
        write_dynamic(builder,any(value),Depth)
    } else when intrinsics.type_is_array(T)||intrinsics.type_is_slice(T) {
        strings.write_string(builder,`{"kind":"sequence","storage":"`)
        strings.write_string(builder,"slice" when intrinsics.type_is_slice(T) else "array")
        strings.write_string(builder,`","nil":`)
        when intrinsics.type_is_slice(T) {fmt.sbprintf(builder,"%t",raw_data(value)==nil)}
        else {strings.write_string(builder,"false")}
        when size_of(intrinsics.type_elem_type(T))==0 {strings.write_string(builder,`,"zero_sized":true`)}
        strings.write_string(builder,`,"values":[`)
        index:=0
        for item in value {
            if index>0 {strings.write_byte(builder,',')}
            write(builder,item,Depth+1)
            index+=1
        }
        strings.write_string(builder,` ]}`)
    } else when intrinsics.type_is_struct(T) {
        write_dynamic(builder,any(value),Depth)
    } else when intrinsics.type_is_union(T) {
        write_dynamic(builder,any(value),Depth)
    } else {#panic("Unsupported compile-time value type")}
}

// Runtime RTTI is used only by the compiler-owned serializer. Fields are
// encoded individually; padding and process addresses never enter the packet.
write_dynamic :: proc(builder:^strings.Builder,value:any,depth:int) {
    if depth>32 {panic("Compile-time value nesting limit exceeded")}
    if value.id==typeid_of(big.Integer){write_big(builder,(cast(^big.Integer)value.data)^);return}
    if value.id==typeid_of(big.Rational){write_big(builder,(cast(^big.Rational)value.data)^);return}
    info:=runtime.type_info_base(type_info_of(value.id))
    bytes:=(cast([^]u8)value.data)[:info.size]
    #partial switch variant in info.variant {
    case typeinfo.Type_Info_Integer,typeinfo.Type_Info_Rune:
        signed:=false
        big_endian:=ODIN_ENDIAN==.Big
        #partial switch item in info.variant {
        case typeinfo.Type_Info_Integer:signed=item.signed;big_endian=item.endianness==.Big||(item.endianness==.Platform&&ODIN_ENDIAN==.Big)
        case typeinfo.Type_Info_Rune:signed=true
        }
        number:u128
        for byte,index in bytes {
            shift:=(len(bytes)-1-index)*8 if big_endian else index*8
            number|=u128(byte)<<uint(shift)
        }
        strings.write_string(builder,`{"kind":"integer","value":"`)
        bits:=uint(len(bytes)*8)
        if signed&&(number&(u128(1)<<(bits-1)))!=0 {
            if bits<128 {number|=(~u128(0))<<bits}
            fmt.sbprintf(builder,"%d",transmute(i128)number)
        } else {fmt.sbprintf(builder,"%d",number)}
        strings.write_string(builder,`"}`)
    case typeinfo.Type_Info_Boolean:
        truth:=false;for byte in bytes {truth=truth||byte!=0}
        strings.write_string(builder,`{"kind":"bool","value":`)
        fmt.sbprintf(builder,"%t",truth);strings.write_byte(builder,'}')
    case typeinfo.Type_Info_Enum:
        write_dynamic(builder,any{value.data,variant.base.id},depth+1)
    case typeinfo.Type_Info_Bit_Set:
        strings.write_string(builder,`{"kind":"bitset","values":[`)
        count:=0
        big_endian:=ODIN_ENDIAN==.Big
        if variant.underlying!=nil {
            base:=runtime.type_info_base(variant.underlying)
            if integer,ok:=base.variant.(typeinfo.Type_Info_Integer);ok && integer.endianness!=.Platform {
                big_endian=integer.endianness==.Big
            }
        }
        for bit in 0..<info.size*8 {
            byte_index:=info.size-1-bit/8 if big_endian else bit/8
            if (bytes[byte_index]&(u8(1)<<uint(bit%8)))!=0 {
                if count>0 {strings.write_byte(builder,',')};count+=1
                write(builder,int(variant.lower)+bit,0)
            }
        }
        strings.write_string(builder,` ]}`)
    case typeinfo.Type_Info_Float:
        kind:="float"
        strings.write_string(builder,`{"kind":"`);strings.write_string(builder,kind)
        strings.write_string(builder,`","bytes":"`);hex(builder,bytes)
        strings.write_string(builder,`","byte_order":"`)
        order:="little"
        if variant.endianness==.Big||(variant.endianness==.Platform&&ODIN_ENDIAN==.Big){order="big"}
        strings.write_string(builder,order);strings.write_string(builder,`"}`)
    case typeinfo.Type_Info_String:
        if variant.is_cstring || variant.encoding!=.UTF_8 {panic("Unsupported compile-time string type")}
        text:=(cast(^string)value.data)^
        strings.write_string(builder,`{"kind":"string","bytes":"`)
        hex(builder,transmute([]u8)text);strings.write_string(builder,`"}`)
    case typeinfo.Type_Info_Complex,typeinfo.Type_Info_Quaternion:
        count:=2;kind:="complex"
        if _,ok:=info.variant.(typeinfo.Type_Info_Quaternion);ok {count=4;kind="quaternion"}
        strings.write_string(builder,`{"kind":"`);strings.write_string(builder,kind)
        strings.write_string(builder,`","values":[`)
        width:=info.size/count
        for i in 0..<count {
            if i>0 {strings.write_byte(builder,',')}
            strings.write_string(builder,`{"kind":"float","bytes":"`)
            // Odin stores quaternion lanes as (i,j,k,real); packets use (real,i,j,k).
            lane:=(i+3)%4 if count==4 else i
            hex(builder,bytes[lane*width:(lane+1)*width]);strings.write_string(builder,`"}`)
        }
        strings.write_string(builder,` ]}`)
    case typeinfo.Type_Info_Struct:
        if variant.soa_kind!=.None {
            write_soa(builder,value.data,variant,depth)
            return
        }
        if .raw_union in variant.flags {panic("Raw unions cannot be serialized")}
        strings.write_string(builder,`{"kind":"record","fields":[`)
        written:=0
        for i in 0..<int(variant.field_count) {
            if variant.names[i]=="_" {continue}
            if written>0 {strings.write_byte(builder,',')}
            strings.write_string(builder,`["`);strings.write_string(builder,variant.names[i])
            strings.write_string(builder,`",`)
            item:=any{rawptr(uintptr(value.data)+variant.offsets[i]),variant.types[i].id}
            write_dynamic(builder,item,depth+1);strings.write_byte(builder,']')
            written+=1
        }
        strings.write_string(builder,` ]}`)
    case typeinfo.Type_Info_Enumerated_Array:
        // Implicit enum keys retain private enum identities without embedding
        // helper type names. Unnamed holes have no literal key; reject nonzero
        // contents there instead of silently changing the value.
        enum_info:=runtime.type_info_base(variant.index).variant.(typeinfo.Type_Info_Enum)
        strings.write_string(builder,`{"kind":"enum_array","fields":[`)
        count:=0
        for i in 0..<variant.count {
            key:=int(variant.min_value)+i
            name:=""
            for v,j in enum_info.values {if int(v)==key {name=enum_info.names[j];break}}
            item:=any{rawptr(uintptr(value.data)+uintptr(i*variant.elem_size)),variant.elem.id}
            if name=="" {
                for byte in (cast([^]u8)item.data)[:variant.elem_size] {if byte!=0 {panic("Nonzero unnamed enum-array slots cannot be represented as constants")}}
                continue
            }
            if count>0 {strings.write_byte(builder,',')};count+=1
            strings.write_string(builder,`["`);strings.write_string(builder,name);strings.write_string(builder,`",`)
            write_dynamic(builder,item,depth+1);strings.write_byte(builder,']')
        }
        strings.write_string(builder,` ]}`)
    case typeinfo.Type_Info_Array,typeinfo.Type_Info_Slice,typeinfo.Type_Info_Dynamic_Array:
        count,stride,capacity:=0,0,0
        pointer:=value.data;elem:^runtime.Type_Info;is_nil:=false
        #partial switch item in info.variant {
        case typeinfo.Type_Info_Array:count=item.count;stride=item.elem_size;elem=item.elem
        case typeinfo.Type_Info_Slice:
            slice:=(cast(^runtime.Raw_Slice)value.data)^;count=slice.len;pointer=slice.data
            stride=item.elem_size;elem=item.elem;is_nil=pointer==nil
        case typeinfo.Type_Info_Dynamic_Array:
            array:=(cast(^runtime.Raw_Dynamic_Array)value.data)^;count=array.len;capacity=array.cap;pointer=array.data
            stride=item.elem_size;elem=item.elem;is_nil=pointer==nil
            if count<0||capacity<count||(is_nil&&capacity!=0&&stride!=0) {panic("Invalid pure dynamic-array result")}
        }
        strings.write_string(builder,`{"kind":"sequence","storage":"`)
        storage:="array"
        if _,ok:=info.variant.(typeinfo.Type_Info_Slice);ok {storage="slice"}
        if _,ok:=info.variant.(typeinfo.Type_Info_Dynamic_Array);ok {storage="dynamic"}
        strings.write_string(builder,storage);strings.write_string(builder,`","nil":`)
        fmt.sbprintf(builder,"%t",is_nil)
        if storage=="dynamic" {strings.write_string(builder,`,"capacity":`);fmt.sbprintf(builder,"%d",capacity)}
        if stride==0 {strings.write_string(builder,`,"zero_sized":true`)}
        strings.write_string(builder,`,"values":[`)
        for i in 0..<count {
            if i>0 {strings.write_byte(builder,',')}
            write_dynamic(builder,any{rawptr(uintptr(pointer)+uintptr(i*stride)),elem.id},depth+1)
        }
        strings.write_string(builder,` ]}`)
    case typeinfo.Type_Info_Union:
        item:=reflect.get_union_variant(value);index:=-1
        if item!=nil {for candidate,i in variant.variants {if candidate.id==item.id {index=i;break}}}
        strings.write_string(builder,`{"kind":"union","variant":`)
        fmt.sbprintf(builder,"%d",index)
        if index>=0 {strings.write_string(builder,`,"value":`);write_dynamic(builder,item,depth+1)}
        strings.write_byte(builder,'}')
    case:panic("Unsupported compile-time value type")
    }
}

// Encode logical rows. Column pointers, padding and allocator handles are
// local to the evaluator process and cannot appear in a cached result.
write_soa :: proc(builder:^strings.Builder,data:rawptr,s:typeinfo.Type_Info_Struct,depth:int) {
    columns:=typeinfo.soa_columns(s)
    count:=typeinfo.soa_length(s,data)
    storage:="array"
    if s.soa_kind==.Slice {storage="slice"}
    if s.soa_kind==.Dynamic {storage="dynamic"}
    strings.write_string(builder,`{"kind":"sequence","soa":true,"storage":"`)
    nil_backing:=false
    if s.soa_kind!=.Fixed {
        nil_backing=true
        if columns>0 {
            first,_:=typeinfo.soa_column(s,data,0)
            nil_backing=first==nil
        }
    }
    fmt.sbprintf(builder,`%s","nil":%t,"zero_sized":%t`,storage,nil_backing,s.soa_base_type.size==0)
    if s.soa_kind==.Dynamic {
        capacity:=(cast(^int)(uintptr(data)+s.offsets[columns+1]))^
        fmt.sbprintf(builder,`,"capacity":%d`,capacity)
    }
    strings.write_string(builder,`,"values":[`)
    row:=runtime.type_info_base(s.soa_base_type)
    buffer,err:=runtime.mem_alloc_bytes(max(1,row.size),row.align)
    if err!=.None || raw_data(buffer)==nil {panic("Unable to serialize SOA row")}
    defer delete(buffer)
    for i in 0..<count {
        if i!=0 {strings.write_byte(builder,',')}
        for col in 0..<columns {
            column,elem:=typeinfo.soa_column(s,data,col)
            field_offset:=col*elem.size
            if rec,ok:=row.variant.(typeinfo.Type_Info_Struct);ok {field_offset=int(rec.offsets[col])}
            if elem.size>0 {runtime.mem_copy_non_overlapping(rawptr(uintptr(raw_data(buffer))+uintptr(field_offset)),
                rawptr(uintptr(column)+uintptr(i*elem.size)),elem.size)}
        }
        write_dynamic(builder,any{raw_data(buffer),s.soa_base_type.id},depth+1)
    }
    strings.write_string(builder,` ]}`)
}

emit :: proc(value:$T) {
    builder:strings.Builder;strings.builder_init(&builder);defer strings.builder_destroy(&builder)
    strings.write_string(&builder,`{"thor_value":1,"value":`)
    write(&builder,value,0)
    strings.write_byte(&builder,'}')
    print_packet(builder)
}

print_packet :: proc(builder:strings.Builder) {
    text:=strings.to_string(builder)
    if len(text)>16*1024*1024 {panic("Compile-time result is too large")}
    fmt.println(text)
}

// Canonical decimal contents, independent of backend limb width and allocators.
write_big :: proc(builder:^strings.Builder,value:$T) where T==big.Integer || T==big.Rational {
    when T==big.Integer {
        text:=big.to_string(value);defer delete(text)
        strings.write_string(builder,`{"kind":"big_integer","value":"`)
        strings.write_string(builder,text);strings.write_string(builder,`"}`)
    } else {
        n:=big.numerator(value);d:=big.denominator(value)
        defer big.destroy(&n);defer big.destroy(&d)
        numer:=big.to_string(n);denom:=big.to_string(d)
        defer delete(numer);defer delete(denom)
        strings.write_string(builder,`{"kind":"big_rational","numerator":"`)
        strings.write_string(builder,numer);strings.write_string(builder,`","denominator":"`)
        strings.write_string(builder,denom);strings.write_string(builder,`"}`)
    }
}

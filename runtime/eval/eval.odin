// Versioned, contents-only protocol for native compile-time helper processes.
package thor_eval

import "base:intrinsics"
import "base:runtime"
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
    fmt.println(strings.to_string(builder))
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
    assert(depth<=32,"Compile-time value nesting limit exceeded")
    if value.id==typeid_of(big.Integer){write_big(builder,(cast(^big.Integer)value.data)^);return}
    if value.id==typeid_of(big.Rational){write_big(builder,(cast(^big.Rational)value.data)^);return}
    info:=runtime.type_info_base(type_info_of(value.id))
    bytes:=(cast([^]u8)value.data)[:info.size]
    #partial switch variant in info.variant {
    case runtime.Type_Info_Integer,runtime.Type_Info_Rune:
        signed:=false
        big_endian:=ODIN_ENDIAN==.Big
        #partial switch item in info.variant {
        case runtime.Type_Info_Integer:signed=item.signed;big_endian=item.endianness==.Big||(item.endianness==.Platform&&ODIN_ENDIAN==.Big)
        case runtime.Type_Info_Rune:signed=true
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
    case runtime.Type_Info_Boolean:
        truth:=false;for byte in bytes {truth=truth||byte!=0}
        strings.write_string(builder,`{"kind":"bool","value":`)
        fmt.sbprintf(builder,"%t",truth);strings.write_byte(builder,'}')
    case runtime.Type_Info_Enum:
        write_dynamic(builder,any{value.data,variant.base.id},depth+1)
    case runtime.Type_Info_Bit_Set:
        strings.write_string(builder,`{"kind":"bitset","values":[`)
        count:=0
        for bit in 0..<info.size*8 {
            byte_index:=bit/8 if ODIN_ENDIAN==.Little else info.size-1-bit/8
            if (bytes[byte_index]&(u8(1)<<uint(bit%8)))!=0 {
                if count>0 {strings.write_byte(builder,',')};count+=1
                write(builder,int(variant.lower)+bit,0)
            }
        }
        strings.write_string(builder,` ]}`)
    case runtime.Type_Info_Float:
        kind:="float"
        strings.write_string(builder,`{"kind":"`);strings.write_string(builder,kind)
        strings.write_string(builder,`","bytes":"`);hex(builder,bytes)
        strings.write_string(builder,`","byte_order":"`)
        order:="little"
        if variant.endianness==.Big||(variant.endianness==.Platform&&ODIN_ENDIAN==.Big){order="big"}
        strings.write_string(builder,order);strings.write_string(builder,`"}`)
    case runtime.Type_Info_String:
        assert(!variant.is_cstring&&variant.encoding==.UTF_8,"Unsupported compile-time string type")
        text:=(cast(^string)value.data)^
        strings.write_string(builder,`{"kind":"string","bytes":"`)
        hex(builder,transmute([]u8)text);strings.write_string(builder,`"}`)
    case runtime.Type_Info_Complex,runtime.Type_Info_Quaternion:
        count:=2;kind:="complex"
        if _,ok:=info.variant.(runtime.Type_Info_Quaternion);ok {count=4;kind="quaternion"}
        strings.write_string(builder,`{"kind":"`);strings.write_string(builder,kind)
        strings.write_string(builder,`","values":[`)
        width:=info.size/count
        for i in 0..<count {
            if i>0 {strings.write_byte(builder,',')}
            strings.write_string(builder,`{"kind":"float","bytes":"`)
            hex(builder,bytes[i*width:(i+1)*width]);strings.write_string(builder,`"}`)
        }
        strings.write_string(builder,` ]}`)
    case runtime.Type_Info_Struct:
        assert(.raw_union not_in variant.flags,"Raw unions cannot be serialized")
        strings.write_string(builder,`{"kind":"record","fields":[`)
        for i in 0..<int(variant.field_count) {
            if i>0 {strings.write_byte(builder,',')}
            strings.write_string(builder,`["`);strings.write_string(builder,variant.names[i])
            strings.write_string(builder,`",`)
            item:=any{rawptr(uintptr(value.data)+variant.offsets[i]),variant.types[i].id}
            write_dynamic(builder,item,depth+1);strings.write_byte(builder,']')
        }
        strings.write_string(builder,` ]}`)
    case runtime.Type_Info_Enumerated_Array:
        // Implicit enum keys retain private enum identities without embedding
        // helper type names. Unnamed holes have no literal key; reject nonzero
        // contents there instead of silently changing the value.
        enum_info:=runtime.type_info_base(variant.index).variant.(runtime.Type_Info_Enum)
        strings.write_string(builder,`{"kind":"enum_array","fields":[`)
        count:=0
        for i in 0..<variant.count {
            key:=int(variant.min_value)+i
            name:=""
            for v,j in enum_info.values {if int(v)==key {name=enum_info.names[j];break}}
            item:=any{rawptr(uintptr(value.data)+uintptr(i*variant.elem_size)),variant.elem.id}
            if name=="" {
                for byte in (cast([^]u8)item.data)[:variant.elem_size] {assert(byte==0,"Nonzero unnamed enum-array slots cannot be represented as constants")}
                continue
            }
            if count>0 {strings.write_byte(builder,',')};count+=1
            strings.write_string(builder,`["`);strings.write_string(builder,name);strings.write_string(builder,`",`)
            write_dynamic(builder,item,depth+1);strings.write_byte(builder,']')
        }
        strings.write_string(builder,` ]}`)
    case runtime.Type_Info_Array,runtime.Type_Info_Slice,runtime.Type_Info_Dynamic_Array:
        count,stride,capacity:=0,0,0
        pointer:=value.data;elem:^runtime.Type_Info;is_nil:=false
        #partial switch item in info.variant {
        case runtime.Type_Info_Array:count=item.count;stride=item.elem_size;elem=item.elem
        case runtime.Type_Info_Slice:
            slice:=(cast(^runtime.Raw_Slice)value.data)^;count=slice.len;pointer=slice.data
            stride=item.elem_size;elem=item.elem;is_nil=pointer==nil
        case runtime.Type_Info_Dynamic_Array:
            array:=(cast(^runtime.Raw_Dynamic_Array)value.data)^;count=array.len;capacity=array.cap;pointer=array.data
            stride=item.elem_size;elem=item.elem;is_nil=pointer==nil
            if count<0||capacity<count||(is_nil&&capacity!=0&&stride!=0) {panic("Invalid pure dynamic-array result")}
        }
        strings.write_string(builder,`{"kind":"sequence","storage":"`)
        storage:="array"
        if _,ok:=info.variant.(runtime.Type_Info_Slice);ok {storage="slice"}
        if _,ok:=info.variant.(runtime.Type_Info_Dynamic_Array);ok {storage="dynamic"}
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
    case runtime.Type_Info_Union:
        item:=reflect.get_union_variant(value);index:=-1
        if item!=nil {for candidate,i in variant.variants {if candidate.id==item.id {index=i;break}}}
        strings.write_string(builder,`{"kind":"union","variant":`)
        fmt.sbprintf(builder,"%d",index)
        if index>=0 {strings.write_string(builder,`,"value":`);write_dynamic(builder,item,depth+1)}
        strings.write_byte(builder,'}')
    case:panic("Unsupported compile-time value type")
    }
}

emit :: proc(value:$T) {
    builder:strings.Builder;strings.builder_init(&builder);defer strings.builder_destroy(&builder)
    strings.write_string(&builder,`{"thor_value":1,"value":`)
    write(&builder,value,0)
    strings.write_byte(&builder,'}')
    assert(len(strings.to_string(builder))<=16*1024*1024,"Compile-time result is too large")
    fmt.println(strings.to_string(builder))
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

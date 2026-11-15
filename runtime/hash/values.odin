#+private
package thor_hash

import "base:runtime"
import "thor:typeinfo"
import "thor:big"

integer :: proc(s:^State,value:big.Integer) {
    raw:=value._value
    if raw.used<0||raw.used>len(raw.digit)||!span(s,raw_data(raw.digit),raw.used,8,type_info_of(big.Integer)) {
        fail(s,.Invalid_Value,type_info_of(big.Integer));return
    }
    used:=raw.used
    for used>0&&raw.digit[used-1]==0 {used-=1}
    number(s,u128(used));number(s,u128(used>0&&raw.sign==.Negative),1)
    for digit in raw.digit[:used] {number(s,u128(digit))}
}
floating :: proc(s:^State,p:rawptr,width:int,endian:=runtime.Platform_Endianness.Platform) {
    bits:=unsigned(p,width,endian)
    // Equal positive/negative zero values must hash equally at every nesting level.
    mask:=(u128(1)<<uint(width*8-1))-1
    if bits&mask==0 {bits=0}
    number(s,bits,width)
}
sequence :: proc(s:^State,p:rawptr,count,stride:int,elem:^runtime.Type_Info,root:bool) {
    if !span(s,p,count,stride,elem) {return}
    if byte_element(elem) {
        if !root {tag(s,.Bytes);number(s,u128(count))}
        bytes:[]u8
        if count>0 {bytes=([^]u8)(p)[:count]}
        if root {direct_bytes(s,bytes)}else{emit(s,bytes)}
        return
    }
    tag(s,.Sequence);number(s,u128(count))
    for i in 0..<count {
        if !push_path(s,Path{index=i,indexed=true}) {return}
        walk(s,offset(p,i*stride),elem,false)
        s.depth-=1
        if s.failure.error!=.None {return}
    }
}
text :: proc(s:^State,p:rawptr,info:typeinfo.Type_Info_String,root:bool) {
    data:rawptr;count:int
    width:=1 if info.encoding==.UTF_8 else 2
    if info.is_cstring {
        data=load(p,rawptr)
        if data!=nil {
            for unsigned(offset(data,count*width),width)!=0 {count+=1}
        }
    }else{
        value:=load(p,runtime.Raw_String)
        data=rawptr(value.data);count=value.len
    }
    if !span(s,data,count,width,nil) {return}
    if !root {tag(s,.String);number(s,u128(width),1);number(s,u128(count))}
    if width==1 {
        bytes:[]u8
        if count>0 {bytes=([^]u8)(data)[:count]}
        if root {direct_bytes(s,bytes)}else{emit(s,bytes)}
    }
    else {for i in 0..<count {number(s,unsigned(offset(data,i*2),2),2)}}
}
sparse_array :: proc(s:^State,p:rawptr,item:typeinfo.Type_Info_Enumerated_Array,root:bool) {
    if !span(s,p,item.count,item.elem_size,item.elem) {return}
    enum_info,ok:=runtime.type_info_base(item.index).variant.(typeinfo.Type_Info_Enum)
    if !ok {fail(s,.Unsupported_Type);return}
    // Enum aliases can name the same cell. Hash each live cell once, never the
    // gaps between sparse enum values, which are not logical array elements.
    count:=0
    for value,i in enum_info.values {
        duplicate:=false
        for previous in enum_info.values[:i] {if previous==value {duplicate=true;break}}
        if !duplicate {count+=1}
    }
    bytes:=byte_element(item.elem)
    if !root||!bytes {tag(s,.Bytes if bytes else .Sequence);number(s,u128(count))}
    integer_info,_:=runtime.type_info_base(enum_info.base).variant.(typeinfo.Type_Info_Integer)
    for value,i in enum_info.values {
        duplicate:=false
        for previous in enum_info.values[:i] {if previous==value {duplicate=true;break}}
        if duplicate {continue}
        index:=i128(value)-i128(item.min_value) if integer_info.signed else i128(u64(value))-i128(u64(item.min_value))
        if index<0||index>=i128(item.count) {fail(s,.Invalid_Value);return}
        cell:=offset(p,int(index)*item.elem_size)
        if bytes {emit(s,([^]u8)(cell)[:1]);continue}
        if !push_path(s,Path{index=int(index),indexed=true}) {return}
        walk(s,cell,item.elem,false);s.depth-=1
        if s.failure.error!=.None {return}
    }
}
union_value :: proc(s:^State,p:rawptr,item:typeinfo.Type_Info_Union) {
    tag(s,.Union)
    if len(item.variants)==0 {number(s,0);return}
    if len(item.variants)==1 {
        _,pointer:=runtime.type_info_base(item.variants[0]).variant.(typeinfo.Type_Info_Pointer)
        if pointer {
            if load(p,rawptr)==nil {number(s,0);return}
            number(s,1);walk(s,p,item.variants[0],false);return
        }
    }
    if item.tag_type==nil||item.tag_type.size>16 {fail(s,.Unsupported_Type);return}
    index:=unsigned(offset(p,int(item.tag_offset)),item.tag_type.size)
    if !item.no_nil {if index==0 {number(s,0);return};index-=1}
    if index>=u128(len(item.variants)) {fail(s,.Invalid_Value);return}
    number(s,index+1)
    walk(s,p,item.variants[int(index)],false)
}
walk :: proc(s:^State,p:rawptr,info:^runtime.Type_Info,root:bool) {
    if s.failure.error!=.None {return}
    info:=base(info)
    if info==nil||p==nil&&info.size>0 {fail(s,.Invalid_Value,info);return}
    if s.frame_count==MAX_DEPTH {fail(s,.Too_Deep,info);return}
    for frame in s.frames[:s.frame_count] {
        if frame.address==p&&frame.id==info.id {fail(s,.Cyclic_Data,info);return}
    }
    s.frames[s.frame_count]=Frame{p,info.id};s.frame_count+=1
    defer s.frame_count-=1
    if info.id==typeid_of(big.Integer) {tag(s,.Big_Integer);integer(s,load(p,big.Integer));return}
    if info.id==typeid_of(big.Rational) {
        tag(s,.Big_Rational)
        value:=load(p,big.Rational)
        integer(s,value._numerator)
        if big.is_zero(value) {number(s,1);number(s,0,1);number(s,1)}else{integer(s,value._denominator)}
        return
    }
    #partial switch item in info.variant {
    case typeinfo.Type_Info_Boolean:
        tag(s,.Boolean);number(s,u128(unsigned(p,info.size)!=0),1)
    case typeinfo.Type_Info_Integer:
        tag(s,.Integer);number(s,u128(info.size),1);number(s,u128(item.signed),1)
        number(s,unsigned(p,info.size,item.endianness),info.size)
    case typeinfo.Type_Info_Rune:
        tag(s,.Integer);number(s,u128(info.size),1);number(s,1,1);number(s,unsigned(p,info.size),info.size)
    case typeinfo.Type_Info_Float:
        tag(s,.Float);number(s,u128(info.size),1);floating(s,p,info.size,item.endianness)
    case typeinfo.Type_Info_Complex,typeinfo.Type_Info_Quaternion:
        _,is_complex:=info.variant.(typeinfo.Type_Info_Complex)
        count:=2 if is_complex else 4
        tag(s,.Complex if count==2 else .Quaternion);number(s,u128(info.size),1)
        for i in 0..<count {floating(s,offset(p,i*(info.size/count)),info.size/count)}
    case typeinfo.Type_Info_Enum:tag(s,.Enum);walk(s,p,item.base,false)
    case typeinfo.Type_Info_Bit_Set:
        endian:=runtime.Platform_Endianness.Platform
        if item.underlying!=nil {
            if integer_info,ok:=runtime.type_info_base(item.underlying).variant.(typeinfo.Type_Info_Integer);ok {endian=integer_info.endianness}
        }
        tag(s,.Bit_Set);number(s,u128(info.size),1);number(s,unsigned(p,info.size,endian),info.size)
    case typeinfo.Type_Info_Pointer,typeinfo.Type_Info_Multi_Pointer:
        tag(s,.Pointer);number(s,u128(uintptr(load(p,rawptr))))
    case typeinfo.Type_Info_Type_Id:tag(s,.Type_Id);number(s,unsigned(p,info.size))
    case typeinfo.Type_Info_String:text(s,p,item,root)
    case typeinfo.Type_Info_Slice:
        value:=load(p,runtime.Raw_Slice);sequence(s,value.data,value.len,item.elem_size,item.elem,root)
    case typeinfo.Type_Info_Dynamic_Array:
        value:=load(p,runtime.Raw_Dynamic_Array)
        if value.len<0||value.cap<value.len {fail(s,.Invalid_Value,info);return}
        sequence(s,value.data,value.len,item.elem_size,item.elem,root)
    case typeinfo.Type_Info_Fixed_Capacity_Dynamic_Array:
        count:=load(offset(p,int(item.len_offset)),int)
        if count<0||count>item.capacity {fail(s,.Invalid_Value,info);return}
        sequence(s,p,count,item.elem_size,item.elem,root)
    case typeinfo.Type_Info_Array:
        sequence(s,p,item.count,item.elem_size,item.elem,root)
    case typeinfo.Type_Info_Enumerated_Array:
        if item.is_sparse {sparse_array(s,p,item,root)}else{sequence(s,p,item.count,item.elem_size,item.elem,root)}
    case typeinfo.Type_Info_Struct:
        tag(s,.Struct);number(s,u128(item.field_count))
        for i in 0..<int(item.field_count) {
            if !push_path(s,Path{name=item.names[i]}) {return}
            walk(s,offset(p,int(item.offsets[i])),item.types[i],false)
            s.depth-=1
            if s.failure.error!=.None {return}
        }
    case typeinfo.Type_Info_Union:union_value(s,p,item)
    case typeinfo.Type_Info_Any:
        tag(s,.Any)
        value:=load(p,any)
        if value.id==nil {number(s,0);return}
        number(s,1)
        actual:=type_info_of(value.id)
        validate(s,actual);walk(s,value.data,actual,false)
    case typeinfo.Type_Info_Simd_Vector:
        tag(s,.Vector);sequence(s,p,item.count,item.elem_size,item.elem,false)
    case typeinfo.Type_Info_Matrix:
        tag(s,.Matrix);number(s,u128(item.row_count));number(s,u128(item.column_count))
        for row in 0..<item.row_count {for column in 0..<item.column_count {
            index:=column*item.elem_stride+row if item.layout==.Column_Major else row*item.elem_stride+column
            walk(s,offset(p,index*item.elem_size),item.elem,false)
        }}
    case typeinfo.Type_Info_Bit_Field:
        tag(s,.Bit_Field);number(s,u128(item.field_count))
        endian:=runtime.Platform_Endianness.Platform
        if integer_info,ok:=runtime.type_info_base(item.backing_type).variant.(typeinfo.Type_Info_Integer);ok {endian=integer_info.endianness}
        bits:=unsigned(p,item.backing_type.size,endian)
        for i in 0..<item.field_count {
            width:=int(item.bit_sizes[i]);number(s,u128(width))
            mask:=max(u128) if width==128 else (u128(1)<<uint(width))-1
            number(s,(bits>>uint(item.bit_offsets[i]))&mask,16)
        }
    case:fail(s,.Unsupported_Type,info)
    }
}
calculate :: proc(s:^State,value:any)->u64 {
    if s.failure.error!=.None {return 0}
    if value.id==nil {tag(s,.Any);number(s,0);return digest(s)}
    info:=type_info_of(value.id)
    validate(s,info)
    walk(s,value.data,info,true)
    return digest(s)
}

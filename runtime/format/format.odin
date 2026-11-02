// Caller-owned formatting primitives used by Thor's interpolation lowering.
package thor_format

import "base:intrinsics"
import "base:runtime"
import "core:fmt"
import "core:strings"
import "core:unicode/utf8"
import "core:io"
import "thor:big"

Alignment :: enum { Default, Left, Right, Center }

// All conversions and scratch allocations use the requested allocator. Only the
// final, exact-sized result escapes. Static options retain native type checking.
format_spec :: proc(
    value: $T,
    $grouping: bool,
    width: int = 0,
    align: Alignment = .Default,
    fill: rune = ' ',
    allocator := context.allocator,
    $verb: rune,
    $precision: int,
    $plus: bool,
    $space: bool,
    $zero: bool,
    $alternate: bool,
) -> string {
    buffer:Buffer=---
    buffer_init(&buffer,allocator)
    defer buffer_destroy(&buffer)
    write_spec(&buffer.builder,value,grouping,width,align,fill,allocator,verb,precision,plus,space,zero,alternate)
    return buffer_owned_text(&buffer,allocator)
}

// Append a field directly; the destination owns its storage. This is shared by
// interpolation and the allocating format_spec compatibility API.
write_spec :: proc(
    builder:^strings.Builder,
    value:$T,
    $grouping:bool,
    width:int=0,
    align:Alignment=.Default,
    fill:rune=' ',
    allocator:=context.allocator,
    $verb:rune,
    $precision:int,
    $plus:bool,
    $space:bool,
    $zero:bool,
    $alternate:bool,
) {
    #assert(!grouping || (intrinsics.type_is_integer(T) || intrinsics.type_is_float(T) || T==big.Integer || T==big.Rational) && (verb=='v'&&T!=big.Rational&&T!=rune||verb=='d'||verb=='i'||verb=='f'||verb=='F'||verb=='e'||verb=='E'||verb=='g'||verb=='G'||verb=='%'||verb=='m'||verb=='M'), "Grouping requires a decimal numeric format.")
    assert(width >= 0, "format width must be nonnegative")
    assert(fill >= 0 && fill <= 0x10ffff && !(0xd800 <= fill && fill <= 0xdfff), "format fill must be a Unicode scalar")
    context.allocator=allocator
    when intrinsics.type_is_string(T) && (verb=='v'||verb=='s') {
        validate_render(T,verb,precision)
        if width==0 {
            append_borrowed(builder,truncate_runes(string(value),precision),allocator)
            return
        }
    }
    rendered:Buffer=---
    buffer_init(&rendered,allocator)
    defer buffer_destroy(&rendered)
    text:=render_view(&rendered.builder,value,verb,precision,plus,space,alternate)
    if rendered.scratch.failed {panic("format allocation failed")}
    numeric_verb::verb=='d'||verb=='i'||verb=='b'||verb=='o'||verb=='z'||verb=='x'||verb=='X'||verb=='f'||verb=='F'||verb=='e'||verb=='E'||verb=='g'||verb=='G'||verb=='%'||verb=='m'||verb=='M'||verb=='h'||verb=='H'||verb=='p'||verb=='v'&&T!=rune&&!intrinsics.type_is_enum(T)
    numeric::numeric_verb && (intrinsics.type_is_integer(T)||intrinsics.type_is_float(T)||intrinsics.type_is_enum(T)||intrinsics.type_is_pointer(T)||intrinsics.type_is_multi_pointer(T)||T==big.Integer||T==big.Rational)
    signed:=text
    when numeric && (plus || space) && verb!='T' && verb!='w' && verb!='h' && verb!='H' {
        if len(text)>0 && text[0]!='-' && text[0]!='+' && text!="NaN" {
            // Native floats/big numbers can need a sign added after rendering.
            // Insert in the caller-owned scratch instead of allocating a string.
            if resize(&rendered.builder.buf,len(text)+1)!=nil {panic("format allocation failed")}
            for i:=len(text);i>0;i-=1 {rendered.builder.buf[i]=rendered.builder.buf[i-1]}
            rendered.builder.buf[0]='+' when plus else ' '
            signed=strings.to_string(rendered.builder)
        }
    }
    sign:=0
    if len(signed)>0 && (signed[0]=='-' || signed[0]=='+' || signed[0]==' ') {sign=1}
    integer_end:=sign
    for integer_end<len(signed) && signed[integer_end]>='0' && signed[integer_end]<='9' {integer_end+=1}
    separators:=0
    when grouping {separators=max(0,(integer_end-sign-1)/3)}
    padding:=0
    if width>0 {padding=max(0,width-utf8.rune_count_in_string(signed)-separators)}
    left,right:=0,0
    switch align {
    case .Default,.Right:left=padding
    case .Center:left=padding/2;right=padding-left
    case .Left:right=padding
    }
    pad_fill:=fill
    numeric_zero:=false
    when zero && numeric && verb!='T' && verb!='w' {
        digits_precision::precision>=0 && (intrinsics.type_is_integer(T)||T==big.Integer) && (verb=='d'||verb=='i'||verb=='b'||verb=='o'||verb=='z'||verb=='x'||verb=='X'||verb=='v')
        if align!=.Left && align!=.Center && !digits_precision {numeric_zero=true;pad_fill='0'}
    }
    prefix:=sign
    if len(signed)>=sign+2 && signed[sign]=='0' && (signed[sign+1]=='x'||signed[sign+1]=='X'||signed[sign+1]=='o'||signed[sign+1]=='b'||signed[sign+1]=='z'||signed[sign+1]=='h') {prefix+=2}
    encoded,fill_size:=utf8.encode_rune(pad_fill)
    assert(padding<=(max(int)-len(signed)-separators)/fill_size,"format width overflow")
    total:=len(signed)+separators+padding*fill_size
    used:=len(builder.buf)
    assert(total<=max(int)-used,"format width overflow")
    // A direct borrowed string/byte view can also be a view of this builder.
    // Preserve it before a grow that could release the original backing data.
    alias_copy:string
    when intrinsics.type_is_string(T) || (intrinsics.type_is_array(T)||intrinsics.type_is_slice(T)||intrinsics.type_is_dynamic_array(T)) {
        if is_builder_view(builder,signed) {
            alias_copy=strings.clone(signed,allocator)
            if len(alias_copy)!=len(signed) {panic("format allocation failed")}
            signed=alias_copy
        }
    }
    defer delete(alias_copy)
    if used+total>cap(builder.buf) {
        // Reserve append slack once, so a long field followed by a short
        // literal/field does not immediately grow the heap fallback again.
        wanted:=used+total
        slack:=min(64,max(int)-wanted)
        if builder.buf.allocator.procedure==scratch_allocate {
            storage:=len((cast(^Scratch_Allocator)builder.buf.allocator.data).storage)
            if wanted<=storage {slack=min(slack,storage-wanted)}
        }
        if reserve(&builder.buf,wanted+slack)!=nil {panic("format allocation failed")}
    }
    if resize(&builder.buf,used+total)!=nil {panic("format allocation failed")}
    data:=builder.buf[used:]
    cursor:=0
    if numeric_zero {copy(data,signed[:prefix]);cursor+=prefix}
    for _ in 0..<left {copy(data[cursor:],encoded[:fill_size]);cursor+=fill_size}
    if separators==0 {
        start:=prefix if numeric_zero else 0
        cursor+=copy(data[cursor:],signed[start:])
    } else {
        for byte,i in transmute([]u8)signed {
            if numeric_zero && i<prefix {continue}
            if grouping && i>sign && i<integer_end && (integer_end-i)%3==0 {data[cursor]=',';cursor+=1}
            data[cursor]=byte;cursor+=1
        }
    }
    for _ in 0..<right {copy(data[cursor:],encoded[:fill_size]);cursor+=fill_size}
}

@(private)
is_builder_view :: proc "contextless"(builder:^strings.Builder,text:string)->bool {
    address,base:=uintptr(raw_data(text)),uintptr(raw_data(builder.buf))
    return len(text)>0 && base!=0 && address>=base && address-base<uintptr(cap(builder.buf))
}

@(private)
append_borrowed :: proc(builder:^strings.Builder,text:string,allocator:runtime.Allocator) {
    text:=text
    owned:string
    defer delete(owned,allocator=allocator)
    if is_builder_view(builder,text) {
        owned=strings.clone(text,allocator)
        if len(owned)!=len(text) {panic("format allocation failed")}
        text=owned
    }
    if strings.write_string(builder,text)!=len(text) {panic("format allocation failed")}
}

format_options :: proc(value:$T,$grouping:bool,width:int=0,align:Alignment=.Default,fill:rune=' ',allocator:=context.allocator)->string {
    return format_spec(value,grouping,width,align,fill,allocator,'v',-1,false,false,false,false)
}
format_plain :: proc(value:$T,width:int=0,align:Alignment=.Default,fill:rune=' ',allocator:=context.allocator)->string {
    return format_options(value,false,width,align,fill,allocator)
}

format :: proc{format_plain, format_options}

group_integer :: proc(value: $T, allocator := context.allocator) -> string where intrinsics.type_is_integer(T) || T==big.Integer {
    return format(value, grouping=true, allocator=allocator)
}

// Dynamic values used by basic.print/table. No global fmt registry is changed.
value_string :: proc(value:any,allocator:=context.allocator)->string {
    if value.id==typeid_of(big.Integer){return big.to_string((cast(^big.Integer)value.data)^,allocator)}
    if value.id==typeid_of(big.Rational){return big.to_string((cast(^big.Rational)value.data)^,allocator)}
    return fmt.aprint(value,allocator=allocator)
}
write_value :: proc(writer:io.Writer,value:any) {
    if value.id==typeid_of(big.Integer) || value.id==typeid_of(big.Rational) {
        text:=value_string(value);defer delete(text)
        fmt.wprint(writer,text,flush=false)
    } else {fmt.wprint(writer,value,flush=false)}
}

validate_pure_value :: proc "contextless"($T:typeid) {
    when !(T==big.Integer || T==big.Rational || intrinsics.type_is_boolean(T) || intrinsics.type_is_numeric(T) || intrinsics.type_is_enum(T) || intrinsics.type_is_string(T)) {
        #panic("Pure formatting supports deterministic scalar/string values only; custom record formatters are not eligible")
    }
}

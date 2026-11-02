// Conversion before grouping/padding. Width is always counted in Unicode runes.
package thor_format

import "base:intrinsics"
import "core:fmt"
import "core:strconv"
import "core:strings"
import "thor:big"

@(private="package")
float_text :: proc(value:f64,bits:int,verb:rune,precision:int)->string {
    buffer:Buffer;buffer_init(&buffer);defer buffer_destroy(&buffer)
    float_into(&buffer.builder,value,bits,verb,precision)
    return buffer_owned_text(&buffer)
}

@(private="package")
float_into :: proc(builder:^strings.Builder,value:f64,bits:int,verb:rune,precision:int) {
    p:=precision
    v:=verb
    if v=='v'||v=='w'||v=='G' {v='g'}
    if v=='F'||v=='%' {v='f'}
    if p<0 {p=3 if v=='f' else 6 if v=='e'||v=='E' else -1}
    assert(p<max(int)-400,"format precision overflow")
    // f64 needs at most 309 integer digits. Common precisions fit on stack;
    // larger precisions retain the original checked, appropriately sized path.
    size:=max(400,p+400)
    local:[512]u8
    heap:[]u8
    defer delete(heap)
    data:=local[:]
    if size>len(local) {
        heap=make([]u8,size)
        if len(heap)!=size {panic("format allocation failed")}
        data=heap
    }
    text:=strconv.write_float(data,value*100 if verb=='%' else value,u8(v),p,bits)
    if len(text)>1 && text[0]=='+' && text[1]!='I' {text=text[1:]}
    if strings.write_string(builder,text)!=len(text) {panic("format allocation failed")}
    if verb=='%' {
        if strings.write_byte(builder,'%')!=1 {panic("format allocation failed")}
    }
}

@(private="package")
truncate_runes :: proc "contextless"(text:string,count:int)->string {
    if count<0 {return text}
    seen:=0
    for _,offset in text {if seen==count {return text[:offset]};seen+=1}
    return text
}

@(private="package")
validate_render :: proc($T:typeid,$verb:rune,$precision:int) {
    bytes::intrinsics.type_elem_type(T)==u8 when (intrinsics.type_is_array(T)||intrinsics.type_is_slice(T)||intrinsics.type_is_dynamic_array(T)) else false
    // Public type contracts are mapped back to the interpolation field by Thor.
    when T==big.Rational {
        #assert(verb=='v'||verb=='s'||verb=='w'||verb=='T'||verb=='f'||verb=='F'||verb=='e'||verb=='E'||verb=='g'||verb=='G'||verb=='%',"Rational formatting does not support this verb.")
        #assert(precision>=0 || verb!='g' && verb!='G',"Rational g/G formatting requires explicit precision, e.g. :.6g; omit the verb for an exact fraction.")
    } else when T==big.Integer {
        #assert(verb=='v'||verb=='s'||verb=='w'||verb=='T'||verb=='d'||verb=='i'||verb=='b'||verb=='o'||verb=='z'||verb=='x'||verb=='X'||verb=='f'||verb=='F'||verb=='e'||verb=='E'||verb=='g'||verb=='G'||verb=='%'||verb=='m'||verb=='M',"Integer formatting does not support this verb.")
    } else when intrinsics.type_is_boolean(T) {
        #assert(verb=='v'||verb=='w'||verb=='T'||verb=='t',"Boolean formatting requires v, w, T, or t.")
        #assert(precision<0,"Precision is not supported for boolean values.")
    } else when intrinsics.type_is_string(T) {
        #assert(verb=='v'||verb=='w'||verb=='T'||verb=='s'||verb=='q'||verb=='x'||verb=='X',"String formatting requires v, w, T, s, q, x, or X.")
    } else when intrinsics.type_is_integer(T) || intrinsics.type_is_enum(T) {
        #assert(verb=='v'||verb=='w'||verb=='T'||verb=='s'&&intrinsics.type_is_enum(T)||verb=='d'||verb=='i'||verb=='b'||verb=='o'||verb=='z'||verb=='x'||verb=='X'||verb=='c'||verb=='r'||verb=='U'||verb=='q'&&T==rune||verb=='f'||verb=='F'||verb=='e'||verb=='E'||verb=='g'||verb=='G'||verb=='%'||verb=='m'||verb=='M',"Integer formatting does not support this verb.")
    } else when intrinsics.type_is_float(T) {
        #assert(verb=='v'||verb=='w'||verb=='T'||verb=='f'||verb=='F'||verb=='e'||verb=='E'||verb=='g'||verb=='G'||verb=='h'||verb=='H'||verb=='%',"Float formatting requires v, w, T, f/F, e/E, g/G, h/H, or %.")
    } else when intrinsics.type_is_complex(T) || intrinsics.type_is_quaternion(T) {
        #assert(verb=='v'||verb=='w'||verb=='T'||verb=='f'||verb=='F'||verb=='e'||verb=='E'||verb=='g'||verb=='G'||verb=='h'||verb=='H',"Complex/quaternion formatting requires v, w, T, f/F, e/E, g/G, or h/H.")
    } else when bytes {
        #assert(precision<0 || verb=='s'||verb=='q'||verb=='w'||verb=='x'||verb=='X',"Byte precision requires s, q, w, x, or X.")
    } else {
        #assert(precision<0,"Precision is supported for scalar numbers and strings, not aggregate values.")
    }
}

// Returned text borrows either the input or the caller's scratch builder.
// Custom formatters execute once, including when their writer grows to heap.
@(private="package")
render_view :: proc(builder:^strings.Builder,value:$T,$verb:rune,$precision:int,$plus:bool,$space:bool,$alternate:bool)->string {
    validate_render(T,verb,precision)
    bytes::intrinsics.type_elem_type(T)==u8 when (intrinsics.type_is_array(T)||intrinsics.type_is_slice(T)||intrinsics.type_is_dynamic_array(T)) else false
    decimal::verb=='f'||verb=='F'||verb=='e'||verb=='E'||verb=='g'||verb=='G'||verb=='%'
    integer_precision::precision>=0 && (verb=='b'||verb=='o'||verb=='z'||verb=='x'||verb=='X'||verb=='d'||verb=='i'||verb=='v'&&T!=rune)
    when intrinsics.type_is_string(T) && (verb=='v'||verb=='s') {
        return truncate_runes(string(value),precision)
    } else when bytes && verb=='s' {
        data:=value[:]
        return string(data[:min(len(data),precision)]) when precision>=0 else string(data)
    } else when T==big.Integer || T==big.Rational ||
                intrinsics.type_is_integer(T) && (decimal || integer_precision || verb=='m'||verb=='M') ||
                (intrinsics.type_is_complex(T)||intrinsics.type_is_quaternion(T)) && (decimal || verb=='v'||verb=='w') {
        text:=render(value,verb,precision,plus,space,alternate)
        defer delete(text)
        if strings.write_string(builder,text)!=len(text) {panic("format allocation failed")}
    } else when intrinsics.type_is_float(T) && (decimal || verb=='v'||verb=='w') {
        float_into(builder,f64(value),size_of(T)*8,verb,precision)
    } else {native_into(builder,value,verb,precision,plus,space,alternate)}
    return strings.to_string(builder^)
}

@(private="package")
render :: proc(value:$T,$verb:rune,$precision:int,$plus:bool,$space:bool,$alternate:bool)->string {
    when verb=='T' && (T==big.Integer || T==big.Rational) {
        return strings.clone("Integer" when T==big.Integer else "Rational")
    } else when (T==big.Integer || T==big.Rational) && verb=='w' {
        when T==big.Integer {
            text:=big.to_string(value);defer delete(text)
            return fmt.aprintf("Integer(%s)",text)
        } else {
            n,d:=big.numerator(value),big.denominator(value);defer big.destroy(&n);defer big.destroy(&d)
            ns,ds:=big.to_string(n),big.to_string(d);defer delete(ns);defer delete(ds)
            return fmt.aprintf("Rational(%s, %s)",ns,ds)
        }
    } else when verb=='f'||verb=='F'||verb=='e'||verb=='E'||verb=='g'||verb=='G'||verb=='%' {
        when intrinsics.type_is_float(T) {return float_text(f64(value),size_of(T)*8,verb,precision)}
        else when T==big.Integer || T==big.Rational {return big.format_decimal(value,verb,precision)}
        else when intrinsics.type_is_integer(T) {
            integer:=big.from_integer(value);defer big.destroy(&integer)
            return big.format_decimal(integer,verb,precision)
        } else when intrinsics.type_is_complex(T) || intrinsics.type_is_quaternion(T) {
            return complex_text(value,verb,precision,plus)
        } else {return native_text(value,verb,precision,plus,space,alternate)}
    } else when (intrinsics.type_is_float(T) || intrinsics.type_is_complex(T) || intrinsics.type_is_quaternion(T)) && (verb=='v'||verb=='w') {
        when intrinsics.type_is_float(T) {return float_text(f64(value),size_of(T)*8,verb,precision)}
        else {return complex_text(value,verb,precision,plus)}
    } else when (T==big.Integer || intrinsics.type_is_integer(T) && precision>=0) && (verb=='b'||verb=='o'||verb=='z'||verb=='x'||verb=='X'||verb=='d'||verb=='i'||verb=='v'&&T!=rune) {
        when T==big.Integer {return integer_text(value,verb,precision,alternate)}
        else {integer:=big.from_integer(value);defer big.destroy(&integer);return integer_text(integer,verb,precision,alternate)}
    } else when (T==big.Integer || intrinsics.type_is_integer(T)) && (verb=='m'||verb=='M') {
        when T==big.Integer {return big_memory(value,verb,precision,alternate)}
        else {integer:=big.from_integer(value);defer big.destroy(&integer);return big_memory(integer,verb,precision,alternate)}
    } else when T==big.Integer || T==big.Rational {return big.to_string(value)}
    else {
        return native_text(value,verb,precision,plus,space,alternate)
    }
}

@(private="package")
complex_text :: proc(value:$T,verb:rune,precision:int,plus:bool)->string {
    count::4 when intrinsics.type_is_quaternion(T) else 2
    bits::size_of(T)*8/count
    components:[count]f64
    components[0],components[1]=f64(real(value)),f64(imag(value))
    when count==4 {components[2],components[3]=f64(jmag(value)),f64(kmag(value))}
    b:strings.Builder;strings.builder_init(&b);defer strings.builder_destroy(&b)
    suffixes:="ijk"
    for component,i in components {
        text:=float_text(component,bits,verb,precision);defer delete(text)
        if (i>0 || plus) && len(text)>0 && text[0]!='-' && text[0]!='+' {strings.write_byte(&b,'+')}
        strings.write_string(&b,text)
        if i>0 {strings.write_byte(&b,suffixes[i-1])}
    }
    return strings.clone(strings.to_string(b))
}

@(private="package")
big_memory :: proc(value:big.Integer,verb:rune,precision:int,alternate:bool)->string {
    unit:=big.from_integer(1024);defer big.destroy(&unit)
    magnitude:=big.unary(value,"-") if big.is_negative(value) else big.clone(value);defer big.destroy(&magnitude)
    divisor:=big.from_integer(1);defer big.destroy(&divisor)
    index:=0
    for index<8 {
        next:=big.binary(divisor,unit,"*")
        if big.compare_integer(magnitude,next)<0 {big.destroy(&next);break}
        big.destroy(&divisor);divisor=next;index+=1
    }
    ratio:=big.fraction(value,divisor);defer big.destroy(&ratio)
    text:=big.format_decimal(ratio,'f',precision if precision>=0 else 0 if index==0 else 2);defer delete(text)
    lower:=[9]string{"b","kib","mib","gib","tib","pib","eib","zib","yib"}
    upper:=[9]string{"B","KiB","MiB","GiB","TiB","PiB","EiB","ZiB","YiB"}
    return fmt.aprintf("%s%s%s",text," " if alternate else "",upper[index] if verb=='M' else lower[index])
}

@(private="package")
integer_text :: proc(value:big.Integer,verb:rune,precision:int,alternate:bool)->string {
    radix:=2 if verb=='b' else 8 if verb=='o' else 12 if verb=='z' else 16 if verb=='x'||verb=='X' else 10
    magnitude:=big.format_radix(value,radix,verb=='X');defer delete(magnitude)
    b:strings.Builder;strings.builder_init(&b);defer strings.builder_destroy(&b)
    if big.is_negative(value) {strings.write_byte(&b,'-')}
    if alternate {
        switch verb {
        case 'b':strings.write_string(&b,"0b")
        case 'o':strings.write_string(&b,"0o")
        case 'z':strings.write_string(&b,"0z")
        case 'x','X':strings.write_string(&b,"0x")
        }
    }
    assert(precision<max(int)-400,"format precision overflow")
    for _ in len(magnitude)..<precision {strings.write_byte(&b,'0')}
    strings.write_string(&b,magnitude)
    return strings.clone(strings.to_string(b))
}

@(private="package")
native_text :: proc(value:$T,$verb:rune,$precision:int,$plus:bool,$space:bool,$alternate:bool)->string {
    buffer:Buffer;buffer_init(&buffer);defer buffer_destroy(&buffer)
    native_into(&buffer.builder,value,verb,precision,plus,space,alternate)
    return buffer_owned_text(&buffer)
}

@(private="package")
native_into :: proc(builder:^strings.Builder,value:$T,$verb:rune,$precision:int,$plus:bool,$space:bool,$alternate:bool) {
    bytes::intrinsics.type_elem_type(T)==u8 when (intrinsics.type_is_array(T)||intrinsics.type_is_slice(T)||intrinsics.type_is_dynamic_array(T)) else false
    fi:=fmt.Info{writer=strings.to_writer(builder)}
    fi.plus=plus;fi.space=space;fi.hash=alternate;fi.prec_set=precision>=0;fi.prec=max(0,precision)
    when intrinsics.type_is_string(T) && verb!='T' {
        fi.prec_set=false
        fmt.fmt_string(&fi,truncate_runes(string(value),precision),verb)
    } else when bytes && (verb=='s'||verb=='q'||verb=='w'||verb=='x'||verb=='X') {
        data:=value[:]
        if precision>=0 {data=data[:min(len(data),precision)]}
        fi.prec_set=false
        fmt.fmt_string(&fi,string(data),verb)
    } else {
        fi.prec_set=false
        fmt.fmt_arg(&fi,value,verb)
    }
    when precision>=0 && (intrinsics.type_is_enum(T) || intrinsics.type_is_pointer(T) || intrinsics.type_is_multi_pointer(T) || intrinsics.type_is_integer(T)) && (verb=='d'||verb=='i'||verb=='b'||verb=='o'||verb=='z'||verb=='x'||verb=='X'||verb=='U'||verb=='p') {
        text:=strings.to_string(builder^)
        prefix:=0
        if len(text)>0 && (text[0]=='-'||text[0]=='+'||text[0]==' '){prefix=1}
        if len(text)>=prefix+2 && (text[prefix]=='0'&&(text[prefix+1]=='b'||text[prefix+1]=='o'||text[prefix+1]=='x'||text[prefix+1]=='X'||text[prefix+1]=='z') || text[:2]=="U+"){prefix+=2}
        padding:=max(0,precision-(len(text)-prefix))
        assert(padding<max(int)-len(text),"format precision overflow")
        length:=len(text)
        if resize(&builder.buf,length+padding)!=nil {panic("format allocation failed")}
        for i:=length-1;i>=prefix;i-=1 {builder.buf[i+padding]=builder.buf[i]}
        for i in prefix..<prefix+padding {builder.buf[i]='0'}
    }
}

package thor_format

import "base:intrinsics"
import "base:runtime"
import "core:strings"
import "core:unicode/utf8"
import "thor:big"

// Own an exact-sized string, borrowing the input. Scalar formatting matches
// an unadorned interpolation field; byte/rune sequences become text instead of
// a printed list. Allocation failure returns empty text and an optional error.
@(require_results)
try_string :: proc(value:$T,allocator:=context.allocator) ->
    (text:string,err:runtime.Allocator_Error) #optional_allocator_error {
    state:=String_Allocator{backing=allocator}
    destination:=runtime.Allocator{procedure=string_allocate,data=&state}
    sequence :: intrinsics.type_is_array(T)||intrinsics.type_is_slice(T)||
                intrinsics.type_is_dynamic_array(T)||intrinsics.type_is_fixed_capacity_dynamic_array(T)
    when sequence {
        E :: intrinsics.type_base_type(intrinsics.type_elem_type(T))
        when E==u8 {
            data:=value
            return strings.clone(string(transmute([]u8)data[:]),destination)
        } else when intrinsics.type_is_rune(E) {
            // Count encoded bytes first, then allocate exactly once. Invalid
            // code points become U+FFFD, as in Odin's UTF-8 conversion helpers.
            size:=0
            for r in value {
                _,width:=utf8.encode_rune(string_scalar(rune(r)))
                if size>max(int)-width {return "",.Out_Of_Memory}
                size+=width
            }
            data:=make([]u8,size,destination) or_return
            offset:=0
            for r in value {
                encoded,width:=utf8.encode_rune(string_scalar(rune(r)))
                copy(data[offset:],encoded[:width]);offset+=width
            }
            return string(data),nil
        } else {
            #panic("_string supports byte/rune sequences, numeric values, Integer, Rational, booleans, enums, strings and cstrings")
        }
    } else when (intrinsics.type_is_string(T)||intrinsics.type_is_cstring(T)) &&
                !intrinsics.type_is_string16(T)&&!intrinsics.type_is_cstring16(T) {
        return strings.clone(string(value),destination)
    } else when intrinsics.type_is_rune(T) {
        encoded,width:=utf8.encode_rune(string_scalar(rune(value)))
        return strings.clone(string(encoded[:width]),destination)
    } else when T==big.Integer||T==big.Rational {
        // big's public error enum groups allocator errors together. Record the
        // actual allocator error so this API preserves its precise cause.
        big_err:big.Error
        text,big_err=big.try_to_string(value,destination)
        if big_err==.None {return text,nil}
        if state.error!=nil {return "",state.error}
        if big_err==.Out_Of_Memory||big_err==.Integer_Overflow {return "",.Out_Of_Memory}
        return "",.Invalid_Argument
    } else when intrinsics.type_is_numeric(T)||intrinsics.type_is_boolean(T)||
                intrinsics.type_is_rune(T)||intrinsics.type_is_enum(T) {
        context.allocator=destination
        buffer:Buffer
        buffer_init(&buffer,destination)
        defer buffer_destroy(&buffer)
        when intrinsics.type_is_float(T) {
            err=try_float_into(&buffer.builder,f64(value),size_of(T)*8,'v',-1,destination)
        } else when intrinsics.type_is_complex(T)||intrinsics.type_is_quaternion(T) {
            count :: 4 when intrinsics.type_is_quaternion(T) else 2
            components:[count]f64
            suffixes:="ijk"
            components[0],components[1]=f64(real(value)),f64(imag(value))
            when count==4 {components[2],components[3]=f64(jmag(value)),f64(kmag(value))}
            for component,i in components {
                // The common float writer supplies matching shortest-roundtrip
                // text for all component sizes, signed zero and infinities.
                part:Buffer
                buffer_init(&part,destination)
                defer buffer_destroy(&part)
                err=try_float_into(&part.builder,component,size_of(T)*8/count,'v',-1,destination)
                if err!=nil {return "",err}
                view:=strings.to_string(part.builder)
                if i>0 && len(view)>0 && view[0]!='-' && view[0]!='+' {strings.write_byte(&buffer.builder,'+')}
                strings.write_string(&buffer.builder,view)
                if i>0 {strings.write_byte(&buffer.builder,suffixes[i-1])}
            }
        } else {
            // This static default-verb path never enters native_into's
            // precision/padding allocations that use panicking checks.
            native_into(&buffer.builder,value,'v',-1,false,false,false)
        }
        if state.error!=nil {return "",state.error}
        if err!=nil {return "",err}
        if buffer.scratch.failed {return "",.Out_Of_Memory}
        return strings.clone(strings.to_string(buffer.builder),destination)
    } else {
        #panic("_string supports byte/rune sequences, numeric values, Integer, Rational, booleans, enums, strings and cstrings")
    }
}

@(private="package")
string_scalar :: proc "contextless"(value:rune)->rune {
    // Normalize before encoding: Odin's current encoder resets r but retains
    // the original width for out-of-range (> U+FFFF) inputs.
    if value<0||value>0x10ffff||value>=0xd800&&value<=0xdfff {return '\ufffd'}
    return value
}

@(private="package")
String_Allocator :: struct {backing:runtime.Allocator,error:runtime.Allocator_Error}

@(private="package")
string_allocate :: proc(data:rawptr,mode:runtime.Allocator_Mode,size,alignment:int,
                       old_memory:rawptr,old_size:int,loc:=#caller_location)->([]byte,runtime.Allocator_Error) {
    state:=cast(^String_Allocator)data
    result,err:=state.backing.procedure(state.backing.data,mode,size,alignment,old_memory,old_size,loc)
    // core:mem.nil_allocator deliberately returns (nil, nil). A conversion
    // cannot report success without storage for its nonempty result.
    allocating:=mode==.Alloc||mode==.Alloc_Non_Zeroed||mode==.Resize||mode==.Resize_Non_Zeroed
    if allocating && size>0 && raw_data(result)==nil && err==nil {err=.Out_Of_Memory}
    // Unsupported resize is recoverable: Odin retries with allocate/copy/free.
    if state.error==nil && err!=nil &&
       (mode==.Alloc||mode==.Alloc_Non_Zeroed||
        (mode==.Resize||mode==.Resize_Non_Zeroed)&&err!=.Mode_Not_Implemented) {state.error=err}
    return result,err
}

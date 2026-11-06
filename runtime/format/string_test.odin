#+test
package thor_format

import "base:runtime"
import "core:testing"
import "core:mem"
import "core:math"
import "core:strings"
import "core:fmt"
import "thor:big"

@(private="file")
string_check :: proc(condition:bool) {if !condition {panic("string conversion check failed")}}

@(private="file")
same_string :: proc(value:$T) {
    actual,err:=try_string(value)
    defer delete(actual)
    expected:=format(value)
    defer delete(expected)
    string_check(err==nil && actual==expected)
}

@(test)
string_numeric_types :: proc(t:^testing.T) {
    same_string(min(int));same_string(max(uint));same_string(max(uintptr))
    same_string(min(i8));same_string(min(i16));same_string(min(i32));same_string(min(i64));same_string(min(i128))
    same_string(max(u8));same_string(max(u16));same_string(max(u32));same_string(max(u64));same_string(max(u128))
    same_string(i16be(-123));same_string(i32le(-123));same_string(i64be(-123));same_string(i128le(-123))
    same_string(u16le(123));same_string(u32be(123));same_string(u64le(123));same_string(u128be(123))
    same_string(f16(1.25));same_string(f32(1.25));same_string(f64(1.25))
    same_string(f16be(1.25));same_string(f32le(1.25));same_string(f64be(1.25))
    same_string(complex32(1+2i));same_string(complex64(1-2i));same_string(complex128(1+2i))
    same_string(quaternion64(1+2i+3j+4k));same_string(quaternion128(1-2i-3j-4k));same_string(quaternion256(1+2i-3j+4k))
    Count::distinct i64
    Ratio::distinct f64
    Flag::distinct bool
    Letter::distinct rune
    same_string(Count(-123));same_string(Ratio(1.25));same_string(Flag(true));same_string(Letter('界'))
    same_string(true);same_string(false);same_string(b8(true));same_string(b16(false));same_string(b32(true));same_string(b64(false))
    same_string('界');same_string(rune(0));same_string(rune(0xd800))
    Choice::enum{First=4,Second=9}
    same_string(Choice.First);same_string(Choice(7))
}

@(test)
string_float_edges :: proc(t:^testing.T) {
    values:=[?]f64{0,transmute(f64)u64(1<<63),1e-300,1e300,min(f64),max(f64),math.inf_f64(1),math.inf_f64(-1),math.nan_f64()}
    for value in values {
        same_string(value)
        same_string(complex(value,value))
        same_string(quaternion(real=value,imag=value,jmag=value,kmag=value))
    }
}

@(test)
string_text_sequences_and_owned_copies :: proc(t:^testing.T) {
    runes:=[?]rune{'é','界','😀',0,rune(0xd800),rune(-1)}
    text,err:=try_string(runes)
    defer delete(text)
    if err!=nil || text!="é界😀\x00\ufffd\ufffd" {fmt.eprintf("rune conversion: %q, error %v\n",text,err)}
    string_check(err==nil && text=="é界😀\x00\ufffd\ufffd")
    slice_text,slice_err:=try_string(runes[:]);defer delete(slice_text)
    string_check(slice_err==nil && slice_text==text)
    growing:=make([dynamic]rune);defer delete(growing)
    append(&growing,..runes[:])
    growing_text,growing_err:=try_string(growing);defer delete(growing_text)
    string_check(growing_err==nil && growing_text==text)
    fixed:[dynamic;8]rune
    append(&fixed,..runes[:])
    fixed_text,fixed_err:=try_string(fixed);defer delete(fixed_text)
    string_check(fixed_err==nil && fixed_text==text)
    bytes:=[?]u8{'a',0,255,'b'}
    copy,copy_err:=try_string(bytes);defer delete(copy)
    string_check(copy_err==nil && copy=="a\x00\xffb" && raw_data(copy)!=raw_data(&bytes))
    bytes[0]='?'
    string_check(copy[0]=='a')
    c:cstring="hello"
    owned,owned_err:=try_string(c);defer delete(owned)
    string_check(owned_err==nil && owned=="hello" && raw_data(owned)!=rawptr(c))
    second,second_err:=try_string(owned);defer delete(second)
    string_check(second_err==nil && second==owned && raw_data(second)!=raw_data(owned))
    terminated:=[?]u8{'a',0,'b',0}
    prefix,prefix_err:=try_string(cstring(&terminated[0]));defer delete(prefix)
    string_check(prefix_err==nil && prefix=="a")
    Word::distinct string
    Byte::distinct u8
    Rune::distinct rune
    same_string(Word("word"))
    a,aerr:=try_string([2]Byte{'a','b'});defer delete(a)
    b,berr:=try_string([2]Rune{'界','😀'});defer delete(b)
    string_check(aerr==nil && a=="ab" && berr==nil && b=="界😀")
    nil_runes:[]rune
    empty,empty_err:=try_string(nil_runes,mem.nil_allocator());defer delete(empty)
    nil_cstring:cstring
    empty_c,empty_cerr:=try_string(nil_cstring,mem.nil_allocator());defer delete(empty_c)
    string_check(empty_err==nil && empty=="" && empty_cerr==nil && empty_c=="")
}

@(private="file")
String_Budget :: struct {backing:mem.Allocator,remaining,attempts:int,error:mem.Allocator_Error,no_resize:bool}
@(private="file")
budget_string_allocate :: proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,old:rawptr,old_size:int,
                              loc:=#caller_location)->([]byte,mem.Allocator_Error) {
    state:=cast(^String_Budget)data
    if state.no_resize && (mode==.Resize||mode==.Resize_Non_Zeroed) {return nil,.Mode_Not_Implemented}
    if size>0 && (mode==.Alloc||mode==.Alloc_Non_Zeroed||mode==.Resize||mode==.Resize_Non_Zeroed) {
        state.attempts+=1
        if state.remaining==0 {return nil,state.error}
        state.remaining-=1
    }
    return state.backing.procedure(state.backing.data,mode,size,alignment,old,old_size,loc)
}

@(private="file")
string_failures :: proc(value:$T) {
    tracker:mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker,context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    for error in ([?]mem.Allocator_Error{.Out_Of_Memory,.Invalid_Pointer,.Invalid_Argument,.Mode_Not_Implemented}) {
        for no_resize in ([?]bool{false,true}) {
            succeeded:=false
            for budget in 0..<64 {
                state:=String_Budget{backing=mem.tracking_allocator(&tracker),remaining=budget,error=error,no_resize=no_resize}
                allocator:=mem.Allocator{budget_string_allocate,&state}
                text,err:=try_string(value,allocator)
                if err==nil {succeeded=true;delete(text,allocator)}
                else {string_check(err==error && text=="" && raw_data(text)==nil)}
                string_check(tracker.current_memory_allocated==0)
                if succeeded {break}
            }
            string_check(succeeded)
        }
    }
}

@(private="file")
string_silent_failure :: proc(value:$T) {
    for allocator in ([?]mem.Allocator{mem.nil_allocator(),runtime.nil_allocator()}) {
        text,err:=try_string(value,allocator)
        string_check(text=="" && raw_data(text)==nil && err==.Out_Of_Memory)
    }
}

@(test)
string_silent_nil_allocations_are_errors :: proc(t:^testing.T) {
    string_silent_failure(42);string_silent_failure(1.25);string_silent_failure(true)
    string_silent_failure('界');string_silent_failure("text")
    string_silent_failure(complex128(1+2i));string_silent_failure(quaternion256(1+2i+3j+4k))
    string_silent_failure([2]u8{'a','b'});string_silent_failure([2]rune{'界','😀'})
    integer:=big.from_integer(123);defer big.destroy(&integer)
    rational,err:=big.try_parse_rational("22/13")
    string_check(err==.None);defer big.destroy(&rational)
    string_silent_failure(integer);string_silent_failure(rational)
    for value in ([?]rune{-1,0xd800,0xdfff,0x110000,0x7fffffff}) {
        text,text_err:=try_string(value);defer delete(text)
        string_check(text_err==nil && text=="\ufffd")
    }
}

@(test)
string_allocation_failures_are_atomic :: proc(t:^testing.T) {
    string_failures(123);string_failures(1.25);string_failures(true);string_failures('界')
    string_failures(complex128(1+2i));string_failures(quaternion256(1+2i+3j+4k))
    string_failures("hello");string_failures([2]rune{'界','😀'});string_failures([2]u8{'a','b'})
    // Exercise owned big text intermediates and failure after a successful
    // numerator, rather than just denying the final result allocation.
    digits:=strings.repeat("1234567890",120);defer delete(digits)
    integer,integer_err:=big.try_parse_integer(digits)
    string_check(integer_err==.None)
    defer big.destroy(&integer)
    rational,rational_err:=big.try_parse_rational("123456789012345678901234567890/17")
    string_check(rational_err==.None)
    defer big.destroy(&rational)
    string_failures(integer);string_failures(rational)
    actual,err:=try_string(integer);defer delete(actual)
    string_check(err==nil && actual==digits)
    zero:big.Integer
    ratio_zero:big.Rational
    string_failures(zero);string_failures(ratio_zero)
    same_string(integer);same_string(rational);same_string(zero);same_string(ratio_zero)
}

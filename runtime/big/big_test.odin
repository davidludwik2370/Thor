#+test
package thor_big

import "base:runtime"
import backend "core:math/big"
import "core:mem"
import "core:fmt"
import "core:testing"

@(private="file")
expect_text :: proc(t: ^testing.T, value: $T, expected: string, loc := #caller_location) {
    text := to_string(value)
    defer delete(text)
    testing.expect_value(t, text, expected, loc=loc)
}

@(test)
zero_values_and_zero_construction_never_allocate :: proc(t: ^testing.T) {
    saved := context.allocator
    context.allocator = mem.panic_allocator()
    i: Integer
    r: Rational
    ci := clone(i)
    cr := clone(r)
    ni := from_integer(0)
    nr := rational_from_integer(0)
    zr := fraction(0, -999)
    zi, integer_ok := parse_integer("-0_000")
    zd, decimal_ok := parse_rational("-000.0(00)e100")
    zf, fraction_ok := parse_fraction("-000", "-99999")
    component := numerator(r)
    all_zero := is_zero(i) && is_zero(r) && is_zero(ci) && is_zero(cr) && is_zero(ni) && is_zero(nr) && is_zero(zr)
    all_zero &&= is_zero(zi) && is_zero(zd) && is_zero(zf) && is_zero(component)
    equal_zero := equal(r, cr) && equal(i, ci)
    destroy(&i); destroy(&r); destroy(&ci); destroy(&cr); destroy(&ni); destroy(&nr)
    destroy(&zr); destroy(&zi); destroy(&zd); destroy(&zf); destroy(&component)
    // Destruction resets the real caller value and is repeatable on zero.
    destroy(&i); destroy(&r)
    context.allocator = saved
    testing.expect(t, all_zero && equal_zero && integer_ok && decimal_ok && fraction_ok)
    expect_text(t, i, "0")
    expect_text(t, r, "0/1")
    d := denominator(r)
    defer destroy(&d)
    expect_text(t, d, "1")
}

@(private="file")
check_native_bounds :: proc(t: ^testing.T, $T: typeid) {
    lo := from_integer(min(T))
    hi := from_integer(max(T))
    defer destroy(&lo)
    defer destroy(&hi)
    raw_lo, raw_hi := lo._value, hi._value
    if min(T) != 0 {
        n, err := backend.int_get(&raw_lo, T)
        testing.expect(t, err == .None && n == min(T))
    } else { testing.expect(t, is_zero(lo)) }
    n, err := backend.int_get(&raw_hi, T)
    testing.expect(t, err == .None && n == max(T))
}

@(test)
all_machine_integer_widths_signed_minima_and_distinct_types :: proc(t: ^testing.T) {
    check_native_bounds(t, i8); check_native_bounds(t, u8)
    check_native_bounds(t, i16); check_native_bounds(t, u16)
    check_native_bounds(t, i32); check_native_bounds(t, u32)
    check_native_bounds(t, i64); check_native_bounds(t, u64)
    check_native_bounds(t, i128); check_native_bounds(t, u128)
    check_native_bounds(t, int); check_native_bounds(t, uint)
    check_native_bounds(t, uintptr)
    check_native_bounds(t, i16le); check_native_bounds(t, u16be)
    check_native_bounds(t, i32be); check_native_bounds(t, u32le)
    check_native_bounds(t, i64le); check_native_bounds(t, u64be)
    check_native_bounds(t, i128be); check_native_bounds(t, u128le)
    Distinct :: distinct i64
    value := from_integer(Distinct(-42))
    defer destroy(&value)
    expect_text(t, value, "-42")
    unsigned := from_integer(max(u128))
    defer destroy(&unsigned)
    expect_text(t, unsigned, "340282366920938463463374607431768211455")
    signed := from_integer(min(i128))
    defer destroy(&signed)
    expect_text(t, signed, "-170141183460469231731687303715884105728")
}

@(test)
integer_strings_bases_signs_underscores_and_large_values :: proc(t: ^testing.T) {
    cases := [][2]string{
        {"+000123", "123"}, {"-123_456_789", "-123456789"}, {"0xFF_ff", "65535"},
        {"-0B101_010", "-42"}, {"0o777", "511"}, {"0zBA", "142"}, {"0d123", "123"},
        {"000", "0"}, {"-0x00", "0"},
        {"123456789012345678901234567890123456789012345678901234567890", "123456789012345678901234567890123456789012345678901234567890"},
        {"-6883661286568296169366055923135928706438374499439310035948184781185165467381544654431642658888691999972355395598306633643402292590272242970162148509272223", "-6883661286568296169366055923135928706438374499439310035948184781185165467381544654431642658888691999972355395598306633643402292590272242970162148509272223"},
    }
    for item in cases {
        value, ok := parse_integer(item[0])
        testing.expect(t, ok)
        expect_text(t, value, item[1])
        destroy(&value)
    }
}

@(test)
invalid_integer_strings_are_rejected_completely_without_allocating :: proc(t: ^testing.T) {
    cases := []string{"", "+", "-", " 1", "1 ", "1\n", "1\rjunk", "1\x00junk", "1\t", "é", "１２", "0x", "0b2", "0zC", "0o8", "0d_1", "_1", "1_", "1__2", "--1", "+-1", "1.0", "1e3", "1/2", "12abc"}
    for text in cases {
        value, err := try_parse_integer(text, mem.panic_allocator())
        testing.expect(t, err == .Invalid_Text && is_zero(value), text)
        destroy(&value)
    }
}

@(test)
fraction_signs_reduction_and_zero_denominators :: proc(t: ^testing.T) {
    cases := [][3]int{{6,8,1}, {6,-8,-1}, {-6,8,-1}, {-6,-8,1}}
    for item in cases {
        value := fraction(item[0], item[1])
        expect_text(t, value, "-3/4" if item[2] < 0 else "3/4")
        destroy(&value)
    }
    zero := fraction(0, -100)
    expect_text(t, zero, "0/1")
    testing.expect(t, raw_data(zero._numerator._value.digit) == nil && raw_data(zero._denominator._value.digit) == nil)
    destroy(&zero)
    for n in ([2]int{0, 1}) {
        value, err := try_fraction(n, 0, mem.panic_allocator())
        testing.expect(t, err == .Division_by_Zero && is_zero(value))
        destroy(&value)
    }
    n := from_integer(-6)
    d := from_integer(-8)
    value := fraction(n, d)
    defer destroy(&value)
    expect_text(t, n, "-6")
    expect_text(t, d, "-8")
    destroy(&n); destroy(&d)
    expect_text(t, value, "3/4")
    mixed := fraction(i8(-6), u128(8))
    defer destroy(&mixed)
    expect_text(t, mixed, "-3/4")
}

@(test)
finite_and_repeating_decimals_are_exact :: proc(t: ^testing.T) {
    cases := [][2]string{
        {"0.1", "1/10"}, {".125", "1/8"}, {"12.", "12/1"}, {"-0.00", "0/1"},
        {"+1_234.5_00", "2469/2"}, {"1.25e2", "125/1"}, {"1.25E-2", "1/80"},
        {"-12e+3", "-12000/1"}, {"1e-3", "1/1000"}, {"1e1_0", "10000000000/1"},
        {"0.(3)", "1/3"}, {".(3)", "1/3"}, {"0.1(6)", "1/6"}, {"0.(142857)", "1/7"},
        {"1.2(34)", "611/495"}, {"123.45(89)", "305561/2475"},
        {"-123.45(89)e-2", "-305561/247500"}, {"0.(9)", "1/1"},
        {"0.0(9)", "1/10"}, {"0.1(0)", "1/10"}, {"0.(003)", "1/333"},
        {"-0.(0)", "0/1"}, {"1.2(3_4)e+1", "1222/99"},
        {"0xFF", "255/1"}, {"-0b10", "-2/1"}, {"6/-8", "-3/4"},
        {"-6/-8", "3/4"}, {"0x10/0b10", "8/1"},
    }
    for item in cases {
        value, ok := parse_rational(item[0])
        testing.expect(t, ok, item[0])
        expect_text(t, value, item[1])
        round_trip, round_trip_ok := parse_rational(item[1])
        testing.expect(t, round_trip_ok && equal(value, round_trip))
        destroy(&value); destroy(&round_trip)
    }
}

@(test)
malformed_rationals_are_zero_results_not_partial_values :: proc(t: ^testing.T) {
    cases := []string{"", ".", "+.", "e1", "1e", "1e+", "1e_1", "1._0", "1_.0", "0.()", "0.(3", "0.3)", "1(3)", "0.(3)(4)", "0.(3)tail", "0.(3)e", "0.(é)", "1.2.3", "1r3", "0.1...3", "1/2/3", "/2", "1/", "1.5/2", "bad/0", "0/bad", "1/0junk", "1 /2", "1/ 2", "NaN", "Inf", "1\x00", "0\n"}
    for text in cases {
        value, err := try_parse_rational(text, mem.panic_allocator())
        testing.expect(t, err == .Invalid_Text && is_zero(value), text)
        destroy(&value)
    }
    for text in ([]string{"1/0", "0/0", "1/-000"}) {
        value, err := try_parse_rational(text)
        testing.expect(t, err == .Division_by_Zero && is_zero(value))
        destroy(&value)
    }
    value, err := try_parse_rational("1e999999999999999999999999999999", mem.panic_allocator())
    testing.expect(t, err == .Integer_Overflow && is_zero(value))
    destroy(&value)
    _, ok := parse_rational("invalid")
    testing.expect(t, !ok)
    _, ok = parse_integer("invalid")
    testing.expect(t, !ok)
    _, ok = parse_fraction("1", "invalid")
    testing.expect(t, !ok)
}

@(test)
clones_getters_and_constructors_have_independent_storage :: proc(t: ^testing.T) {
    source := from_integer(123)
    copied := clone(source)
    constructed := from_integer(source)
    testing.expect(t, raw_data(source._value.digit) != raw_data(copied._value.digit))
    testing.expect(t, raw_data(source._value.digit) != raw_data(constructed._value.digit))
    destroy(&source)
    expect_text(t, copied, "123")
    expect_text(t, constructed, "123")
    destroy(&copied); destroy(&constructed)
    r := fraction(22, 13)
    c := clone(r)
    n := numerator(r)
    d := denominator(r)
    set_fraction(&r, 1, 2)
    expect_text(t, c, "22/13")
    expect_text(t, n, "22")
    expect_text(t, d, "13")
    destroy(&r)
    expect_text(t, c, "22/13")
    destroy(&c); destroy(&n); destroy(&d)
}

@(test)
replacement_is_atomic_and_handles_borrowed_parts_of_itself :: proc(t: ^testing.T) {
    r := fraction(22, 13)
    defer destroy(&r)
    set_fraction(&r, r._denominator, r._numerator)
    expect_text(t, r, "13/22")
    err := try_set_fraction(&r, 1, 0)
    testing.expect_value(t, err, .Division_by_Zero)
    expect_text(t, r, "13/22")
    err = try_set_fraction(&r, 3, 4, mem.nil_allocator())
    testing.expect_value(t, err, .Out_Of_Memory)
    expect_text(t, r, "13/22")
    set_fraction(&r, 0, 1)
    testing.expect(t, is_zero(r) && raw_data(r._denominator._value.digit) == nil)
}

@(test)
explicit_and_context_allocators_survive_context_changes :: proc(t: ^testing.T) {
    tracker: mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker, context.allocator, context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    allocator := mem.tracking_allocator(&tracker)
    value := from_integer(123, allocator)
    r := fraction(-22, 13, allocator)
    c := clone(r, allocator)
    n := numerator(r, allocator)
    d := denominator(r, allocator)
    saved := context.allocator
    context.allocator = allocator
    implicit, ok := parse_rational("123.45(89)")
    context.allocator = saved
    testing.expect(t, ok && len(tracker.allocation_map) > 0)
    // All destruction must use stored allocators, even with an unusable context.
    context.allocator = mem.panic_allocator()
    destroy(&value); destroy(&r); destroy(&c); destroy(&n); destroy(&d); destroy(&implicit)
    context.allocator = saved
    testing.expect_value(t, len(tracker.allocation_map), 0)
    testing.expect_value(t, len(tracker.bad_free_array), 0)
}

@(private="file")
Failing :: struct {
    backing: runtime.Allocator,
    fail_after, attempts: int,
    failed: bool,
}

@(private="file")
invalid_allocate :: proc(data: rawptr, mode: runtime.Allocator_Mode, size, alignment: int,
    old: rawptr, old_size: int, loc := #caller_location) -> ([]byte, runtime.Allocator_Error) {
    return nil, .Invalid_Argument
}

@(test)
allocator_errors_are_distinct_from_invalid_text :: proc(t: ^testing.T) {
    allocator := runtime.Allocator{invalid_allocate, nil}
    i, i_err := try_parse_integer("123", allocator)
    r, r_err := try_parse_rational("0.1(6)", allocator)
    f, f_err := try_parse_fraction("22", "13", allocator)
    text, text_err := try_to_string(Integer{}, allocator)
    testing.expect(t, i_err == .Invalid_Allocator && r_err == .Invalid_Allocator && f_err == .Invalid_Allocator)
    testing.expect(t, text_err == .Invalid_Allocator && text == "")
    destroy(&i); destroy(&r); destroy(&f)
    // Malformed text is rejected before allocation, regardless of allocator.
    invalid_i, i_ok := parse_integer("not a number", allocator)
    invalid_r, r_ok := parse_rational("0.(bad)", allocator)
    invalid_f, f_ok := parse_fraction("1", "bad", allocator)
    testing.expect(t, !i_ok && !r_ok && !f_ok)
    destroy(&invalid_i); destroy(&invalid_r); destroy(&invalid_f)
}
@(private="file")
fail_allocate :: proc(data: rawptr, mode: runtime.Allocator_Mode, size, alignment: int,
    old: rawptr, old_size: int, loc := #caller_location) -> ([]byte, runtime.Allocator_Error) {
    state := (^Failing)(data)
    if size > 0 && (mode == .Alloc || mode == .Alloc_Non_Zeroed || mode == .Resize || mode == .Resize_Non_Zeroed) {
        state.attempts += 1
        if state.attempts > state.fail_after { state.failed = true; return nil, .Out_Of_Memory }
    }
    return state.backing.procedure(state.backing.data, mode, size, alignment, old, old_size, loc)
}

@(test)
allocation_failure_sweep_cleans_partial_values_and_scratch :: proc(t: ^testing.T) {
    tracker: mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker, context.allocator, context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    source := fraction(min(i128), 13)
    defer destroy(&source)
    long_digits: [3600]u8
    pattern := "1234567890"
    for &digit, i in long_digits { digit = pattern[i%10] }
    for operation in 0..<11 {
        completed := false
        for limit in 0..<512 {
            state := Failing{backing=mem.tracking_allocator(&tracker), fail_after=limit}
            allocator := runtime.Allocator{fail_allocate, &state}
            err: Error
            switch operation {
            case 0:
                value: Integer
                value, err = try_from_integer(max(u128), allocator)
                if err != .None { testing.expect(t, is_zero(value)) }
                destroy(&value)
            case 1:
                value: Integer
                value, err = try_parse_integer("-123456789012345678901234567890123456789012345678901234567890", allocator)
                if err != .None { testing.expect(t, is_zero(value)) }
                destroy(&value)
            case 2:
                value: Rational
                value, err = try_fraction(1234567890, -987654321, allocator)
                if err != .None { testing.expect(t, is_zero(value)) }
                destroy(&value)
            case 3:
                value: Rational
                value, err = try_parse_rational("-123.45(89)e-3", allocator)
                if err != .None { testing.expect(t, is_zero(value)) }
                destroy(&value)
            case 4:
                value: Rational
                value, err = try_clone(source, allocator)
                if err != .None { testing.expect(t, is_zero(value)) }
                destroy(&value)
            case 5:
                text: string
                text, err = try_to_string(source, allocator)
                if err != .None { testing.expect_value(t, text, "") }
                delete(text, allocator)
            case 6:
                value: Rational
                value, err = try_rational_from_integer(42, allocator)
                if err != .None { testing.expect(t, is_zero(value)) }
                destroy(&value)
            case 7:
                value := clone(source)
                err = try_set_fraction(&value, 123456, 987654, allocator)
                if err != .None { testing.expect(t, equal(value, source)) }
                destroy(&value)
            case 8:
                value: Integer
                value, err = try_parse_integer(string(long_digits[:]), allocator)
                if err != .None { testing.expect(t, is_zero(value)) }
                destroy(&value)
            case 9:
                value: Rational
                value, err = try_parse_fraction("123456789012345678901234567890", "-9876543210987654321", allocator)
                if err != .None { testing.expect(t, is_zero(value)) }
                destroy(&value)
            case 10:
                value: Rational
                value, err = try_parse_rational("-0x123456789abcdef123456789abcdef", allocator)
                if err != .None { testing.expect(t, is_zero(value)) }
                destroy(&value)
            }
            testing.expect(t, err == .None || err == .Out_Of_Memory)
            if len(tracker.allocation_map) != 0 {
                testing.fail_now(t, fmt.tprintf("Allocation leaked: operation=%d, successful allocations before failure=%d", operation, limit))
            }
            testing.expect_value(t, len(tracker.bad_free_array), 0)
            if !state.failed { completed = true; break }
        }
        testing.expect(t, completed, "Failure sweep did not reach a successful operation")
    }
}

@(test)
operator_failure_sweeps_release_results_and_scratch :: proc(t:^testing.T) {
    tracker:mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    a,_:=parse_integer("-123456789012345678901234567890123456789012345678901234567890")
    b:=from_integer(123456789)
    exponent:=from_integer(8)
    r:=fraction(-12345,67)
    s:=fraction(76543,89)
    defer destroy(&a);defer destroy(&b);defer destroy(&exponent);defer destroy(&r);defer destroy(&s)
    negative_exponent:=fraction(-3,1)
    defer destroy(&negative_exponent)
    for operation in 0..<16 {
        completed:=false
        for limit in 0..<512 {
            state:=Failing{backing=mem.tracking_allocator(&tracker),fail_after=limit}
            allocator:=runtime.Allocator{fail_allocate,&state}
            i:Integer;q:Rational;err:Error
            switch operation {
            case 0:i,err=try_binary(a,b,"+",allocator)
            case 1:i,err=try_binary(a,b,"-",allocator)
            case 2:i,err=try_binary(a,b,"*",allocator)
            case 3:i,err=try_binary(a,b,"/",allocator)
            case 4:i,err=try_binary(a,b,"%%",allocator)
            case 5:i,err=try_binary(a,b,"&~",allocator)
            case 6:i,err=try_binary(a,exponent,">>",allocator)
            case 7:i,err=try_binary(b,exponent,"**",allocator)
            case 8:q,err=try_binary(r,s,"+",allocator)
            case 9:q,err=try_binary(r,s,"-",allocator)
            case 10:q,err=try_binary(r,s,"*",allocator)
            case 11:q,err=try_binary(r,s,"/",allocator)
            case 12:q,err=try_binary(r,s,"%%",allocator)
            case 13:_,err=try_compare(r,s,allocator)
            case 14:q,err=try_binary(r,negative_exponent,"**",allocator)
            case 15:i,err=try_unary(Integer{},"~",allocator)
            }
            if err!=.None{testing.expect(t,is_zero(i)&&is_zero(q))}
            destroy(&i);destroy(&q)
            if len(tracker.allocation_map)!=0 || len(tracker.bad_free_array)!=0 {
                testing.fail_now(t,fmt.tprintf("Operator leak/bad free: operation=%d, allocation failure=%d",operation,limit))
            }
            if !state.failed {testing.expect(t,err==.None);completed=true;break}
            testing.expect(t,err==.Out_Of_Memory)
        }
        testing.expect(t,completed)
    }
}

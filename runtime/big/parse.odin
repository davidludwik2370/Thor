package thor_big

import "base:runtime"
import backend "core:math/big"

@(private="package")
digit_value :: proc "contextless"(c: u8) -> int {
    if '0' <= c && c <= '9' { return int(c-'0') }
    if 'a' <= c && c <= 'z' { return int(c-'a')+10 }
    if 'A' <= c && c <= 'Z' { return int(c-'A')+10 }
    return -1
}

// Underscores are permitted only between valid digits. Validation consumes the
// whole string before any allocation; backend.atoi accepts some trailing junk.
@(private="package")
valid_digits :: proc "contextless"(text: string, base: int) -> (count: int, nonzero: bool, ok: bool) {
    previous_digit := false
    for c in text {
        if c == '_' {
            if !previous_digit { return 0, false, false }
            previous_digit = false
        } else {
            digit := digit_value(u8(c))
            if c > 127 || digit < 0 || digit >= base { return 0, false, false }
            count += 1
            nonzero ||= digit != 0
            previous_digit = true
        }
    }
    return count, nonzero, previous_digit
}

@(private="package")
integer_syntax :: proc "contextless"(text: string) -> (digits: string, base: int, negative, nonzero, ok: bool) {
    digits, base = text, 10
    if len(digits) == 0 { return }
    if digits[0] == '+' || digits[0] == '-' {
        negative = digits[0] == '-'
        digits = digits[1:]
    }
    if len(digits) >= 2 && digits[0] == '0' {
        switch digits[1] {
        case 'b', 'B': base = 2
        case 'o', 'O': base = 8
        case 'd', 'D': base = 10
        case 'z', 'Z': base = 12
        case 'x', 'X': base = 16
        }
        if base != 10 || digits[1] == 'd' || digits[1] == 'D' { digits = digits[2:] }
    }
    _, nonzero, ok = valid_digits(digits, base)
    return
}

@(private="package")
append_digits :: proc(value: ^Integer, text: string, base: int, allocator: runtime.Allocator) -> Error {
    for c in text {
        if c == '_' { continue }
        digit := backend.DIGIT(digit_value(u8(c)))
        if is_zero_integer(value^) && digit == 0 { continue }
        err := backend_error(backend.int_mul_digit(&value._value, &value._value, backend.DIGIT(base), allocator))
        if err != .None { return err }
        err = backend_error(backend.int_add_digit(&value._value, &value._value, digit, allocator))
        if err != .None { return err }
    }
    return .None
}

try_parse_integer :: proc(text: string, allocator := context.allocator) -> (result: Integer, err: Error) {
    digits, base, negative, nonzero, ok := integer_syntax(text)
    if !ok { return {}, .Invalid_Text }
    if !nonzero { return }
    err = append_digits(&result, digits, base, allocator)
    if err != .None { destroy_integer(&result); return }
    if negative { result._value.sign = .Negative }
    return
}

parse_integer :: proc(text: string, allocator := context.allocator, loc := #caller_location) -> (result: Integer, ok: bool) {
    value, err := try_parse_integer(text, allocator)
    if err == .Invalid_Text { return {}, false }
    check(err, loc)
    return value, true
}

@(private="package")
Decimal :: struct {
    whole, fractional, repeating: string,
    negative: bool,
    fractional_count, exponent: int,
}

@(private="package")
decimal_syntax :: proc "contextless"(text: string) -> (syntax: Decimal, err: Error) {
    text := text
    if len(text) == 0 { return {}, .Invalid_Text }
    if text[0] == '+' || text[0] == '-' {
        syntax.negative = text[0] == '-'
        text = text[1:]
    }
    i := 0
    for i < len(text) && (('0' <= text[i] && text[i] <= '9') || text[i] == '_') { i += 1 }
    syntax.whole = text[:i]
    whole_count := 0
    if i > 0 {
        count, _, ok := valid_digits(syntax.whole, 10)
        if !ok { return {}, .Invalid_Text }
        whole_count = count
    }
    has_dot := i < len(text) && text[i] == '.'
    if has_dot {
        i += 1
        start := i
        for i < len(text) && (('0' <= text[i] && text[i] <= '9') || text[i] == '_') { i += 1 }
        syntax.fractional = text[start:i]
        if i > start {
            count, _, ok := valid_digits(syntax.fractional, 10)
            if !ok { return {}, .Invalid_Text }
            syntax.fractional_count = count
        }
    }
    if has_dot && i < len(text) && text[i] == '(' {
        i += 1
        start := i
        for i < len(text) && (('0' <= text[i] && text[i] <= '9') || text[i] == '_') { i += 1 }
        syntax.repeating = text[start:i]
        _, _, ok := valid_digits(syntax.repeating, 10)
        if !ok || i >= len(text) || text[i] != ')' { return {}, .Invalid_Text }
        i += 1
    }
    if whole_count == 0 && syntax.fractional_count == 0 && len(syntax.repeating) == 0 { return {}, .Invalid_Text }
    if i < len(text) && (text[i] == 'e' || text[i] == 'E') {
        i += 1
        negative := false
        if i < len(text) && (text[i] == '+' || text[i] == '-') { negative = text[i] == '-'; i += 1 }
        exponent_digits := text[i:]
        _, _, ok := valid_digits(exponent_digits, 10)
        if !ok { return {}, .Invalid_Text }
        for c in exponent_digits {
            if c == '_' { continue }
            digit := int(c-'0')
            if syntax.exponent > (max(int)-digit)/10 { return {}, .Integer_Overflow }
            syntax.exponent = syntax.exponent*10 + digit
        }
        if negative { syntax.exponent = -syntax.exponent }
        i = len(text)
    }
    if i != len(text) { return {}, .Invalid_Text }
    return
}

// All multiplication destinations are fresh; source ownership is never changed.
@(private="package")
multiply_owned :: proc(a, b: Integer, allocator: runtime.Allocator) -> (result: Integer, err: Error) {
    if is_zero_integer(a) || is_zero_integer(b) { return }
    x, y := a._value, b._value
    err = backend_error(backend.int_mul(&result._value, &x, &y, allocator))
    if err != .None { destroy_integer(&result) }
    return
}

@(private="package")
power_of_ten :: proc(exponent: int, allocator: runtime.Allocator) -> (result: Integer, err: Error) {
    result, err = try_from_integer(1, allocator)
    if err != .None || exponent == 0 { return }
    factor: Integer
    factor, err = try_from_integer(10, allocator)
    defer destroy_integer(&factor)
    remaining := exponent
    for err == .None && remaining > 0 {
        if remaining & 1 != 0 {
            next: Integer
            next, err = multiply_owned(result, factor, allocator)
            destroy_integer(&result)
            result = next
        }
        remaining >>= 1
        if err == .None && remaining > 0 {
            next: Integer
            next, err = multiply_owned(factor, factor, allocator)
            destroy_integer(&factor)
            factor = next
        }
    }
    if err != .None { destroy_integer(&result) }
    return
}

try_parse_fraction :: proc(numerator, denominator: string, allocator := context.allocator) -> (result: Rational, err: Error) {
    // Validate both sides before allocation or deciding a valid denominator is 0.
    _, _, _, n_nonzero, n_ok := integer_syntax(numerator)
    _, _, _, d_nonzero, d_ok := integer_syntax(denominator)
    if !n_ok || !d_ok { return {}, .Invalid_Text }
    if !d_nonzero { return {}, .Division_by_Zero }
    if !n_nonzero { return }
    n, n_err := try_parse_integer(numerator, allocator)
    if n_err != .None { return {}, n_err }
    d, d_err := try_parse_integer(denominator, allocator)
    if d_err != .None { destroy_integer(&n); return {}, d_err }
    return normalize_owned(n, d, allocator)
}

parse_fraction :: proc(numerator, denominator: string, allocator := context.allocator, loc := #caller_location) -> (Rational, bool) {
    result, err := try_parse_fraction(numerator, denominator, allocator)
    if err == .Invalid_Text { return {}, false }
    check(err, loc)
    return result, true
}

try_parse_rational :: proc(text: string, allocator := context.allocator) -> (result: Rational, err: Error) {
    // Fraction strings are signed integers on either side of exactly one slash.
    for c, i in text {
        if c == '/' { return try_parse_fraction(text[:i], text[i+1:], allocator) }
    }
    // Native-style integer prefixes are also accepted for whole-number rationals.
    _, _, _, _, integer_ok := integer_syntax(text)
    if integer_ok {
        n, parse_err := try_parse_integer(text, allocator)
        if parse_err != .None { return {}, parse_err }
        if is_zero_integer(n) { return }
        d, denominator_err := try_from_integer(1, allocator)
        if denominator_err != .None { destroy_integer(&n); return {}, denominator_err }
        return {n, d}, .None
    }
    syntax: Decimal
    syntax, err = decimal_syntax(text)
    if err != .None { return }
    n, d, repeat, scale, next: Integer
    defer destroy_integer(&n)
    defer destroy_integer(&d)
    defer destroy_integer(&repeat)
    defer destroy_integer(&scale)
    defer destroy_integer(&next)
    err = append_digits(&n, syntax.whole, 10, allocator)
    if err != .None { return }
    err = append_digits(&n, syntax.fractional, 10, allocator)
    if err != .None { return }
    repeat_count := 0
    if len(syntax.repeating) > 0 {
        repeat_count, _, _ = valid_digits(syntax.repeating, 10)
        err = append_digits(&repeat, syntax.repeating, 10, allocator)
        if err != .None { return }
    }
    // A syntactically valid zero needs neither digit storage nor a denominator.
    if is_zero_integer(n) && is_zero_integer(repeat) { return }
    d, err = power_of_ten(syntax.fractional_count, allocator)
    if err != .None { return }
    if repeat_count > 0 {
        // A.B(C) = (AB * (10^len(C)-1) + C) / (10^len(B)*(10^len(C)-1)).
        // Build the all-nines factor without a machine-width intermediate.
        err = append_digits(&scale, "9", 10, allocator)
        for _ in 1..<repeat_count {
            if err != .None { break }
            err = append_digits(&scale, "9", 10, allocator)
        }
        if err != .None { return }
        next, err = multiply_owned(n, scale, allocator)
        if err != .None { return }
        destroy_integer(&n)
        n, next = next, {}
        if !is_zero_integer(repeat) {
            err = backend_error(backend.int_add(&n._value, &n._value, &repeat._value, allocator))
            if err != .None { return }
        }
        next, err = multiply_owned(d, scale, allocator)
        if err != .None { return }
        destroy_integer(&d)
        d, next = next, {}
    }
    destroy_integer(&scale)
    if syntax.exponent != 0 {
        scale, err = power_of_ten(abs(syntax.exponent), allocator)
        if err != .None { return }
        if syntax.exponent > 0 {
            next, err = multiply_owned(n, scale, allocator)
            if err != .None { return }
            destroy_integer(&n)
            n, next = next, {}
        } else {
            next, err = multiply_owned(d, scale, allocator)
            if err != .None { return }
            destroy_integer(&d)
            d, next = next, {}
        }
    }
    if syntax.negative { n._value.sign = .Negative }
    result, err = normalize_owned(n, d, allocator)
    n, d = {}, {} // normalize_owned consumes its arguments on success and failure.
    return
}

parse_rational :: proc(text: string, allocator := context.allocator, loc := #caller_location) -> (result: Rational, ok: bool) {
    value, err := try_parse_rational(text, allocator)
    if err == .Invalid_Text { return {}, false }
    check(err, loc)
    return value, true
}

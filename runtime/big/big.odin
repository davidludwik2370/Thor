// Runtime foundation for Thor's Integer and Rational language types.
// Native Odin assignments are shallow: clone explicitly and destroy each owner.
package thor_big

import "base:intrinsics"
import "base:runtime"
import backend "core:math/big"

// Keep malformed text distinct from allocator Invalid_Argument: only a genuine
// syntax failure is allowed to turn into ok=false in the parsing API.
Error :: enum u8 {
    None,
    Invalid_Text,
    Division_by_Zero,
    Out_Of_Memory,
    Invalid_Allocator,
    Integer_Overflow,
    Backend_Failure,
    Invalid_Exponent,
    Invalid_Shift,
}

@(private="package")
backend_error :: proc "contextless"(err: backend.Error) -> Error {
    #partial switch err {
    case .None: return .None
    case .Out_Of_Memory: return .Out_Of_Memory
    case .Invalid_Pointer, .Invalid_Argument, .Mode_Not_Implemented: return .Invalid_Allocator
    case .Division_by_Zero: return .Division_by_Zero
    case .Integer_Overflow, .Integer_Underflow, .Buffer_Overflow: return .Integer_Overflow
    case: return .Backend_Failure
    }
}

@(private="package")
allocation_error :: proc "contextless"(err: runtime.Allocator_Error) -> Error {
    return backend_error(cast(backend.Error)err)
}

// These fields are compiler/runtime implementation details, not Thor properties.
// All-zero storage represents zero without allocating any digits.
Integer :: struct { _value: backend.Int }
Rational :: struct { _numerator, _denominator: Integer }

is_zero_integer :: proc "contextless"(value: Integer) -> bool { return value._value.used == 0 }
is_zero_rational :: proc "contextless"(value: Rational) -> bool { return is_zero_integer(value._numerator) }
is_zero :: proc{is_zero_integer, is_zero_rational}

// Use the allocator recorded in the dynamic array, not the current allocator.
// Also reset the actual value; backend.destroy does not provide that guarantee.
destroy_integer :: proc(value: ^Integer) {
    delete(value._value.digit)
    value^ = {}
}
destroy_rational :: proc(value: ^Rational) {
    destroy_integer(&value._numerator)
    destroy_integer(&value._denominator)
    value^ = {}
}
destroy :: proc{destroy_integer, destroy_rational}

@(private="package")
release_raw :: proc(value: ^backend.Int) {
    delete(value.digit)
    value^ = {}
}

@(private="package")
check :: proc(err: Error, loc: runtime.Source_Code_Location) {
    #partial switch err {
    case .None:
    case .Division_by_Zero: panic("Thor big-number divisor or Rational denominator must not be zero", loc)
    case .Invalid_Exponent: panic("Integer powers need nonnegative whole exponents; Rational powers need whole exponents", loc)
    case .Invalid_Shift: panic("Integer shift counts must be nonnegative", loc)
    case .Out_Of_Memory: panic("Thor big-number allocation failed", loc)
    case .Invalid_Allocator: panic("Thor big-number allocator failed", loc)
    case .Integer_Overflow: panic("Thor big-number size is too large", loc)
    case: panic("Thor big-number operation failed", loc)
    }
}

try_from_integer :: proc(value: $T, allocator := context.allocator) -> (result: Integer, err: Error)
    where T == Integer || intrinsics.type_is_integer(T) {
    when T == Integer { return try_clone_integer(value, allocator) }
    else {
        if value == 0 { return }
        err = backend_error(backend.int_set_from_integer(&result._value, value, allocator=allocator))
        if err != .None { destroy_integer(&result) }
        return
    }
}

from_integer :: proc(value: $T, allocator := context.allocator, loc := #caller_location) -> Integer
    where T == Integer || intrinsics.type_is_integer(T) {
    result, err := try_from_integer(value, allocator)
    check(err, loc)
    return result
}

try_clone_integer :: proc(value: Integer, allocator := context.allocator) -> (result: Integer, err: Error) {
    if is_zero_integer(value) { return }
    source := value._value
    err = backend_error(backend.int_copy(&result._value, &source, minimize=true, allocator=allocator))
    if err != .None { destroy_integer(&result) }
    return
}

try_clone_rational :: proc(value: Rational, allocator := context.allocator) -> (result: Rational, err: Error) {
    if is_zero_rational(value) { return }
    result._numerator, err = try_clone_integer(value._numerator, allocator)
    if err == .None { result._denominator, err = try_clone_integer(value._denominator, allocator) }
    if err != .None { destroy_rational(&result) }
    return
}
try_clone :: proc{try_clone_integer, try_clone_rational}

clone_integer :: proc(value: Integer, allocator := context.allocator, loc := #caller_location) -> Integer {
    result, err := try_clone_integer(value, allocator)
    check(err, loc)
    return result
}
clone_rational :: proc(value: Rational, allocator := context.allocator, loc := #caller_location) -> Rational {
    result, err := try_clone_rational(value, allocator)
    check(err, loc)
    return result
}
clone :: proc{clone_integer, clone_rational}

// Consumes both parts, including on failure. Callers must pass independent owners.
@(private="package")
normalize_owned :: proc(numerator, denominator: Integer, allocator: runtime.Allocator) -> (result: Rational, err: Error) {
    context.allocator = allocator
    n, d := numerator, denominator
    defer destroy_integer(&n)
    defer destroy_integer(&d)
    if is_zero_integer(d) { return {}, .Division_by_Zero }
    if is_zero_integer(n) { return }
    if d._value.sign == .Negative {
        d._value.sign = .Zero_or_Positive
        n._value.sign = .Zero_or_Positive if n._value.sign == .Negative else .Negative
    }
    if !is_one(d) {
        divisor, reduced_n, reduced_d: backend.Int
        defer release_raw(&divisor)
        defer release_raw(&reduced_n)
        defer release_raw(&reduced_d)
        err = backend_error(backend.int_gcd(&divisor, &n._value, &d._value, allocator))
        if err != .None { return {}, err }
        if !(divisor.used == 1 && divisor.digit[0] == 1) {
            err = backend_error(backend.int_div(&reduced_n, &n._value, &divisor, allocator))
            if err != .None { return {}, err }
            err = backend_error(backend.int_div(&reduced_d, &d._value, &divisor, allocator))
            if err != .None { return {}, err }
            destroy_integer(&n)
            destroy_integer(&d)
            n._value, d._value = reduced_n, reduced_d
            reduced_n, reduced_d = {}, {}
        }
    }
    result = {n, d}
    n, d = {}, {}
    return
}

@(private="package")
is_one :: proc "contextless"(value: Integer) -> bool {
    return value._value.used == 1 && value._value.sign == .Zero_or_Positive && value._value.digit[0] == 1
}

try_fraction :: proc(numerator: $N, denominator: $D, allocator := context.allocator) -> (result: Rational, err: Error)
    where (N == Integer || intrinsics.type_is_integer(N)) && (D == Integer || intrinsics.type_is_integer(D)) {
    // Check the denominator first: zero must fail even when the numerator is zero.
    if zero_input(denominator) { return {}, .Division_by_Zero }
    if zero_input(numerator) { return }
    d: Integer
    d, err = try_from_integer(denominator, allocator)
    if err != .None { return }
    if is_zero_integer(d) { return {}, .Division_by_Zero }
    n: Integer
    n, err = try_from_integer(numerator, allocator)
    if err != .None { destroy_integer(&d); return }
    return normalize_owned(n, d, allocator)
}

@(private="package")
zero_input :: proc "contextless"(value: $T) -> bool
    where T == Integer || intrinsics.type_is_integer(T) {
    when T == Integer { return is_zero_integer(value) }
    else { return value == 0 }
}

fraction :: proc(numerator: $N, denominator: $D, allocator := context.allocator, loc := #caller_location) -> Rational
    where (N == Integer || intrinsics.type_is_integer(N)) && (D == Integer || intrinsics.type_is_integer(D)) {
    result, err := try_fraction(numerator, denominator, allocator)
    check(err, loc)
    return result
}

try_rational_from_integer :: proc(value: $T, allocator := context.allocator) -> (result: Rational, err: Error)
    where T == Integer || intrinsics.type_is_integer(T) {
    n: Integer
    n, err = try_from_integer(value, allocator)
    if err != .None || is_zero_integer(n) { return }
    d: Integer
    d, err = try_from_integer(1, allocator)
    if err != .None { destroy_integer(&n); return }
    return {n, d}, .None
}
rational_from_integer :: proc(value: $T, allocator := context.allocator, loc := #caller_location) -> Rational
    where T == Integer || intrinsics.type_is_integer(T) {
    result, err := try_rational_from_integer(value, allocator)
    check(err, loc)
    return result
}

// Getters return independent owners, including through Thor's computed
// properties. A zero denominator sentinel is never visible to the caller.
numerator :: proc(value: Rational, allocator := context.allocator, loc := #caller_location) -> Integer {
    return clone_integer(value._numerator, allocator, loc)
}
denominator :: proc(value: Rational, allocator := context.allocator, loc := #caller_location) -> Integer {
    if is_zero_rational(value) { return from_integer(1, allocator, loc) }
    return clone_integer(value._denominator, allocator, loc)
}

// Construct before destroying, so failure and references to target's own parts
// are safe. Native shallow aliases still require explicit ownership discipline.
try_set_fraction :: proc(target: ^Rational, numerator: $N, denominator: $D, allocator := context.allocator) -> Error
    where (N == Integer || intrinsics.type_is_integer(N)) && (D == Integer || intrinsics.type_is_integer(D)) {
    result, err := try_fraction(numerator, denominator, allocator)
    if err != .None { return err }
    destroy_rational(target)
    target^ = result
    return .None
}
set_fraction :: proc(target: ^Rational, numerator: $N, denominator: $D, allocator := context.allocator, loc := #caller_location)
    where (N == Integer || intrinsics.type_is_integer(N)) && (D == Integer || intrinsics.type_is_integer(D)) {
    check(try_set_fraction(target, numerator, denominator, allocator), loc)
}

// Numerical equality, without allocating or modifying the borrowed inputs.
equal_integer :: proc "contextless"(a, b: Integer) -> bool {
    if a._value.used != b._value.used { return false }
    if is_zero_integer(a) { return true }
    if a._value.sign != b._value.sign { return false }
    for i in 0..<a._value.used { if a._value.digit[i] != b._value.digit[i] { return false } }
    return true
}
equal_rational :: proc "contextless"(a, b: Rational) -> bool {
    if is_zero_rational(a) || is_zero_rational(b) { return is_zero_rational(a) && is_zero_rational(b) }
    return equal_integer(a._numerator, b._numerator) && equal_integer(a._denominator, b._denominator)
}
equal :: proc{equal_integer, equal_rational}

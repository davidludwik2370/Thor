package thor_big

import "base:runtime"
import backend "core:math/big"

@(private="package")
owned_text :: proc(text: string, allocator: runtime.Allocator) -> (string, Error) {
    data, err := make([]u8, len(text), allocator)
    if err != .None || len(data) != len(text) {
        delete(data, allocator)
        return "", allocation_error(err) if err != .None else .Out_Of_Memory
    }
    copy(data, text)
    return string(data), .None
}

// Decimal output is a foundation/debugging API, not yet fmt/f-string integration.
// Every successful string owns an exact-sized allocation, including "0".
try_to_string_integer :: proc(value: Integer, allocator := context.allocator) -> (result: string, err: Error) {
    if is_zero_integer(value) { return owned_text("0", allocator) }
    context.allocator = allocator
    source := value._value
    negative := source.sign == .Negative
    source.sign = .Zero_or_Positive
    // A full limb fits in this many decimal digits (the backend uses at most
    // size_of(DIGIT)*8 bits). Avoid radix_size's integer-log/power scratch path:
    // its power helper can leak scratch if a later allocation fails.
    digits_per_limb :: (size_of(backend.DIGIT)*8*30103+99999)/100000
    if source.used > (max(int)-1)/digits_per_limb { return "", .Integer_Overflow }
    size := source.used*digits_per_limb+1
    buffer, alloc_err := make([]u8, size, allocator)
    if alloc_err != .None || len(buffer) != size {
        delete(buffer, allocator)
        return "", allocation_error(alloc_err) if alloc_err != .None else .Out_Of_Memory
    }
    defer delete(buffer, allocator)
    written: int
    backend_err: backend.Error
    written, backend_err = backend.int_itoa_raw(&source, 10, buffer, size)
    err = backend_error(backend_err)
    if err != .None { return }
    // The backend's negative, multi-chunk path can emit an extra leading zero.
    // Format the magnitude and canonicalize padding before adding our own sign.
    start := 0
    for start < written-1 && buffer[start] == '0' { start += 1 }
    sign_size := 1 if negative else 0
    output_size := written-start+sign_size
    data, output_err := make([]u8, output_size, allocator)
    if output_err != .None || len(data) != output_size {
        delete(data, allocator)
        return "", allocation_error(output_err) if output_err != .None else .Out_Of_Memory
    }
    if negative { data[0] = '-' }
    copy(data[sign_size:], buffer[start:written])
    return string(data), .None
}

try_to_string_rational :: proc(value: Rational, allocator := context.allocator) -> (result: string, err: Error) {
    if is_zero_rational(value) { return owned_text("0/1", allocator) }
    n, n_err := try_to_string_integer(value._numerator, allocator)
    if n_err != .None { return "", n_err }
    defer delete(n, allocator)
    d, d_err := try_to_string_integer(value._denominator, allocator)
    if d_err != .None { return "", d_err }
    defer delete(d, allocator)
    if len(n) > max(int)-len(d)-1 { return "", .Integer_Overflow }
    data, alloc_err := make([]u8, len(n)+len(d)+1, allocator)
    if alloc_err != .None || len(data) != len(n)+len(d)+1 {
        delete(data, allocator)
        return "", allocation_error(alloc_err) if alloc_err != .None else .Out_Of_Memory
    }
    copy(data, n)
    data[len(n)] = '/'
    copy(data[len(n)+1:], d)
    return string(data), .None
}
try_to_string :: proc{try_to_string_integer, try_to_string_rational}

to_string_integer :: proc(value: Integer, allocator := context.allocator, loc := #caller_location) -> string {
    result, err := try_to_string_integer(value, allocator)
    check(err, loc)
    return result
}
to_string_rational :: proc(value: Rational, allocator := context.allocator, loc := #caller_location) -> string {
    result, err := try_to_string_rational(value, allocator)
    check(err, loc)
    return result
}
to_string :: proc{to_string_integer, to_string_rational}

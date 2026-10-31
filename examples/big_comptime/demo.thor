// Run: thor run examples/big_comptime -debug
// Constants borrow read-only data; final named runtime owners are deleted.
package main

import "core:fmt"
import "thor:big"
import "thor:pure"

// These numbers never pass through a native machine integer or floating point.
LARGE :: Integer(1234567890123456789012345678901234567890)
NEXT :: LARGE * 2 + 1
THIRD :: Rational(1, 3)
EXACT :: THIRD + 0.25

// A normal numeric parameter is borrowed. Arithmetic creates a new owner.
power :: fn(base: Integer, count: int) -> Integer {
    result := Integer(1)
    defer delete(result)
    for _ in 0..<count { result *= base }
    return result
}

// The result is calculated once during compilation and stored in rodata.
POWER :: #run power(Integer(22), 20)

main :: proc() {
    // Direct copies allocate independent storage. Replacing one preserves others.
    value := LARGE
    defer delete(value)
    value += 1
    assert(value != LARGE)
    assert(NEXT == LARGE * 2 + 1)
    assert(EXACT == Rational(7, 12))

    // This #run result is reconstructed as an owned runtime number each time.
    computed := #run power(Integer(22), 20)
    defer delete(computed)
    assert(computed == POWER)

    // Ordinary runtime arguments still execute fn at runtime with scratch storage.
    count := 3
    runtime := power(value, count)
    defer delete(runtime)
    assert(runtime == value ** 3)

    // General formatting integration follows in stage five. Explicit conversion
    // already produces a normal caller-owned string.
    text := big.to_string(computed)
    defer delete(text)
    fmt.println("22 to the twentieth power:", text)
    fmt.println("Compile-time arbitrary-precision checks passed")
}

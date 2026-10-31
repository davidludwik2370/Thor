// Run: ./thor run examples/big_runtime --track-allocations=basic
//
// Stage 1 is the mathematical runtime foundation. These are ordinary package
// calls. See examples/big_numbers for the stage-two built-in types, unquoted
// literals, computed properties and ownership. Operators and #run follow later.
package main

import "core:fmt"
import "thor:big"

show :: proc(label: string, value: $T) {
    // Each returned string is owned. Release it after printing.
    text := big.to_string(value)
    defer delete(text)
    fmt.println(label, text)
}

main :: proc() {
    // An arbitrary-length number does not pass through a machine int or float.
    large, ok := big.parse_integer("123456789012345678901234567890123456789012345678901234567890")
    assert(ok)
    defer big.destroy(&large)
    show("Integer:", large)

    // Zero-initialized runtime values have no allocation. Rational zero still
    // behaves as 0/1, even though it does not store an allocated denominator.
    zero: big.Rational
    assert(big.is_zero(zero))
    show("Zero:", zero)

    // Preserve finite decimals exactly; 0.1 means 1/10, with no float rounding.
    finite, finite_ok := big.parse_rational("0.1")
    assert(finite_ok)
    defer big.destroy(&finite)
    show("Finite:", finite)

    // Parentheses identify the repeating block. 0.166666... is exactly 1/6.
    repeating, repeating_ok := big.parse_rational("0.1(6)")
    assert(repeating_ok)
    defer big.destroy(&repeating)
    show("Repeating:", repeating)

    // Reduce fractions and put any negative sign on the numerator.
    fraction := big.fraction(numerator=6, denominator=-8)
    defer big.destroy(&fraction)
    show("Normalized:", fraction)

    // Stage 1 uses native Odin ownership. Use clone, not a shallow assignment,
    // for another independently owned runtime number.
    independent := big.clone(fraction)
    defer big.destroy(&independent)
    big.set_fraction(&fraction, numerator=22, denominator=13)
    show("Updated:", fraction)
    show("Independent copy:", independent)

    // Component getters also create independent owners. Reading components
    // through read-only dot properties is part of the next compiler stage.
    numerator := big.numerator(fraction)
    denominator := big.denominator(fraction)
    defer big.destroy(&numerator)
    defer big.destroy(&denominator)
    show("Numerator:", numerator)
    show("Denominator:", denominator)

    // Invalid text gives false and a safe zero result, without partial storage.
    invalid, invalid_ok := big.parse_rational("12.34oops")
    assert(!invalid_ok && big.is_zero(invalid))
    big.destroy(&invalid)

    // Checked replacement is atomic: a bad denominator leaves the old number.
    err := big.try_set_fraction(&fraction, 1, 0)
    assert(err == .Division_by_Zero)
    expected := big.fraction(22, 13)
    defer big.destroy(&expected)
    assert(big.equal(fraction, expected))

    fmt.println("All big-number runtime examples passed.")
}

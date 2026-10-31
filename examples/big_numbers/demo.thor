// Run: thor run examples/big_numbers --track-allocations=basic
//
// Stage 2: language types, exact construction, and direct value ownership.
// Arithmetic/operators and compile-time big constants follow in stages 3 and 4.
package main

import "core:fmt"
import "thor:big" // Helpers; Integer/Rational themselves need no import.

// Parameters borrow their inputs. This procedure only owns its formatted text.
show :: proc(label: string, value: $T) {
    text := big.to_string(value)
    defer delete(text)
    fmt.println(label, text)
}

// Returning a borrowed number establishes an independent result, even generically.
identity :: proc(value: $T) -> T {
    return value
}

// A named local is still our responsibility. Returning it makes a copy before
// the defer destroys our local owner. A fresh constructor can be returned directly.
make_number :: proc() -> Integer {
    local := Integer(123)
    defer delete(local)
    return local
}

main :: proc() {
    // No quotes, no machine-integer limit, and no floating-point intermediary.
    // Ordinary inferred/native numbers retain Odin's normal overflow behavior.
    enormous: Integer = 123456789012345678901234567890123456789012345678901234567890
    exact: Rational = 0.1000000000000000000000000000000000000001
    show("Integer:", enormous)
    show("Exact decimal:", exact)
    delete(enormous)
    delete(exact)

    // Zero-initialized storage allocates nothing. Rational's logical zero is 0/1.
    zero_i: Integer
    zero_r: Rational
    show("Zero Integer:", zero_i)
    show("Zero Rational:", zero_r)
    delete(zero_i)
    delete(zero_r)

    // Numeric construction returns one owner. Text parsing returns owner/status.
    // Repeating decimals are written inside strings, using parentheses.
    fraction := Rational(numerator=22, denominator=13)
    repeating, ok := Rational("0.1(6)")
    assert(ok)
    show("Fraction:", fraction)
    show("Repeating:", repeating) // Exactly 1/6.
    bad, bad_ok := Integer("not a number")
    assert(!bad_ok && big.is_zero(bad))
    delete(bad)
    delete(repeating)

    // Read-only computed properties return independent Integers. They do not
    // expose the representation, and need no import to access. Named results
    // must be deleted; anonymous numeric results are released after known calls.
    numerator := fraction.numerator
    denominator := fraction.denominator
    big.set_fraction(&fraction, 1, 2)
    show("Original numerator:", numerator)     // Still 22.
    show("Original denominator:", denominator) // Still 13.
    show("Replaced fraction:", fraction)        // 1/2.
    show("Anonymous component:", Rational(7, 9).numerator)
    // fraction.numerator = 3 // Error: this property is read-only.
    delete(numerator)
    delete(denominator)
    delete(fraction)

    // Direct copies clone. Replacement frees the old destination after all RHS
    // values have been preserved, so self-assignment and swaps are safe.
    a := Integer(10)
    b := a
    a = Integer(20)
    show("Independent copy:", b) // Still 10.
    a, b = b, a
    show("Swapped a:", a) // 10.
    show("Swapped b:", b) // 20.
    a = a
    a = switch {
        case false: Integer(99)
        case: break // Preserve the old owner; nothing is allocated or deleted.
    }
    copy := identity(a)
    delete(a)
    show("Returned copy:", copy)
    delete(copy)
    delete(b)
    created := make_number()
    show("Local return:", created)
    delete(created)

    // Individual numeric fields/elements also copy independently.
    // A WHOLE aggregate copy is shallow: it does not create another owner.
    Record :: struct { value: Integer }
    source := Integer(42)
    record := Record{value=source}
    alias := record // Shares record.value's allocation. Never delete both.
    member := record.value
    delete(source)
    delete(record.value)
    _ = alias // Its contained header is invalid after the deletion above.
    show("Direct member copy:", member) // Still 42.
    delete(member)

    fmt.println("All Integer/Rational stage-two examples passed.")
}

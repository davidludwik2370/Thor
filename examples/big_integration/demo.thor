// Run: thor run examples/big_integration -debug
// This intentionally combines the language features with the shared library.
package main

import "core:fmt"
import "thor:basic"
import "thor:format"

// The inferred receiver may be Rational, ^Rational, or another record with an
// Integer numerator field. Actual Rational components are read-only copies.
component :: proc(value: $T) -> Integer { return value.numerator }

main :: proc() {
    basic.print("Exact numbers and ordinary literal coercion")

    value: Integer = 1
    defer delete(value)
    value += 2
    assert(value == 3)

    // A named array owns its numeric elements. Its outer storage is on the
    // stack; only the elements need deletion. Whole array copies are shallow.
    values := [value, Integer(7), Integer(-2)]
    defer for &item in values { delete(item) }

    total := basic.sum(values[:])
    defer delete(total)
    largest, found := max(..values[:])
    defer delete(largest)
    assert(found && total == 8 && largest == 7)
    assert(value in [1, 3, 8] && 7 in values)

    // Canonical fractions and independently owned components need no float.
    ratio := Rational(22, 13)
    defer delete(ratio)
    numerator := component(&ratio)
    defer delete(numerator)
    assert(numerator == 22)

    message := f"sum: {total}, largest: {largest}, fraction: {ratio}, grouped: {Integer(1234567890):,}"
    defer delete(message)
    basic.print(message)

    // basic.table uses logical numeric text and numeric right alignment.
    Row :: struct { name: string, amount: Integer, ratio: Rational }
    rows := [Row{"example", value, ratio}]
    defer delete(rows[0].amount)
    defer delete(rows[0].ratio)
    table := basic.table(rows[:])
    defer delete(table)
    basic.print(table, end="")

    // Native fmt registration is explicit and shares its usual caller-owned
    // registry with any custom application formatters.
    registry: map[typeid]fmt.User_Formatter
    format.add_big_formatters(&registry)
    fmt.set_user_formatters(&registry)
    defer format.destroy_formatter_registry(&registry)
    fmt.println("Native fmt:", value, ratio)
    basic.print("All numeric integration checks passed")
}

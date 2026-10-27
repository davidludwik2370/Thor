/*
    Thor min/max over arrays and slices.
    Run: ./thor run examples/extrema

    These forms add to Odin's existing scalar and type-limit builtins.
    No library import is needed, and the reductions allocate no heap storage.
*/
package main

import "core:fmt"

// Required constant expressions use Thor's compile-time evaluator.
CAPACITY :: max(**[3]int{2, 8, 3})

main :: proc() {
    // ** expands a fixed array. Its min/max result is a single element value.
    fixed := [?]int{3, 1, 2}
    assert(max(**fixed) == 3 && min(**fixed) == 1)
    assert(min(**[1]int{7}) == 7)

    // .. spreads a slice. The second result is an optional presence flag.
    values := fixed[:]
    lowest, present := min(..values)
    assert(lowest == 1 && present)
    highest := max(..values) // Ignore the flag when nonemptiness is known.
    assert(highest == 3)

    // Empty slices return the element type's zero value with false.
    empty: []int
    missing, found := max(..empty)
    assert(missing == 0 && !found)
    fallback := min(..empty) or_else 99
    assert(fallback == 99)

    // Starting scalar values are candidates too, so this call is never empty.
    seeded, seeded_ok := max(-3, ..empty)
    assert(seeded == -3 && seeded_ok)
    assert(max(0, ..[]int{-5, -2}) == 0)
    assert(max(..[]int{-5, -2}) == -2)

    // Untyped scalar literals adapt to the sequence's element type.
    floats := []f32{1.5, 3}
    fractional := min(2, ..floats)
    assert(type_of(fractional) == f32 && fractional == 1.5)

    // String results borrow an input string; the reduction creates no copies.
    words := []string{"pear", "apple", "plum"}
    assert(min(..words) == "apple")

    // Source expressions run once, in their original order.
    order := 0
    result, ok := max(mark(&order, 1), mark(&order, 2), ..supply(&order, values))
    assert(result == 3 && ok && order == 123)

    // Existing Odin forms retain their usual meaning.
    assert(max(1, 2, 3) == 3 && min(i8) == -128)
    storage: [CAPACITY]int
    assert(len(storage) == 8)

    // Invalid examples:
    // _ = min(**[0]int{}) // An empty fixed-array expansion is a compile error.
    // _ = min(..fixed)   // Use a slice: ..fixed[:].
    // n: int; _ = max(n, ..floats) // Typed operands must match.

    fmt.println("Spread min/max examples passed.")
}

mark :: proc(order: ^int, value: int) -> int {
    order^ = order^*10 + value
    return value
}
supply :: proc(order: ^int, values: []int) -> []int {
    order^ = order^*10 + 3
    return values
}

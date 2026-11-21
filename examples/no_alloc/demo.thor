#+vet no_alloc
package main

// This file uses stack storage only. Verification includes helper calls.
add_to_each :: proc(values: []int, amount: int) {
    for &value in values {
        value += amount
    }
}

@(no_alloc)
sum :: fn(values: []int) -> (result: int) {
    for value in values {
        result += value
    }
    return
}

main :: proc() {
    values := [1, 2, 3]
    add_to_each(values[:], 2)
    result := sum(values[:])
    _ = result

    // Each example below would be a compile error:
    // allocated := make([]int, 10)
    // append(&growing, 1)
    // text := f"{result}"
}

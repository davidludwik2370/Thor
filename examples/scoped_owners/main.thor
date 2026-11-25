package main

import "core:fmt"
import "core:slice"

owned_copy :: proc() -> []int {
    working~ := make([]int, 3)
    working[0], working[1], working[2] = 1, 2, 3
    return slice.clone(working)
    // return working // Error: scoped storage cannot escape through a return.
}

main :: proc() {
    values~ :> owned_copy()
    growing ~:= make([dynamic]int)
    for value in values { append(&growing, value * 2) }
    squares ~:> [x*x for x in 0..<5]
    fmt.println(values, growing[:], squares)
    // Each owner is shallowly cleaned at this closing brace.
}

package main

import "core:fmt" / println

double :: fn(value: int) -> int {
    result := value * 2
    return result
}

main :: proc() {
    // Set a breakpoint here, then press Cmd+Shift+R (F5 also works).
    numbers := [3, 6, 9]
    last := numbers[-1]

    // Step Into can enter double through the interpolation's generated helper.
    message := f"Last: {last}; doubled: {double(last)}"
    defer delete(message)

    println(message)
}

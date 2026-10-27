package main

import "core:math"

rounded_in_odin :: proc(value: f64) -> int {
    return int(math.round(value))
}

// A foreign calling convention is different from a foreign implementation:
// this procedure has an Odin body that Thor can inspect and check.
c_square :: proc "c" (value: int) -> int {
    return value * value
}

Report :: struct {
    label: string,
    values: []int,
}

make_report :: proc(label: string, value: int) -> Report {
    values := make([]int, 2)
    values[0] = value
    values[1] = value + 1
    return Report{label, values}
}

// Native-port acceptance example; the main language tour remains demo/.
package main

import "core:fmt" / println
import "base:intrinsics"
import "core:slice"
import "core:time"

SEPARATOR :: "-" * 16
VALUES :: [1, 2] ++ [3, 4]

Snapshot :: struct { values: []int, label: string }

snapshot :: fn(input: []int) -> Snapshot {
    values := make([]int, len(input))
    copy(values, input)
    return {values, f"stored {len(input)} values"}
}

independent :: fn(value: $T) -> T { return value }
square :: fn(value: $T) -> T { return value * value }
square_root :: fn(value: f64) -> f64 { return intrinsics.sqrt(value) }
seconds :: fn(value: time.Duration) -> f64 { return time.duration_seconds(value) }
reversed_end :: fn(value: int) -> int {
    local := [value, value + 1]
    slice.reverse(local[:])
    return local[0]
}

checked_value :: proc(counter: ^int) -> (int, bool) {
    counter^ += 1
    return 7, true
}

main :: proc() {
    original :> [10, 20, 30]
    copied := original
    copied[-1] = 40
    if original[-1] != 30 || copied[-1] != 40 { panic("inline copy") }
    if 2 not_in VALUES { panic("membership") }

    saved := snapshot(original[:])
    defer delete(saved.values)
    defer delete(saved.label)
    independent_values := independent(saved.values)
    defer delete(independent_values)
    independent_values[0] = 99
    if saved.values[0] != 10 || saved.label != "stored 3 values" { panic("pure ownership") }
    number: f64 = 9
    if square_root(number) != 3 || square(number) != 81 { panic("pure intrinsics") }
    duration := time.Second
    seed := 3
    if seconds(duration) != 1 || reversed_end(seed) != 4 { panic("checked SDK calls") }

    calls := 0
    value := checked_value(&calls) or_panic
    if value != 7 || calls != 1 { panic("optional evaluation") }

    width := 5
    probability: f64 = 0.125
    base, exponent: f64 = 2, -3
    if base ** exponent != probability { panic("numeric power") }

    sum := 0
    for item, (index: u8) in copied { sum += item + int(index) }
    description := switch sum {
        case 73: "typed loop and switch"
        case: unreachable()
    }
    first, second := switch { case true: 1, 2; case: 0, 0 }
    first, second = switch { case true: second, first; case: break }
    if first != 2 || second != 1 { panic("multiple results") }

    selected := set[int]{1, 2, 2}
    defer delete(selected)
    selected += 3
    if len(selected) != 3 || 3 not_in selected { panic("set operations") }

    println(SEPARATOR)
    println(f"value {value:0{width}d}; chance {probability:.2%}")
    println(original, copied)
    println(description, first, second)
}

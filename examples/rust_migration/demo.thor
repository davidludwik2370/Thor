// Initial native-port acceptance example; the main language tour remains demo/.
package main

import "core:fmt" / println

SEPARATOR :: "-" * 16
VALUES :: [1, 2] ++ [3, 4]

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

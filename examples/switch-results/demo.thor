// Run: thor run examples/switch-results
package main

import "core:fmt"

Degrees :: distinct f64
Kind :: enum {ordinary, special}

Record :: struct {
    side: f64,
    angle: Degrees,
    kind: Kind,
}

// Both selector forms can return several values. Each result keeps the type
// of its own destination: here f64, Degrees, and Kind, respectively.
update :: proc(using record: ^Record, special: bool) {
    side, angle, kind = switch {
        case special: 3, 60, .special
        case:         break
    }
}

// A procedure's multiple returns can also supply one complete result arm.
pair :: proc() -> (int, string) {
    return 7, "seven"
}

choose :: proc(selector: int) -> (int, string) {
    return switch selector {
        case 7: pair()
        case:   0, "other"
    }
}

// Compile-time switches retain constant bindings and ignore inactive arms.
NUMBER, TEXT :: #switch {
    case true: 7, "seven"
    case:      undefined_inactive_value, undefined_inactive_value
}

main :: proc() {
    record := Record{1, 45, .ordinary}
    update(&record, false)
    assert(record.side == 1 && record.angle == 45 && record.kind == .ordinary)

    update(&record, true)
    assert(record.side == 3 && record.angle == 60 && record.kind == .special)

    // All selected RHS expressions run before any destination is written.
    a, b := 1, 2
    a, b = switch {
        case true: b, a
        case:      a, b
    }
    assert(a == 2 && b == 1)

    // break cancels just this assignment. The surrounding loop continues.
    visits := 0
    for i in 0..<3 {
        a, b = switch i {
            case 1: break
            case:   a + 1, b + 1
        }
        visits += 1
    }
    assert(a == 4 && b == 3 && visits == 3)

    number, text := choose(7)
    assert(number == NUMBER && text == TEXT)

    // A panic arm terminates instead of producing values. Ordinary void calls
    // cannot stand in for result expressions.
    number, text = switch {
        case number == 7: 8, "eight"
        case:            panic("unexpected number")
    }
    assert(number == 8 && text == "eight")

    // Named allocated results belong to the caller. Anonymous Thor allocations
    // discarded into _ are freed automatically.
    message, _ := switch {
        case true: f"number {number}", set[int]{1, 2}
        case:      f"other {number}", set[int]{3}
    }
    defer delete(message)
    assert(message == "number 8")

    // Invalid: x := switch {case true: 1; case: break}
    // Invalid: return switch {case true: 1, "ok"; case: break}
    // Invalid: a, b = switch {case true: 1, 2} // a default is still required

    fmt.println("Multiple switch results and assignment skipping passed.")
}

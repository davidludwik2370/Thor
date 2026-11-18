package main

import "core:fmt"
import "core:sys/info"

Mode :: enum { Fast, Safe }
Options :: struct { mode: Mode, attempts: int }

load :: proc(available: bool) -> (int, string, Options, bool) {
    return 42, "loaded", {.Fast, 2}, available
}

default_text :: proc() -> string {
    fmt.println("Evaluating the fallback")
    return "default"
}

choose :: proc() -> (int, string, Options) {
    return load(false) or_else (7, "fallback", {.Safe, 1})
}

main :: proc() {
    _, cores :> info.cpu_core_count() or_else (_, 1)
    fmt.println("Logical cores:", cores)

    // The call executes once; default_text is evaluated only on failure.
    number, text, options :> load(false) or_else (7, default_text(), {.Safe, 1})
    if number != 7 || text != "default" || options.mode != .Safe {
        panic("Incorrect defaults")
    }
    fmt.println(number, text, options)

    _, kept, _ := load(true) or_else (_, default_text(), _)
    if kept != "loaded" { panic("Unexpected fallback evaluation") }
    fmt.println(kept)

    // Existing destinations support the same matched discard positions.
    _, kept, _ = load(false) or_else (_, "replacement", _)
    fmt.println(kept)
    fmt.println(choose())
}

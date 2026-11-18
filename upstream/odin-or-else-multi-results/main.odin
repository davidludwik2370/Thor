package main

import "core:fmt"

read :: proc() -> (int, string, bool) { return 0, "", false }
fallback :: proc() -> (int, string) { return 7, "ok" }

main :: proc() {
    actual, text := read() or_else fallback()
    expected, expected_text := read() or_else expand_values(compress_values(fallback()))
    fmt.printf("direct: %d %q\n", actual, text)
    fmt.printf("packed: %d %q\n", expected, expected_text)
    if expected != 7 || expected_text != "ok" { panic("Workaround failed") }
    if actual != expected || text != expected_text { panic("Direct fallback lost results") }
}

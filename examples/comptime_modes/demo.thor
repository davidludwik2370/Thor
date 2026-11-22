package main

import "core:fmt"

// Try: thor run examples/comptime_modes --comptime=required
// Then repeat with normal, aggressive, and override (also --cache-report).
square :: proc(x: int) -> int { return x*x }
square_fn :: fn(x: int) -> int { return x*x }
barrier :: fn(x: int) -> int { return #runtime (x*x) }

SIZE :: square_fn(3) // Required even with --comptime=required.
EXPLICIT :: #run square(4) // Explicit in every mode.
// In aggressive/override, this also works without #run:
// AUTO_CONSTANT :: square(5)

main :: proc() {
    a := square(7) // Automatic ordinary-proc execution in aggressive/override.
    b := square_fn(7) // Automatic fn execution in normal/aggressive/override.
    c := #runtime square_fn(7) // Automatic only in override.
    d := barrier(7) // A transitive barrier, also ignored only in override.
    values: [SIZE]int
    if a != 49 || b != 49 || c != 49 || d != 49 || len(values) != 9 || EXPLICIT != 16 {
        panic("compile-time policy changed the result")
    }
    fmt.println(a, b, c, d, len(values), EXPLICIT)
}

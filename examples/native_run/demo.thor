/*
    Compile-time execution of eligible Odin procedures, including the standard
    library. Run from the Thor checkout with:

        ./thor run examples/native_run

    #run checks the called implementation and its required dependencies. The
    package does not need a Thor-specific annotation or an entry in a library
    allowlist. The ordinary Odin procedures in eligible.odin work the same way.
*/
package main

import "core:fmt"
import "core:math"
import "core:slice"
import "core:strings"
import "thor:pure"

// This is a genuine constant integer, so it can size an ordinary Odin array.
// The fractional power is evaluated before round consumes its result.
COUNT :: int(#run math.round(1000 ** .33333))

// Overloads, transitive calls and deterministic compiler math intrinsics work.
// round uses numeric bit casts internally; pointer casts remain ineligible.
ROOT :: #run math.sqrt(f32(81))
SINE :: #run math.sin(0.0)
PREFIX :: #run strings.has_prefix("Thor examples", "Thor")

// An optional second return value is ignored, just as in a single-value native
// call. Empty slice.min returns the zero value; request status inside a pure
// consumer and return a record when both results are needed at compile time.
LOW :: #run slice.min([]int{4, -2, 9})
EMPTY :: #run slice.min([]int{})

// An ordinary procedure can call another eligible ordinary procedure. These
// definitions are in an .odin file, and neither is declared fn.
ROUNDED :: #run rounded_in_odin(4.6)
C_SQUARE :: #run c_square(4)

main :: proc() {
    data: [COUNT]int
    assert(len(data) == 10)
    assert(ROOT == f32(9) && SINE == 0 && PREFIX)
    assert(LOW == -2 && EMPTY == 0 && ROUNDED == 5 && C_SQUARE == 16)
    fmt.println("compile-time array length:", len(data))

    // Allocated aggregate results need a runtime binding. Their materialized
    // strings/slices are independent, caller-owned copies of the frozen value.
    report := #run make_report("native Odin", 4)
    defer pure.destroy(report)
    report.values[0] = 99

    another := #run make_report("native Odin", 4)
    defer pure.destroy(another)
    assert(another.values[0] == 4 && report.values[0] == 99)
    fmt.println(another.label, another.values)

    // #run still rejects runtime globals, borrowed-input mutation, ambient
    // state, uncontrolled foreign/indirect calls and unsupported value types.
    // It copies only required library declarations into its temporary helper;
    // unrelated package initializers are not run during evaluation.
    fmt.println("Standard-library and ordinary Odin #run examples passed.")
}

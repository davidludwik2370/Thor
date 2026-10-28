// Run: thor run examples/pure_calls
// Ordinary procedures can participate in pure call graphs without being fn.
package main

import "core:fmt" / println
import "core:math"

square :: proc(n: int) -> int { return n*n }
adjusted :: proc(n: int) -> int { return square(n)+1 }
calculate :: fn(n: int) -> int { return adjusted(n) }

// The fn entry automatically evaluates known arguments. Each ordinary callee
// and its transitive callees must satisfy the same purity rules.
COMPILED :: calculate(3)
#assert(COMPILED == 10)

// Installed Odin procedures are inspected too; source availability and effects
// determine eligibility. An ordinary direct call still requires explicit #run
// when it is needed as a compile-time value.
root :: fn(value: f64) -> f64 { return math.sqrt(value) }
COMPILED_ROOT :: root(16.0)
#assert(COMPILED_ROOT == 4)

view :: proc(input: []int) -> []int { return input[1:] }
change_copy :: fn(input: []int) -> []int {
    // Calls from fn use independent returned storage. Changing this result
    // therefore cannot mutate the borrowed input.
    result := view(input)
    result[0] = 9
    return result
}

main :: proc() {
    // A variable keeps these calls at runtime. Purity applies here as well.
    value := 4
    assert(calculate(value) == 17)
    number := 9.0
    assert(root(number) == 3)

    input := [1, 2, 3]
    result := change_copy(input[:])
    assert(len(result) == 2 && result[0] == 9 && result[1] == 3 && input == [1, 2, 3])
    delete(result) // visible fn results retain the usual manual lifetime

    // Calling the ordinary proc directly retains its shallow view semantics.
    borrowed := view(input[:])
    borrowed[0] = 7
    assert(input[1] == 7)

    // Rejected examples (uncomment one to inspect the source-mapped error):
    // impure :: proc() -> int { return context.user_index }
    // invalid :: fn() -> int { return impure() }
    // Also rejected: mutable globals, borrowed writes, indirect/foreign calls,
    // unsupported pointer/procedure values and explicit allocator selection.
    println("Transitive fn/proc calls passed at compile time and runtime.")
}

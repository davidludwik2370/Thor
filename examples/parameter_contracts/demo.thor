package main

import "core:fmt"

// The caller supplies value; Thor supplies the remaining documented inputs.
// Trying describe(value, expression="pretend") is a compiler error.
describe :: proc(value: int,
    expression :> #caller_expression(value),
    call       :> #caller_expression,
    location   :> #caller_location,
) {
    fmt.printf("%s = %d; call %s at line %d\n", expression, value, call, location.line)
}

// Explicit callback types preserve automatic metadata and its capture target.
Capture :: #type proc(value: int, expression :> #caller_expression(value)) -> string
capture :: fn(value: int, expression :> #caller_expression(value)) -> string {
    return f"{expression}"
}

read_sum :: proc(#ro values: []int) -> int {
    result: int
    for value in values { result += value }
    // values[0] = 9 // Error, including when the caller has writable backing.
    return result
}

copy_values :: proc(#ro source, #mut destination: []int) {
    copy(destination, source)
}

square :: fn(value: int) -> int { return value*value }

// Suppress this declaration's prefer-fn advice; normal uses remain allowed.
@(vet_ignore={.pure})
keep_as_proc :: proc(value: int) -> int { return value+1 }

LIMIT :: 7
CAPTURED :: #run capture(LIMIT)

main :: proc() {
    number := 7
    describe(number)
    alias :: describe
    alias(number+1)

    callback: Capture = capture
    captured ~:= callback(number*2)
    fmt.println(captured)
    fmt.println("Compile-time capture:", CAPTURED)

    // Thor leaves this execution to runtime. Odin can still optimize the code.
    runtime_value := #runtime square(number)
    if runtime_value != 49 { panic("runtime result") }

    source :> []int{1, 2, 3}
    destination := make([]int, len(source))
    defer delete(destination)
    copy_values(source, destination)
    if read_sum(destination) != 6 { panic("read-only input") }

    // Keep an intentionally mutable declaration, including during vet --apply.
    @(vet_ignore={.immutable})
    editable := keep_as_proc(runtime_value)
    fmt.println(editable)
}

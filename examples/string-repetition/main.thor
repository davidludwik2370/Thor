// Run: thor run examples/string-repetition
// String multiplication is compile-time only, just like string addition.
package main

import "core:fmt"

X :: "a" * 3
Y :: X + "b"
Z :: 4 * X + "z"

// * binds more tightly than +. Parentheses can repeat an entire phrase.
GREETING :: ("hi" + "!") * 2
REPETITIONS :: #config(REPETITIONS, 3)
DIVIDER :: "-" * REPETITIONS

// Both arguments are constant parameters, so repetition stays a constant.
repeat :: proc($text: string, $count: int) -> string {
    return text * count
}

main :: proc() {
    assert(X == "aaa" && Y == "aaab" && Z == "aaaaaaaaaaaaz")
    assert("a" * 3 + "b" == "aaab" && GREETING == "hi!hi!")
    assert("abc" * 0 == "" && 2 * "é😀" == "é😀é😀")
    assert(repeat("ab", 3) == "ababab")

    // The result occupies constant storage. Assigning it to a runtime binding
    // does not allocate a string, and it must not be deleted.
    text := GREETING
    fmt.println(X, Y, Z, text, DIVIDER)

    // Both operands must be compile-time constants, and the count must be a
    // nonnegative integer. These would be compile errors:
    // count := 3
    // text := "a" * count
    // text := "a" * -1
}

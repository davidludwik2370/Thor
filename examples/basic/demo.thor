/*
    The basic package is part of Thor's bundled collection.

    Run from the Thor checkout:
        ./thor run examples/basic

    The same import works from a project elsewhere on your machine. No extra
    collection flag or copied library file is required.
*/
package main

import "thor:basic"

main :: proc() {
    // A plain import uses the basic namespace. To use individual names without
    // a prefix instead, write: import "thor:basic" / sorted, print
    basic.print("Thor basic helpers")

    // Sorting returns independent slice storage and leaves its input alone.
    // The inline generic default comparator supplies ascending order.
    numbers := []int{8, 3, 5, 3}
    ascending := basic.sorted(numbers)
    defer delete(ascending)

    assert(ascending[0] == 3 && ascending[3] == 8)
    assert(numbers[0] == 8)
    basic.print("ascending:", ascending)

    // Passing a comparator overrides that default. Both outputs belong to us.
    descending := basic.sorted(numbers, proc(a, b: int) -> bool {
        return a > b
    })
    defer delete(descending)

    assert(descending[0] == 8 && descending[3] == 3)
    assert(basic.asc(3, 3, 5, 8))
    assert(basic.desc(8, 5, 3, 3))
    assert(basic.ordered(..descending))

    // A custom comparator also allows types that do not support native <.
    Item :: struct { name: string, score: int }
    items := []Item{{"oak", 8}, {"elm", 3}}
    ranked := basic.sorted(items, less=proc(a, b: Item) -> bool {
        return a.score < b.score
    })
    defer delete(ranked)
    assert(ranked[0].name == "elm")

    // fmap allocates a result with the same element type as the input.
    doubled := basic.fmap(numbers, proc(value: int) -> int {
        return value * 2
    })
    defer delete(doubled)
    assert(doubled[0] == 16 && numbers[0] == 8)

    // Parsing can report failure or overflow through an optional second result.
    parsed, ok := basic._i16("0x7fff")
    assert(ok && parsed == max(i16))
    _, overflow := basic._u8("256")
    assert(!overflow)

    // split allocates only the outer slice. Its strings borrow the input text.
    // join creates a new string that must also be deleted.
    parts := basic.split("oak,elm,ash", ",")
    defer delete(parts)
    joined := basic.join(parts, " / ")
    defer delete(joined)
    assert(joined == "oak / elm / ash")
    basic.print(joined)

    // clone is a shallow value copy. A cloned slice still shares its elements.
    alias := basic.clone(numbers)
    assert(raw_data(alias) == raw_data(numbers))
    // Do not delete alias: numbers uses literal storage, and no backing storage
    // was allocated by cloning its slice header.

    assert(basic.type(numbers) == []int)
    assert(basic.size(numbers) == size_of([]int))
    basic.print("Basic package examples passed.")
}

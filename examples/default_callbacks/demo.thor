// Run from the Thor checkout: ./thor run examples/default_callbacks
// The assertions check the results as well as preservation of the input.
package main

import "core:fmt"
import "core:slice"

// E comes from data. Thor allows this entire default comparator inline,
// including its use of the enclosing procedure's generic element type.
sorted :: proc(
    data: $T/[]$E,
    less := proc(i, j: E) -> bool { return i < j },
) -> []E {
    result := make([]E, len(data))
    copy(result, data)

    // Sort the copy: sorting data here would modify the caller's original.
    slice.sort_by(result, less)
    return result
}

Person :: struct {
    name: string,
    age:  int,
}

main :: proc() {
    numbers := []int{7, 2, 5}

    // Omit less to use the inline default. The caller owns the returned slice.
    ascending := sorted(numbers)
    defer delete(ascending)
    assert(ascending[0] == 2 && ascending[1] == 5 && ascending[2] == 7)
    assert(numbers[0] == 7 && numbers[1] == 2 && numbers[2] == 5)

    // An explicit comparator replaces the default. Named arguments also work.
    descending := sorted(numbers, less = proc(i, j: int) -> bool {
        return i > j
    })
    defer delete(descending)
    assert(descending[0] == 7 && descending[2] == 2)

    // The same inline default specializes independently for floating point.
    fractions := sorted([]f64{1.25, -0.5, 3.0})
    defer delete(fractions)
    assert(fractions[0] == -0.5 && fractions[2] == 3.0)

    // Records do not support < directly. Providing a comparator means Thor
    // does not instantiate the default comparator for this element type.
    people := []Person{{"Ada", 36}, {"Grace", 29}, {"Linus", 32}}
    youngest_first := sorted(people, proc(a, b: Person) -> bool {
        return a.age < b.age
    })
    defer delete(youngest_first)
    assert(youngest_first[0].name == "Grace")
    assert(people[0].name == "Ada")

    fmt.println("ascending:", ascending)
    fmt.println("descending:", descending)
    fmt.println("fractions:", fractions)
    fmt.println("youngest:", youngest_first[0].name)

    // Copies are shallow, as in ordinary Odin. Deleting youngest_first frees
    // its slice storage; it does not free storage referenced by Person fields.
}

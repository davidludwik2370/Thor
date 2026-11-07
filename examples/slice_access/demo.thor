// Run: thor run examples/slice_access -debug
// Read-only slice access is checked at compile time and erased before Odin.
package main

import "core:fmt" / println
import "core:slice"

sum :: proc(values: []int) -> int {
    total := 0
    for value in values { total += value }
    return total
}

// Only destination needs writable access. TLS shows the inferred #mut in grey.
copy_inferred :: proc(source, destination: []int) {
    copy(destination, source)
}

// An annotation explicitly requires writable input. It belongs to the next
// parameter name, rather than to every name sharing the []int annotation.
copy_explicit :: proc(source, #mut destination: []int) {
    copy(destination, source)
}

require_first_writable :: proc(#mut first, second: []int) {
    // first requires writable access even though this implementation only reads.
    println(len(first), len(second))
}

tail :: proc(values: []int) -> []int {
    // Returning a view preserves the input's access permission at the caller.
    return values[1:]
}

optional_tail :: proc(values: []int) -> (int, []int, bool) {
    return len(values)-1, values[1:], len(values)>1
}

Row :: struct {count: int, items: []int}

main :: proc() {
    println("Read-only views share storage; they do not freeze other aliases.")
    storage := make([]int, 3)
    defer delete(storage) // The view is an alias, not another allocation owner.
    copy(storage, []int{1, 2, 3})

    view :> storage
    alias :> view
    storage[0] = 9
    if alias[0] != 9 || sum(view) != 14 { panic("shared view") }

    // These are independent scalar values, so ordinary mutable bindings work.
    count := len(view)
    doubled := view[0] * 2
    count += 1
    doubled += 1
    if count != 4 || doubled != 19 { panic("scalar copies") }

    // Rejected examples:
    // view[0] = 7
    // writable := view
    // pointer := &view[0]
    // pointer := raw_data(view)
    // for &value in view { value = 7 }

    println("Readers accept views; writers require writable arguments.")
    output := make([]int, len(view))
    defer delete(output)
    copy_inferred(view, output)
    copy_explicit(view, output)
    require_first_writable(output, view)
    // copy_explicit(output, view) // Rejected at the second argument.

    part :> tail(view)
    if part[0] != 2 { panic("returned view") }
    // writable := tail(view) // Still rejected after crossing a procedure.

    println("Clones and value-only comprehensions create independent storage.")
    independent := slice.clone(view)
    defer delete(independent)
    independent[0] = 100
    squares := [value*value for value in view]
    defer delete(squares)
    squares[0] = 0
    if view[0] != 9 { panic("independent storage") }

    readonly_squares :> [value*value for value in view]
    defer delete(readonly_squares)
    if readonly_squares[0] != 81 { panic("readonly comprehension") }
    // readonly_squares[0] = 0 // Rejected.

    println("Inline value copies remain independent; nested slices stay views.")
    rows :> []Row{{count=1, items=storage}}
    row := rows[0]
    row.count = 7
    if rows[0].count != 1 || row.items[0] != 9 { panic("record copy") }
    // row.items[0] = 0 // Rejected: this member still references read-only data.

    fixed :> [10, 20, 30]
    fixed_copy := fixed
    fixed_copy[0] = 99
    fixed_view :> fixed[:2]
    if sum(fixed_view) != 30 || fixed[0] != 10 { panic("fixed arrays") }

    println("Multiple results preserve each view; statuses are scalar values.")
    count_tail, tail_view :> (optional_tail(view) or_panic)
    if count_tail != 2 || tail_view[0] != 2 { panic("optional tuple") }
    // tail_view[0] = 0 // Rejected, including through parentheses/or_panic.
    // _, writable := optional_tail(view) or_panic // Also rejected.

    allocated, error :> make([]int, 1)
    if error != nil { panic("allocation failed") }
    defer delete(allocated)
    copied_status := error // Independent enum; not an alias to allocated.
    if copied_status != nil { panic("status copy") }

    // Compile-time selection does not inherit an inactive arm's restriction.
    selected := output when true else view
    selected[0] = 10 // output still owns the shared allocation.
    if output[0] != 10 || view[0] != 9 { panic("selected writable view") }

    println("slice access demo passed")
}

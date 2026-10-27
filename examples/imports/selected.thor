package main

// Select just these names, and suppress the basic package namespace.
// basic.print(...) and unselected helpers are unavailable in this file.
import _ "thor:basic" / sorted, print

// Each blank selection has its own internal namespace, so different packages
// can contribute selected names without an '_' namespace collision.
import _ "core:strings" / has_prefix

demonstrate_selected_imports :: proc() {
    result := sorted([]int{6, 2, 4})
    defer delete(result)

    assert(result[0] == 2 && has_prefix("Thor", "Th"))
    print("selected:", result)
}

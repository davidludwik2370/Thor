/*
    Wildcard and namespace-free imports

    Run from the Thor checkout:
        ./thor run examples/imports

    Import names belong to the file containing the import. They are not
    re-exported as declarations of this package or shared with sibling files.
*/
package main

// Bring all public basic declarations into this file's scope. This includes
// public helpers whose names start with _, such as _int, but excludes private
// helpers and basic's own imports (fmt, slice, and so on).
import * "thor:basic"

main :: proc() {
    numbers := []int{8, 3, 5}

    // Generic procedures and their inline default callbacks work unqualified.
    ascending := sorted(numbers)
    defer delete(ascending)

    assert(ascending[0] == 3 && ascending[2] == 8)
    assert(numbers[0] == 8)
    print("wildcard:", ascending)

    // The default package namespace remains available with a wildcard import.
    assert(basic.sum(ascending) == 16)
    parsed, ok := _int("42")
    assert(ok && parsed == 42)

    // A local binding may shadow an imported name. Another file-scope
    // declaration of that name, or a conflicting wildcard import, is an error.
    {
        print := "a local variable"
        basic.print(print)
    }

    // This procedure is declared in selected.thor. That file makes its own
    // imports; it does not inherit the wildcard names from this one.
    demonstrate_selected_imports()
}

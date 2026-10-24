package main

main :: proc() {
    values := [1, "two"] // Each element must fit the inferred int element type.
    _ = values
}

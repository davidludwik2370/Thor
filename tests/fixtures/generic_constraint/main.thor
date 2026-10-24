package main

numeric :: proc(value: $T) -> T where T in [int, f64] {
    return value
}

main :: proc() {
    _ = numeric(true) // bool is not an allowed exact type.
}

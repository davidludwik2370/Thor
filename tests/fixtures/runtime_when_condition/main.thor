package main

runtime_value: int

main :: proc() {
    when runtime_value == 1 { // when requires a compile-time condition.
        assert(true)
    }
}

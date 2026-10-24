package main

runtime_value: int

main :: proc() {
    #if runtime_value == 1 { // #if requires a compile-time condition.
        assert(true)
    }
}

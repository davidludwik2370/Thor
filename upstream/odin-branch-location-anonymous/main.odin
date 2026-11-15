package main

import "core:fmt"

main :: proc() {
    p := proc() { defer fmt.println(#branch_location) }
    p()
}

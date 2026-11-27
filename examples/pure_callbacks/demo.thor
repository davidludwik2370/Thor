package main

import "core:fmt"

Callback :: fn(value: int) -> int
square :: fn(value: $T) -> T { return value*value }
triple :: fn(value: int) -> int { return value*3 }
apply :: fn(value: $T, callback: fn(T) -> T) -> T { return callback(value) }
ANSWER :: #run apply(7, square)

main :: proc() {
    callback: Callback = square
    value := 8
    fmt.println("compile time:", ANSWER)
    fmt.println("runtime square:", apply(value, callback))
    callback = triple
    fmt.println("runtime triple:", apply(value, callback))
}

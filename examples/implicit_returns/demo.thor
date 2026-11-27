package main

import "core:fmt"

quad :: fn(x: int) -> int { 4*x }
absolute :: fn(x: int) -> int {
    if x < 0 { return -x }
    x
}
apply :: fn(x: $T, callback: fn(T) -> T) -> T { callback(x) }
bucket :: fn(x: int) -> int {
    switch x {
    case < 10: 0
    case < 20: 1
    case < 30: 2
    case:      3
    }
}

ANSWER :: quad(5)
FORCED :: #run apply(6, quad)

main :: proc() {
    value := 7
    if ANSWER != 20 || FORCED != 24 || quad(value) != 28 || absolute(-3) != 3 || bucket(12) != 1 {
        panic("implicit return regression")
    }
    fmt.println(ANSWER, FORCED, quad(value))
}

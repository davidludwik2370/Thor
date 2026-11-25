package main
import "thor:basic" / print

twice :: fn(n: int) -> int { return n * 2 }
ANSWER :: #run twice(21)

main :: proc() {
    values~ :> [x * x for x in 0..<4]
    if ANSWER != 42 || len(values) != 4 || values[3] != 9 {
        panic("Thor installation check failed")
    }
    print(f"Thor installation OK: {ANSWER}")
}

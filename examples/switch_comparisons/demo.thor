package main

import "thor:basic"

classify :: fn(n: int) -> string {
    return switch n {
    case < 0:   "negative"
    case <= 10: "zero through ten"
    case != 20: "above ten, except twenty"
    case:       "twenty"
    }
}

LABEL :: #switch 12 {
case < 10:  "small"
case >= 10: "large"
case:       "other"
}

main :: proc() {
    for n in [-5, 0, 10, 12, 20] {
        label~ :> classify(n)
        basic.print(n, label)
    }
    basic.print(LABEL)

    // Comparison alternatives can share an arm with ordinary values/ranges.
    n := 100
    switch n {
    case < 0, 20..<30, >= 100: basic.print("matched")
    case: basic.print("other")
    }
}

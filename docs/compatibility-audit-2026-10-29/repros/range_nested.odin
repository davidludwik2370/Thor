package main
main :: proc() {
    for start in 1..<3 {
        for n in start..<3 { _ = n }
    }
}

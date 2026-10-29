package main
consume :: proc(value: int) { _ = value }
main :: proc() {
    values := [2]int{1, 2}
    i := 0
    defer for i in 0..<2 { consume(values[i]) }
}

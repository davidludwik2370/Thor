package main
Color :: enum {Red, Blue}
main :: proc() {
    values := [2]int{1, 2}
    i := 0
    _ = values[i]
    for color in Color { _ = color }
}

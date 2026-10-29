package main
Color :: enum {Red, Blue}
main :: proc() {
    for color in Color { _ = color }
}

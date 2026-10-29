package main
Color :: enum {Red, Blue}
main :: proc() { values: [Color]int; color := Color.Red; _ = values[color] }

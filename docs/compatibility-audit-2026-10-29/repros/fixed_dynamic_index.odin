package main
main :: proc() {
    values: [dynamic; 4]int
    append(&values, 1)
    i := 0
    values[i] = 2
}

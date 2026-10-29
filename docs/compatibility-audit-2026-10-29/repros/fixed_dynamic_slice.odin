package main
main :: proc() {
    values: [dynamic; 4]int
    append(&values, 1)
    i := 0
    _ = values[i:i+1]
}

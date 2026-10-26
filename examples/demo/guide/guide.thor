package guide

// Both procedures below can see declarations in native.odin, in the same package.
offset :: proc(value: int) -> int {
    return native_offset(value)
}

identity :: proc(value: $T) -> T {
    return value
}

// A genuine fn in an imported package also participates in automatic evaluation.
next_code :: fn(value: int) -> int {
    return value + 2
}

package library

Record :: struct {number: int}
identity :: proc(value: $T) -> T { return value }
integer_version :: proc(value: int) -> int { return value }
float_version :: proc(value: f64) -> f64 { return value }
overloaded :: proc{integer_version, float_version}

// This file is deliberately ordinary Odin. It shares a package with guide.thor.
package guide

native_offset :: proc(value: int) -> int {
    return value + 3
}

Parcel :: struct {
    weight: int,
}

integer_label :: proc(value: int) -> int {
    return value + 1
}

floating_label :: proc(value: f64) -> f64 {
    return value + 0.5
}

label :: proc {integer_label, floating_label}

@(private="package")
Signal :: enum {
    Waiting,
    Ready,
}

// The caller can use contextual enum literals without naming the private type.
choose_numeric :: proc(values: [2]Signal, hint: int) -> int {
    return int(values[1]) + hint
}

choose_text :: proc(values: [2]Signal, hint: string) -> int {
    return len(hint)
}

choose :: proc {choose_numeric, choose_text}

accept_signals :: proc(values: $A/[2]Signal) -> int {
    return int(values[0]) + int(values[1])
}

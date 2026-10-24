package library

Record :: struct {number: int}
identity :: proc(value: $T) -> T { return value }
integer_version :: proc(value: int) -> int { return value }
float_version :: proc(value: f64) -> f64 { return value }
overloaded :: proc{integer_version, float_version}

@(private="file") HiddenColor :: enum {Red, Blue}
@(private="file") pick_numeric :: proc(values: [2]HiddenColor, hint: int) -> int { return int(values[1]) }
@(private="file") pick_text :: proc(values: [2]HiddenColor, hint: string) -> int { return 99 }
pick_color :: proc{pick_numeric, pick_text}
accept_colors :: proc(values: $A/[2]HiddenColor) -> int { return int(values[1]) }

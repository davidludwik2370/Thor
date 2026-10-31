// Trusted compiler bridge. Native Odin callers still use explicit clone/destroy.
package thor_big

integer :: proc{from_integer, parse_integer}
rational :: proc{rational_from_integer, clone_rational, parse_rational, fraction, parse_fraction}

literal_integer :: proc(text: string, allocator := context.allocator, loc := #caller_location) -> Integer {
    value, ok := parse_integer(text, allocator=allocator, loc=loc)
    if ok { return value }
    rational, rational_ok := parse_rational(text, allocator=allocator, loc=loc)
    defer destroy(&rational)
    if !rational_ok || !is_zero(rational) && !is_one(rational._denominator) {
        panic("An Integer literal must represent a whole number exactly", loc)
    }
    return clone(rational._numerator, allocator, loc)
}
literal_rational :: proc(text: string, allocator := context.allocator, loc := #caller_location) -> Rational {
    value, ok := parse_rational(text, allocator=allocator, loc=loc)
    if !ok { panic("Invalid Rational literal", loc) }
    return value
}

// Only direct numeric values own another copy. Whole aggregates remain shallow.
compiler_clone :: proc(value: $T) -> T {
    when T == Integer || T == Rational { return clone(value) }
    else { return value }
}
compiler_own :: proc(value: $T, owned: bool) -> T {
    when T == Integer || T == Rational {
        if !owned { return clone(value) }
    }
    return value
}
compiler_destroy :: proc(target: ^$T) {
    when T == Integer || T == Rational { destroy(target) }
}
compiler_destroy_value :: proc(value: $T) {
    when T == Integer || T == Rational {
        temporary := value
        destroy(&temporary)
    }
}
// Preserve native delete behavior for other generic specializations.
compiler_delete :: proc(target: ^$T) {
    when T == Integer || T == Rational { destroy(target) }
    else { delete(target^) }
}
compiler_optional_ok :: proc "contextless"(value: $T) -> bool {
    when T == bool { return value }
    else { return value == T{} }
}

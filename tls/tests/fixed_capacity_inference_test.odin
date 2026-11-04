package tests

import "core:testing"
import test "src:testing"

@(test)
fixed_capacity_generic_element_return :: proc(t: ^testing.T) {
	source := test.Source{main = `package test
peek :: proc(a: ^$A/[dynamic; $N]$E) -> E { return a[0] }
main :: proc() {
    values: [dynamic; 8]int
    result := peek(&values)
    _ = res{*}ult
}
`}
	test.expect_hover(t, &source, "test.result: int")
}

@(test)
fixed_capacity_generic_result_shape :: proc(t: ^testing.T) {
	source := test.Source{main = `package test
shape :: proc(a: $A/[dynamic; $N]$E) -> [dynamic; N]E { return a }
main :: proc() {
    values: [dynamic; 8]string
    result := shape(values)
    _ = res{*}ult
}
`}
	test.expect_hover(t, &source, "test.result: [dynamic; 8]string")
}

@(test)
fixed_capacity_nested_generic_record_field :: proc(t: ^testing.T) {
	source := test.Source{main = `package test
Box :: struct($E: typeid, $N: int) { values: [dynamic; N]E }
main :: proc() {
    box: Box(string, 4)
    result := box.values
    _ = res{*}ult
}
`}
	test.expect_hover(t, &source, "test.result: [dynamic; 4]string")
}

@(test)
fixed_capacity_overload_checks_element_type :: proc(t: ^testing.T) {
	source := test.Source{main = `package test
ints :: proc(a: [dynamic; 4]int) -> int { return 1 }
strings :: proc(a: [dynamic; 4]string) -> string { return "text" }
choose :: proc{ints, strings}
main :: proc() {
    values: [dynamic; 4]string
    result := choose(values)
    _ = res{*}ult
}
`}
	test.expect_hover(t, &source, "test.result: string")
}

@(test)
builtin_nested_complex_result :: proc(t: ^testing.T) {
	source := test.Source{main = `package test
main :: proc() {
    result := real(complex(f32(1), f32(2)))
    _ = res{*}ult
}
`}
	test.expect_hover(t, &source, "test.result: f32")
}

@(test)
generic_call_retains_calling_convention :: proc(t: ^testing.T) {
	source := test.Source{main = `package test
identity :: proc "contextless"(value: $T) -> T { return value }
main :: proc() { _ = iden{*}tity(4) }
`}
	test.expect_hover(t, &source, "test.identity :: proc \"contextless\" (value: $T) -> T")
}

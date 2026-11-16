package tests

import "core:testing"
import test "src:testing"

@(test)
builtin_type_of_alias_field_completion :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
kind :: type_of
Person :: struct { name: string, age: int }
main :: proc() {
    Derived :: kind(Person{})
    person: Derived
    person.{*}
}
`}
    test.expect_completion_labels(t, &source, ".", {"name", "age"})
}

@(test)
builtin_swizzle_alias_result_shape :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
select :: swizzle
Person :: struct { name: string, age: int }
main :: proc() {
    people: [3]Person
    selected := select(people, 2, 1)
    _ = sele{*}cted
}
`}
    test.expect_hover(t, &source, "test.selected: [2]Person")
}

@(test)
builtin_unzip_alias_second_result :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
unzip :: soa_unzip
main :: proc() {
    people := soa_zip(name=[]string{"Tim"}, age=[]int{22})
    names, ages := unzip(people)
    _ = ag{*}es
}
`}
    test.expect_hover(t, &source, "test.ages: []int")
}

@(test)
builtin_nested_compress_expand_alias :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
pack :: compress_values
unpack :: expand_values
main :: proc() {
    name, age := unpack(pack("Tim", 22))
    _ = ag{*}e
}
`}
    test.expect_hover(t, &source, "test.age: int")
}

@(test)
builtin_compress_multi_results_with_extra_arguments :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
pair :: proc() -> (int, string) { return 22, "Tim" }
main :: proc() {
    values := compress_values(pair(), true)
    values.{*}
}
`}
    test.expect_completion_labels(t, &source, ".", {"v0", "v1", "v2"})
}

@(test)
builtin_offset_alias_result :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
offset :: offset_of
Person :: struct { name: string, age: int }
main :: proc() {
    result := offset(Person, age)
    _ = res{*}ult
}
`}
    test.expect_hover(t, &source, "test.result: uintptr")
}

@(test)
builtin_raw_data_enum_indexed_array :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
raw :: raw_data
Key :: enum { first, second }
main :: proc() {
    values: [Key]string
    result := raw(&values)
    _ = res{*}ult
}
`}
    test.expect_hover(t, &source, "test.result: [^]string")
}

@(test)
enum_indexed_overloads_preserve_index_type_identity :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
A :: enum { first, second }
B :: enum { first, second }
choose_a :: proc(values: [A]int) -> string { return "a" }
choose_b :: proc(values: [B]int) -> int { return 1 }
choose :: proc { choose_a, choose_b }
main :: proc() {
    values: [B]int
    result := choose(values)
    _ = res{*}ult
}
`}
    test.expect_hover(t, &source, "test.result: int")
}

@(test)
builtin_swizzle_alias_simd_shape :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
pick :: swizzle
main :: proc() {
    values: #simd[4]f32
    result := pick(values, 1, 0)
    _ = res{*}ult
}
`}
    test.expect_hover(t, &source, "test.result: #simd[2]f32")
}

@(test)
builtin_swizzle_alias_same_size_preserves_distinct_type :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
pick :: swizzle
Vector :: distinct [3]f32
Other :: distinct [3]f32
vector :: proc(value: Vector) -> string { return "vector" }
other :: proc(value: Other) -> int { return 1 }
choose :: proc { vector, other }
main :: proc() {
    values: Vector
    selected := pick(values, 2, 1, 0)
    result := choose(selected)
    _ = res{*}ult
}
`}
    test.expect_hover(t, &source, "test.result: string")
}

@(test)
builtin_expand_preserves_embedded_field_order :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
Metadata :: struct { title: string }
Entry :: struct { using metadata: Metadata, count: int }
unpack :: expand_values
main :: proc() {
    metadata, count := unpack(Entry{})
    _ = co{*}unt
}
`}
    test.expect_hover(t, &source, "test.count: int")
}

@(test)
builtin_unzip_preserves_embedded_field_order :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
Metadata :: struct { title: string }
Entry :: struct { using metadata: Metadata, count: int }
unzip :: soa_unzip
main :: proc() {
    rows: #soa[]Entry
    metadata, counts := unzip(rows)
    _ = co{*}unts
}
`}
    test.expect_hover(t, &source, "test.counts: []int")
}

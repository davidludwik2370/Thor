package tests

import "core:testing"
import test "src:testing"

@(test)
soa_zip_row_hover_through_alias :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
main :: proc() {
    zip :: soa_zip
    names := []string{"Tim"}
    ages := []int{22}
    people := zip(name=names, age=ages)
    for person in people { _ = per{*}son }
}
`}
    test.expect_hover_contains(t, &source, "person: struct {")
}

@(test)
soa_zip_row_completions :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
main :: proc() {
    names := []string{"Tim"}
    ages := []int{22}
    people := soa_zip(name=names, age=ages)
    for person in people { person.{*} }
}
`}
    test.expect_completion_docs(t, &source, ".", {"person.name: string", "person.age: int"})
}

@(test)
soa_zip_positional_index_completions :: proc(t: ^testing.T) {
    source := test.Source{main = `package test
main :: proc() {
    names := []string{"Tim"}
    ages := []int{22}
    people := soa_zip(names, ages)
    person := people[0]
    person.{*}
}
`}
    test.expect_completion_docs(t, &source, ".", {"struct._0: string", "struct._1: int"})
}

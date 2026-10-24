package tests

import "core:odin/ast"
import parser "thor:parser"
import "core:odin/tokenizer"
import "core:os"
import "core:testing"
import "src:server"
import test "src:testing"

thor_source :: proc(source: string) -> test.Source {
    files := make([]test.File, 1, context.temp_allocator)
    files[0] = {"main.thor", source}
    return {files=files}
}

@(test)
thor_parser_main_demo :: proc(t: ^testing.T) {
    context.allocator = context.temp_allocator
    data, err := os.read_entire_file("../demo/main.thor", context.temp_allocator)
    testing.expect(t, err == nil)
    file := ast.new(ast.File, tokenizer.Pos{}, tokenizer.Pos{})
    file.src = string(data)
    file.fullpath = "main.thor"
    p := parser.default_parser()
    testing.expect(t, server.parse_file(&p, file))
    testing.expect_value(t, file.syntax_error_count, 0)
    testing.expect_value(t, len(p.selections), 3)
}

@(test)
thor_hover_inferred_array :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { values := [1,2,3]; value{*}s }
`)
    test.expect_hover_contains(t, &source, "[3]int")
}

@(test)
thor_hover_typed_array :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { values: [2]f64 = [1,2]; value{*}s }
`)
    test.expect_hover_contains(t, &source, "[2]f64")
}

@(test)
thor_hover_nested_array :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { values := [[1,2],[3,4]]; value{*}s }
`)
    test.expect_hover_contains(t, &source, "[2][2]int")
}

@(test)
thor_hover_array_element :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { values := [1,2]; first := values[0]; fir{*}st }
`)
    test.expect_hover_contains(t, &source, "first: int")
}

@(test)
thor_hover_membership :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { found := 2 in [1,2]; fou{*}nd }
`)
    test.expect_hover_contains(t, &source, "found: bool")
}

@(test)
thor_hover_not_in :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { missing := 3 not_in [1,2]; miss{*}ing }
`)
    test.expect_hover_contains(t, &source, "missing: bool")
}

@(test)
thor_hover_multiline_ternary :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() {
    chosen := true ? 1
                   : 2
    cho{*}sen
}
`)
    test.expect_hover_contains(t, &source, "chosen: int")
}

@(test)
thor_hover_selected_procedure :: proc(t: ^testing.T) {
    source := thor_source(`package test
import "library" / selected
main :: proc() { sele{*}cted(2) }
`)
    source.packages = {{pkg="library", source=`package library
selected :: proc(value: int) -> int { return value }
`}}
    test.expect_hover_contains(t, &source, "proc(value: int) -> int")
}

@(test)
thor_hover_selected_type :: proc(t: ^testing.T) {
    source := thor_source(`package test
import "library" / Record
main :: proc() { r: Rec{*}ord }
`)
    source.packages = {{pkg="library", source="package library\nRecord :: struct {value:int}\n"}}
    test.expect_hover_contains(t, &source, "struct")
}

@(test)
thor_selected_import_shadowing :: proc(t: ^testing.T) {
    source := thor_source(`package test
import "library" / selected
main :: proc() { selected := 42; sele{*}cted }
`)
    source.packages = {{pkg="library", source="package library\nselected :: proc() {}\n"}}
    test.expect_hover_contains(t, &source, "selected: int")
}

@(test)
thor_selected_import_completion :: proc(t: ^testing.T) {
    source := thor_source(`package test
import "library" /
    selected,
    Record
main :: proc() { sel{*} }
`)
    source.packages = {{pkg="library", source="package library\nselected :: proc() {}\nRecord :: struct {}\n"}}
    test.expect_completion_labels(t, &source, "", {"selected"})
}

@(test)
thor_selected_type_completion :: proc(t: ^testing.T) {
    source := thor_source("package test\nimport \"library\" / Record\nmain :: proc() { Rec{*} }\n")
    source.packages = {{pkg="library", source="package library\nRecord :: struct {}\n"}}
    test.expect_completion_labels(t, &source, "", {"Record"})
}

@(test)
thor_selected_import_file_privacy :: proc(t: ^testing.T) {
    source := thor_source("package test\nmain :: proc() { {*} }\n")
    source.files = {{"main.thor", source.files[0].source},
                    {"other.thor", "package test\nimport \"library\" / selected\n"}}
    source.packages = {{pkg="library", source="package library\nselected :: proc() {}\n"}}
    test.expect_completion_labels(t, &source, "", {"main"}, {"selected"})
}

@(test)
thor_if_integer_condition :: proc(t: ^testing.T) {
    source := thor_source(`package test
MODE :: 2
#if MODE == 1 { VALUE :: "wrong" } #else if MODE == 2 { VALUE :: 42 } #else { VALUE :: false }
main :: proc() { VAL{*}UE }
`)
    test.expect_hover_contains(t, &source, "42")
}

@(test)
thor_if_membership_condition :: proc(t: ^testing.T) {
    source := thor_source(`package test
#if 2 in [1,2] { VALUE :: 42 } #else { VALUE :: false }
main :: proc() { VAL{*}UE }
`)
    test.expect_hover_contains(t, &source, "42")
}

@(test)
thor_if_config_default :: proc(t: ^testing.T) {
    source := thor_source(`package test
MODE :: #config(MODE, 2)
#if MODE == 2 { VALUE :: 42 } #else { VALUE :: false }
main :: proc() { VAL{*}UE }
`)
    test.expect_hover_contains(t, &source, "42")
}

@(test)
thor_generic_constraint_signature :: proc(t: ^testing.T) {
    source := thor_source(`package test
numeric :: proc(x: $T) -> T where T in [int, f64] { return x }
main :: proc() { num{*}eric(2) }
`)
    test.expect_hover_contains(t, &source, "proc(x: $T) -> T")
}

@(test)
thor_incomplete_array_retains_other_symbols :: proc(t: ^testing.T) {
    source := thor_source(`package test
Record :: struct {value:int}
main :: proc() { r: Record; r.{*}
unfinished := [1,
`)
    test.expect_completion_labels(t, &source, ".", {"value"})
}

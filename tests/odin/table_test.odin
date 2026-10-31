package main

import "core:fmt"
import "core:strings"
import "core:testing"

@(test)
table_sample_and_spillover :: proc(t: ^testing.T) {
    Row :: struct { field_a, field_b, field_c: string }
    rows := []Row{
        {"data 1", "asd", "asdfa"},
        {"dta 2", "asdff", "asdfsd"},
        {"jdkn", "asdfasadfa", "asd"},
        {"asasdf", "f", "pf"},
    }
    text := table(rows, max_col_width=7)
    defer delete(text)
    testing.expect_value(t, text, `+---------+---------+---------+
| field_a | field_b | field_c |
+---------+---------+---------+
| data 1  | asd     | asdfa   |
| dta 2   | asdff   | asdfsd  |
| jdkn    | asdfasa | asd     |
|         |   dfa   |         |
| asasdf  | f       | pf      |
+---------+---------+---------+
`)
    testing.expect_value(t, rows[2].field_b, "asdfasadfa")
}

@(test)
table_tags_numeric_alignment_and_default_float_precision :: proc(t: ^testing.T) {
    Row :: struct {
        name: string `Item`,
        quantity: int `Qty`,
        cost: f64 `Price`,
    }
    text := table([]Row{{"2", 2, 2.5}, {"long name", -12, -1.25}})
    defer delete(text)
    testing.expect_value(t, text, `+-----------+-----+-------+
| Item      | Qty | Price |
+-----------+-----+-------+
| 2         |   2 |  2.50 |
| long name | -12 | -1.25 |
+-----------+-----+-------+
`)
}

@(test)
table_distinct_numeric_types_and_custom_float_precision :: proc(t: ^testing.T) {
    Amount :: distinct f64
    Count :: distinct u64
    Row :: struct { amount: Amount `Value`, count: Count `N` }
    text := table([]Row{{1.5, 12}, {100, 1}}, float_format="%.1f")
    defer delete(text)
    testing.expect_value(t, text, `+-------+----+
| Value | N  |
+-------+----+
|   1.5 | 12 |
| 100.0 |  1 |
+-------+----+
`)
}

@(test)
table_wrapped_headings_and_strings :: proc(t: ^testing.T) {
    Row :: struct { value: string `abcdefghij` }
    text := table([]Row{{"1234567890"}}, max_col_width=5)
    defer delete(text)
    testing.expect_value(t, text, `+-------+
| abcde |
|   fgh |
|   ij  |
+-------+
| 12345 |
|   678 |
|   90  |
+-------+
`)
}

@(test)
table_wrapped_numbers_keep_right_alignment :: proc(t: ^testing.T) {
    Row :: struct { value: i64 `Number` }
    text := table([]Row{{1234567890}}, max_col_width=5)
    defer delete(text)
    testing.expect_value(t, text, `+-------+
| Numbe |
|   r   |
+-------+
| 12345 |
|   678 |
|    90 |
+-------+
`)
}

@(test)
table_multiline_cells_and_completed_columns :: proc(t: ^testing.T) {
    Row :: struct { a: string `A`, b: string `B` }
    text := table([]Row{{"ab\nc\n", "X\nY\nZ"}, {"", ""}})
    defer delete(text)
    testing.expect_value(t, text, `+----+---+
| A  | B |
+----+---+
| ab | X |
|  c | Y |
|    | Z |
|    |   |
+----+---+
`)
}

@(test)
table_very_narrow_columns_make_progress :: proc(t: ^testing.T) {
    Row :: struct { value: string `h` }
    one := table([]Row{{"abcd"}}, max_col_width=1)
    defer delete(one)
    testing.expect_value(t, one, `+---+
| h |
+---+
| a |
| b |
| c |
| d |
+---+
`)
    two := table([]Row{{"abcd"}}, max_col_width=2)
    defer delete(two)
    testing.expect_value(t, two, `+----+
| h  |
+----+
| ab |
|  c |
|  d |
+----+
`)
}

@(test)
table_unicode_display_width_and_intact_graphemes :: proc(t: ^testing.T) {
    Row :: struct { word: string }
    text := table([]Row{{"é界e\u0301😀"}}, max_col_width=4)
    defer delete(text)
    testing.expect_value(t, text, `+------+
| word |
+------+
| é界é |
|   😀 |
+------+
`)
    joined_emoji := table([]Row{{"👩‍💻é"}})
    defer delete(joined_emoji)
    testing.expect_value(t, joined_emoji, `+------+
| word |
+------+
| 👩‍💻é  |
+------+
`)
}

@(test)
table_empty_input_and_empty_structs :: proc(t: ^testing.T) {
    Row :: struct { name: string, n: int }
    text := table([]Row{})
    defer delete(text)
    testing.expect_value(t, text, `+------+---+
| name | n |
+------+---+
`)
    Empty :: struct {}
    none := table([]Empty{{}})
    defer delete(none)
    testing.expect_value(t, none, "")
}

@(test)
table_control_characters_and_line_endings :: proc(t: ^testing.T) {
    Row :: struct { value: string `v` }
    text := table([]Row{{"a\tb"}, {"x\r\ny\r"}, {"\x1b[31m"}})
    defer delete(text)
    testing.expect(t, strings.contains(text, "a    b"))
    testing.expect(t, strings.contains(text, "\\x1b[31m"))
    testing.expect(t, !strings.contains(text, "\t") && !strings.contains(text, "\r"))
    // All resulting lines are ASCII and have the same width as the border.
    lines := strings.split(text, "\n")
    defer delete(lines)
    for line in lines[:len(lines)-1] {
        testing.expect_value(t, len(line), len(lines[0]))
    }
}

@(test)
table_other_field_types_use_ordinary_formatting :: proc(t: ^testing.T) {
    State :: enum { Ready, Waiting }
    Box :: struct { n: int }
    Row :: struct { flag: bool, state: State, box: Box, values: [2]int, letter: rune }
    row := Row{true, .Ready, {7}, {1, 2}, 'é'}
    text := table([]Row{row, {false, .Waiting, {9}, {3, 4}, 'x'}})
    defer delete(text)
    values := []any{row.flag, row.state, row.box, row.values, row.letter}
    for value in values {
        formatted := fmt.aprint(value)
        testing.expect(t, strings.contains(text, formatted))
        delete(formatted)
    }
    testing.expect(t, strings.contains(text, "| true  | Ready   |"))
}

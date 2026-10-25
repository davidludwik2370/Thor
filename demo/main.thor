// Run: ./thor run demo
// Inspect the generated Odin: ./thor transpile demo
// Every section includes assertions so this is an evaluation program too.
package main

import "core:fmt" / println
import math "core:math" /
    sin, // a selected procedure group
    cos,
    PI   // a selected constant; no trailing comma
import "library" / Record, identity, overloaded
import format "thor:format"
import sets "thor:sets"

Color :: enum {Red, Green, Blue}
calls: int
order: int

mark :: proc(value: int) -> int {
    calls += 1
    order = order * 10 + value
    return value
}

unselected :: proc() -> int {
    assert(false, "an unselected branch was evaluated")
    return 0
}

numeric_identity :: proc(x: $T) -> T where T in [int, f64] {
    return x
}
other_identity :: proc(x: $T) -> T where T not_in [int, f64] {
    return x
}
allowed_size :: proc($N: int) -> int where N in [2, 4, 8] {
    return N
}
sum_pair :: proc(values: [2]f64) -> f64 {
    return values[0] + values[1]
}
make_pair :: proc() -> [2]f64 {
    return [3, 4]
}

last_color :: proc(values: [2]$T, hint: T) -> T { return values[1] }
callback_last :: proc "c"(values: []int, index: int) -> int { return values[index] }

DEMO_MODE :: #config(DEMO_MODE, 2)
#if DEMO_MODE == 1 {
    BRANCH :: "first"
} #else if DEMO_MODE == 2 {
    BRANCH :: "second"
} #else {
    BRANCH :: "default"
}

// Original Odin syntax is still valid and may surround Thor syntax.
when true {
    #if 4 in [2, 4, 8] {
        ALLOWED :: true
    } #else {
        ALLOWED :: false
    }
}

main :: proc() {
    println("T01: CLI, project packages, and ordinary Odin")
    assert(support_value() == 7) // comes from the ordinary .odin file
    original := [?]int{10, 20, 30}
    assert(original[1] == 20)

    println("T02: selective imports, multiline lists, and retained namespaces")
    assert(sin(f64(0)) == 0 && cos(f64(0)) == 1)
    assert(PI > 3 && math.PI == PI)
    record := Record{number=9}
    assert(identity(record).number == 9)
    assert(overloaded(int(2)) == 2 && overloaded(f64(3)) == 3)
    fmt.println("  The original fmt namespace still works.")

    println("T03: multiline, nested, typed, and lazy ternaries")
    answer := true ? 27 // this comment survives transpilation
                   : unselected()
    nested := false ? 0
                    : true ? 2
                           : 3
    color: Color = false ? .Red
                        : .Blue
    assert(answer == 27 && nested == 2 && color == .Blue)

    println("T04: #if / #else if / #else, nesting, defines, and native when")
    println("  Selected compile-time branch:", BRANCH)
    assert(ALLOWED)
    #if true {
        when true { assert(ALLOWED) }
    } #else {
        intentionally_undefined_in_an_unselected_branch()
    }
    native_conditional := 8 when ALLOWED else 0
    assert(native_conditional == 8)

    println("T05: inferred and contextual fixed-array literals")
    numbers := [1, 2, 3]
    words := ["one", "two"]
    typed: [2]f64 = [1, 2]
    colors: [2]Color = [.Red, .Blue]
    empty: [0]int = []
    grid: [2][2]f64 = [[1, 2], [3, 4]]
    typed = [5, 6]
    assert(type_of(numbers) == [3]int && words[1] == "two")
    assert(type_of(typed) == [2]f64 && colors[1] == .Blue)
    assert(len(empty) == 0 && grid[1][0] == 3)
    assert(sum_pair([1, 2]) == 3 && sum_pair(values=[3, 4]) == 7)
    assert(make_pair()[1] == 4)
    calls, order = 0, 0
    evaluated := [mark(1), mark(2), mark(3)]
    assert(evaluated[2] == 3 && calls == 3 && order == 123)

    println("T06: sequence membership, native maps/bit sets, and constraints")
    assert(2 in numbers && 9 not_in numbers)
    assert(2 in numbers[:] && "two" in words)
    growing: [dynamic]int
    defer delete(growing)
    append(&growing, 1, 2, 3)
    fixed: [dynamic; 8]int
    append(&fixed, 1, 2, 3)
    assert(2 in growing && 9 not_in growing)
    assert(3 in fixed && 9 not_in fixed)
    table: map[string]int
    defer delete(table)
    table["present"] = 0 // membership checks keys, even when a value is zero
    assert("present" in table && "missing" not_in table)
    flags: bit_set[Color] = {.Red, .Blue}
    assert(.Red in flags && .Green not_in flags)

    // An early match does not skip construction of the remaining elements.
    calls, order = 0, 0
    assert(mark(1) in [mark(1), mark(2), mark(3)])
    assert(calls == 4 && order == 1123)
    calls, order = 0, 0
    assert(true || (mark(1) in [mark(2)]))
    assert(calls == 0 && order == 0)
    assert(mark(1) not_in [])
    assert(calls == 1)

    // These constraints lower to constant comparisons, with no runtime helpers.
    assert(numeric_identity(int(7)) == 7)
    assert(numeric_identity(f64(2.5)) == 2.5)
    assert(other_identity(f32(3)) == 3)
    assert(allowed_size(4) == 4)
    #assert(int in [int, f64])
    #assert(bool not_in [int, f64])

    println("T07: owned integer grouping, padding, and Unicode fill")
    grouped := format.group_integer(i64(-9223372036854775808))
    padded := format.format(1234567, grouping=true, width=14, align=.Center, fill='界')
    text := format.format("é😀", width=5)
    assert(grouped == "-9,223,372,036,854,775,808")
    assert(padded == "界界1,234,567界界界" && text == "é😀   ")
    delete(grouped); delete(padded); delete(text)

    println("T08: Set support library, independent results, and alias-safe operations")
    names: sets.Set(string)
    sets.insert(&names,"apple"); sets.insert(&names,"apple"); sets.insert(&names,"pear")
    assert(sets.count(names)==2 && "pear" in sets.entries(names))
    other_names := sets.clone(names)
    sets.insert(&other_names,"plum")
    added := sets.difference(other_names,names)
    assert(sets.count(added)==1 && sets.contains(added,"plum") && sets.count(names)==2)
    alias := other_names
    sets.symmetric_difference_assign(&other_names,alias)
    assert(sets.count(other_names)==0)
    sets.destroy(added); sets.destroy(other_names); sets.destroy(names)

    println("T09: shared Thor frontend and ordinary Odin compatibility")
    fn := 3
    set := 4 // these remain ordinary identifiers outside later Thor forms
    assert(fn+set==7 && identity("ordinary Odin") == "ordinary Odin")

    println("T10: native configuration, mixed packages, and original resource paths")
    assert(support_value()==7 && DEMO_MODE in [1,2,3])
    assert(#exists("assets/message.txt"))
    assert(string(#load("assets/message.txt")) == "original project resource\n")

    println("T11: overloaded/generic contexts and private types in imported packages")
    assert(library.pick_color([.Red,.Blue], int(1)) == 1)
    assert(library.accept_colors([.Red,.Blue]) == 1)
    assert(last_color([.Red,.Blue], Color.Red) == .Blue)

    println("T12: runtime-initialized immutable storage and shallow references")
    calls, order = 0, 0
    immutable :> mark(5)
    typed_immutable: int > mark(6)
    assert(immutable==5 && typed_immutable==6 && calls==2 && order==56)
    frozen_record :> Record{number=3}
    mutable_copy := frozen_record
    mutable_copy.number=9
    assert(frozen_record.number==3 && mutable_copy.number==9)
    backing := [1,2]
    view :> backing[:]
    view[0]=8 // existing referenced data remains mutable; the header is fixed
    assert(backing[0]==8)
    // Rejections are separate executable fixtures under tests/fixtures/:
    // immutable = 9; &frozen_record.number; frozen_array[:]

    println("T13: quoted/raw interpolation, braces, nested expressions, and formatting")
    calls, order = 0, 0
    interpolated := f"{{fields}} {mark(1)} {mark(2)} {1234567:0>12,}"
    assert(interpolated == "{fields} 1 2 0001,234,567" && order == 12)
    raw_interpolated := f`raw \n {"é😀":界^5}`
    assert(raw_interpolated == `raw \n 界é😀界界`)
    nested_interpolated := f"outer {f"inner {3}"}"
    assert(nested_interpolated == "outer inner 3")
    // Interpolation owns its result and formatting intermediates. A field's
    // borrowed value remains yours. Delete each final allocated result once.
    delete(interpolated); delete(raw_interpolated); delete(nested_interpolated)

    println("T14: runtime and compile-time switches, with and without selectors")
    calls, order = 0, 0
    switched := switch mark(2) {
        case 1: unselected()
        case 2, 3: mark(4)
        case: unselected()
    }
    assert(switched == 4 && calls == 2 && order == 24)
    boolean_switched := switch {
        case false: unselected()
        case switched == 4: 7
        case: unselected()
    }
    contextual: [2]Color = switch switched {
        case 3..<5: [.Red, .Blue]
        case: [.Green, .Green]
    }
    assert(boolean_switched == 7 && contextual[1] == .Blue)
    COMPILED :: #switch DEMO_MODE {
        case 1: "first"
        case 2: "second"
        case: "default"
    }
    COMPILED_BOOL :: #switch {
        case false: undefined_unselected_expression
        case true: 8
        case: another_undefined_expression
    }
    #switch DEMO_MODE {
        case 1, 2: SELECTED_DECLARATION :: 10
        case: SELECTED_DECLARATION :: 20
    }
    #switch {
        case COMPILED_BOOL == 8: SELECTED_BOOLEAN :: true
        case: SELECTED_BOOLEAN :: false
    }
    assert(COMPILED == BRANCH && COMPILED_BOOL == 8 && SELECTED_BOOLEAN)
    assert(SELECTED_DECLARATION == (DEMO_MODE == 3 ? 20 : 10))

    println("T15: Set syntax, operators, iteration, equality, and explicit ownership")
    fruit: set[string]
    fruit += "apple"; fruit += "pear"; fruit += "apple"
    more_fruit := set[string]{"pear", "plum"}
    combined := fruit | more_fruit
    common := fruit & more_fruit
    only_first := fruit - more_fruit
    either := fruit ~ more_fruit
    assert(len(fruit) == 2 && "pear" in common && "apple" in only_first)
    assert(len(combined) == 3 && len(either) == 2 && fruit != more_fruit)
    visited := 0
    for name in combined { assert(name in combined); visited += 1 }
    assert(visited == 3)
    fruit |= more_fruit; fruit &= combined; fruit -= "apple"
    fruit -= common; fruit ~= either
    assert("apple" in fruit && "plum" not_in fruit)
    clear(fruit)
    assert(len(fruit) == 0)
    // Mathematical results have independent tables. Ordinary assignment is a
    // shallow header copy; use sets.clone for an independently owned copy.
    delete(fruit); delete(more_fruit); delete(combined)
    delete(common); delete(only_first); delete(either)

    println("T16: negative sequence indices, slices, writes, and ordinary map keys")
    sequence := [10, 20, 30, 40]
    last_index := -1
    assert(sequence[last_index] == 40 && sequence[-2] == 30)
    sequence[-1] += 2
    last_pointer := &sequence[last_index]
    last_pointer^ += 1
    middle := sequence[1:-1]
    tail := sequence[-2:]
    assert(sequence[3] == 43 && len(middle) == 2 && tail[0] == 30)
    growing[-1] = 9
    assert(growing[-1] == 9 && "hello"[-1] == 'o' && "hello"[-3:] == "llo")
    signed_keys: map[int]int
    signed_keys[-1] = 5
    assert(signed_keys[-1] == 5) // key -1 retains native map meaning
    delete(signed_keys)

    println("T17: generated work stays inside lazy branches and loop iterations")
    calls, order = 0, 0
    lazy := false && (switch {case mark(1) == 1: true; case: false})
    assert(!lazy && calls == 0)
    loop_index := 0
    for (switch loop_index {case 0, 1: true; case: false}) {
        loop_index += 1
    }
    assert(loop_index == 2)
    combined_syntax := f"last: {switch sequence[-1] {case 43: "ok"; case: "bad"}}"
    assert(combined_syntax == "last: ok")
    delete(combined_syntax)

    println("Hardening: anonymous temporaries, native xor, and C callbacks")
    a := set[int]{1}; b := set[int]{2}; c := set[int]{3}
    all := a | b | c // one result table; no hidden intermediate table to delete
    defer delete(all)
    assert(len(all) == 3 && len(a) == 1 && len(b) == 1 && len(c) == 1)
    assert((a ~ b) == set[int]{1, 2}) // both anonymous tables are cleaned up
    assert(len((a | b) & (b | c)) == 1)
    a ~= b
    assert(2 in a)
    delete(a); delete(b); delete(c)
    for allocated in [false, true] {
        borrowed := "existing"
        outer := f"{(allocated ? f"nested {7}" : borrowed)}"
        assert(outer == (allocated ? "nested 7" : borrowed))
        delete(outer) // only the selected allocated inner string is deleted
    }
    assert(len(f"text {7}"[1:]) == 5)
    // Retain a named owner when a view needs to survive its expression.
    owner := f"text {7}"
    owner_view := owner[1:]
    assert(owner_view == "ext 7")
    delete(owner)
    for item in set[int]{1, 2} { assert(item in [1, 2]) }
    pointer_value := 1; pointer := &pointer_value; pointer^=3; pointer^~=1
    assert(pointer^ == 2 && callback_last(sequence[:], -1) == 43)

    println("All T01-T17 demos passed.")
}

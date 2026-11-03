package main

import "core:fmt" / println
import "core:mem"
import math "core:math" /
    sqrt,
    PI

import "guide" / Parcel, identity, label

// These default names come from the final component of the import path.
// Explicit aliases, such as `import bags "thor:sets"`, are also supported.
import "thor:format"
import "thor:sets"
import "thor:pure"
import "thor:big"
import "thor:basic"


/*
    A commented tour of Thor

    Thor starts with Odin. Ordinary Odin declarations, types, procedures, when
    statements, allocators, and packages continue to work. This file concentrates
    on the additions, especially where a convenient spelling can obscure an
    important rule about types, evaluation, or ownership.

    Each topic is a procedure, and related variations have their own blocks.
    Assertions make the explanations executable. Examples marked "Not valid"
    are intentionally commented out; the reason is immediately above them.

    Run from the Thor repository:

        ./thor run examples/demo
        ./thor run examples/demo/demo.thor
        ./thor run examples/demo -define:TOUR_MODE=1
        ./thor run examples/demo -define:TOUR_MODE=3

    Inspect or check the translation:

        ./thor check examples/demo
        ./thor transpile examples/demo

    Check ownership as well as results:

        ./thor test examples/demo -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true

    Compile-time results are cached beside the entry package. Run twice to see
    the cold and warm behavior; neither run changes the source files:

        ./thor run examples/demo --cache-report
        ./thor run examples/demo --cache-report
        ./thor run examples/demo --no-cache

    --clear-cache removes Thor's cached evaluation results. --cache-dir selects
    another cache location. The cache stores typed contents, not live allocations.

    The guide/ package contains both native.odin and guide.thor. The notice under
    assets/ is deliberately read relative to this original source directory.

    Coverage includes the T01-T23 language additions and their hardening, plus
    interval membership, numeric power, and Integer/Rational construction,
    ownership, operators, compile-time evaluation, and library interactions.
    Compiler-internal tasks such as source maps and serialization are illustrated
    through their observable behavior.
*/


// A selector shared by a few control-flow examples. Change it with -define.
TOUR_MODE :: #config(TOUR_MODE, 2)
#assert(TOUR_MODE in [1, 2, 3])

Weather :: enum {
    Sun,
    Rain,
    Snow,
}

Point :: struct {
    x, y: int,
}

// Record decimal digits to make evaluation order observable without printing
// from the expressions being observed. This is an ordinary, effectful proc.
step :: proc(order: ^int, digit: int) -> int {
    order^ = order^ * 10 + digit
    return digit
}

must_not_run :: proc() -> int {
    panic("An example evaluated a branch that should have been skipped.")
}


imports_and_odin :: proc() {
    println("\n# imports and ordinary Odin")

    {
        // A selective import introduces the chosen names, including procedure
        // groups. It does not remove the package's original namespace.
        assert(sqrt(f64(81)) == 9)
        assert(math.sqrt(f64(16)) == 4)
        assert(PI == math.PI)

        // Imports of user packages follow the same rules. These overloads and
        // this record are implemented in the ordinary Odin helper file.
        parcel := Parcel{weight=12}

        assert(identity(parcel).weight == 12)
        assert(label(int(6)) == 7)
        assert(label(f64(6)) == 6.5)
        assert(guide.offset(10) == 13)
    }

    {
        // New spellings do not replace the old ones. Native fixed-array syntax,
        // procedure declarations, and when statements remain valid.
        native_array := [?]int{7, 14, 21}
        assert(native_array[1] == 14)

        when size_of(int) >= 4 {
            assert(true)
        } else {
            assert(false, "This walkthrough expects an ordinary native target.")
        }

        // `fn` and `set` remain usable as identifiers outside their special
        // syntactic positions. Prefer descriptive names in real programs!
        fn := 8
        set := 9
        assert(fn + set == 17)
    }

    {
        // Resources keep the original source directory as their base, even
        // though Odin ultimately compiles a generated copy of this package.
        NOTICE :: string(#load("assets/notice.txt"))

        assert(#exists("assets/notice.txt"))
        assert(NOTICE == "Meet at the cedar gate.\n")
        println("  Resource:", NOTICE[:len(NOTICE)-1])
    }
}


arrays_and_context :: proc() {
    println("\n# fixed-array literals and contextual types")

    {
        // Brackets without an explicit type create a fixed array, not a slice
        // or dynamic array. No heap allocation is implied by this syntax.
        distances := [3, 6, 9]
        assert(type_of(distances) == [3]int)

        // An expected type supplies the element type. Odin's ordinary literal
        // coercions still apply, so integer literals can initialize f64 values.
        weights: [3]f64 = [1, 2, 4]
        colors: [2]Weather = [.Rain, .Snow]
        empty: [0]int = []
        grid: [2][2]f32 = [[1, 2], [3, 4]]

        assert(weights[2] == 4.0)
        assert(colors[0] == .Rain)
        assert(len(empty) == 0)
        assert(grid[1][0] == 3)

        // There is no element from which to infer the type of an untyped empty
        // literal, and arrays do not acquire implicit array-to-slice conversion.
        // Not valid: unknown := []
        // Use distances[:] when a slice is needed.
    }

    {
        sum_pair :: proc(values: [2]f64) -> f64 {
            return values[0] + values[1]
        }

        make_pair :: proc() -> [2]f64 {
            return [2, 7]
        }

        assert(sum_pair([2, 7]) == 9)
        assert(make_pair()[1] == 7)

        // Context also travels through overloads and generic procedure shapes.
        // The enum below is private to guide/, so we use implicit selectors
        // rather than trying to name its type from this package.
        assert(guide.choose([.Waiting, .Ready], int(4)) == 5)
        assert(guide.accept_signals([.Ready, .Waiting]) == 1)
    }

    {
        // Each element is evaluated once, in source order.
        order := 0
        values := [step(&order, 2), step(&order, 4), step(&order, 6)]

        assert(order == 246)
        assert(values == [2, 4, 6])
    }
}


membership_and_constraints :: proc() {
    println("\n# membership, intervals, and generic constraints")

    {
        codes := [11, 22, 33]
        assert(22 in codes)
        assert(44 not_in codes[:])

        growing: [dynamic]int
        defer delete(growing)
        append(&growing, 11, 22)

        fixed_capacity: [dynamic; 4]int
        append(&fixed_capacity, 22, 33)

        assert(11 in growing)
        assert(33 in fixed_capacity)

        // Map membership searches keys, even when the associated value is zero.
        counts: map[string]int
        defer delete(counts)
        counts["empty"] = 0
        counts["full"] = 8

        assert("empty" in counts)
        assert("missing" not_in counts)

        weather_flags: bit_set[Weather] = {.Sun, .Snow}
        assert(.Snow in weather_flags)
        assert(.Rain not_in weather_flags)

        // Thor does not assign substring-search meaning to string membership.
        // Not valid: found := "cedar" in "cedar grove"
    }

    {
        // A match does not skip construction of the rest of a literal container.
        // The needle runs first, followed by every element, and then the search.
        order := 0
        found := step(&order, 4) in [step(&order, 4), step(&order, 6), step(&order, 7)]

        assert(found)
        assert(order == 4467)
        assert(4 not_in []) // the needle supplies the empty container's type
    }

    {
        low, high := 2, 8
        value := 5

        assert(value in low..<high)     // includes low, excludes high
        assert(high not_in low..<high)
        assert(high in low..=high)      // includes both bounds
        assert(3 in low+1..<high-1 && 3 != 6)

        // Reversed intervals are empty. Inclusive bounds are compared directly;
        // the implementation does not add one to a maximum integer bound.
        assert(5 not_in 8..=2)
        largest: i64 = max(i64)
        assert(largest in 0..=max(i64))

        // Intervals also use ordinary ordered rune, float, enum, or string types.
        // A string interval is lexicographic comparison, not substring search.
        assert('m' in 'a'..='z')
        assert("elm" in "cedar"..<"pine")

        // All three operands run once, in value / low / high order, even when
        // the value is already outside the interval after the first comparison.
        order := 0
        outside := step(&order, 1) not_in step(&order, 3)..<step(&order, 8)
        assert(outside)
        assert(order == 138)
    }

    {
        // Membership in a type list means exact type identity. An alias matches
        // its target; a new distinct type does not silently match that target.
        numeric :: proc(value: $T) -> T where T in [int, f32, f64] {
            return value * 2
        }

        nonnumeric :: proc(value: $T) -> T where T not_in [int, f32, f64] {
            return value
        }

        Integer_Alias :: int
        Ticket_Number :: distinct int

        assert(numeric(Integer_Alias(6)) == 12)
        assert(numeric(f32(1.5)) == 3)
        assert(nonnumeric(Ticket_Number(6)) == Ticket_Number(6))

        // Not valid: numeric(Ticket_Number(6))

        small_count :: proc($N: int) -> int where N in 2..=6 {
            return N
        }

        listed_count :: proc($N: int) -> int where N in [3, 6, 9] {
            return N
        }

        assert(small_count(6) == 6)
        assert(listed_count(9) == 9)
        // Not valid: small_count(7)
    }

    {
        // In a for header, an unparenthesized `name in range` is iteration.
        // Parentheses make a condition-only membership loop unambiguous.
        cursor := 0
        for (cursor in 0..<3) {
            cursor += 1
        }
        assert(cursor == 3)

        visits := 0
        for i := 0; i in 0..<3; i += 1 {
            visits += 1
        }
        assert(visits == 3)
    }
}


conditional_expressions :: proc() {
    println("\n# ternaries and runtime switch expressions")

    {
        // The colon can start the next line, including after a comment. Only
        // the selected arm is evaluated, just as in a single-line ternary.
        temperature := 4
        clothing := temperature < 10 ? "coat" // this is still one expression
                                     : "shirt"

        nested := temperature < 0 ? "freezing"
                  : temperature < 10 ? "cool"
                                     : "warm"

        weather: Weather = true ? .Rain
                                : .Sun

        assert(clothing == "coat")
        assert(nested == "cool")
        assert(weather == .Rain)
        assert((false ? must_not_run() : 12) == 12)
    }

    {
        // Cases retain Odin's first-match behavior, multi-value matches, and
        // range matches. An arm is a single expression, not a statement block.
        score := 6
        grade := switch score {
            case 0, 1: "start"
            case 2..<5: "practice"
            case: "ready"
        }

        // Omitting the selector makes the cases boolean conditions.
        sign := switch {
            case score < 0: "negative"
            case score == 0: "zero"
            case: "positive"
        }

        assert(grade == "ready")
        assert(sign == "positive")

        // The current implementation requires a default on every expression
        // switch. There is no invented zero value for an unmatched arm.
        // Not valid: result := switch score {case 1: 10}
    }

    {
        // Expected result types also apply to complete array-valued arms.
        score := 6
        forecast: [2]Weather = switch score {
            case 1..<5: [.Sun, .Sun]
            case: [.Rain, .Snow]
        }

        // Odin also checks explicit coverage of named enum values. List them
        // all, then include the default Thor currently requires even here.
        packing := switch forecast[1] {
            case .Sun: "hat"
            case .Rain: "shell"
            case .Snow: "gloves"
            case: "unknown weather"
        }

        assert(forecast[0] == .Rain)
        assert(packing == "gloves")
    }

    {
        order := 0
        selected := switch step(&order, 6) {
            case step(&order, 5): must_not_run()
            case step(&order, 6): step(&order, 8)
            case: must_not_run()
        }

        assert(selected == 8)
        assert(order == 6568)
    }
}


compile_time_selection :: proc() {
    println("\n# compile-time selection and ordinary when")

    {
        // when is compile-time selection. Inactive branches do not need valid
        // names or executable calls. Runtime if is a separate language construct.
        when TOUR_MODE == 1 {
            SEASON :: "spring"
        } else when TOUR_MODE == 2 {
            SEASON :: "summer"
        } else {
            SEASON :: "winter"
        }

        println("  Selected season:", SEASON)

        when true {
            assert(len(SEASON) > 0)
        } else {
            this_name_is_deliberately_undefined()
        }

        // Native when branches can nest and contain Thor expressions.
        when true {
            when TOUR_MODE in 1..=3 {
                assert(true)
            }
        }

        CHOSEN :: 17 when TOUR_MODE > 0 else 0
        assert(CHOSEN == 17)
    }

    {
        // Both expression-switch forms also have a compile-time counterpart.
        // Unselected arms do not evaluate their calls or resolve their names.
        EQUIPMENT :: #switch TOUR_MODE {
            case 1: "boots"
            case 2: "sandals"
            case: "snowshoes"
        }

        COMFORT :: #switch {
            case TOUR_MODE == 3: "cold"
            case: "mild"
        }

        PROOF :: #switch {
            case true: 31
            case: another_deliberately_undefined_name
        }

        assert(PROOF == 31)
        assert(EQUIPMENT in ["boots", "sandals", "snowshoes"])
        assert(COMFORT == (TOUR_MODE == 3 ? "cold" : "mild"))
    }

    {
        // In statement position, #switch selects declarations in the surrounding
        // compile-time scope. A statement form need not produce a value.
        #switch TOUR_MODE {
            case 1, 2: WATER_BOTTLES :: 1
            case: WATER_BOTTLES :: 2
        }

        #switch {
            case WATER_BOTTLES == 1: EXTRA_LAYER :: false
            case: EXTRA_LAYER :: true
        }

        assert(EXTRA_LAYER == (TOUR_MODE == 3))
    }
}


immutable_storage :: proc() {
    println("\n# immutable storage is initialized at runtime")

    {
        order := 0
        first :> step(&order, 3)
        second: int > step(&order, 7)

        assert(first == 3 && second == 7)
        assert(order == 37)

        // :> fixes the storage after initialization. It does not turn a runtime
        // result into an Odin :: constant or permit it in a #run argument.
        // Not valid: first += 1
        // Not valid: address := &first
    }

    {
        position :> Point{x=2, y=5}
        movable := position
        movable.x = 9

        assert(position.x == 2)
        assert(movable.x == 9)

        // Directly stored fields and array elements are protected as well.
        // Not valid: position.x = 9
        // Not valid: address := &position.x
        // Not valid: frozen :> [1, 2]; writable_slice := frozen[:]
    }

    {
        // This is shallow storage immutability. A fixed slice header does not
        // make the pre-existing storage to which it points read-only.
        backing := [2, 4, 6]
        view :> backing[:]
        view[-1] = 12

        assert(backing[2] == 12)

        point := Point{x=1, y=2}
        pointer :> &point
        pointer.y = 8

        assert(point.y == 8)
        // Not valid: view = backing[:2] // replacing the fixed header
    }
}


formatting_and_strings :: proc() {
    println("\n# formatting, interpolation, and string ownership")

    {
        // These helpers allocate their result with the current allocator.
        grouped := format.group_integer(-9_001_020)
        defer delete(grouped)

        padded := format.format("中😀", width=5, align=.Center, fill='·')
        defer delete(padded)

        assert(grouped == "-9,001,020")
        assert(padded == "·中😀··")

        // Width is counted in Unicode runes, not UTF-8 bytes or screen columns.
        // Odd center padding puts the extra padding on the right.
    }

    {
        place := "cedar gate"
        walkers := 12_345

        message := f"Meet at {place}; permits {walkers:'0'9,}"
        defer delete(message)

        assert(message == "Meet at cedar gate; permits 00012,345")
        println(" ", message)

        // Doubled braces are literal braces. Only the single-braced fields
        // execute. Formatting punctuation is outside the field expression.
        order := 0
        braces := f"{{order}} {step(&order, 5)} {step(&order, 2)}"
        defer delete(braces)

        assert(braces == "{order} 5 2")
        assert(order == 52)

        // An explicit padding fill is a proper rune literal, including quotes.
        unicode := f"{"é😀":'界'^6}"
        defer delete(unicode)

        assert(unicode == "界界é😀界界")
        // Not valid: f"{walkers:>9,}" // use 9, for right alignment
    }

    {
        // Use Odin's format verbs without the leading %. f/F defaults to three
        // decimal places; e/E defaults to six; .N requests explicit precision.
        // A percentage multiplies by 100 and includes the % in the width.
        probability := 0.123456
        percentage := f"{probability:10.2%}"
        defer delete(percentage)
        assert(percentage == "    12.35%")

        // Width is a minimum. Right alignment is the default for strings too;
        // - selects left alignment, ^ centers. < and > are no longer accepted.
        // Precision on a string truncates before quoting or width is applied.
        label := f"{"é😀tail":'_'8.2s}|{"left":-6s}"
        defer delete(label)
        assert(label == "______é😀|left  ")

        // Numeric 0 padding goes after the sign. A quoted '0' is literal fill,
        // so it pads outside the complete text. # requests the native prefix.
        numbers := f"{-42:08d}|{-42:'0'8d}|{42:#08x}|{1234567.125:,.2f}"
        defer delete(numbers)
        assert(numbers == "-0000042|00000-42|0x00002a|1,234,567.12")

        // A nested pair of braces supplies a runtime minimum width. It can
        // contain a variable, function call, or any integer-valued expression.
        // The value is evaluated first, then its width, once each.
        column_width := len(numbers)/4 + 2
        padded_columns := f"{12:{column_width}}|{12:-{column_width}}|{12:'_'^{column_width}}"
        defer delete(padded_columns)
        assert(len(padded_columns) == 3*column_width + 2)
        // Other flags and literal precision still follow their normal order:
        // f"{probability:{column_width}.2%}"

        // Exact arithmetic is not converted through f64 for decimal formatting.
        // This example intentionally exceeds native integer precision.
        enormous := Integer(123456789012345678901234567890)
        third := Rational(1, 3)
        defer delete(enormous)
        defer delete(third)
        exact := f"{enormous:.2f}|{third:.20f}|{third:.6g}"
        defer delete(exact)
        assert(exact == "123456789012345678901234567890.00|0.33333333333333333333|0.333333")
        println(" ", percentage, " ", numbers)

        // Not valid: f"{third:g}". A repeating Rational has no finite shortest
        // decimal; g/G requires significant-digit precision. The default remains
        // the exact reduced fraction, 1/3. This rule also covers terminating values.
        // The same formats work in fn constants and #run, with sized storage for
        // high precision. See docs/interpolation-formatting.md for the full table.
    }

    {
        // Raw interpolation keeps backslashes literal, like an Odin raw string.
        raw := f`path\n {3}`
        defer delete(raw)

        assert(raw == `path\n 3`)

        nested := f"outside [{f"inside {8}"}]"
        defer delete(nested)

        assert(nested == "outside [inside 8]")

        // The inner allocated field is consumed and released by the outer
        // interpolation. Only the final named result remains our responsibility.
        for allocate_inner in [false, true] {
            borrowed := "existing text"
            outer := f"{(allocate_inner ? f"fresh {8}" : borrowed)}"
            defer delete(outer)

            assert(outer == (allocate_inner ? "fresh 8" : borrowed))
        }
    }

    {
        // A view that outlives its expression needs a named owner.
        owner := f"trail {7}"
        defer delete(owner)

        suffix := owner[1:]
        assert(suffix == "rail 7")

        // An immediately consumed anonymous view is safe; its owner survives
        // until the read has finished and is then released by Thor.
        assert(len(f"trail {7}"[1:]) == 6)

        // Not valid: dangling := f"trail {7}"[1:]

        // Named strings and ordinary factory returns still need an owner.
        // The next block demonstrates automatic call-argument temporaries.
    }

    {
        // Every procedure gets the same rule: an anonymous f-string argument
        // lives until the whole expression finishes, then Thor deletes it.
        // There is no special case for print or the standard fmt procedures.
        borrowed_identity :: proc(text: string) -> string { return text }
        println(f"  Automatically released: {1234567:,}")
        assert(borrowed_identity(f"nested call {7}") == "nested call 7")

        named := f"a named owner {8}"
        manual_named := f!"also a named owner {9}"
        defer delete(named)
        defer delete(manual_named)
        println(named, manual_named) // Neither named owner is deleted here.

        for allocate in [false, true] {
            // Only the selected anonymous allocation is released. The other
            // branch borrows named, which is still valid after the call.
            println(allocate ? f"temporary branch {10}" : named)
        }
        assert(named == "a named owner 8")

        // f! explicitly leaves lifetime management to the receiving API.
        // A retaining or consuming function needs this spelling, or a named
        // owner whose lifetime the program manages explicitly.
        retain :: proc(target: ^string, text: string) { target^ = text }
        consume :: proc(text: string) { delete(text) }
        retained: string
        retain(&retained, f!"retained {11}")
        defer delete(retained)
        consume(f!`consumed raw {12}`)
        assert(retained == "retained 11")

        messages: [dynamic]string
        append(&messages, f!"stored by append {13}")
        assert(messages[0] == "stored by append 13")
        delete(messages[0]) // Free element bytes before deleting the container.
        delete(messages)

        // f! does not verify that an API really takes ownership. Likewise,
        // retaining a default f argument would outlive its temporary storage.
        // Ordinary string factory calls keep their usual manual contracts.
    }
}


sets_and_ownership :: proc() {
    println("\n# sets, mathematical operations, and shallow copies")

    {
        // A zero-initialized Set is usable and allocates lazily on insertion.
        // The library spelling and the Thor spelling name the same type.
        staff: set[string]
        defer delete(staff)

        same_type: sets.Set(string)
        defer delete(same_type)

        assert(type_of(staff) == type_of(same_type))
        assert(len(staff) == 0)

        staff += "Ada"
        staff += "Lin"
        staff += "Ada" // duplicate insertion is harmless

        visitors := set[string]{"Lin", "Mira", "Mira"}
        defer delete(visitors)

        assert(len(staff) == 2 && len(visitors) == 2)

        // Each mathematical result owns an independent table.
        everyone := staff | visitors
        defer delete(everyone)

        common := staff & visitors
        defer delete(common)

        staff_only := staff - visitors
        defer delete(staff_only)

        either := staff ~ visitors
        defer delete(either)

        assert(len(everyone) == 3)
        assert("Lin" in common)
        assert("Ada" in staff_only)
        assert(either == set[string]{"Ada", "Mira"})

        // This anonymous Set literal is consumed by == and cleaned up. In a
        // chain, fresh intermediates are also consumed rather than leaked.
        merged := staff | visitors | set[string]{"Nia"}
        defer delete(merged)

        assert(len(merged) == 4)

        // Clone when another independently owned table is wanted. Assignment
        // alone copies a header and aliases the original table.
        live := sets.clone(staff)
        defer delete(live)

        live += "Nia"
        live -= "Ada"
        live |= visitors
        live &= everyone
        live -= common
        live ~= staff_only

        assert(live == either)
        assert(len(staff) == 2)

        visits := 0
        for name in live {
            assert(name in either)
            visits += 1
        }
        assert(visits == 2)

        // Iteration order is unspecified. Do not depend on hash-table order.
        borrowed_header := live
        assert("Mira" in borrowed_header)
        // Do not delete borrowed_header as well: it is not another owner.

        clear(live)
        assert(len(live) == 0)
    }

    {
        // Set element ownership remains shallow. This Set borrows a string
        // owned by this scope. Defer order frees the table before the string.
        key := f"checkpoint {4}"
        defer delete(key)

        checkpoints := set[string]{key}
        defer delete(checkpoints)

        assert(key in checkpoints)
        // delete(checkpoints) releases its table, not the bytes of key.
    }

    {
        // Set xor follows Odin's ~ spelling. ^ remains pointer dereference,
        // even without whitespace before the assignment operator.
        value := 10
        pointer := &value
        pointer^=12
        pointer^~=5

        assert(value == 9)
    }
}


negative_indices :: proc() {
    println("\n# negative indices and counted sequence views")

    {
        samples := [5, 15, 25, 35]
        last := -1

        assert(samples[last] == 35)
        samples[-2] += 1

        tail_pointer := &samples[-1]
        tail_pointer^ = 40

        middle := samples[1:-1]
        tail := samples[-2:]

        // Slices keep Odin's rules: compare their contents explicitly rather
        // than using ==, which does not compare slice elements.
        assert(len(middle) == 2 && middle[0] == 15 && middle[1] == 26)
        assert(tail[0] == 26 && tail[1] == 40)

        // -len is the first element. Negative bounds are translated relative
        // to the length; they do not disable ordinary bounds checks.
        assert(samples[-len(samples)] == 5)
        // Out of bounds at runtime: samples[-len(samples)-1]
        // A reversed slice is not a request to reverse the sequence.
    }

    {
        growing: [dynamic]int
        defer delete(growing)
        append(&growing, 8, 16, 24)

        growing[-1] = 48
        assert(growing[-1] == 48)

        assert("forest"[-3:] == "est")

        // Odin strings are UTF-8 byte sequences. Negative string indexing still
        // returns a byte, not a Unicode rune. Use rune data for rune indexing.
        assert("café"[-1] == u8(0xa9))
        letters := ['c', 'a', 'f', 'é']
        assert(letters[-1] == 'é')

        // A negative map key keeps native map meaning; it is not normalized.
        names: map[int]string
        defer delete(names)
        names[-1] = "saved"
        names[0] = "current"
        assert(names[-1] == "saved")
    }

    {
        sequence :: proc(data: []int, order: ^int) -> []int {
            order^ = order^ * 10 + 1
            return data
        }

        index :: proc(order: ^int) -> int {
            order^ = order^ * 10 + 2
            return -1
        }

        data := [4, 8, 12]
        order := 0
        found := sequence(data[:], &order)[index(&order)]

        assert(found == 12)
        assert(order == 12)
    }
}


power_and_native_expansion :: proc() {
    println("\n# numeric power and native expansion")

    {
        // Power associates to the right and binds above multiplication and
        // unary signs. Parentheses make a negative base explicit.
        assert(3 ** 2 ** 2 == 81)
        assert((3 ** 2) ** 2 == 81)
        assert(2 ** 3 ** 2 == 512)
        assert((2 ** 3) ** 2 == 64)
        assert(-3 ** 2 == -9)
        assert((-3) ** 2 == 9)
        assert(2 * 3 ** 2 + 1 == 19)

        assert(4.0 ** -2 == 0.0625)
        assert(9.0 ** 0.5 == 3)
        assert(0 ** 0 == 1)

        // Literals follow native coercion. Typed values must match exactly.
        base: f32 = 4
        exponent: f32 = -2
        assert(type_of(base ** exponent) == f32)
        assert(base ** exponent == 0.0625)
        assert(base ** -2 == 0.0625)

        integer_base: int = 3
        floating_exponent: f64 = 2
        // Not valid: integer_base ** floating_exponent
        assert(f64(integer_base) ** floating_exponent == 9)

        // A negative integer exponent is an error when evaluated at compile
        // time and a panic when reached at runtime. Use floats for fractions.
        // Not valid: reciprocal :: 4 ** -2
    }

    {
        // Untyped constant powers remain usable as ordinary numeric constants.
        CAPACITY :: 3 ** 2
        storage: [CAPACITY]int
        assert(len(storage) == 9)

        // Runtime integer arithmetic keeps the native fixed-width behavior.
        small_base: u8 = 16
        small_exponent: u8 = 2
        assert(small_base ** small_exponent == 0)
    }

    {
        add :: proc(a, b: int) -> int {
            return a + b
        }

        pair := Point{x=3, y=4}

        // Prefix ** still expands fields into arguments. Position distinguishes
        // this from infix power; no Odin spelling had to be removed.
        assert(add(**pair) == 7)
        assert(3 ** 4 == 81)
    }
}


typed_loops :: proc() {
    println("\n# typed numeric ranges and checked array/slice indices")

    {
        // The annotation gives untyped bounds an f64 context. The loop value
        // remains immutable, and the range advances by one in that type.
        // This also supplies the matching exponent type required by power.
        ROUNDS :: 3
        HOUSE_ODDS :: f64(0.5)
        last := 0.0
        for round: f64 in 1..=ROUNDS {
            last = HOUSE_ODDS ** round
        }
        assert(last == 0.125)

        // An already typed bound still needs an explicit conversion.
        count: int = 3
        for round: f64 in 1..<f64(count) {
            assert(round >= 1 && round < 3)
        }
        // Not valid: for round: f64 in 1..=count {}
    }

    {
        values := [10, 20, 30]
        total := 0

        // Parentheses make the annotation belong only to index. The element
        // type remains int; both value bindings are immutable in the body.
        for value, (index: i8) in values {
            total += value + int(index)
        }
        assert(total == 63)

        // A reference element stays mutable; its separate index is readonly.
        for &value, (index: u8) in values {
            value += int(index)
        }
        assert(values == [10, 21, 32])

        // Dynamic-length views receive the same checked conversion. Nothing
        // allocates merely to change the index type, and the input is evaluated
        // according to ordinary Odin iteration semantics.
        view := values[:]
        for value, (index: u16) in view do assert(value == values[index])

        // A fixed array whose last index exceeds max(i8) fails at compile time.
        // A slice/dynamic array panics if iteration reaches such an index,
        // including when ordinary assertions have been disabled.
        // Not valid: too_many: [129]int; for _, (index: i8) in too_many {}
        // Not valid: for value, index: i8 in values {} // group is ambiguous
        // Not valid: for value: f64 in values {}      // no element conversion
    }
}


expression_interactions :: proc() {
    println("\n# lazy expressions, loops, and callbacks")

    {
        order := 0
        skipped := false && (switch {
            case step(&order, 9) == 9: true
            case: false
        })

        assert(!skipped)
        assert(order == 0)

        // The allocated condition/post operands are consumed on each iteration.
        // Generated work must not be hoisted out of the loop or lazy branch.
        visits := 0
        for i := 0; i < 3 && len(f"round {i}") > 0; i += len(f"{i}") {
            visits += 1
        }
        assert(visits == 3)
    }

    {
        values := [3, 6, 9]
        report := f"last {values[-1]}, result {switch values[-1] {case 9: "ready"; case: "wait"}}"
        defer delete(report)

        assert(report == "last 9, result ready")

        // Anonymous collection storage also survives exactly as long as needed
        // for iteration, including continue and break paths.
        visits := 0
        for code in set[int]{4, 8, 12} {
            visits += 1
            if code == 8 {
                continue
            }
        }
        assert(visits == 3)
    }

    {
        // Nonallocating Thor expressions also work in a native C callback.
        // Such a callback has no implicit Odin context to allocate through.
        last_square :: proc "c"(values: []int) -> int {
            return values[-1] ** 2
        }

        values := [2, 5]
        assert(last_square(values[:]) == 25)
    }
}


// These functions are deliberately separate from the tour procedures: fn bodies
// can be verified and evaluated without copying the main program or its I/O.
triangular :: fn(value: int) -> int {
    if value <= 0 {
        return 0
    }
    return value + triangular(value - 1)
}

double_value :: fn(value: $T) -> T where T in [int, f32, f64] {
    return value * 2
}

copied_sum :: fn(values: []int) -> int {
    // Inputs are transitively read-only, including slice elements. A fresh
    // local copy may be changed, and scratch allocations are cleaned up.
    scratch := make([]int, len(values))
    copy(scratch, values)

    total := 0
    for i in 0..<len(scratch) {
        scratch[i] += 1
        total += scratch[i]
    }
    return total

    // Not valid in fn: values[0] = 99
    // Not valid in fn: fmt.println(total)
    // Mutable-reference iteration (`for &value in scratch`) is currently
    // rejected along with raw addresses. Use indexed writes as above.
}

ordinary_total :: proc() -> int {
    values := [3, 7, 11]
    return copied_sum(values[:])
}

Unit_Count :: distinct i32

Snapshot :: struct {
    units: Unit_Count,
    forecast: [2]Weather,
    samples: [3]int,
    negative_zero: f64,
}

snapshot :: fn(units: int) -> Snapshot {
    return {
        units=Unit_Count(units),
        forecast=[.Sun, .Rain],
        samples=[units, units + 1, units + 2],
        negative_zero=-f64(0),
    }
}

render_label :: fn(value: int) -> string {
    return f"route {value}"
}

never_evaluate :: fn() -> int {
    assert(false, "An unselected pure call was evaluated.")
    return 0
}

// Forward dependencies are resolved by the evaluator. These remain real Odin
// constants; automatic evaluation does not require the #run spelling.
LATER_CODE :: double_value(FIRST_CODE)
FIRST_CODE :: guide.next_code(5)
TABLE_LENGTH :: triangular(4)

// #run also accepts a directly named ordinary proc when its relevant transitive
// call graph is eligible. Ordinary procs are not automatically folded like fn.
EXPLICIT_TOTAL :: #run ordinary_total()
SAVED_SNAPSHOT :: #run snapshot(18)
BORROWED_LABEL :: #run render_label(14)


pure_functions_and_constants :: proc() {
    println("\n# fn, #run, contents, and automatic constants")

    {
        assert(FIRST_CODE == 7)
        assert(LATER_CODE == 14)
        assert(TABLE_LENGTH == 10)
        assert(EXPLICIT_TOTAL == 24)

        table: [TABLE_LENGTH]int
        assert(len(table) == 10)

        // Exact result types survive the helper process, including distinct
        // integers, enums and fixed arrays. Floating-point bits survive too.
        assert(type_of(SAVED_SNAPSHOT.units) == Unit_Count)
        assert(SAVED_SNAPSHOT.units == Unit_Count(18))
        assert(SAVED_SNAPSHOT.forecast[1] == .Rain)
        assert(SAVED_SNAPSHOT.samples[2] == 20)
        assert(transmute(u64)SAVED_SNAPSHOT.negative_zero == u64(1) << 63)

        // A constant string is borrowed Odin constant storage, not a runtime
        // allocation to delete. Runtime-owned results are shown in the next topic.
        assert(BORROWED_LABEL == "route 14")
    }

    {
        // A mutable local is a runtime input, even when initialized with a literal.
        // The readonly runtime-storage form is not a compile-time constant either.
        value := 8
        fixed :> value + 1
        assert(double_value(value) == 16)
        assert(double_value(fixed) == 18)

        // Not valid: FORCED :: #run double_value(value)

        values := [2, 5, 8]
        assert(copied_sum(values[:]) == 18)
        assert(values[0] == 2) // mutation was confined to the fresh scratch copy
    }

    {
        SELECTED :: #switch {
            case TABLE_LENGTH == 10: double_value(9)
            case: never_evaluate()
        }

        assert(SELECTED == 18)

        // An unresolved runtime path keeps a normal lazy runtime call. Neither
        // automatic folding nor #run scheduling may execute the skipped branch.
        flag := false
        result := flag ? never_evaluate() : 23
        assert(result == 23)

        when false {
            UNREACHED :: #run never_evaluate()
        }
    }
}


Travel_Log :: struct {
    title: string,
    labels: []string,
    lengths: []int,
}

build_log :: fn(label: string, start: int) -> Travel_Log {
    labels := make([]string, 2)
    labels[0] = label
    labels[1] = label

    lengths := make([]int, 3)
    for i in 0..<len(lengths) {
        lengths[i] = start + i
    }

    return {
        title=f"journey {label}",
        labels=labels,
        lengths=lengths,
    }
}

finish_after_defers :: fn() -> []int {
    values := [10, 20, 30]
    defer values[1] = 99
    return values[:]
}

Empty :: struct {}

empty_slots :: fn(count: int) -> []Empty {
    return make([]Empty, count)
}


owned_pure_results :: proc() {
    println("\n# owned pure results and independent storage")

    {
        route_name := "cedar"
        start := 40
        runtime_log := build_log(route_name, start)
        defer pure.destroy(runtime_log)

        assert(runtime_log.title == "journey cedar")
        assert(len(runtime_log.lengths) == 3)
        assert(runtime_log.lengths[0] == 40 && runtime_log.lengths[1] == 41 && runtime_log.lengths[2] == 42)

        // A pure result owns its returned strings/slices recursively. Repeated
        // aliases become separate occurrences, each with independent backing.
        assert(runtime_log.labels[0] == runtime_log.labels[1])
        assert(raw_data(runtime_log.labels[0]) != raw_data(runtime_log.labels[1]))

        // Ordinary assignment is still shallow. Use pure.clone for a second
        // independently owned tree, and destroy each independent tree once.
        independent := pure.clone(runtime_log)
        defer pure.destroy(independent)

        independent.lengths[0] = 70
        assert(runtime_log.lengths[0] == 40)

        borrowed_header := runtime_log
        assert(borrowed_header.title == runtime_log.title)
        // Do not call pure.destroy(borrowed_header) as well.
    }

    {
        // Slice-containing compile-time results need runtime storage. They
        // cannot become an Odin :: constant with a hidden mutable allocation.
        compiled := #run build_log("compiled", 50)
        defer pure.destroy(compiled)

        // Not valid: CONSTANT_LOG :: #run build_log("compiled", 50)

        assert(compiled.title == "journey compiled")
        assert(compiled.lengths[2] == 52)

        // An automatically folded runtime binding has the same fresh-storage
        // semantics. The cache never hands out one shared mutable allocation.
        automatic := build_log("compiled", 50)
        defer pure.destroy(automatic)

        compiled.lengths[0] = 80
        assert(automatic.lengths[0] == 50)
    }

    {
        // User defers run before the returned contents are promoted out of
        // scratch storage. Returning a local array view therefore stays valid.
        finished := #run finish_after_defers()
        defer pure.destroy(finished)

        assert(len(finished) == 3 && finished[0] == 10 && finished[1] == 99 && finished[2] == 30)

        // A zero-sized element still has a logical count in a serialized slice.
        slots := #run empty_slots(4)
        defer pure.destroy(slots)

        assert(len(slots) == 4)
        assert(size_of(Empty) == 0)
    }
}


size_for :: fn($T: typeid) -> int {
    return size_of(T)
}

packet_width :: proc(sample: $T, $N: int) -> int {
    // These depend on the actual generic instance. A runtime procedure can
    // contain compile-time work for its type and constant parameters.
    COUNT :: double_value(N)
    BYTES :: #run size_for(T)
    buffer: [COUNT]T

    return len(buffer) * BYTES
}


generic_evaluation :: proc() {
    println("\n# specialized compile-time work inside generic procedures")

    small: i16 = 3
    large: u64 = 4

    assert(packet_width(small, 3) == 12)
    assert(packet_width(large, 2) == 32)

    // The value argument can be runtime data. Its concrete type and the $N
    // parameter are enough to resolve this instance's COUNT and BYTES.
    // Unknown active #run arguments are diagnosed; Thor does not invent zeros.
    // Compile-time execution currently runs native helpers, not cross targets.
}


// These are genuine constants: their exact contents live in read-only data.
// Reading them needs no allocation; copying one into a runtime owner does.
TOUR_BIG_BASE :: Integer(1234567890123456789012345678901234567890)
TOUR_BIG_NEXT :: TOUR_BIG_BASE * 2 + 1
TOUR_BIG_FRACTION :: Rational(0.1) + 0.2


big_numbers_and_construction :: proc() {
    println("\n# Integer/Rational construction and exact source digits")

    {
        // No import is needed for the two type names or their constructors.
        // A requested big-number type preserves every source digit. Native
        // inference stays native: an overflowing int/u128 never promotes itself.
        enormous: Integer = 123456789012345678901234567890123456789012345678901234567890
        decimal: Rational = 0.1000000000000000000000000000000000000001
        defer delete(enormous)
        defer delete(decimal)

        assert(enormous == Integer(123456789012345678901234567890123456789012345678901234567890))
        assert(decimal == Rational(1000000000000000000000000000000000000001, 10000000000000000000000000000000000000000))
        basic.print("  Unquoted Integer:", enormous)
        basic.print("  Exact decimal:", decimal)

        native := 12
        assert(type_of(native) == int)
        // Not valid: overflow := max(u128) * 2
        // Not valid: rounded := Integer(f64(1.5))
    }

    {
        // Uninitialized numbers represent zero without allocating storage.
        // Read-only constants can also be borrowed with an allocator that
        // forbids allocation. Creating a nonzero runtime copy would allocate.
        previous := context.allocator
        context.allocator = mem.nil_allocator()
        defer context.allocator = previous

        zero_integer: Integer
        zero_fraction: Rational
        assert(big.is_zero(zero_integer) && big.is_zero(zero_fraction))
        assert(big.equal(TOUR_BIG_BASE, TOUR_BIG_BASE))
        delete(zero_integer)
        delete(zero_fraction)
    }

    {
        // Native integers convert explicitly and aliases denote the same type.
        // Fractions normalize their signs and reduce by the common divisor.
        Count :: Integer
        source: i64 = -42
        count := Count(source)
        fraction := Rational(numerator=6, denominator=-8)
        whole := Rational(5)
        zero: Rational
        defer delete(count)
        defer delete(fraction)
        defer delete(whole)
        defer delete(zero)

        assert(count == -42 && fraction == Rational(-3, 4))
        assert(whole == Rational(5, 1))
        assert(zero.numerator == 0 && zero.denominator == 1)

        // Text construction returns value/status. Repeating decimals use
        // parentheses IN STRINGS; no new numeric suffix is involved.
        parsed, parsed_ok := Integer("0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF")
        repeating, repeating_ok := Rational("0.1(6)")
        from_parts, parts_ok := Rational("-22", "-13")
        bad, bad_ok := Integer("not a number")
        defer delete(parsed)
        defer delete(repeating)
        defer delete(from_parts)
        defer delete(bad)

        assert(parsed_ok && parsed == Integer(max(u128)))
        assert(repeating_ok && repeating == Rational(1, 6))
        assert(parts_ok && from_parts == Rational(22, 13))
        assert(!bad_ok && bad == 0)
        // Allocation failure is an error, not malformed text/ok=false.
        // A zero denominator panics (or is rejected when statically known).
        // Not valid: impossible := Rational(1, 0)
    }

    {
        // Properties need no helper import and return independent owners.
        // Changing the original fraction cannot change saved components.
        fraction := Rational(22, 13)
        pointer := &fraction
        numerator := fraction.numerator
        denominator := pointer.denominator
        defer delete(fraction)
        defer delete(numerator)
        defer delete(denominator)

        big.set_fraction(&fraction, numerator=2, denominator=4)
        assert(numerator == 22 && denominator == 13)
        assert(fraction == Rational(1, 2))
        // Not valid: fraction.numerator = 9
        // Not valid: address := &fraction.denominator
    }
}


tour_big_local :: proc(value: Integer) -> Integer {
    // Parameters borrow immutable values. Our local is an independent owner.
    // A direct numeric return is preserved before this defer deletes the local.
    local := value
    defer delete(local)
    local += 1
    return local
}

tour_big_total :: proc(first: Integer, rest: ..Integer) -> Integer {
    total := first
    defer delete(total)
    for value in rest { total += value }
    return total
}

Tour_Big_Callback :: struct {
    apply: proc(self: ^Tour_Big_Callback, value: Integer) -> Integer,
}

tour_big_increment :: proc(self: ^Tour_Big_Callback, value: Integer) -> Integer {
    return value + 1
}


big_ownership_and_aggregates :: proc() {
    println("\n# big-number owners, simultaneous assignment, and shallow aggregates")

    {
        a := Integer(10)
        b := a // Direct numeric assignment clones, including immutable bindings.
        saved :> b
        defer delete(a)
        defer delete(b)
        defer delete(saved)

        a = Integer(20) // Replacing an owner frees its old storage.
        assert(b == 10 && saved == 10)
        a, b = b, a + 1 // Preserve both old RHS values before replacing either.
        assert(a == 10 && b == 21 && saved == 10)
        a = a
        assert(a == 10)

        returned := tour_big_local(a)
        generic := guide.identity(a)
        native := guide.clone_big(a)
        explicit := big.clone(a)
        defer delete(returned)
        defer delete(generic)
        defer delete(native)
        defer delete(explicit)

        a += 2
        assert(returned == 11 && generic == 10 && native == 10 && explicit == 10)
        // guide/clone_big.odin is native Odin: its return explicitly clones.
        // Native Odin header assignments themselves remain shallow.
    }

    {
        Holder :: struct { value: Integer }
        source := Integer(42)
        record := Holder{value=source} // Individual numeric fields clone.
        member := record.value // Reading a numeric member also clones.
        alias := record // A WHOLE record copy remains shallow.
        independent := pure.clone(record) // Explicit independent deep tree.
        defer delete(source)
        defer delete(record.value)
        defer delete(member)
        defer pure.destroy(independent)

        assert(alias.value == 42)
        independent.value += 1
        record.value = Integer(9)
        assert(source == 42 && member == 42 && independent.value == 43)

        // Replacing record.value invalidated alias.value. Do not read/delete
        // alias again. A shallow aggregate copy is not another numeric owner.
        _ = alias
        // Arrays, slices and native copy/append(..slice) have the same boundary.
    }

    {
        values := [Integer(1), Integer(2)]
        defer for &value in values { delete(value) }
        last := values[-1] // Negative-index reads still make independent owners.
        defer delete(last)
        values[-1] = values[0]
        assert(last == 2 && values[-1] == 1)

        growing: [dynamic]Integer
        defer delete(growing) // Only the outer container, after its elements.
        defer for &value in growing { delete(value) }
        append(&growing, values[0], Integer(8))
        growing[0] += 3
        assert(values[0] == 1 && growing[0] == 4)
        // By-value iteration borrows: do not delete(value) in `for value in ...`.

        entries: map[string]Integer
        defer delete(entries)
        entries["answer"] = Integer(7)
        entries["answer"] = entries["answer"] + 1
        copied := entries["answer"]
        defer delete(copied)
        delete(entries["answer"])
        assert(copied == 8)
        // Containers do not recursively destroy their numeric elements.
        // Big-number map keys / set[Integer] are currently unsupported.
    }
}


big_arithmetic_and_control_flow :: proc() {
    println("\n# exact operators, literal coercion, and lazy big-number results")

    {
        a: Integer = 1
        defer delete(a)
        a += 2 // Untyped integer literals adopt the big operand's type.
        a *= 4
        a -= 1
        a /= 11
        assert(a == 1)

        native: i64 = 2
        a += Integer(native)
        assert(a == 3)
        // Not valid: a += native // Typed native operands require conversion.
        // Not valid: a + Rational(1) // The two big types also remain distinct.

        // Typed variadic and method parameters supply the same literal context.
        // Anonymous argument owners are released after the call returns.
        total := tour_big_total(Integer(1), 2, 3)
        callback := Tour_Big_Callback{tour_big_increment}
        incremented := callback->apply(2)
        defer delete(total)
        defer delete(incremented)
        assert(total == 6 && incremented == 3)

        product: Integer = 123456789012345678901234567890 * 123456789012345678901234567890
        defer delete(product)
        assert(product > TOUR_BIG_BASE)
        assert(Integer(-7) / 3 == -2)
        assert(Integer(-7) % 3 == -1) // Remainder has the dividend's sign.
        assert(Integer(-7) %% 3 == 2) // Odin modulo has the divisor's sign.
        assert(Integer(7) %% -3 == -2)

        // Infinite two's-complement bitwise operations; >> extends the sign.
        assert((Integer(5) & 3) == 1 && (Integer(5) | 3) == 7)
        assert((Integer(5) ~ 3) == 6 && (Integer(5) &~ 3) == 4)
        assert(~Integer(5) == -6 && (Integer(-7) >> 1) == -4)
        assert((Integer(1) << 100) == Integer(2) ** 100)

        fraction: Rational = 1/3 + 0.25 // Exact 7/12, including the division.
        defer delete(fraction)
        assert(fraction == Rational(7, 12))
        assert(Rational(2, 3) ** -3 == Rational(27, 8))
        assert(Rational(7, 3) % Rational(1, 2) == Rational(1, 3))
        // Not valid: Integer(2) ** -3 // Use Rational for reciprocals.
        // Not valid: Rational(2) ** 0.5 // Exponents must be whole.
        // Not valid: Rational(1, 2) & 1 // Bitwise operators need Integer.
    }

    {
        // Only the selected path executes. Anonymous arithmetic/constructors
        // and unselected branches do not leak hidden number allocations.
        a := Integer(10)
        b := Integer(20)
        choice := true ? a + 1 : Integer(must_not_run())
        defer delete(a)
        defer delete(b)
        defer delete(choice)
        assert(choice == 11)

        selected := switch a {
            case 0..<5: Integer(0)
            case 10: a + b
            case: panic("unreachable")
        }
        defer delete(selected)
        assert(selected == 30)

        a, b = switch {
            case a < b: b, a + 1
            case: a, b
        }
        assert(a == 20 && b == 11)
        a = switch { case false: Integer(99); case: break }
        assert(a == 20) // A skipped assignment preserves the existing owner.

        parsed := Integer("bad") or_else Integer(7)
        defer delete(parsed)
        assert(parsed == 7)

        values := [a, b]
        defer for &value in values { delete(value) }
        assert(a in [1, 20, 100] && Integer(11) in values)
        assert(7 not_in values[:] && 21 in [a + 1, b + 1])
    }
}


tour_big_power :: fn(base: Integer, count: int) -> Integer {
    result := Integer(1)
    defer delete(result)
    for _ in 0..<count { result *= base }
    return result
}

Tour_Big_Report :: struct {
    value: Integer,
    ratio: Rational,
    rows: []Integer,
}

tour_big_report :: fn() -> Tour_Big_Report {
    rows := make([]Integer, 2)
    rows[0] = Integer(7)
    rows[1] = Integer(8)
    return {Integer(123456789012345678901234567890), Rational(1, 3), rows}
}

TOUR_BIG_POWER :: #run tour_big_power(Integer(22), 20)


big_constants_and_evaluation :: proc() {
    println("\n# exact constants, fn/#run, and independent returned trees")

    {
        assert(TOUR_BIG_NEXT == TOUR_BIG_BASE * 2 + 1)
        assert(TOUR_BIG_FRACTION == Rational(3, 10))
        assert(TOUR_BIG_POWER == Integer(705429498686404044207947776))

        // Both #switch forms and when can consume exact big-number constants.
        CHOICE :: #switch TOUR_BIG_BASE {
            case 0..<100: Integer(0)
            case: TOUR_BIG_NEXT
        }
        BOOLEAN_CHOICE :: #switch {
            case TOUR_BIG_BASE > 100: Rational(1, 3)
            case: Rational(0)
        }
        when TOUR_BIG_NEXT > TOUR_BIG_BASE {
            assert(CHOICE == TOUR_BIG_NEXT)
        } else {
            missing_name_is_in_an_inactive_branch()
        }
        assert(BOOLEAN_CHOICE == Rational(1, 3))

        LOCAL_POWER :: #run tour_big_power(Integer(3), 4)
        assert(LOCAL_POWER == 81)

        copied := TOUR_BIG_BASE
        defer delete(copied)
        copied += 1
        assert(copied != TOUR_BIG_BASE)
        // Not valid: TOUR_BIG_BASE += 1
        // Not valid: delete(TOUR_BIG_BASE)
        // Not valid: address := &TOUR_BIG_BASE
    }

    {
        // Known fn calls fold automatically. Explicit #run forces eligible
        // calls; both reconstruct an independent runtime owner at each use.
        automatic := tour_big_power(Integer(22), 20)
        forced := #run tour_big_power(Integer(22), 20)
        defer delete(automatic)
        defer delete(forced)
        automatic += 1
        assert(forced == TOUR_BIG_POWER && automatic != forced)

        runtime_count := 3
        runtime := tour_big_power(forced, runtime_count)
        defer delete(runtime)
        assert(runtime == forced ** 3)
        // Cached contents never share live number storage between owners.
        // Native generic VALUE parameters ($N: Integer) are unsupported;
        // use a normal numeric fn parameter as above. Type generics work.
    }

    {
        // Numbers nested in returned records/slices survive scratch cleanup.
        // Aggregate #run results need :=; pure.destroy releases the whole tree.
        first := #run tour_big_report()
        second := #run tour_big_report()
        defer pure.destroy(first)
        defer pure.destroy(second)
        first.value += 1
        first.rows[0] += 10
        assert(first.value != second.value)
        assert(second.rows[0] == 7 && second.rows[1] == 8)
        assert(second.ratio == Rational(1, 3))
        // Not valid: REPORT :: #run tour_big_report()
        // A whole runtime assignment `alias := second` would still be shallow.
    }
}


tour_big_component :: proc(value: $T) -> Integer {
    // Rational and ^Rational dispatch to the computed read-only property.
    // An ordinary record with a real numerator field keeps that field instead.
    return value.numerator
}

tour_big_description :: fn(value: int) -> string {
    return f"exact {Rational(value, 13)}"
}


big_formatting_and_libraries :: proc() {
    println("\n# numerical builtins, shared basic, formatting, and generic properties")

    {
        values := [Integer(8), Integer(-2), Integer(3)]
        defer for &value in values { delete(value) }
        low := min(**values) // Fixed-array expansion always has a value.
        high, found := max(..values[:]) // Slice reduction can be empty.
        magnitude := abs(values[1])
        limited := clamp(values[0], -2, 2)
        defer delete(low)
        defer delete(high)
        defer delete(magnitude)
        defer delete(limited)
        assert(found && low == -2 && high == 8 && magnitude == 2 && limited == 2)

        empty: []Integer
        missing, present := min(..empty)
        defer delete(missing)
        assert(!present && missing == 0)
        // Not valid: min(Integer) // There is no finite type minimum.

        total := basic.sum(values[:])
        sorted := basic.sorted(values[:])
        defer delete(total)
        defer delete(sorted)
        assert(total == 9)
        assert(sorted[0] == -2 && sorted[-1] == 8)
        // sorted owns its outer slice, but its numeric elements are shallow
        // aliases of values. Delete the elements through values exactly once.
        native := [1, 2, 3]
        assert(basic.sum(native[:]) == 6)
    }

    {
        fraction := Rational(22, 13)
        component := tour_big_component(&fraction)
        Record :: struct { numerator: Integer }
        record := Record{Integer(7)}
        ordinary := tour_big_component(record)
        defer delete(fraction)
        defer delete(component)
        defer delete(record.numerator)
        defer delete(ordinary)
        assert(component == 22 && ordinary == 7)

        text := f"{Integer(12345678901234567890):,} {fraction:8} {Integer(2) + 3}"
        nested := f"outer {f"inner {Integer(2) ** 10}"}"
        compiled := #run tour_big_description(22)
        defer delete(text)
        defer delete(nested)
        defer delete(compiled)
        assert(text == "12,345,678,901,234,567,890    22/13 5")
        assert(nested == "outer inner 1024" && compiled == "exact 22/13")
        basic.print("  Formatted:", text)
        // Not valid: f"{fraction:,}" // Grouping needs Integer, not Rational.

        Row :: struct { name: string, amount: Integer, ratio: Rational }
        rows := [Row{"sample", Integer(3), fraction}]
        defer delete(rows[0].amount)
        defer delete(rows[0].ratio)
        table := basic.table(rows[:])
        defer delete(table)
        basic.print(table, end="") // Numeric columns are right aligned.

        explicit := big.to_string(fraction)
        defer delete(explicit)
        assert(explicit == "22/13")
    }

    {
        // Native fmt uses an explicit shared registry. Basic/fstrings above
        // require no registration. Detach before releasing the caller's map.
        registry := new(map[typeid]fmt.User_Formatter)
        defer free(registry)
        format.add_big_formatters(registry)
        fmt.set_user_formatters(registry)
        defer format.destroy_formatter_registry(registry)

        text := fmt.aprintf("%d %v", Integer(12345678901234567890), Rational(22, 13))
        defer delete(text)
        assert(text == "12345678901234567890 22/13")
        println("  Native fmt:", text)
    }
}


main :: proc() {
    println("Thor: a commented, executable language tour")

    imports_and_odin()
    arrays_and_context()
    membership_and_constraints()
    conditional_expressions()
    compile_time_selection()
    immutable_storage()
    formatting_and_strings()
    sets_and_ownership()
    negative_indices()
    power_and_native_expansion()
    typed_loops()
    expression_interactions()
    pure_functions_and_constants()
    owned_pure_results()
    generic_evaluation()
    big_numbers_and_construction()
    big_ownership_and_aggregates()
    big_arithmetic_and_control_flow()
    big_constants_and_evaluation()
    big_formatting_and_libraries()

    println("\nAll Thor walkthrough examples passed.")
}

/*
    The basic package is part of Thor's bundled collection.

    Run from the Thor checkout:
        ./thor run examples/basic

    The same import works from a project elsewhere on your machine. No extra
    collection flag or copied library file is required.
*/
package main

import "thor:basic"

main :: proc() {
    // A plain import uses the basic namespace. To use individual names without
    // a prefix instead, write: import "thor:basic" / sorted, print
    basic.print("Thor basic helpers")

    // Boolean reductions accept individual bools or an existing slice.
    // Scanning stops at the first decisive value; arguments evaluate first.
    checks :> []bool{true, false, true}
    if !basic.and() || basic.or() { panic("empty boolean identities") }
    if basic.and(..checks) || !basic.or(..checks) { panic("boolean reductions") }
    basic.print("and/or:", basic.and(true, true), basic.or(false, true))

    // Use one growing arena for this block, including allocations inside called
    // helpers. Caller-scope cleanup also runs on early return/break/continue.
    // The allocator must be created directly in this scope, not in a switch arm.
    {
        context.allocator = basic.scoped_arena()
        values := make([]int, 100)
        text := f"Created {len(values)} values"
        if len(values) != 100 || text != "Created 100 values" { panic("scoped arena") }
        basic.print(text)
        // No individual deletes here. Keep these allocations inside the block.
    }
    // The enclosing allocator is restored; context.temp_allocator is unchanged.

    // Runtime hashing needs no allocation or deletion. FNV64a on a string
    // matches Odin's compile-time #hash when using its default seed.
    {
        h := basic.hash("hello", algorithm=.FNV64a)
        if h != #hash("hello", "fnv64a") { panic("FNV compatibility") }
        data := [1, 2, 3]
        owned := make([dynamic]int, 0, 100)
        defer delete(owned)
        append(&owned, ..data[:])
        if basic.hash(data) != basic.hash(owned) { panic("hash live contents") }

        // Maps/sets need order-independent hashing and are deferred. The
        // non-panicking API lets a caller handle unsupported data explicitly.
        unsupported: map[string]int
        result, err := basic.try_hash(unsupported)
        if result != 0 || err != .Unsupported_Type { panic("hash error") }
        basic.print("runtime hash:", h)
    }

    // Sorting returns independent slice storage and leaves its input alone.
    // The inline generic default comparator supplies ascending order.
    numbers := []int{8, 3, 5, 3}
    ascending := basic.sorted(numbers)
    defer delete(ascending)

    assert(ascending[0] == 3 && ascending[3] == 8)
    assert(numbers[0] == 8)
    basic.print("ascending:", ascending)

    // Collection conversion allocates independent storage in both directions.
    // Duplicates collapse when making a set; converting back gives an owned
    // slice in unspecified order. The element values themselves are shallow
    // copies, so separately allocated strings/pointees keep their own owners.
    unique := basic.to_set(numbers)
    defer delete(unique)
    values := basic.to_slice(unique)
    defer delete(values)
    assert(len(unique) == 3 && len(values) == 3)
    assert(8 in unique && 3 in unique && 5 in unique)

    // Sort a converted slice when order matters. Neither conversion changes
    // numbers. The same two functions are available from import "thor:sets".
    ordered_values := basic.sorted(values)
    defer delete(ordered_values)
    assert(ordered_values[0] == 3 && ordered_values[2] == 8)
    basic.print("unique, sorted:", ordered_values)

    // Passing a comparator overrides that default. Both outputs belong to us.
    descending := basic.sorted(numbers, proc(a, b: int) -> bool {
        return a > b
    })
    defer delete(descending)

    assert(descending[0] == 8 && descending[3] == 3)

    // A custom comparator also allows types that do not support native <.
    Item :: struct { name: string, score: int }
    items := []Item{{"oak", 8}, {"elm", 3}}
    ranked := basic.sorted(items, less=proc(a, b: Item) -> bool {
        return a.score < b.score
    })
    defer delete(ranked)
    assert(ranked[0].name == "elm")

    // fmap allocates a result with the same element type as the input.
    doubled := basic.fmap(numbers, proc(value: int) -> int {
        return value * 2
    })
    defer delete(doubled)
    assert(doubled[0] == 16 && numbers[0] == 8)

    // Parsing can report failure or overflow through an optional second result.
    parsed, ok := basic._i16("0x7fff")
    assert(ok && parsed == max(i16))
    _, overflow := basic._u8("256")
    assert(!overflow)

    // Conversions in the other direction return independent owned text. Rune
    // slices are UTF-8 encoded; numbers/bools use normal interpolation output.
    // Check the optional allocator error, omit it, or use Thor's or_panic.
    {
        letters := []rune{'a', '界', '😀'}
        text, err := basic._string(letters)
        if err != nil { panic("could not allocate text") }
        defer delete(text)
        letters[0] = '?'
        if text != "a界😀" { panic("_string must own its result") }
        number := basic._string(12345) or_panic
        flag := basic._string(true)
        defer delete(number); defer delete(flag)
        integer: Integer = 123456789012345678901234567890
        ratio := Rational(22, 13)
        defer delete(integer); defer delete(ratio)
        large := basic._string(integer) or_panic
        fraction := basic._string(ratio) or_panic
        defer delete(large); defer delete(fraction)
        if number != "12345" || flag != "true" || fraction != "22/13" { panic("_string formatting") }
        basic.print("owned conversions:", text, number, flag, large, fraction)
        // Unlike an anonymous f-string, an anonymous _string result has no
        // automatic cleanup. Bind it as above or use a scoped arena.
    }

    // split allocates only the outer slice. Its strings borrow the input text.
    // join creates a new string that must also be deleted.
    parts := basic.split("oak,elm,ash", ",")
    defer delete(parts)
    joined := basic.join(parts, " / ")
    defer delete(joined)
    assert(joined == "oak / elm / ash")
    basic.print(joined)

    // clone is a shallow value copy. A cloned slice still shares its elements.
    alias := basic.clone(numbers)
    assert(raw_data(alias) == raw_data(numbers))
    // Do not delete alias: numbers uses literal storage, and no backing storage
    // was allocated by cloning its slice header.

    assert(basic.type(numbers) == []int)
    assert(basic.size(numbers) == size_of([]int))

    // filter preserves order and creates another shallow, caller-owned slice.
    // Even an empty result is valid to delete.
    large := basic.filter(numbers, proc(value: int) -> bool {
        return value > 3
    })
    defer delete(large)
    assert(len(large) == 2 && large[0] == 8 && large[1] == 5)
    assert(basic.sum(large) == 13)
    assert(basic.sum([]int{}) == 0)

    // Ranges allocate integer slices. The ending bound is excluded; negative
    // steps walk downward. This is a collection, not a lazy loop iterator.
    countdown := basic.range(5, 0, -2)
    defer delete(countdown)
    assert(len(countdown) == 3 && countdown[0] == 5 && countdown[-1] == 1)

    // chr returns a rune (a Unicode scalar), not an allocated string. A string
    // passed to ord must contain exactly one code point, not a whole grapheme.
    assert(basic.chr(0x754c) == '界')
    assert(basic.ord("😀") == 0x1f600)
    assert(basic.ord([]u8{255}) == 255) // A byte is deliberately not UTF-8-decoded.

    // Random bounds are inclusive; explicit zero is a real bound. Supplying a
    // generator lets callers control reproducibility; omission uses context.
    zero_sample := basic.random(int, 0, 0)
    assert(zero_sample == 0)
    sample := basic.random(f64, 0, 1)
    assert(sample >= 0 && sample <= 1)

    // A table uses field names or tags for headings. Numeric values align to
    // the right, strings to the left. Long text wraps; Unicode widths are
    // measured in terminal cells. The returned string belongs to us.
    Row :: struct { name: string `Item`, count: int `Qty`, price: f64 `Price` }
    report := basic.table([]Row{{"pear", 2, 2.5}, {"apple", 12, 1.25}})
    defer delete(report)
    basic.print(report, end="")

    basic.print("Basic package examples passed.")
}

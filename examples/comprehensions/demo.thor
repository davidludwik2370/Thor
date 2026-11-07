package main

import "core:fmt" / println

// Run: thor run examples/comprehensions -debug
// Every construction below has a matching owner/cleanup. The same checks stay
// active in unchecked release builds, including when assertions are disabled.

square :: proc(x: int) -> int {return x*x}

// A :: comprehension is evaluated under the normal fn rules. Its filtered
// length determines the native fixed-array type. There is no runtime owner.
EVEN_SQUARES :: [square(x) for x in 0..<10 if x%2==0]

main :: proc() {
    println("Thor collection comprehensions")

    values := [1, 2, 2, 3]

    // A bare runtime comprehension returns an allocated slice. :> keeps its
    // existing shallow meaning; it does not freeze referenced storage deeply.
    squares :> [square(x) for x in values if x>1]
    defer delete(squares)
    if len(squares)!=3 || squares[0]!=4 || squares[2]!=9 {panic("filtered slice")}
    if EVEN_SQUARES!=[0,4,16,36,64] {panic("compile-time fixed array")}

    // Pick the destination explicitly. Slices and dynamic arrays preserve
    // duplicates; sets remove them. Map entries use key = value, and the last
    // value for a repeated key wins in the source's iteration order.
    slice := []int{x for x in values}
    growing := [dynamic]int{x for x in values}
    unique := set[int]{x for x in values}
    lookup := map[int]int{x=i for x,i in values}
    defer delete(slice); defer delete(growing)
    defer delete(unique); defer delete(lookup)
    if len(slice)!=4 || len(growing)!=4 || len(unique)!=3 || lookup[2]!=2 {panic("duplicates")}

    // .. flattens one level, regardless of the input/destination collection.
    // The input is evaluated once; the optional filter precedes unpacking.
    rows := [][]int{{1,2},{},{2,3}}
    flat := [..row for row in rows if len(row)>0]
    flat_unique := set[int]{..row for row in rows}
    defer delete(flat); defer delete(flat_unique)
    if len(flat)!=4 || flat[2]!=2 || len(flat_unique)!=3 {panic("flatten")}

    // Map unpacking merges whole entries. A comprehension has only one for;
    // use named intermediate collections instead of nesting comprehensions.
    first: map[int]string
    second: map[int]string
    first[1]="first"; second[1]="last"; second[2]="second"
    defer delete(first); defer delete(second)
    parts := [first,second]
    merged := map[int]string{..part for part in parts}
    defer delete(merged)
    if merged[1]!="last" || merged[2]!="second" {panic("map merge")}

    // Loop names do not leak. Even when the same name is used on both sides
    // of in, the source refers to the enclosing binding.
    x := [10,20,30]
    offset := 1
    shifted := [x+offset for x in x]
    defer delete(shifted)
    if shifted[0]!=11 || x[0]!=10 {panic("lexical scope")}

    // Existing typed-loop syntax works here too. Element annotations belong
    // to numeric ranges; parenthesized index annotations belong to sequences.
    floating := [x/2 for x:f64 in 1..=3]
    indices := [i for value,(i:i8) in values if value>1]
    defer delete(floating); defer delete(indices)
    if floating[0]!=0.5 || type_of(indices)!=[]i8 || indices[2]!=3 {panic("typed bindings")}

    // Outer storage is fresh; referenced element storage is shallow. Changing
    // a referenced inner slice is visible through both headers.
    views := [row for row in rows]
    defer delete(views)
    if raw_data(views[0])!=raw_data(rows[0]) {panic("shallow element")}

    // Fresh f-strings stored as elements remain allocated. Delete each string
    // before deleting the outer slice. Passing a comprehension anonymously to
    // a procedure does not add f-string-style automatic collection cleanup.
    labels := [f"item {i}" for i in 0..<3]
    for label in labels {
        if len(label)!=6 {panic("retained string")}
        delete(label)
    }
    delete(labels)

    println("All comprehension examples passed.")
}

package main

import "core:fmt"

Pair :: struct {a, b: int}
Bits :: struct {a, b: uint}
Batch :: struct {pairs: [2]Pair}
Padded :: struct {_: [3]byte, value: int, _: bool}

POWERS :: Bits{2, 3} ** {5, 4}
BASE :: Pair{1, 2} + {3, 4}
grow :: fn(value: Pair) -> Pair { return value + {1, 2} }
AUTO :: grow(BASE)
EXPLICIT :: #run grow(BASE)

main :: proc() {
    value := Pair{1, 2}
    value += {3, 4}
    value *= {2, 3}
    if (value != Pair{8, 18}) { panic("compound arithmetic") }

    rows := Batch{[2]Pair{{1, 2}, {3, 4}}}
    doubled :> rows + rows
    if (doubled.pairs != [2]Pair{{2, 4}, {6, 8}}) { panic("nested arithmetic") }
    if (AUTO != Pair{5, 8} || EXPLICIT != AUTO) { panic("compile-time arithmetic") }

    // Padding does not participate, even when its type has no '/' operator.
    padded := Padded{value=6}
    padded /= {value=2}
    if padded.value != 3 { panic("ignored padding") }

    bits := Bits{12, 10}
    bits &= {10, 6}             // {8, 2}
    bits <<= {1, 2}             // {16, 8}; counts follow Odin's unsigned rule.
    bits **= {2, 3}             // {256, 512}
    complement :> ~bits
    if (bits != Bits{256, 512} || complement.a != ~uint(256)) { panic("struct bits") }
    if (POWERS != Bits{32, 81}) { panic("constant struct power") }

    // These are intentionally invalid:
    // Other :: struct {a, b: int}
    // _ = value + Other{1, 2} // Distinct struct types.
    // Holder :: struct {items: []int}
    // _ = Holder{} + Holder{} // Reference-containing storage.

    fmt.println(value, doubled, BASE, AUTO, EXPLICIT)
}

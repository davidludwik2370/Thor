// Compile with an Odin compiler built with C++ ThreadSanitizer to expose the
// backend name-cache race reliably. No Thor code or generated files are needed.
package main

import "core:fmt"

main :: proc() {
    fmt.println("hello")
}

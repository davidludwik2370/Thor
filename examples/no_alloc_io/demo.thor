#+vet no_alloc
package main

import "core:os"
import "core:fmt"
import * "thor:basic"

// This buffer belongs to the current call; no heap storage is constructed.
main :: proc() {
    fmt.println("Enter a short line:")
    buffer: [128]byte
    count, err := os.read(os.stdin, buffer[:])
    if err != nil {
        fmt.eprintln(err)
        return
    }
    fmt.printf("Read %d bytes: %s\n", count, string(buffer[:count]))
    print("Done", true)
}

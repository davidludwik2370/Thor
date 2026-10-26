package main

import "core:testing"

// Running the walkthrough as an Odin test also checks all of its allocations.
// The main program's assertions remain the behavioral checks.
@(test)
walkthrough :: proc(t: ^testing.T) {
    main()
}

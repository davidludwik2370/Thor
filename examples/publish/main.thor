// Development: thor run examples/publish
// Package:     thor publish examples/publish
//
// This small program exercises packaging; it does not create a GUI window.
// The default GUI bundle launches quietly in Finder. Run its inner executable
// from a shell to see the text, or publish with --app-type console for Terminal.
package main

import "thor:app"
import "core:os"
import "core:fmt"

// Publishing shares the normal compiler and its compile-time evaluation cache.
double :: fn(value: int) -> int {
    return value * 2
}
EXPECTED :: #run double(21)

main :: proc() {
    // Include "assets/" in the lookup. The same relative name works during
    // development and inside the published app, regardless of the working dir.
    path := app.resource_path("assets/message.txt")
    defer delete(path)

    data, err := os.read_entire_file(path, context.allocator)
    assert(err == nil)
    defer delete(data)

    // This second file is outside assets/, so thor.toml includes it explicitly.
    settings_path := app.resource_path("data/defaults.txt")
    defer delete(settings_path)
    settings, settings_err := os.read_entire_file(settings_path, context.allocator)
    assert(settings_err == nil)
    defer delete(settings)

    report := f"{string(data)}Compile-time answer: {EXPECTED}\n{string(settings)}"
    defer delete(report)
    fmt.print(report)
}

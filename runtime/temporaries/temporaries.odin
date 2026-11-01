// Expression-local f-string owners. Headers use caller-provided stack storage;
// registering a string never allocates another buffer or clones its contents.
package thor_temporaries

import "base:runtime"

Entry :: struct {
    text: string,
    allocator: runtime.Allocator,
}

Scope :: struct {
    entries: []Entry,
    count: int,
}

capture :: proc(scope: ^Scope, text: string, allocator := context.allocator) -> string {
    assert(scope.count < len(scope.entries), "Invalid f-string temporary capacity")
    scope.entries[scope.count] = {text, allocator}
    scope.count += 1
    return text
}

destroy :: proc(scope: ^Scope) {
    for scope.count > 0 {
        scope.count -= 1
        entry := &scope.entries[scope.count]
        delete(entry.text, allocator=entry.allocator)
        entry^ = {}
    }
}

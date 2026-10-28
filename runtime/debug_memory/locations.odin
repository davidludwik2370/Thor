package debug_memory

import "base:runtime"

Mapper :: #type proc "contextless" (loc: runtime.Source_Code_Location) -> runtime.Source_Code_Location

Span :: struct {
    line, column, end_column: int,
    file: string,
    source_line, source_column: int,
    exact: bool,
    procedure: string,
}

File :: struct {
    path: string,
    spans: []Span,
}

Alias :: struct {old, new: string}

// No allocations: this is also used while an allocator holds its mutex and
// while handling a bad free. Columns follow Source_Code_Location conventions.
map_location :: proc "contextless" (loc: runtime.Source_Code_Location, files: []File, aliases: []Alias) -> runtime.Source_Code_Location {
    result := loc
    for file in files {
        if file.path != loc.file_path { continue }
        spans := file.spans
        lo, hi := 0, len(spans)
        column := max(1, int(loc.column))
        for lo < hi {
            mid := lo + (hi-lo)/2
            span := spans[mid]
            if span.line < int(loc.line) || span.line == int(loc.line) && span.column <= column {
                lo = mid+1
            } else { hi = mid }
        }
        index := lo-1
        if index < 0 || spans[index].line != int(loc.line) { index = lo }
        if index >= 0 && index < len(spans) {
            span := spans[index]
            if span.line == int(loc.line) && (column <= 1 || span.column <= column && column < span.end_column) {
                result.file_path = span.file
                result.line = i32(span.source_line)
                result.column = i32(span.source_column)
                if span.exact { result.column += i32(max(0, column-span.column)) }
                if len(span.procedure)>0 && is_helper(loc.procedure) {
                    result.procedure = span.procedure
                }
                return result
            }
        }
    }
    // Alias replacements require string construction. The generator supplies
    // concrete file aliases, so native report paths need no temporary strings.
    for alias in aliases {
        if loc.file_path == alias.old { result.file_path = alias.new; return result }
    }
    return result
}

@(private)
is_helper :: proc "contextless" (name: string) -> bool {
    for i := 0; i+7 <= len(name); i += 1 {
        if name[i:i+7] == "__thor_" { return true }
    }
    return false
}

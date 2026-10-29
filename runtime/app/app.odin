// Resource paths for development builds and relocatable published applications.
package app

import "core:os"
import "core:mem"
import "core:path/filepath"
import "core:strings"

@(private)
SOURCE_ROOT :: #config(THOR_APP_SOURCE_ROOT, "")
@(private)
LAYOUT :: #config(THOR_APP_LAYOUT, "")

@(private)
join :: proc(parts: []string, allocator: mem.Allocator) -> string {
    result, err := filepath.join(parts, allocator)
    assert(err == nil, "Cannot allocate the resource path")
    return result
}

// Return an owned absolute path to bundled data. The path must be relative to
// the project root, e.g. "assets/image.png" or "data/defaults.json". Delete the
// returned string with the same allocator. This does not change the process's
// working directory or redirect ordinary file I/O.
resource_path :: proc(relative: string, allocator := context.allocator) -> string {
    assert(!os.is_absolute_path(relative), "Resource paths must be relative")
    remaining := relative
    for component in strings.split_iterator(&remaining, "/") {
        assert(component != "..", "Resource paths cannot contain ..")
    }
    when ODIN_OS == .Windows {
        assert(filepath.volume_name(relative) == "", "Resource paths cannot specify a drive")
        remaining = relative
        for component in strings.split_iterator(&remaining, "\\") {
            assert(component != "..", "Resource paths cannot contain ..")
        }
    }
    when LAYOUT == "" && SOURCE_ROOT != "" {
        return join({SOURCE_ROOT, relative}, allocator)
    } else {
        executable, err := os.get_executable_path(allocator)
        assert(err == nil, "Cannot determine the application executable directory")
        defer delete(executable, allocator)
        executable_dir := filepath.dir(executable)
        when LAYOUT == "darwin" {
            return join({executable_dir, "..", "Resources", relative}, allocator)
        } else when LAYOUT == "windows" {
            return join({executable_dir, "resources", relative}, allocator)
        } else when LAYOUT == "linux" {
            return join({executable_dir, "..", "resources", relative}, allocator)
        } else {
            return join({executable_dir, relative}, allocator)
        }
    }
}

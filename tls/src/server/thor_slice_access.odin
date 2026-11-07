// Compiler-derived access contracts. This cache never contains lowered source.
package server

import "base:runtime"
import "core:hash"
import "core:strings"
import "core:sync"
import "src:common"

Thor_Slice_Parameter :: struct {
    file: string,
    offset, end: int,
    name: string,
    explicit: bool,
}
Thor_Slice_Info :: struct {
    file: string,
    source_hash: u64,
    parameters: []Thor_Slice_Parameter,
    readonly_access: []int,
}
Thor_Cached_Slice_Info :: struct {
    source_hash: u64,
    parameters: map[int]bool, // offset -> explicitly written
    readonly_access: map[int]bool,
}
thor_slice_info: map[string]Thor_Cached_Slice_Info
thor_slice_mutex: sync.Mutex

thor_update_slice_info :: proc(infos: []Thor_Slice_Info) {
    sync.lock(&thor_slice_mutex)
    defer sync.unlock(&thor_slice_mutex)
    allocator := runtime.heap_allocator()
    if thor_slice_info == nil { thor_slice_info = make(map[string]Thor_Cached_Slice_Info, allocator) }
    for info in infos {
        key := info.file
        if old, found := thor_slice_info[key]; found { delete(old.parameters); delete(old.readonly_access) }
        else { key = strings.clone(key, allocator) }
        parameters := make(map[int]bool, allocator)
        for parameter in info.parameters { parameters[parameter.offset] = parameter.explicit }
        readonly_access := make(map[int]bool, allocator)
        for offset in info.readonly_access { readonly_access[offset] = true }
        thor_slice_info[key] = {info.source_hash, parameters, readonly_access}
    }
}

thor_slice_parameter_mut :: proc(file: string, offset: int) -> bool {
    sync.lock(&thor_slice_mutex)
    defer sync.unlock(&thor_slice_mutex)
    info, found := thor_slice_info[file]
    if !found { return false }
    // A hover may run before the next compiler check has finished.
    if document := &document_storage.documents[file]; document != nil {
        if hash.fnv64a(document.text[:document.used_text]) != info.source_hash { return false }
    }
    _, mut := info.parameters[offset]
    return mut
}

thor_slice_access_hints :: proc(document: ^Document, range: common.Range) -> []InlayHint {
    sync.lock(&thor_slice_mutex)
    defer sync.unlock(&thor_slice_mutex)
    info, found := thor_slice_info[document.fullpath]
    if !found || hash.fnv64a(document.text[:document.used_text]) != info.source_hash { return nil }
    hints := make([dynamic]InlayHint, context.temp_allocator)
    for offset, explicit in info.parameters {
        if explicit { continue }
        pos := common.get_relative_token_position(offset, document.text[:document.used_text], 0)
        if pos.line < range.start.line || pos.line > range.end.line { continue }
        append(&hints, InlayHint{pos, .Parameter, "#mut "})
    }
    return hints[:]
}

thor_slice_storage_readonly :: proc(file: string, offset: int) -> bool {
    sync.lock(&thor_slice_mutex)
    defer sync.unlock(&thor_slice_mutex)
    info, found := thor_slice_info[file]
    if !found { return false }
    if document := &document_storage.documents[file]; document != nil {
        if hash.fnv64a(document.text[:document.used_text]) != info.source_hash { return false }
    }
    return info.readonly_access[offset]
}

thor_delete_slice_info :: proc() {
    sync.lock(&thor_slice_mutex)
    defer sync.unlock(&thor_slice_mutex)
    allocator := runtime.heap_allocator()
    for file, info in thor_slice_info { delete(file, allocator); delete(info.parameters); delete(info.readonly_access) }
    delete(thor_slice_info)
    thor_slice_info = nil
}

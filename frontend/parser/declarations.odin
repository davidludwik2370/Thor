package thor_parser

import "core:fmt"
import "core:odin/ast"

Redeclaration :: struct {
    name: ^ast.Ident,
    message: string,
}

// Only unconditional, original file declarations are unambiguous without a
// type checker. Branches, build-tagged files, locals and other files stay with
// the backend. Consumers call this before any compile-time execution.
file_redeclarations :: proc(file:^ast.File, allocator:=context.allocator)->[]Redeclaration {
    if len(file.tags)>0 {return nil}
    context.allocator=allocator
    seen:=make(map[string]^ast.Ident)
    defer delete(seen)
    duplicates:=make([dynamic]Redeclaration)
    for statement in file.decls {
        declaration:=statement.derived.(^ast.Value_Decl) or_continue
        for node in declaration.names {
            name:=node.derived.(^ast.Ident) or_continue
            // TLS's synthetic selected-import aliases span only their name.
            if name.name=="_" || name.name=="" || declaration.end.offset<=name.end.offset {continue}
            if name.pos.offset<0 || name.end.offset>len(file.src) || name.end.offset<=name.pos.offset {continue}
            if file.src[name.pos.offset:name.end.offset]!=name.name {continue}
            if first,found:=seen[name.name];found {
                append(&duplicates,Redeclaration{name=name,message=fmt.aprintf("Redeclaration of '%s' in this scope; previous declaration on line %d",name.name,first.pos.line)})
            }else{seen[name.name]=name}
        }
    }
    return duplicates[:]
}

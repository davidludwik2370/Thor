// Thor-specific AST adaptation. Original text and offsets remain unchanged.
package server

import "core:fmt"
import "core:odin/ast"
import parser "thor:parser"
import "core:odin/tokenizer"
import "core:os"
import "core:path/filepath"
import "core:strings"

thor_source_file :: proc(name: string) -> bool {
    extension := filepath.ext(name)
    return extension == ".odin" || extension == ".thor"
}

thor_source_files :: proc(directory: string) -> ([]string, os.Error) {
    odin_files, err := filepath.glob(fmt.tprintf("%s/*.odin", directory), context.temp_allocator)
    if err != nil { return nil, err }
    thor_files, thor_err := filepath.glob(fmt.tprintf("%s/*.thor", directory), context.temp_allocator)
    if thor_err != nil { return nil, thor_err }
    files := make([dynamic]string, context.temp_allocator)
    append(&files, ..odin_files)
    append(&files, ..thor_files)
    return files[:], nil
}

thor_ident :: proc(name: string, pos: tokenizer.Pos) -> ^ast.Ident {
    end := pos
    end.offset += len(name)
    end.column += len(name)
    ident := ast.new(ast.Ident, pos, end)
    ident.name = name
    return ident
}

thor_exprs :: proc(nodes: ..^ast.Expr) -> []^ast.Expr {
    result := make([]^ast.Expr, len(nodes))
    copy(result, nodes)
    return result
}

thor_is_immutable :: proc(attributes: []^ast.Attribute) -> bool {
    for attr in attributes {
        for elem in attr.elems {
            if ident, ok := elem.derived.(^ast.Ident); ok && ident.name == "__thor_immutable" { return true }
        }
    }
    return false
}

thor_mark_immutables :: proc(p: ^parser.Parser, file: ^ast.File) {
    if len(p.immutables) == 0 { return }
    State :: struct {starts: map[int]bool}
    state := State{starts=make(map[int]bool)}
    for span in p.immutables { state.starts[span[0]] = true }
    visit :: proc(v: ^ast.Visitor, node: ^ast.Node) -> ^ast.Visitor {
        if node == nil { return v }
        state := cast(^State)v.data
        if declaration, ok := node.derived.(^ast.Value_Decl); ok && state.starts[node.pos.offset] {
            attribute := ast.new(ast.Attribute, node.pos, node.pos)
            attribute.elems = thor_exprs(thor_ident("__thor_immutable", node.pos))
            append(&declaration.attributes, attribute)
        }
        return v
    }
    visitor := ast.Visitor{visit=visit, data=&state}
    // TLS stores ast.File as a value; unlike ast.new(File), that does not set
    // the root's derived union. Parsed declarations have valid derived nodes.
    for declaration in file.decls { ast.walk(&visitor, declaration) }
    delete(state.starts)
}

thor_is_import_alias :: proc(decl: ^ast.Value_Decl) -> bool {
    return thor_is_import_alias_attributes(decl.attributes[:])
}

thor_is_import_alias_attributes :: proc(attributes: []^ast.Attribute) -> bool {
    for attribute in attributes {
        for elem in attribute.elems {
            if ident, ok := elem.derived.(^ast.Ident); ok && ident.name == "__thor_import_alias" { return true }
        }
    }
    return false
}

thor_add_import_aliases :: proc(p: ^parser.Parser, file: ^ast.File) {
    for selection in p.selections {
        namespace := selection.package_name
        if namespace == "" {
            namespace = strings.trim(selection.path, "\"`")
            slash := max(strings.last_index(namespace, "/"), strings.last_index(namespace, ":"))
            namespace = namespace[slash+1:]
        }
        if namespace == "_" { continue }
        for name, i in selection.names {
            ident := thor_ident(name, selection.positions[i])
            selector := ast.new(ast.Selector_Expr, ident.pos, ident.end)
            selector.expr = thor_ident(namespace, ident.pos)
            selector.field = thor_ident(name, ident.pos)
            selector.op = {kind=.Period, text=".", pos=ident.pos}

            // These aliases exist only in the server AST, never in emitted Odin.
            // OLS's normal scope and file-private symbol machinery can reuse them.
            decl := ast.new(ast.Value_Decl, ident.pos, ident.end)
            decl.names = thor_exprs(ident)
            decl.values = thor_exprs(selector)
            attr := ast.new(ast.Attribute, ident.pos, ident.end)
            field := ast.new(ast.Field_Value, ident.pos, ident.end)
            field.field = thor_ident("private", ident.pos)
            privacy := ast.new(ast.Basic_Lit, ident.pos, ident.end)
            privacy.tok = {kind=.String, text="\"file\"", pos=ident.pos}
            field.value = privacy
            attr.elems = thor_exprs(field, thor_ident("__thor_import_alias", ident.pos))
            append(&decl.attributes, attr)
            append(&file.decls, decl)
        }
    }
}

thor_is_array_literal :: proc(file: ast.File, literal: ^ast.Comp_Lit) -> bool {
    offset := literal.open.offset
    return offset >= 0 && offset < len(file.src) && file.src[offset] == '['
}

thor_array_type :: proc(literal: ^ast.Comp_Lit) -> ^ast.Array_Type {
    if len(literal.elems) == 0 { return nil }
    array := ast.new(ast.Array_Type, literal.pos, literal.end)
    count := ast.new(ast.Basic_Lit, literal.pos, literal.end)
    count.tok = {kind=.Integer, text=fmt.aprintf("%d", len(literal.elems)), pos=literal.pos}
    array.len = count
    first := literal.elems[0]
    if inner, ok := first.derived.(^ast.Comp_Lit); ok && inner.type == nil {
        array.elem = thor_array_type(inner)
        return array
    }
    if basic, ok := first.derived.(^ast.Basic_Lit); ok {
        name := ""
        #partial switch basic.tok.kind {
        case .Integer: name = "int"
        case .Float: name = "f64"
        case .String: name = "string"
        case .Rune: name = "rune"
        }
        if name != "" {
            array.elem = thor_ident(name, first.pos)
            return array
        }
    }
    call := ast.new(ast.Call_Expr, first.pos, first.end)
    call.expr = thor_ident("type_of", first.pos)
    call.args = thor_exprs(first)
    array.elem = call
    return array
}

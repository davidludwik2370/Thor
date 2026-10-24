// Odin owns the grammar; this bridge exports source spans for Thor's lowerer.
package main

import "core:encoding/json"
import "core:fmt"
import "core:odin/ast"
import "core:odin/tokenizer"
import "core:os"
import "core:path/filepath"
import "core:strings"
import parser "parser"

Span :: [2]int
Info :: struct {
    kind: string,
    start, end, parent: int,
    op, name: string,
    mutable: bool,
    roles: map[string]Span,
    lists: map[string][]Span,
}
Output :: struct {
    excluded: bool,
    nodes: [dynamic]Info,
    edits: [dynamic]parser.Edit,
    selections: [dynamic]parser.Selection,
    comments: [dynamic]Span,
    diagnostics: []Parse_Error,
}
Parse_Error :: struct {
    pos: tokenizer.Pos,
    msgs: []string,
    type: string,
    stage: string,
}
parse_errors: [dynamic]Parse_Error
collect_error :: proc(pos: tokenizer.Pos, message: string, args: ..any) {
    messages := make([]string, 1)
    messages[0] = fmt.aprintf(message, ..args)
    append(&parse_errors, Parse_Error{pos=pos, msgs=messages, type="error", stage="parser"})
}
collect_warning :: proc(pos: tokenizer.Pos, message: string, args: ..any) {
    collect_error(pos, message, ..args)
    parse_errors[len(parse_errors)-1].type = "warning"
    parse_errors[len(parse_errors)-1].stage = "parser-warning"
}
filename_excluded :: proc(path: string) -> bool {
    base := filepath.base(path)
    if strings.has_prefix(base, ".") { return true }
    stem := strings.trim_suffix(base, filepath.ext(base))
    parts := strings.split(stem, "_")
    if len(parts) < 2 { return false }
    last, previous := parts[len(parts)-1], parts[len(parts)-2]
    os1, _ := parser.get_build_os_from_string(last)
    os2, _ := parser.get_build_os_from_string(previous)
    arch1 := parser.get_build_arch_from_string(last)
    arch2 := parser.get_build_arch_from_string(previous)
    if os1 != .Unknown && arch2 != .Unknown { return os1 != ODIN_OS || arch2 != ODIN_ARCH }
    if arch1 != .Unknown && os2 != .Unknown { return arch1 != ODIN_ARCH || os2 != ODIN_OS }
    if os1 != .Unknown { return os1 != ODIN_OS }
    if arch1 != .Unknown { return arch1 != ODIN_ARCH }
    return false
}
Walk_State :: struct {out: ^Output, stack: [dynamic]int}
span :: proc(n: ^ast.Node) -> Span {
    if n == nil { return {-1, -1} }
    return {n.pos.offset, n.end.offset}
}
spans :: proc(xs: []^ast.Expr) -> []Span {
    result := make([]Span, len(xs))
    for x, i in xs { result[i] = span(x) }
    return result
}
visit :: proc(v: ^ast.Visitor, node: ^ast.Node) -> ^ast.Visitor {
    state := cast(^Walk_State)v.data
    if node == nil {
        pop(&state.stack)
        return v
    }
    info := Info{kind="Other", start=node.pos.offset, end=node.end.offset, parent=-1,
                 roles=make(map[string]Span), lists=make(map[string][]Span)}
    if len(state.stack) > 0 { info.parent = state.stack[len(state.stack)-1] }
    #partial switch n in node.derived {
    case ^ast.File: info.kind = "File"
    case ^ast.Ident: info.kind = "Ident"; info.name = n.name
    case ^ast.Basic_Lit: info.kind = "Literal"; info.op = tokenizer.to_string(n.tok.kind)
    case ^ast.Implicit_Selector_Expr: info.kind = "ImplicitSelector"
    case ^ast.Poly_Type: info.kind = "PolyType"; info.name = n.type.name
    case ^ast.Comp_Lit:
        // The bracket position is exported; Python distinguishes Thor '[' from '{'.
        info.kind = "Compound"
        info.roles["open"] = {n.open.offset, n.open.offset+1}
        info.roles["type"] = span(n.type)
        info.lists["elems"] = spans(n.elems)
    case ^ast.Binary_Expr:
        info.kind = "Binary"; info.op = tokenizer.to_string(n.op.kind)
        info.roles["left"] = span(n.left); info.roles["right"] = span(n.right)
    case ^ast.Unary_Expr:
        info.kind = "Unary"; info.op = n.op.text; info.roles["expr"] = span(n.expr)
    case ^ast.Paren_Expr: info.kind = "Paren"; info.roles["expr"] = span(n.expr)
    case ^ast.Selector_Expr:
        info.kind = "Selector"; info.roles["expr"] = span(n.expr); info.roles["field"] = span(n.field)
    case ^ast.Call_Expr:
        info.kind = "Call"; info.roles["callee"] = span(n.expr); info.lists["args"] = spans(n.args)
    case ^ast.Index_Expr: info.kind = "Index"; info.roles["expr"] = span(n.expr)
    case ^ast.Slice_Expr: info.kind = "Slice"; info.roles["expr"] = span(n.expr)
    case ^ast.Field_Value:
        info.kind = "FieldValue"; info.roles["field"] = span(n.field); info.roles["value"] = span(n.value)
    case ^ast.Ternary_If_Expr:
        info.kind = "Ternary"; info.op = n.op1.text
        info.roles["cond"] = span(n.cond); info.roles["x"] = span(n.x); info.roles["y"] = span(n.y)
    case ^ast.Ternary_When_Expr:
        info.kind = "WhenExpr"; info.roles["cond"] = span(n.cond)
        info.roles["x"] = span(n.x); info.roles["y"] = span(n.y)
    case ^ast.Value_Decl:
        info.kind = "Declaration"; info.mutable = n.is_mutable
        info.roles["type"] = span(n.type); info.lists["names"] = spans(n.names)
        info.lists["values"] = spans(n.values)
    case ^ast.Assign_Stmt:
        info.kind = "Assignment"; info.lists["lhs"] = spans(n.lhs); info.lists["rhs"] = spans(n.rhs)
    case ^ast.Proc_Lit:
        info.kind = "Procedure"; info.roles["type"] = span(n.type); info.roles["body"] = span(n.body)
        info.lists["where"] = spans(n.where_clauses)
    case ^ast.Proc_Type:
        info.kind = "ProcedureType"; info.roles["params"] = span(n.params); info.roles["results"] = span(n.results)
    case ^ast.Field_List: info.kind = "Fields"
    case ^ast.Field:
        info.kind = "Field"; info.roles["type"] = span(n.type); info.lists["names"] = spans(n.names)
        info.roles["default"] = span(n.default_value)
    case ^ast.Array_Type:
        info.kind = "ArrayType"; info.roles["elem"] = span(n.elem); info.roles["len"] = span(n.len)
    case ^ast.Dynamic_Array_Type:
        info.kind = "DynamicArrayType"; info.roles["elem"] = span(n.elem)
    case ^ast.Fixed_Capacity_Dynamic_Array_Type:
        info.kind = "DynamicArrayType"; info.roles["elem"] = span(n.elem)
    case ^ast.Struct_Type:
        info.kind = "StructType"; info.roles["fields"] = span(n.fields); info.lists["where"] = spans(n.where_clauses)
    case ^ast.Union_Type: info.kind = "UnionType"; info.lists["where"] = spans(n.where_clauses)
    case ^ast.When_Stmt:
        info.kind = "When"; info.roles["cond"] = span(n.cond)
    case ^ast.Return_Stmt: info.kind = "Return"; info.lists["values"] = spans(n.results)
    case ^ast.Block_Stmt: info.kind = "Block"
    case ^ast.Import_Decl:
        info.kind = "Import"; info.name = n.name.text; info.op = n.relpath.text
    case ^ast.If_Stmt: info.kind = "If"
    case ^ast.For_Stmt: info.kind = "For"
    case ^ast.Range_Stmt:
        info.kind = "Range"; info.lists["names"] = spans(n.vals)
    case ^ast.Switch_Stmt: info.kind = "Switch"
    case ^ast.Type_Switch_Stmt: info.kind = "TypeSwitch"
    case ^ast.Case_Clause: info.kind = "Case"
    case ^ast.Enum_Type: info.kind = "EnumType"
    case ^ast.Pointer_Type: info.kind = "PointerType"
    case ^ast.Typeid_Type: info.kind = "TypeidType"
    case ^ast.Map_Type: info.kind = "MapType"
    case ^ast.Bit_Set_Type: info.kind = "BitSetType"
    case ^ast.Distinct_Type:
        info.kind = "DistinctType"; info.roles["type"] = span(n.type)
    }
    index := len(state.out.nodes)
    append(&state.out.nodes, info)
    append(&state.stack, index)
    return v
}

main :: proc() {
    if len(os.args) < 2 { fmt.eprintln("usage: thor-frontend FILE [--odin]"); os.exit(2) }
    data: []u8
    from_stdin := false
    for arg in os.args[2:] { if arg == "--stdin" { from_stdin = true } }
    if from_stdin {
        input := make([dynamic]u8)
        buffer: [4096]u8
        for {
            count, read_err := os.read(os.stdin, buffer[:])
            if count > 0 { append(&input, ..buffer[:count]) }
            if read_err != nil || count == 0 { break }
        }
        data = input[:]
    } else {
        err: os.Error
        data, err = os.read_entire_file(os.args[1], context.allocator)
        if err != nil { fmt.eprintln("cannot read source:", err); os.exit(1) }
    }
    file := ast.new(ast.File, tokenizer.Pos{}, tokenizer.Pos{})
    file.src = string(data)
    file.fullpath = os.args[1]
    package_mode, test_mode := false, false
    project_name := ""
    thor := true
    for arg in os.args[2:] {
        if arg == "--package" { package_mode = true }
        if arg == "--test" { test_mode = true }
        if arg == "--odin" { thor = false }
        if strings.has_prefix(arg, "--project-name=") { project_name = strings.trim_prefix(arg, "--project-name=") }
    }
    excluded := package_mode && filename_excluded(os.args[1])
    header: tokenizer.Tokenizer
    tokenizer.init(&header, file.src, file.fullpath, collect_error)
    for {
        tok := tokenizer.scan(&header)
        if tok.kind == .Package || tok.kind == .EOF { break }
        if tok.kind == .File_Tag {
            append(&file.tags, tok)
            if strings.trim_space(tok.text) == "#+test" && !test_mode { excluded = true }
        }
    }
    tags := parser.parse_file_tags(file^)
    if !parser.match_build_tags(tags, parser.Build_Target{os=ODIN_OS, arch=ODIN_ARCH, project_name=project_name}) {
        excluded = true
    }
    if excluded { fmt.println("{\"excluded\":true,\"nodes\":[]}"); return }
    p := parser.default_parser()
    p.thor = thor
    p.err = collect_error
    p.warn = collect_warning
    ok := parser.parse_file(&p, file)
    if !ok || file.syntax_error_count > 0 || p.tok.error_count > 0 {
        encoded, _ := json.marshal(struct{errors: []Parse_Error}{parse_errors[:]})
        fmt.println(string(encoded))
        os.exit(1)
    }
    file.end.offset = len(data)
    out := Output{edits=p.edits, selections=p.selections, diagnostics=parse_errors[:]}
    for group in file.comments {
        for t in group.list { append(&out.comments, Span{t.pos.offset, t.pos.offset+len(t.text)}) }
    }
    state := Walk_State{out=&out}
    visitor := ast.Visitor{visit=visit, data=&state}
    ast.walk(&visitor, file)
    encoded, json_err := json.marshal(out)
    if json_err != nil { fmt.eprintln(json_err); os.exit(1) }
    fmt.println(string(encoded))
}

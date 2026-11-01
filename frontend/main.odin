// Shared Thor frontend: export Odin-compatible AST roles and original byte spans.
package main

import "core:encoding/json"
import "core:fmt"
import "core:odin/ast"
import tokenizer "tokenizer"
import "core:os"
import "core:path/filepath"
import "core:strings"
import parser "parser"

Span :: [2]int
Info :: struct {
    kind: string,
    start, end, parent: int,
    op, name: string,
    calling_convention: string,
    mutable: bool,
    using_field: bool,
    variadic: bool,
    optional_ok: bool,
    optional_allocator_error: bool,
    roles: map[string]Span,
    lists: map[string][]Span,
}
Output :: struct {
    odin_root: string,
    private: bool,
    private_file: bool,
    excluded: bool,
    nodes: [dynamic]Info,
    edits: [dynamic]parser.Edit,
    selections: [dynamic]parser.Selection,
    comments: [dynamic]Span,
    diagnostics: []Parse_Error,
    immutables: [dynamic][2]int,
    do_tokens: [dynamic]Span,
    selection_tags: [dynamic]Span,
    file_tags: [dynamic]string,
    features: []parser.Feature,
    declaration_separators: [dynamic][3]int,
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
filename_excluded :: proc(path: string, target: parser.Build_Target) -> bool {
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
    if os1 != .Unknown && arch2 != .Unknown { return os1 != target.os || arch2 != target.arch }
    if arch1 != .Unknown && os2 != .Unknown { return arch1 != target.arch || os2 != target.os }
    if os1 != .Unknown { return os1 != target.os }
    if arch1 != .Unknown { return arch1 != target.arch }
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
    case ^ast.File:
        info.kind="File";info.name=n.pkg_name
        if n.pkg_decl!=nil {info.roles["package"]={n.pkg_decl.pos.offset,n.pkg_decl.pos.offset+len(n.pkg_name)}}
    case ^ast.Package_Decl: info.kind="Package"
    case ^ast.Ident: info.kind = "Ident"; info.name = n.name
    case ^ast.Implicit: info.kind="Implicit";info.name=tokenizer.to_string(n.tok.kind)
    case ^ast.Basic_Directive: info.kind="Directive";info.name=n.name
    case ^ast.Asm_Template, ^ast.Undef: info.kind="Unsafe"
    case ^ast.Basic_Lit: info.kind = "Literal"; info.op = tokenizer.to_string(n.tok.kind)
    case ^ast.Implicit_Selector_Expr: info.kind = "ImplicitSelector"
    case ^ast.Poly_Type:
        info.kind = "PolyType"; info.name = n.type.name
        info.roles["specialization"] = span(n.specialization)
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
        info.kind = "Selector"; info.op = n.op.text
        info.roles["expr"] = span(n.expr); info.roles["field"] = span(n.field)
    case ^ast.Call_Expr:
        info.kind = "Call"; info.roles["callee"] = span(n.expr); info.lists["args"] = spans(n.args)
        if n.ellipsis.pos.line != 0 {
            info.roles["ellipsis"] = {n.ellipsis.pos.offset, n.ellipsis.pos.offset+2}
        }
    case ^ast.Index_Expr: info.kind = "Index"; info.roles["expr"] = span(n.expr); info.roles["index"] = span(n.index)
    case ^ast.Slice_Expr:
        info.kind = "Slice"; info.roles["expr"] = span(n.expr)
        info.roles["low"] = span(n.low); info.roles["high"] = span(n.high)
    case ^ast.Deref_Expr: info.kind = "Deref"; info.roles["expr"] = span(n.expr)
    case ^ast.Type_Cast:
        info.kind = "Cast"; info.op = n.tok.text
        info.roles["expr"] = span(n.expr); info.roles["type"] = span(n.type)
    case ^ast.Auto_Cast: info.kind="AutoCast";info.roles["expr"]=span(n.expr)
    case ^ast.Type_Assertion:
        info.kind="TypeAssertion";info.roles["expr"]=span(n.expr);info.roles["type"]=span(n.type)
    case ^ast.Or_Else_Expr:
        info.kind="OrElse";info.roles["x"]=span(n.x);info.roles["y"]=span(n.y)
    case ^ast.Or_Return_Expr: info.kind="OrReturn";info.roles["expr"]=span(n.expr)
    case ^ast.Or_Branch_Expr: info.kind="OrBranch";info.roles["expr"]=span(n.expr)
    case ^ast.Proc_Group: info.kind = "ProcedureGroup"; info.lists["args"] = spans(n.args)
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
        attributes:=make([]Span,len(n.attributes))
        for attribute,i in n.attributes {attributes[i]=span(attribute)}
        info.lists["attributes"]=attributes
        if len(n.attributes)>0 {info.start=n.attributes[0].pos.offset}
        info.using_field = n.is_using
        info.roles["type"] = span(n.type); info.lists["names"] = spans(n.names)
        info.lists["values"] = spans(n.values)
    case ^ast.Assign_Stmt:
        info.kind = "Assignment"; info.op = n.op.text
        info.lists["lhs"] = spans(n.lhs); info.lists["rhs"] = spans(n.rhs)
    case ^ast.Proc_Lit:
        info.kind = "Procedure"; info.roles["type"] = span(n.type); info.roles["body"] = span(n.body)
        info.lists["where"] = spans(n.where_clauses)
    case ^ast.Proc_Type:
        info.kind = "ProcedureType"; info.roles["params"] = span(n.params); info.roles["results"] = span(n.results)
        info.optional_ok = .Optional_Ok in n.tags || .Optional_Allocator_Error in n.tags
        info.optional_allocator_error = .Optional_Allocator_Error in n.tags
        if cc,ok:=n.calling_convention.(string);ok {info.calling_convention=strings.trim(cc,"\"`")}
        else if n.calling_convention!=nil {info.calling_convention="foreign"}
    case ^ast.Foreign_Block_Decl:
        info.kind="ForeignBlock";info.calling_convention="c"
        if len(n.attributes)>0 {info.start=n.attributes[0].pos.offset}
        info.lists["attributes"] = make([]Span, len(n.attributes))
        for attribute, i in n.attributes { info.lists["attributes"][i] = span(attribute) }
        for attribute in n.attributes {for elem in attribute.elems {
            field:=elem.derived.(^ast.Field_Value) or_continue
            name:=field.field.derived.(^ast.Ident) or_continue
            if name.name!="default_calling_convention"{continue}
            value:=field.value.derived.(^ast.Basic_Lit) or_continue
            info.calling_convention=strings.trim(value.tok.text,"\"`")
        }}
    case ^ast.Field_List: info.kind = "Fields"
    case ^ast.Field:
        info.kind = "Field"; info.roles["type"] = span(n.type); info.lists["names"] = spans(n.names)
        info.roles["default"] = span(n.default_value)
        info.using_field = .Using in n.flags
        if n.type!=nil {_,info.variadic=n.type.derived.(^ast.Ellipsis)}
    case ^ast.Array_Type:
        info.kind = "ArrayType"; info.roles["elem"] = span(n.elem); info.roles["len"] = span(n.len)
    case ^ast.Dynamic_Array_Type:
        info.kind = "DynamicArrayType"; info.roles["elem"] = span(n.elem)
    case ^ast.Fixed_Capacity_Dynamic_Array_Type:
        info.kind = "DynamicArrayType"; info.roles["elem"] = span(n.elem)
        info.roles["capacity"] = span(n.capacity)
    case ^ast.Struct_Type:
        info.kind = "StructType"; info.roles["fields"] = span(n.fields); info.roles["params"] = span(n.poly_params)
        if n.is_raw_union {info.op="raw_union"}
        info.lists["where"] = spans(n.where_clauses)
    case ^ast.Union_Type:
        info.kind = "UnionType"; info.lists["where"] = spans(n.where_clauses)
        info.lists["variants"]=spans(n.variants)
    case ^ast.When_Stmt:
        info.kind = "When"; info.roles["cond"] = span(n.cond)
        info.roles["body"] = span(n.body); info.roles["else"] = span(n.else_stmt)
    case ^ast.Return_Stmt: info.kind = "Return"; info.lists["values"] = spans(n.results)
    case ^ast.Defer_Stmt: info.kind = "Defer"; info.roles["stmt"] = span(n.stmt)
    case ^ast.Block_Stmt: info.kind = "Block"
    case ^ast.Import_Decl:
        info.kind = "Import"; info.name = n.name.text; info.op = n.relpath.text
        if len(n.attributes)>0 {info.start=n.attributes[0].pos.offset}
        info.lists["attributes"] = make([]Span, len(n.attributes))
        for attribute, i in n.attributes { info.lists["attributes"][i] = span(attribute) }
        info.roles["path"] = {n.relpath.pos.offset, n.relpath.pos.offset+len(n.relpath.text)}
        if n.name.text != "" { info.roles["alias"] = {n.name.pos.offset, n.name.pos.offset+len(n.name.text)} }
    case ^ast.If_Stmt:
        info.kind = "If"; info.roles["init"] = span(n.init); info.roles["cond"] = span(n.cond)
        info.roles["body"] = span(n.body); info.roles["else"] = span(n.else_stmt)
    case ^ast.For_Stmt:
        info.kind = "For"; info.roles["init"] = span(n.init); info.roles["cond"] = span(n.cond)
        info.roles["body"] = span(n.body); info.roles["post"] = span(n.post)
    case ^ast.Range_Stmt:
        info.kind = "Range"; info.lists["names"] = spans(n.vals)
        info.lists["bindings"] = spans(n.vals)
        for binding,i in n.vals {
            if typed,ok:=binding.derived.(^ast.Field_Value);ok {
                info.lists["names"][i]=span(typed.field)
                if i==0 {info.roles["value_type"]=span(typed.value)}
                if i==1 {info.roles["index_type"]=span(typed.value)}
            }
        }
        info.roles["init"] = span(n.init)
        if n.label!=nil{info.start=n.label.pos.offset}
        info.roles["expr"] = span(n.expr); info.roles["body"] = span(n.body)
    case ^ast.Switch_Stmt: info.kind = "Switch"
    case ^ast.Type_Switch_Stmt:
        info.kind = "TypeSwitch"
        info.roles["expr"]=span(n.expr)
        if n.tag != nil {
            if assignment, ok := n.tag.derived.(^ast.Assign_Stmt); ok {
                info.lists["names"] = spans(assignment.lhs)
                if len(assignment.rhs)>0 {info.roles["expr"]=span(assignment.rhs[0])}
            }
        }
    case ^ast.Expr_Stmt: info.kind = "ExpressionStatement"; info.roles["expr"] = span(n.expr)
    case ^ast.Case_Clause:
        info.kind = "Case"
        info.roles["colon"] = {n.terminator.pos.offset,n.terminator.pos.offset+1}
    case ^ast.Enum_Type: info.kind = "EnumType"
    case ^ast.Pointer_Type: info.kind = "PointerType"; info.roles["elem"] = span(n.elem)
    case ^ast.Multi_Pointer_Type: info.kind = "MultiPointerType"; info.roles["elem"] = span(n.elem)
    case ^ast.Typeid_Type: info.kind = "TypeidType"
    case ^ast.Map_Type: info.kind = "MapType"; info.roles["key"] = span(n.key); info.roles["value"] = span(n.value)
    case ^ast.Bit_Set_Type: info.kind = "BitSetType"
    case ^ast.Distinct_Type:
        info.kind = "DistinctType"; info.roles["type"] = span(n.type)
    case ^ast.Using_Stmt: info.kind = "Using"; info.lists["values"] = spans(n.list)
    case ^ast.Attribute: info.kind="Attribute";info.lists["elems"]=spans(n.elems)
    case ^ast.Foreign_Import_Decl:
        info.kind = "ForeignImport"; info.lists["paths"] = spans(n.fullpaths)
        if len(n.attributes)>0 {info.start=n.attributes[0].pos.offset}
        info.lists["attributes"] = make([]Span, len(n.attributes))
        for attribute, i in n.attributes { info.lists["attributes"][i] = span(attribute) }
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
    target := parser.Build_Target{os=ODIN_OS, arch=ODIN_ARCH}
    thor := true
    for arg in os.args[2:] {
        if arg == "--package" { package_mode = true }
        if arg == "--test" { test_mode = true }
        if arg == "--odin" { thor = false }
        if strings.has_prefix(arg, "--project-name=") { project_name = strings.trim_prefix(arg, "--project-name=") }
        if strings.has_prefix(arg, "--target-os=") { target.os, _ = parser.get_build_os_from_string(strings.trim_prefix(arg, "--target-os=")) }
        if strings.has_prefix(arg, "--target-arch=") { target.arch = parser.get_build_arch_from_string(strings.trim_prefix(arg, "--target-arch=")) }
    }
    target.project_name = project_name
    excluded := package_mode && filename_excluded(os.args[1], target)
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
    if !parser.match_build_tags(tags, target) {
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
    duplicates:=parser.file_redeclarations(file)
    if len(duplicates)>0 {
        for duplicate in duplicates {
            collect_error(duplicate.name.pos,"%s",duplicate.message)
            parse_errors[len(parse_errors)-1].stage="declaration"
        }
        encoded,_:=json.marshal(struct{errors:[]Parse_Error}{parse_errors[:]})
        fmt.println(string(encoded))
        os.exit(1)
    }
    file.end.offset = len(data)
    out := Output{odin_root=ODIN_ROOT, private=tags.private!=.Public, private_file=tags.private==.File,
                  edits=p.edits, selections=p.selections, diagnostics=parse_errors[:], immutables=p.immutables,features=p.features[:],declaration_separators=p.declaration_separators}
    for tag in file.tags {
        append(&out.file_tags, tag.text)
        text := strings.trim_space(tag.text)
        if text == "#+test" || strings.has_prefix(text, "#+build ") {
            append(&out.selection_tags, Span{tag.pos.offset, tag.pos.offset+len(tag.text)})
        }
    }
    lexer: tokenizer.Tokenizer
    tokenizer.init(&lexer, file.src, file.fullpath, collect_error)
    lexer.thor = thor
    for {
        token := tokenizer.scan(&lexer)
        if token.kind == .EOF { break }
        if token.kind == .Do { append(&out.do_tokens, Span{token.pos.offset, token.pos.offset+len(token.text)}) }
    }
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

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

// Set types resolve through the real support record. This import exists only
// in the server's import index, never in original text or semantic tokens.
thor_adapt_expressions :: proc(p: ^parser.Parser,file:^ast.File){
    wanted:=make(map[int]int)
    defer delete(wanted)
    results:=make(map[[2]int]int)
    defer delete(results)
    for feature in p.features {if feature.kind=="SetType"{wanted[feature.start]=feature.end}}
    for feature in p.features {if feature.kind=="SwitchExpr"{results[{feature.start,feature.end}]=len(feature.cases)}}
    if len(wanted)==0&&len(results)==0{return}
    alias:="__thor_tls_sets"
    for strings.contains(file.src,alias){alias=strings.concatenate({alias,"_"})}
    import_decl:=ast.new(ast.Import_Decl,file.pos,file.pos)
    import_decl.name={kind=.Ident,text=alias,pos=file.pos}
    import_decl.relpath={kind=.String,text="\"thor:sets\"",pos=file.pos}
    import_decl.fullpath="\"thor:sets\""
    if len(wanted)>0{append(&file.imports,import_decl)}
    State::struct{wanted:map[int]int,results:map[[2]int]int,alias:string}
    state:=State{wanted=wanted,results=results,alias=alias}
    visit::proc(v:^ast.Visitor,node:^ast.Node)->^ast.Visitor{
        if node==nil{return v}
        state:=cast(^State)v.data
        if call,ok:=node.derived.(^ast.Call_Expr);ok{
            if count,found:=state.results[{node.pos.offset,node.end.offset}];found{
                if ident,ok:=call.expr.derived.(^ast.Ident);ok{ident.name=fmt.aprintf("__thor_switch_expression_%d",count)}
            }
            if state.wanted[node.pos.offset]!=node.end.offset{return v}
            selector:=ast.new(ast.Selector_Expr,call.expr.pos,call.expr.end)
            selector.expr=thor_ident(state.alias,call.expr.pos)
            selector.expr.end=selector.expr.pos
            selector.field=thor_ident("Set",call.expr.pos)
            selector.op={kind=.Period,text=".",pos=call.expr.pos}
            call.expr=selector
        }
        return v
    }
    visitor:=ast.Visitor{visit=visit,data=&state}
    for declaration in file.decls{ast.walk(&visitor,declaration)}
}

thor_binary :: proc(left:^ast.Expr,kind:tokenizer.Token_Kind,right:^ast.Expr)->^ast.Expr{
    expr:=ast.new(ast.Binary_Expr,left.pos,right.end)
    expr.left,expr.right=left,right
    expr.op={kind=kind,pos=left.pos}
    return expr
}
thor_case_condition :: proc(selector:^ast.Expr,matches:[]^ast.Expr,pos:tokenizer.Pos)->^ast.Expr{
    condition:^ast.Expr
    for match in matches{
        item:=match
        if selector!=nil{
            if range,ok:=match.derived.(^ast.Binary_Expr);ok&&(range.op.kind==.Range_Half||range.op.kind==.Range_Full||range.op.kind==.Ellipsis){
                high_kind:tokenizer.Token_Kind=.Lt
                if range.op.kind==.Range_Full{high_kind=.Lt_Eq}
                item=thor_binary(thor_binary(selector,.Gt_Eq,range.left),.Cmp_And,thor_binary(selector,high_kind,range.right))
            }else{item=thor_binary(selector,.Cmp_Eq,match)}
        }
        condition=thor_binary(condition,.Cmp_Or,item) if condition!=nil else item
    }
    return condition if condition!=nil else thor_ident("true",pos)
}
// Reuse the existing when evaluator/scoping for compile-time switches. Keep
// original result/condition nodes, so definitions and field hovers keep spans.
thor_adapt_switches :: proc(p:^parser.Parser,file:^ast.File){
    State::struct{features:map[[2]int]parser.Feature}
    state:=State{features=make(map[[2]int]parser.Feature)}
    defer delete(state.features)
    for feature in p.features{
        if feature.kind=="CompileSwitch"||feature.kind=="CompileSwitchExpr"{state.features[{feature.start,feature.end}]=feature}
    }
    if len(state.features)==0{return}
    visit::proc(v:^ast.Visitor,node:^ast.Node)->^ast.Visitor{
        if node==nil{return v}
        state:=cast(^State)v.data
        feature,found:=state.features[{node.pos.offset,node.end.offset}]
        if !found{return v}
        if sw,ok:=node.derived.(^ast.Switch_Stmt);ok{
            block:=sw.body.derived.(^ast.Block_Stmt)
            ordered:=make([dynamic]^ast.Case_Clause)
            default_case:^ast.Case_Clause
            for statement in block.stmts{
                clause:=statement.derived.(^ast.Case_Clause) or_continue
                if len(clause.list)==0{default_case=clause}else{append(&ordered,clause)}
            }
            if default_case!=nil{append(&ordered,default_case)}
            next:^ast.Stmt
            for i:=len(ordered)-1;i>=0;i-=1{
                clause:=ordered[i]
                body:=ast.new(ast.Block_Stmt,clause.pos,clause.end)
                body.stmts=clause.body
                conditional:=ast.new(ast.When_Stmt,clause.pos,node.end)
                conditional.cond=thor_case_condition(sw.cond,clause.list,clause.pos)
                conditional.body=body
                conditional.else_stmt=next
                next=conditional
            }
            if next!=nil{
                statement:=cast(^ast.Stmt)node
                replacement:=next.derived.(^ast.When_Stmt)
                replacement.pos=node.pos
                statement.derived=replacement
                statement.derived_stmt=replacement
            }
        }else if call,ok:=node.derived.(^ast.Call_Expr);ok&&feature.kind=="CompileSwitchExpr"{
            selector:^ast.Expr
            for arg in call.args{if arg.pos.offset==feature.selector[0]&&arg.end.offset==feature.selector[1]{selector=arg}}
            result:^ast.Expr
            for arm in feature.cases{
                if len(arm.matches)==0{
                    for arg in call.args{if arg.pos.offset==arm.result[0]&&arg.end.offset==arm.result[1]{result=arg}}
                }
            }
            if result==nil{return v}
            for i:=len(feature.cases)-1;i>=0;i-=1{
                arm:=feature.cases[i]
                if len(arm.matches)==0{continue}
                value:^ast.Expr
                matches:=make([dynamic]^ast.Expr)
                for arg in call.args{
                    if arg.pos.offset==arm.result[0]&&arg.end.offset==arm.result[1]{value=arg}
                    for span in arm.matches{if arg.pos.offset==span[0]&&arg.end.offset==span[1]{append(&matches,arg)}}
                }
                if value==nil{continue}
                conditional:=ast.new(ast.Ternary_When_Expr,call.pos,call.end)
                conditional.x,conditional.y=value,result
                conditional.cond=thor_case_condition(selector,matches[:],value.pos)
                result=conditional
            }
            if conditional,ok:=result.derived.(^ast.Ternary_When_Expr);ok{
                call.derived=conditional
                call.derived_expr=conditional
            }
        }
        return v
    }
    visitor:=ast.Visitor{visit=visit,data=&state}
    for declaration in file.decls{ast.walk(&visitor,declaration)}
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

thor_selection_namespace :: proc(selection: parser.Selection, file: ast.File) -> string {
    namespace := selection.package_name
    if namespace == "_" {
        namespace = fmt.aprintf("__thor_import_%d", selection.start)
        for strings.contains(file.src, namespace) { namespace = strings.concatenate({namespace, "_"}) }
    } else if namespace == "" {
        namespace = strings.trim(selection.path, "\"`")
        slash := max(strings.last_index(namespace, "/"), strings.last_index(namespace, ":"))
        namespace = namespace[slash+1:]
    }
    return namespace
}

thor_add_import_aliases :: proc(p: ^parser.Parser, file: ^ast.File) {
    for selection in p.selections {
        if selection.wildcard { continue }
        namespace := thor_selection_namespace(selection, file^)
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

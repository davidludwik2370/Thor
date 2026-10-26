// A presentation-only view: original/indexed ASTs keep Odin's native types.
package server

import "core:odin/ast"
import path "core:path/slashpath"
import "core:strings"

thor_type_display_enabled :: proc(ast_context: ^AstContext) -> bool {
    return ast_context != nil && strings.has_suffix(ast_context.fullpath,".thor")
}

thor_set_package :: proc() -> string {
    config:=indexer.index.collection.config
    if config==nil {return ""}
    root,found:=config.collections["thor"]
    if !found {return ""}
    return path.join({root,"sets"},context.temp_allocator)
}

thor_is_set_symbol :: proc(symbol:Symbol) -> bool {
    if .Distinct in symbol.flags {return false}
    value:=symbol.value.(SymbolStructValue) or_return
    if value.poly==nil {return false}
    pkg:=thor_set_package()
    if pkg=="" || get_package_from_node(value.poly^)!=pkg {return false}
    canonical,found:=memory_index_lookup(&indexer.index,"Set",pkg)
    if !found {return false}
    declaration:=canonical.value.(SymbolStructValue) or_return
    return declaration.poly!=nil && declaration.poly.pos==value.poly.pos
}

thor_display_node :: proc(ast_context:^AstContext,node:^ast.Node) -> ^ast.Node {
    if node==nil {return nil}
    copy:=clone_node(node,ast_context.allocator,nil)
    State::struct{ctx:^AstContext}
    state:=State{ast_context}
    visit::proc(v:^ast.Visitor,node:^ast.Node)->^ast.Visitor {
        if node==nil {return v}
        call,ok:=node.derived.(^ast.Call_Expr)
        if !ok || len(call.args)!=1 {return v}
        state:=cast(^State)v.data
        candidate:=false
        #partial switch callee in call.expr.derived {
        case ^ast.Ident: candidate=callee.name=="Set"
        case ^ast.Selector_Expr: candidate=callee.field.name=="Set"
        }
        if !candidate {return v}
        ctx:=state.ctx^
        ctx.call=nil
        source_pkg:=get_package_from_node(call^)
        if source_pkg!="" && source_pkg!=ctx.document_package {
            ctx.current_package=source_pkg
            ctx.use_locals=false
        }
        symbol,found:=resolve_type_expression(&ctx,call.expr)
        if !found || !thor_is_set_symbol(symbol) {return v}
        // The existing AST printer recognizes this zero-width presentation
        // marker, also used by the parser's set[T] adaptation.
        selector:=new_type(ast.Selector_Expr,call.expr.pos,call.expr.end,ctx.allocator)
        alias:=new_type(ast.Ident,call.expr.pos,call.expr.pos,ctx.allocator)
        alias.name="__thor_tls_sets_display"
        field:=new_type(ast.Ident,call.expr.pos,call.expr.end,ctx.allocator)
        field.name="Set"
        selector.expr,selector.field=alias,field
        call.expr=selector
        return v
    }
    visitor:=ast.Visitor{visit=visit,data=&state}
    ast.walk(&visitor,copy)
    return copy
}

thor_display_nodes :: proc(ast_context:^AstContext,nodes:[]^$T) -> []^T {
    result:=make([]^T,len(nodes),ast_context.allocator)
    for node,i in nodes {result[i]=cast(^T)thor_display_node(ast_context,node)}
    return result
}

thor_display_symbol :: proc(ast_context:^AstContext,symbol:Symbol,depth:=0) -> Symbol {
    if !thor_type_display_enabled(ast_context) || depth>16 {return symbol}
    result:=symbol
    result.type_expr=cast(^ast.Expr)thor_display_node(ast_context,symbol.type_expr)
    if .Mutable not_in symbol.flags && (.Variable in symbol.flags || symbol.type_expr!=nil) {
        result.value_expr=cast(^ast.Expr)thor_display_node(ast_context,symbol.value_expr)
    }
    #partial switch &value in result.value {
    case SymbolProcedureValue:
        value.orig_arg_types=thor_display_nodes(ast_context,value.orig_arg_types)
        value.orig_return_types=thor_display_nodes(ast_context,value.orig_return_types)
        value.where_clauses=thor_display_nodes(ast_context,value.where_clauses)
    case SymbolStructValue:
        value.types=thor_display_nodes(ast_context,value.types)
        value.args=thor_display_nodes(ast_context,value.args)
        value.poly=cast(^ast.Field_List)thor_display_node(ast_context,value.poly)
        // Generic argument names are cached as strings upstream. Rebuild
        // changed arguments from ASTs rather than replacing text in names.
        names:=make([]string,len(value.poly_names),ast_context.allocator)
        copy(names,value.poly_names)
        for arg,i in value.args {
            if i<len(names) && node_to_string(arg)!=node_to_string(symbol.value.(SymbolStructValue).args[i]) {
                names[i]=node_to_string(arg)
            }
        }
        value.poly_names=names
    case SymbolUnionValue:
        value.types=thor_display_nodes(ast_context,value.types)
        value.poly=cast(^ast.Field_List)thor_display_node(ast_context,value.poly)
    case SymbolMapValue:
        value.key=cast(^ast.Expr)thor_display_node(ast_context,value.key)
        value.value=cast(^ast.Expr)thor_display_node(ast_context,value.value)
    case SymbolSliceValue:
        value.expr=cast(^ast.Expr)thor_display_node(ast_context,value.expr)
    case SymbolDynamicArrayValue:
        value.expr=cast(^ast.Expr)thor_display_node(ast_context,value.expr)
    case SymbolFixedArrayValue:
        value.expr=cast(^ast.Expr)thor_display_node(ast_context,value.expr)
    case SymbolMultiPointerValue:
        value.expr=cast(^ast.Expr)thor_display_node(ast_context,value.expr)
    case SymbolGenericValue:
        value.expr=cast(^ast.Expr)thor_display_node(ast_context,value.expr)
    case SymbolAggregateValue:
        symbols:=make([]Symbol,len(value.symbols),ast_context.allocator)
        for member,i in value.symbols {symbols[i]=thor_display_symbol(ast_context,member,depth+1)}
        value.symbols=symbols
    }
    return result
}

thor_write_set_type :: proc(sb:^strings.Builder,ast_context:^AstContext,symbol:Symbol) -> bool {
    if !thor_type_display_enabled(ast_context) || !thor_is_set_symbol(symbol) {return false}
    value:=symbol.value.(SymbolStructValue)
    strings.write_string(sb,repeat("^",symbol.pointers,ast_context.allocator))
    strings.write_string(sb,"set[")
    if len(value.args)==1 {
        build_string_node(thor_display_node(ast_context,value.args[0]),sb,false)
    } else if len(value.poly_names)==1 {
        strings.write_string(sb,value.poly_names[0])
    } else {
        strings.write_string(sb,"$T")
    }
    strings.write_string(sb,"]")
    return true
}

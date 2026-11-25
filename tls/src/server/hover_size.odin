package server

import "core:fmt"
import "core:odin/ast"
import "core:strings"
import "src:common"

// Request-local, target-specific results. Declaration identity keys ordinary
// records; source call identity keeps generic instantiations distinct.
Layout_Cache_Key :: struct {
    types, args, align, min_align, max_align: rawptr,
    count: int,
    flags: u32,
    tags: SymbolStructTags,
    is_union: bool,
    target: int,
}
Layout_Cache_Result :: struct {
    layout: Type_Layout,
    ok: bool,
    busy: bool,
}
HOVER_LAYOUT_BUDGET :: 4096

Layout_Bindings :: struct {
    origin: ^ast.Node,
    poly: ^ast.Field_List,
    args: []^ast.Expr,
    parent: ^Layout_Bindings,
}

// Bind only identifiers written within this generic definition. Arguments keep
// their caller positions, so same-named globals and outer parameters retain
// their own scope instead of becoming self-referential substitutions.
layout_bound_expression :: proc(ctx: ^AstContext, ident: ^ast.Ident) -> (^ast.Expr,bool) {
    for scope:=ctx.layout_bindings; scope!=nil; scope=scope.parent {
        if scope.origin==nil || scope.poly==nil || ident.pos.file!=scope.origin.pos.file ||
            ident.pos.offset<scope.origin.pos.offset || ident.pos.offset>=scope.origin.end.offset {continue}
        i:=0
        for field in scope.poly.list {
            for name in field.names {
                if parameter,ok:=get_poly_param_name(name); ok && parameter==ident.name && i<len(scope.args) {
                    return scope.args[i],true
                }
                i+=1
            }
        }
    }
    return nil,false
}

hover_layout_visible :: proc(symbol: Symbol) -> bool {
    #partial switch symbol.type {
    case .Function, .Package, .Keyword, .EnumMember, .Unresolved: return false
    }
    #partial switch _ in symbol.value {
    case SymbolProcedureValue: return symbol.type == .Variable || symbol.type == .Field || symbol.type == .Type_Function
    case SymbolUntypedValue:
        return (.Variable in symbol.flags || symbol.type==.Variable || symbol.type==.Field) && .CompileTime not_in symbol.flags
    case SymbolProcedureGroupValue, SymbolAggregateValue, SymbolPackageValue: return false
    }
    return true
}

construct_hover_size :: proc(ctx: ^AstContext, symbol: Symbol, allocator := context.temp_allocator) -> string {
    if !hover_layout_visible(symbol) {return ""}
    layout, ok := symbol_layout(ctx, symbol)
    if !ok || layout.size < 0 || layout.align <= 0 {return ""}
    if layout.size_dependent && !hover_layout_target_known() {return ""}
    qualifier := ""
    if symbol.pointers > 0 {qualifier = "pointer"} else {
        #partial switch value in symbol.value {
        case SymbolMultiPointerValue, SymbolProcedureValue: qualifier = "pointer"
        case SymbolSliceValue: qualifier = "slice header"
        case SymbolDynamicArrayValue: if value.cap == nil {qualifier = "dynamic array header"}
        case SymbolMapValue: qualifier = "map header"
        case SymbolUntypedValue: if value.type==.String {qualifier="string header"}
        case SymbolBasicValue:
            if value.ident != nil {
                switch value.ident.name {
                case "rawptr", "cstring", "cstring16": qualifier = "pointer"
                case "string", "string16": qualifier = "string header"
                }
            }
        }
        if thor_type_display_enabled(ctx) && thor_big_kind(symbol) != "" {qualifier = "variable storage"}
    }
    dependent := layout.size_dependent
    if dependent {
        // Only potentially variable sizes need comparison. Reuse the same
        // calculator with request-local, ABI-keyed caches; never change config.
        previous_target, previous_budget := ctx.layout_target, ctx.layout_budget
        defer {ctx.layout_target,ctx.layout_budget=previous_target,previous_budget}
        dependent = false
        active_abi:=target_abi(ctx)
        for target in 1..=LAYOUT_COMPARISON_TARGETS {
            ctx.layout_target,ctx.layout_budget=target,HOVER_LAYOUT_BUDGET
            if target_abi(ctx)==active_abi {continue}
            alternative, valid := symbol_layout(ctx,symbol)
            if !valid {return ""}
            if alternative.size!=layout.size {dependent=true;break}
        }
    }
    return fmt.aprintf("%d %s%s%s", layout.size,"byte" if layout.size==1 else "bytes",
        strings.concatenate({" · ", qualifier}, allocator) if qualifier != "" else "",
        " · target dependent" if dependent else "", allocator=allocator)
}

// Constants used in counts/alignment may themselves depend on a target-sized
// type, including imported aliases and named size_of/align_of expressions.
layout_expr_dependent :: proc(ctx: ^AstContext, expr: ^ast.Expr, depth: int) -> bool {
    if expr == nil {return false}
    if depth > LAYOUT_MAX_DEPTH {return true}
    set_ast_package_from_node_scoped(ctx, expr)
    #partial switch value in expr.derived {
    case ^ast.Basic_Lit: return false
    case ^ast.Paren_Expr: return layout_expr_dependent(ctx, value.expr, depth+1)
    case ^ast.Unary_Expr: return layout_expr_dependent(ctx, value.expr, depth+1)
    case ^ast.Binary_Expr:
        return layout_expr_dependent(ctx, value.left, depth+1) || layout_expr_dependent(ctx, value.right, depth+1)
    case ^ast.Call_Expr:
        if name, ok := value.expr.derived.(^ast.Ident); ok && len(value.args)==1 && (name.name=="size_of" || name.name=="align_of") {
            layout, valid := type_expr_layout(ctx, value.args[0], depth+1)
            return !valid || (layout.size_dependent if name.name=="size_of" else layout.align_dependent)
        }
    case ^ast.Ident, ^ast.Selector_Expr:
        if ident,ok:=expr.derived.(^ast.Ident); ok {
            if bound,found:=layout_bound_expression(ctx,ident); found {return layout_expr_dependent(ctx,bound,depth+1)}
        }
        symbol, ok := resolve_type_expression(ctx, expr)
        if !ok {return true}
        if symbol.value_expr != nil && symbol.value_expr != expr {return layout_expr_dependent(ctx, symbol.value_expr, depth+1)}
        if _, literal := symbol.value.(SymbolUntypedValue); literal {return false}
    }
    return true
}

layout_product :: proc(a,b: i64) -> (i64,bool) {
    if a<0 || b<0 || b!=0 && a>max(i64)/b {return 0,false}
    return a*b,true
}

hover_size_in_builtin_docs :: proc(doc, size_info: string) -> string {
    if size_info=="" {return doc}
    closing := strings.index(doc,"\n```")
    if closing<0 {return doc}
    end := closing+4
    return strings.concatenate({doc[:end],"\n\n<!-- thor-hover-size -->\n",size_info,"\n",doc[end:]},context.temp_allocator)
}

hover_layout_target_known :: proc() -> bool {
    switch common.config.profile.arch {
    case "", "amd64", "arm64", "i386", "arm32", "wasm32", "wasm64p32", "riscv32", "riscv64": return true
    }
    return false
}

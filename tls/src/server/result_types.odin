// Call-result reflection is a type query, never a call to the library stub.
package server

import "core:odin/ast"
import "core:fmt"
import path "core:path/slashpath"
import "core:strings"

thor_result_query :: proc(symbol: Symbol) -> bool {
    actual, is_proc := symbol.value.(SymbolProcedureValue)
    if !is_proc || len(actual.orig_arg_types)==0 || !strings.contains(actual.orig_arg_types[0].pos.file,"/intrinsics/") { return false }
    config := indexer.index.collection.config
    if config == nil { return false }
    root, found := config.collections["thor"]
    if !found { return false }
    canonical, ok := memory_index_lookup(&indexer.index, "result_type_of", path.join({root,"intrinsics"},context.temp_allocator))
    if !ok { return false }
    expected, canonical_proc := canonical.value.(SymbolProcedureValue)
    return canonical_proc && len(expected.orig_arg_types)>0 && actual.orig_arg_types[0].pos == expected.orig_arg_types[0].pos
}

thor_result_record :: proc(ctx: ^AstContext, call: ^ast.Call_Expr, target: Maybe(Symbol)=nil) -> (^ast.Expr, bool) {
    local := ctx^
    local.call = call
    local.use_locals = true
    local.use_imports = true
    local.resolve_specific_overload=true
    local.deferred_count = 0
    local.recursion_map=make(map[rawptr]struct{},context.temp_allocator)
    local.current_package = get_package_from_node(call)
    symbol:Symbol
    found:=false
    if actual,ok:=target.?;ok {
        symbol,found=actual,true
        if procedure,is_proc:=actual.value.(SymbolProcedureValue);is_proc {
            generic:=procedure.generic
            for param in procedure.orig_arg_types { if param.type!=nil && expr_contains_poly(param.type) {generic=true} }
            if generic {
                if specialized,valid:=resolve_generic_function_symbol(&local,procedure.orig_arg_types,procedure.orig_return_types,procedure.inlining,actual);valid {symbol=specialized}
            }
        } else {symbol,found=resolve_call_expr(&local,call)}
    } else { symbol,found=resolve_call_expr(&local,call) }
    if !found { return nil, false }
    procedure, found_proc := symbol.value.(SymbolProcedureValue)
    if !found_proc { return nil, false }
    results := get_proc_return_types(&local,symbol,call,true)
    if len(results)==0 { return nil,false }
    if len(results)==1 { return results[0],true }
    record := new_type(ast.Struct_Type,call.pos,call.end,ctx.allocator)
    record.fields = new_type(ast.Field_List,call.pos,call.end,ctx.allocator)
    record.fields.list = make([]^ast.Field,len(results),ctx.allocator)
    index:=0
    for result in procedure.return_types {
        for i in 0..<max(1,len(result.names)) {
            if index>=len(results) { return nil,false }
            name:=fmt.tprintf("v%d",index)
            if i<len(result.names) {
                if ident,ok:=unwrap_ident(result.names[i]);ok && ident.name!="_" { name=ident.name }
            }
            field:=new_type(ast.Field,call.pos,call.end,ctx.allocator)
            field.names=make([]^ast.Expr,1,ctx.allocator)
            field.names[0]=make_ident_ast(ctx,call.pos,call.end,name)
            field.type=results[index]
            record.fields.list[index]=field
            index+=1
        }
    }
    return record,true
}

resolve_thor_result_query :: proc(ctx: ^AstContext, query: ^ast.Call_Expr) -> (Symbol,bool) {
    if len(query.args)!=1 { return {},false }
    call,ok:=ast.unparen_expr(query.args[0]).derived.(^ast.Call_Expr)
    if !ok { return {},false }
    expr,found:=thor_result_record(ctx,call)
    if !found { return {},false }
    local:=ctx^
    local.call=nil
    symbol,valid:=resolve_type_expression(&local,expr)
    if valid { symbol.flags-={.Variable,.Mutable,.Immutable,.Parameter};symbol.type=.Type }
    return symbol,valid
}

// General dependent type defaults: infer from the actual callback and the
// already-bound input types, rather than recognizing pmap by its name.
Thor_Result_Default :: enum { None, Resolved, Invalid }

thor_has_result_default :: proc(ctx:^AstContext,procedure:SymbolProcedureValue)->bool {
    for param in procedure.orig_arg_types {
        if param.default_value==nil {continue}
        query,ok:=ast.unparen_expr(param.default_value).derived.(^ast.Call_Expr)
        if !ok {continue}
        local:=ctx^
        local.call=nil
        local.current_package=get_package_from_node(query)
        if symbol,found:=resolve_type_expression(&local,query.expr);found && thor_result_query(symbol) {return true}
    }
    return false
}

thor_result_defaults :: proc(ctx: ^AstContext, params: []^ast.Field, args: []CallArg, poly: ^map[string]^ast.Expr) -> (status: Thor_Result_Default) {
    parameter_name :: proc(expr: ^ast.Expr) -> (^ast.Ident,bool) {
        if p,ok:=expr.derived.(^ast.Poly_Type);ok { return p.type,true }
        return unwrap_ident(expr)
    }
    values:=make(map[string]^ast.Expr,context.temp_allocator)
    symbols:=make(map[string]Symbol,context.temp_allocator)
    position:=0
    for param in params {
        for name in param.names {
            ident,ok:=parameter_name(name)
            if !ok { position+=1;continue }
            for arg,i in args {
                if arg.named && arg.name==ident.name || !arg.named && i==position {
                    values[ident.name]=arg.value_expr
                    if arg.has_symbol {symbols[ident.name]=arg.symbol}
                    break
                }
            }
            // An unresolved overload argument may have no CallArg symbol yet.
            // Its original expression still lets the query select a callback
            // using the actual job fields, rather than the outer call's args.
            for argument,i in ctx.call.args {
                if named,ok:=argument.derived.(^ast.Field_Value);ok {
                    if key,ok:=unwrap_ident(named.field);ok && key.name==ident.name {values[ident.name]=named.value}
                } else if i==position {values[ident.name]=argument}
            }
            position+=1
        }
    }
    for param in params {
        if param.type==nil {continue}
        array,ok:=param.type.derived.(^ast.Array_Type)
        if !ok || array.len!=nil {continue}
        element,poly_element:=array.elem.derived.(^ast.Poly_Type)
        if !poly_element {continue}
        for name in param.names {
            ident,ok:=parameter_name(name);if !ok {continue}
            actual,found:=symbols[ident.name];if !found {continue}
            if slice,is_slice:=actual.value.(SymbolSliceValue);is_slice {poly[element.type.name]=clone_expr(slice.expr,ctx.allocator,nil)}
        }
    }
    for param in params {
        if param.default_value==nil || len(param.names)!=1 { continue }
        name,ok:=parameter_name(param.names[0]);if !ok { continue }
        query,valid:=ast.unparen_expr(param.default_value).derived.(^ast.Call_Expr)
        if !valid || len(query.args)!=1 { continue }
        lookup_ctx:=ctx^
        lookup_ctx.call=nil
        lookup_ctx.current_package=get_package_from_node(query)
        query_symbol,found:=resolve_type_expression(&lookup_ctx,query.expr)
        if !found || !thor_result_query(query_symbol) { continue }
        status=.Invalid
        if explicit,found:=values[name.name];found { poly[name.name]=explicit;status=.Resolved;continue }
        inner,inner_ok:=ast.unparen_expr(query.args[0]).derived.(^ast.Call_Expr)
        if !inner_ok { continue }
        callback_name,ident_ok:=unwrap_ident(inner.expr)
        if !ident_ok { continue }
        callback,callback_found:=values[callback_name.name]
        if !callback_found { continue }
        copy:=cast(^ast.Call_Expr)clone_node(inner,ctx.allocator,nil)
        copy.expr=clone_expr(callback,ctx.allocator,nil)
        find_and_replace_poly_type(cast(^ast.Expr)copy,poly)
        copy.pos,copy.end=ctx.call.pos,ctx.call.end
        // Indexed generic templates retain their definition's lexical file.
        // Resolve spreads from the bound type explicitly, then pass their
        // actual field types to callback specialization in the caller context.
        flattened:=make([dynamic]^ast.Expr,ctx.allocator)
        for arg in copy.args {
            if unary,spread:=arg.derived.(^ast.Unary_Expr);spread && unary.op.kind==.Mul_Mul {
                if literal,comp:=unary.expr.derived.(^ast.Comp_Lit);comp && literal.type!=nil {
                    if ident,ok:=unwrap_ident(literal.type);ok {
                        if bound,found:=poly[ident.name];found {literal.type=clone_expr(bound,ctx.allocator,nil)}
                    }
                    bound_ctx:=ctx^
                    bound_ctx.call=nil
                    bound_ctx.use_locals=true
                    bound_ctx.current_package=get_package_from_node(literal.type)
                    bound_ctx.recursion_map=make(map[rawptr]struct{},context.temp_allocator)
                    typ,found:=resolve_type_expression(&bound_ctx,literal.type)
                    if found {
                        if record,ok:=typ.value.(SymbolStructValue);ok {
                            for field,i in record.types {
                                if i<len(record.from_usings) && record.from_usings[i]!=-1 {continue}
                                append(&flattened,field)
                            }
                            continue
                        }
                    }
                }
            }
            append(&flattened,arg)
        }
        copy.args=flattened[:]
        target:Maybe(Symbol)
        if symbol,ok:=symbols[callback_name.name];ok {target=symbol}
        record,record_found:=thor_result_record(ctx,copy,target)
        if record_found { poly[name.name]=record;status=.Resolved }
    }
    return
}

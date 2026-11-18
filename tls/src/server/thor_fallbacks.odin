// Fallback lists retain the source call's distinct result types per slot.
package server

import "core:odin/ast"

thor_fallback_list :: proc(expr:^ast.Expr)->(^ast.Call_Expr,bool) {
    if expr==nil {return nil,false}
    call,ok:=ast.unparen_expr(expr).derived.(^ast.Call_Expr)
    if !ok {return nil,false}
    name,ident:=call.expr.derived.(^ast.Ident)
    return call,ident&&name.name=="__thor_fallback_list"&&name.pos.offset==name.end.offset
}

thor_fallback_result_type :: proc(ctx:^AstContext,expr:^ast.Expr,index:int,depth:=0)->^ast.Expr {
    if expr==nil||depth>64 {return nil}
    expr:=ast.unparen_expr(expr)
    #partial switch node in expr.derived {
    case ^ast.Or_Else_Expr:return thor_fallback_result_type(ctx,node.x,index,depth+1)
    case ^ast.Or_Return_Expr:return thor_fallback_result_type(ctx,node.expr,index,depth+1)
    case ^ast.Or_Branch_Expr:return thor_fallback_result_type(ctx,node.expr,index,depth+1)
    case ^ast.Call_Expr:
        local:=ctx^
        local.call=node
        local.use_locals,local.use_imports=true,true
        local.resolve_specific_overload=true
        local.deferred_count=0
        local.recursion_map=make(map[rawptr]struct{},context.temp_allocator)
        local.current_package=get_package_from_node(node)
        symbol,ok:=resolve_type_expression(&local,node.expr)
        if !ok {return nil}
        results:=get_proc_return_types(&local,symbol,node,true)
        if index<len(results) {return results[index]}
    }
    return nil
}

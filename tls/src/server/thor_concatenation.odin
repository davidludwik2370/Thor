package server

import "core:fmt"
import "core:odin/ast"

thor_concatenation_value :: proc(ctx:^AstContext,expr:^ast.Expr)->(Symbol,bool) {
    old_locals,old_call,old_package:=ctx.use_locals,ctx.call,ctx.current_package
    defer {ctx.use_locals,ctx.call,ctx.current_package=old_locals,old_call,old_package}
    ctx.use_locals=true
    if expr.pos.file!="" {ctx.current_package=get_package_from_node(expr^)}
    if call,ok:=get_completed_call(expr);ok {ctx.call=call}
    // Keep the caller's recursion guard: a constant can temporarily refer to
    // itself while being edited. Public top-level queries clear that guard.
    return resolve_value_type_expression(ctx,expr)
}

thor_concatenation_anchor :: proc(ctx:^AstContext,expr:^ast.Expr,depth:=0)->(Symbol,bool) {
    if expr==nil || depth>64 {return {},false}
    expr:=ast.unparen_expr(expr)
    if binary,ok:=expr.derived.(^ast.Binary_Expr);ok && binary.op.kind==.Increment {
        if symbol,found:=thor_concatenation_anchor(ctx,binary.left,depth+1);found {return symbol,true}
        return thor_concatenation_anchor(ctx,binary.right,depth+1)
    }
    if literal,ok:=expr.derived.(^ast.Comp_Lit);ok && literal.type==nil && thor_is_array_literal(ctx.file,literal) {return {},false}
    symbol,found:=thor_concatenation_value(ctx,expr)
    if !found {return {},false}
    #partial switch _ in symbol.value {
    case SymbolFixedArrayValue,SymbolSliceValue:return symbol,true
    }
    return {},false
}

// Keep original operands in the AST for hover, completion and rename. Only the
// result shape is synthesized; Odin diagnoses constant eligibility/exact types.
thor_concatenation_type :: proc(ctx:^AstContext, binary:^ast.Binary_Expr)->(Symbol,bool) {
    context.allocator=ctx.allocator
    left,left_ok:=thor_concatenation_value(ctx,binary.left)
    right,right_ok:=thor_concatenation_value(ctx,binary.right)
    a,a_array:=left.value.(SymbolFixedArrayValue)
    b,b_array:=right.value.(SymbolFixedArrayValue)
    if _,slice:=left.value.(SymbolSliceValue);slice {
        _,other_slice:=right.value.(SymbolSliceValue)
        return left,right_ok && other_slice
    }
    // An empty inferred literal borrows the element type from the other side.
    empty::proc(expr:^ast.Expr)->bool {
        expr:=ast.unparen_expr(expr)
        literal,ok:=expr.derived.(^ast.Comp_Lit)
        return ok && literal.type==nil && len(literal.elems)==0
    }
    zero::proc(expr:^ast.Expr)->^ast.Expr {
        count:=ast.new(ast.Basic_Lit,expr.pos,expr.end)
        count.tok={kind=.Integer,text="0",pos=expr.pos}
        return count
    }
    if !a_array && empty(binary.left) && b_array {left=right;a=b;a.len=zero(binary.left);left_ok=true;a_array=true}
    if !b_array && empty(binary.right) && a_array {b=a;b.len=zero(binary.right);right_ok=true;b_array=true}
    if !left_ok || !right_ok || !a_array || !b_array {return {},false}
    if anchor,ok:=thor_concatenation_anchor(ctx,binary);ok {
        if array,found:=anchor.value.(SymbolFixedArrayValue);found {
            a.expr=array.expr
            left.pkg=anchor.pkg
        }
    }
    length:=thor_binary(a.len,.Add,b.len)
    known:=make_when_expr_map()
    register_when_consts_from_globals(&known,ctx.globals)
    av,a_known:=resolve_when_expr(known,a.len)
    bv,b_known:=resolve_when_expr(known,b.len)
    x,x_int:=av.(int);y,y_int:=bv.(int)
    if a_known && b_known && x_int && y_int && x>=0 && y>=0 && x<=max(int)-y {
        count:=ast.new(ast.Basic_Lit,binary.pos,binary.end)
        count.tok={kind=.Integer,text=fmt.aprintf("%d",x+y),pos=binary.pos}
        length=count
    }
    left.value=SymbolFixedArrayValue{expr=a.expr,len=length}
    left.type=.Variable
    left.flags-={.Distinct}
    left.type_name,left.type_pkg="",""
    return left,true
}

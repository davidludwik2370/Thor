// Project each result for OLS type queries without changing the original AST.
// Original nodes remain the source of tokens, references and rename spans.
package server

import "core:fmt"
import "core:odin/ast"
import "core:strconv"
import "core:strings"

thor_switch_root :: proc(expr:^ast.Expr)->bool{
    if expr==nil{return false}
    if paren,ok:=expr.derived.(^ast.Paren_Expr);ok{return thor_switch_root(paren.expr)}
    if _,ok:=expr.derived.(^ast.Ternary_When_Expr);ok{return true}
    if call,ok:=expr.derived.(^ast.Call_Expr);ok{_,sw:=thor_context_switch_count(call);return sw}
    return false
}

thor_switch_group :: proc(expr:^ast.Expr)->(^ast.Call_Expr,bool){
    if expr==nil{return nil,false}
    call,ok:=expr.derived.(^ast.Call_Expr)
    if !ok{return nil,false}
    ident,named:=call.expr.derived.(^ast.Ident)
    return call,named&&ident.pos.offset==ident.end.offset&&ident.name=="__thor_switch_results"
}

thor_switch_arity :: proc(expr:^ast.Expr,ctx:^AstContext=nil,depth:=0)->int{
    if expr==nil||depth>64{return 1}
    if group,ok:=thor_switch_group(expr);ok{return len(group.args)}
    #partial switch node in expr.derived{
    case ^ast.Paren_Expr:return thor_switch_arity(node.expr,ctx,depth+1)
    case ^ast.Ternary_When_Expr:return max(thor_switch_arity(node.x,ctx,depth+1),thor_switch_arity(node.y,ctx,depth+1))
    case ^ast.Call_Expr:
        if count,sw:=thor_context_switch_count(node);sw{
            result:=1
            for arm in node.args[:count]{result=max(result,thor_switch_arity(arm,ctx,depth+1))}
            return result
        }
        if ctx!=nil{
            old:=ctx.call;defer ctx.call=old
            ctx.call=node
            if symbol,ok:=thor_context_type(ctx,node.expr);ok{
                return max(1,len(get_proc_return_types(ctx,symbol,node,true)))
            }
        }
    }
    return 1
}

thor_switch_projection :: proc(expr:^ast.Expr,index:int,depth:=0)->^ast.Expr{
    if expr==nil||depth>64{return expr}
    if group,ok:=thor_switch_group(expr);ok{
        return group.args[index] if index<len(group.args) else expr
    }
    #partial switch node in expr.derived{
    case ^ast.Paren_Expr:return thor_switch_projection(node.expr,index,depth+1)
    case ^ast.Ternary_When_Expr:
        result:=ast.new(ast.Ternary_When_Expr,node.pos,node.end)
        result.cond=node.cond
        result.x=thor_switch_projection(node.x,index,depth+1)
        result.y=thor_switch_projection(node.y,index,depth+1)
        return result
    case ^ast.Call_Expr:
        if count,sw:=thor_context_switch_count(node);sw{
            result:=ast.new(ast.Call_Expr,node.pos,node.end)
            result.expr=node.expr
            result.args=make([]^ast.Expr,len(node.args))
            copy(result.args,node.args)
            for i in 0..<count{result.args[i]=thor_switch_projection(node.args[i],index,depth+1)}
            return result
        }
        result:=ast.new(ast.Call_Expr,node.pos,node.end)
        ident:=thor_ident(fmt.aprintf("__thor_switch_result_%d",index),node.pos)
        ident.end=ident.pos
        result.expr=ident
        result.args=thor_exprs(expr)
        return result
    }
    return expr
}

thor_switch_projected_value :: proc(ctx:^AstContext,call:^ast.Call_Expr,out:^Symbol)->(bool,bool){
    ident,named:=call.expr.derived.(^ast.Ident)
    prefix:="__thor_switch_result_"
    if !named||ident.pos.offset!=ident.end.offset||!strings.has_prefix(ident.name,prefix){return false,false}
    index,valid:=strconv.parse_int(ident.name[len(prefix):])
    if !valid||index<0||len(call.args)!=1{return false,true}
    original,ok:=call.args[0].derived.(^ast.Call_Expr)
    if !ok{return false,true}
    old:=ctx.call;defer ctx.call=old
    ctx.call=original
    symbol,resolved:=thor_context_type(ctx,original.expr)
    if !resolved{return false,true}
    types:=get_proc_return_types(ctx,symbol,original,true)
    if index>=len(types){return false,true}
    out^,ok=thor_context_type(ctx,types[index])
    return ok,true
}

thor_switch_result_index :: proc(expr:^ast.Expr,position:int,depth:=0)->int{
    if expr==nil||depth>64{return 0}
    if group,ok:=thor_switch_group(expr);ok{
        for arg,i in group.args{if position_in_node(arg,position){return i}}
        return 0
    }
    #partial switch node in expr.derived{
    case ^ast.Paren_Expr:return thor_switch_result_index(node.expr,position,depth+1)
    case ^ast.Ternary_When_Expr:
        if position_in_node(node.x,position){return thor_switch_result_index(node.x,position,depth+1)}
        return thor_switch_result_index(node.y,position,depth+1)
    case ^ast.Call_Expr:
        if count,sw:=thor_context_switch_count(node);sw{
            for arm in node.args[:count]{if position_in_node(arm,position){return thor_switch_result_index(arm,position,depth+1)}}
        }
    }
    return 0
}

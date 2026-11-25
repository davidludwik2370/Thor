package server

import "core:fmt"
import "core:odin/ast"
import "core:strconv"
import "core:strings"

import "src:common"
import "base:intrinsics"

Type_Layout :: struct {
	size:  i64,
	align: i64,
    // Conservative triggers for cached ABI verification, not display labels.
	size_dependent: bool,
	align_dependent: bool,
}

Field_Layout :: struct {
	name:   string,
	offset: i64,
	layout: Type_Layout,
}

LAYOUT_MAX_DEPTH :: 96
LAYOUT_MAX_SIMD_ALIGN :: 16
LAYOUT_COMPARISON_TARGETS :: 7

Layout_ABI :: struct {pointer_size, int_size, max_align, scalar_align, simd_align: i64}

// All distinct layout fact sets in the supported Odin target table. The two
// i386 variants distinguish Windows scalar alignment from System V alignment.
@(private)
target_abi :: proc(ctx: ^AstContext) -> Layout_ABI {
    switch ctx.layout_target {
    case 1: return {8,8,16,16,16}   // ARM64
    case 2: return {8,8,16,16,512}  // AMD64/RISC-V64
    case 3: return {4,4,16,4,512}   // i386 System V
    case 4: return {4,4,16,16,512}  // i386 Windows
    case 5: return {4,4,8,8,8}      // ARM32
    case 6: return {4,4,8,8,512}    // wasm32
    case 7: return {4,8,8,8,512}    // wasm64p32
    }
    arch := common.config.profile.arch
    if arch=="" {
        #partial switch ODIN_ARCH {
        case .arm64: arch="arm64"
        case .arm32: arch="arm32"
        case .i386: arch="i386"
        case .wasm32: arch="wasm32"
        case .wasm64p32: arch="wasm64p32"
        }
    }
    switch arch {
    case "i386":
        windows := common.config.profile.os=="windows" || common.config.profile.os=="" && ODIN_OS==.Windows
        return {4,4,16,16 if windows else 4,512}
    case "arm32", "riscv32": return {4,4,8,8,8}
    case "wasm32": return {4,4,8,8,512}
    case "wasm64p32": return {4,8,8,8,512}
    case "arm64": return {8,8,16,16,16}
    }
    return {8,8,16,16,512}
}

@(private = "file")
type_layout :: proc(size, align: i64, size_dependent, align_dependent: bool) -> Type_Layout {
    return {size=size, align=align, size_dependent=size_dependent, align_dependent=align_dependent}
}

@(private = "file")
align_forward :: proc(x, align: i64) -> i64 {
	if x<0 || align<=0 || align & (align-1) != 0 {return -1}
    padding := (align - x%align)%align
    if x>max(i64)-padding {return -1}
    return x+padding
}

@(private = "file")
target_pointer_size :: proc(ast_context: ^AstContext) -> i64 {
    return target_abi(ast_context).pointer_size
}

@(private = "file")
target_int_size :: proc(ast_context: ^AstContext) -> i64 {
    return target_abi(ast_context).int_size
}

@(private = "file")
target_max_align :: proc(ast_context: ^AstContext) -> i64 {
    return target_abi(ast_context).scalar_align
}

@(private = "file")
allocator_layout :: proc(ast_context: ^AstContext) -> Type_Layout {
	ptr := target_pointer_size(ast_context)
	return type_layout(2 * ptr, ptr, true, true)
}

@(private = "file")
basic_type_layout :: proc(ast_context: ^AstContext, name: string) -> (Type_Layout, bool) {
	ptr := target_pointer_size(ast_context)
	word := target_int_size(ast_context)
	switch name {
	case "bool", "b8", "i8", "u8", "byte":
		return type_layout(1, 1, false, false), true
	case "b16", "i16", "u16", "i16le", "i16be", "u16le", "u16be", "f16", "f16le", "f16be":
		return type_layout(2, 2, false, false), true
	case "b32", "i32", "u32", "i32le", "i32be", "u32le", "u32be", "f32", "f32le", "f32be", "rune":
		return type_layout(4, 4, false, false), true
	case "b64", "i64", "u64", "i64le", "i64be", "u64le", "u64be", "f64", "f64le", "f64be":
		return type_layout(8, min(8, target_max_align(ast_context)), false, true), true
	case "typeid": return type_layout(8, 8, false, false), true
	case "i128", "u128", "i128le", "i128be", "u128le", "u128be":
		return type_layout(16, min(16, target_max_align(ast_context)), false, true), true
	case "int", "uint":
		return type_layout(word, word, true, true), true
	case "uintptr", "rawptr", "cstring", "cstring16":
		return type_layout(ptr, ptr, true, true), true
	case "string", "string16":
		return type_layout(2 * word, word, true, true), true
	case "any":
		return type_layout(16, 8, false, false), true
	case "complex32":
		return type_layout(4, 2, false, false), true
	case "complex64":
		return type_layout(8, 4, false, false), true
	case "complex128":
		return type_layout(16, min(8, target_max_align(ast_context)), false, true), true
	case "quaternion64":
		return type_layout(8, 2, false, false), true
	case "quaternion128":
		return type_layout(16, 4, false, false), true
	case "quaternion256":
		return type_layout(32, min(8, target_max_align(ast_context)), false, true), true
	}
	return {}, false
}

eval_const_int :: proc(
	ast_context: ^AstContext,
	expr: ^ast.Expr,
	depth := 0,
	locals: ^map[string]i64 = nil,
) -> (
	value: i64,
	ok: bool,
) {
	if expr == nil || depth > LAYOUT_MAX_DEPTH {
		return 0, false
	}

	set_ast_package_from_node_scoped(ast_context, expr)

	#partial switch v in expr.derived {
	case ^ast.Basic_Lit:
		#partial switch v.tok.kind {
		case .Integer:
			text, _ := strings.remove_all(v.tok.text, "_", context.temp_allocator)
			return strconv.parse_i64(text)
		case .Rune:
			text := v.tok.text
			if len(text) < 3 {
				return 0, false
			}
			r, _, _, success := strconv.unquote_char(text[1:len(text) - 1], '\'')
			return i64(r), success
		}
	case ^ast.Paren_Expr:
		return eval_const_int(ast_context, v.expr, depth + 1, locals)
	case ^ast.Unary_Expr:
		x := eval_const_int(ast_context, v.expr, depth + 1, locals) or_return
		#partial switch v.op.kind {
		case .Sub:
			if x==min(i64) {return 0,false}
			return -x, true
		case .Add:
			return x, true
		case .Xor:
			return ~x, true
		}
	case ^ast.Binary_Expr:
		x := eval_const_int(ast_context, v.left, depth + 1, locals) or_return
		y := eval_const_int(ast_context, v.right, depth + 1, locals) or_return
		#partial switch v.op.kind {
		case .Add:
            value,overflow:=intrinsics.overflow_add(x,y)
            return value,!overflow
		case .Sub:
            value,overflow:=intrinsics.overflow_sub(x,y)
            return value,!overflow
		case .Mul:
            value,overflow:=intrinsics.overflow_mul(x,y)
            return value,!overflow
		case .Quo:
            if y==0 || x==min(i64) && y== -1 {return 0,false}
			return x/y,true
		case .Mod:
            if y==0 || x==min(i64) && y== -1 {return 0,false}
			return x%y,true
		case .Shl:
            if y<0 || y>=64 {return 0,false}
            value:=i128(x)<<u64(y)
            return i64(value),value>=i128(min(i64)) && value<=i128(max(i64))
		case .Shr:
            if y<0 {return 0,false}
            if y>=64 {return -1 if x<0 else 0,true}
			return x>>u64(y),true
		case .And:
			return x & y, true
		case .Or:
			return x | y, true
		case .Xor:
			return x ~ y, true
		case .And_Not:
			return x &~ y, true
		}
	case ^ast.Ident:
		if locals != nil {
			if value, found := locals[v.name]; found {
				return value, true
			}
		}
        if bound,found:=layout_bound_expression(ast_context,v); found {return eval_const_int(ast_context,bound,depth+1,locals)}
		symbol := resolve_type_identifier(ast_context, v^) or_return
		return eval_const_symbol(ast_context, symbol, expr, depth)
	case ^ast.Selector_Expr:
		symbol := resolve_type_expression(ast_context, expr) or_return
		return eval_const_symbol(ast_context, symbol, expr, depth)
	case ^ast.Call_Expr:
		if ident, is_ident := v.expr.derived.(^ast.Ident); is_ident && len(v.args) == 1 {
			switch ident.name {
			case "size_of":
				layout := type_expr_layout(ast_context, v.args[0], depth + 1) or_return
				return layout.size, true
			case "align_of":
				layout := type_expr_layout(ast_context, v.args[0], depth + 1) or_return
				return layout.align, true
			}
		}
	}
	return 0, false
}

@(private = "file")
eval_const_symbol :: proc(
	ast_context: ^AstContext,
	symbol: Symbol,
	from: ^ast.Expr,
	depth: int,
) -> (
	value: i64,
	ok: bool,
) {
	if symbol.value_expr != nil && symbol.value_expr != from {
		return eval_const_int(ast_context, symbol.value_expr, depth + 1)
	}
	if v, is_untyped := symbol.value.(SymbolUntypedValue); is_untyped && v.type == .Integer {
		text, _ := strings.remove_all(v.tok.text, "_", context.temp_allocator)
		return strconv.parse_i64(text)
	}
	return 0, false
}

@(private = "file")
enum_value_range :: proc(ast_context: ^AstContext, v: SymbolEnumValue, depth: int) -> (lo, hi: i64, ok: bool) {
	if len(v.names) == 0 {
		return 0, 0, false
	}
	values := make(map[string]i64, context.temp_allocator)
	current: i64 = -1
	for name, i in v.names {
		if i < len(v.values) && v.values[i] != nil {
			current = eval_const_int(ast_context, v.values[i], depth + 1, &values) or_return
		} else {
			current += 1
		}
		values[name] = current
		if i == 0 {
			lo, hi = current, current
		} else {
			lo, hi = min(lo, current), max(hi, current)
		}
	}
	return lo, hi, true
}

@(private = "file")
array_count :: proc(ast_context: ^AstContext, len_expr: ^ast.Expr, depth: int) -> (count: i64, ok: bool) {
	if n, is_const := eval_const_int(ast_context, len_expr, depth + 1); is_const {
		return n, true
	}
	symbol := resolve_type_expression(ast_context, len_expr) or_return
	if e, is_enum := symbol.value.(SymbolEnumValue); is_enum {
		if len(e.names)==0 {return 0,true}
		lo, hi := enum_value_range(ast_context, e, depth + 1) or_return
        if hi<lo || u64(hi)-u64(lo)>=u64(max(i64)) {return 0,false}
		return hi-lo+1,true
	}
	return 0, false
}

// Infer only target-independent literal indices. Keep unresolved or
// target-dependent indexed literals unknown rather than freezing an ABI's count.
inferred_array_literal_count :: proc(ctx: ^AstContext, literal: ^ast.Comp_Lit) -> (count:i64,ok:bool) {
    // This helper also runs during completion/type inference, outside hover.
    if !ctx.layout_active {
        ctx.layout_active=true
        ctx.layout_cache=make(map[Layout_Cache_Key]Layout_Cache_Result,ctx.allocator)
        ctx.layout_budget=HOVER_LAYOUT_BUDGET
    }
    keyed, positional := false,false
    for elem, i in literal.elems {
        field, indexed := elem.derived.(^ast.Field_Value)
        if !indexed {
            positional=true
            count=i64(i)+1
            continue
        }
        keyed=true
        if field.field==nil || layout_expr_dependent(ctx,field.field,0) {return 0,false}
        end: i64
        if range, ok := field.field.derived.(^ast.Binary_Expr); ok &&
            (range.op.kind==.Range_Half || range.op.kind==.Range_Full || range.op.kind==.Ellipsis) {
            lo := eval_const_int(ctx,range.left) or_return
            hi := eval_const_int(ctx,range.right) or_return
            if lo<0 || hi<lo {return 0,false}
            end=hi
            if range.op.kind!=.Range_Half {
                if end==max(i64) {return 0,false}
                end+=1
            }
        } else {
            index := eval_const_int(ctx,field.field) or_return
            if index<0 || index==max(i64) {return 0,false}
            end=index+1
        }
        count=max(count,end)
    }
    return count, !(keyed && positional)
}

@(private = "file")
bit_set_layout :: proc(ast_context: ^AstContext, v: SymbolBitSetValue, depth: int) -> (result: Type_Layout, ok: bool) {
	if v.underlying != nil {
		return type_expr_layout(ast_context, v.underlying, depth + 1)
	}

	lo, hi: i64
    dependent := false
	if range, is_range := v.expr.derived.(^ast.Binary_Expr);
	   is_range && (range.op.kind == .Range_Half || range.op.kind == .Range_Full || range.op.kind == .Ellipsis) {
        dependent=layout_expr_dependent(ast_context,v.expr,depth+1)
		lo = eval_const_int(ast_context, range.left, depth + 1) or_return
		hi = eval_const_int(ast_context, range.right, depth + 1) or_return
		if range.op.kind == .Range_Half {
            if hi==lo {return type_layout(1,1,dependent,dependent),true}
            if hi==min(i64) {return {},false}
			hi -= 1
		}
	} else {
		symbol := resolve_type_expression(ast_context, v.expr) or_return
		e := symbol.value.(SymbolEnumValue) or_return
        if len(e.names)==0 {return type_layout(1,1,false,false),true}
        for value in e.values {dependent=dependent || layout_expr_dependent(ast_context,value,depth+1)}
		lo, hi = enum_value_range(ast_context, e, depth + 1) or_return
	}

    if hi<lo || u64(hi)-u64(lo)>=128 {return {},false}
	bits := i64(u64(hi)-u64(lo))+1
	switch {
	case bits <= 8:
		return type_layout(1, 1, dependent, dependent), true
	case bits <= 16:
		return type_layout(2, 2, dependent, dependent), true
	case bits <= 32:
		return type_layout(4, 4, dependent, dependent), true
	case bits <= 64:
		return type_layout(8, 8, dependent, dependent), true
	case bits <= 128:
		return type_layout(16, 16, dependent, dependent), true
	}
	return {}, false
}

@(private = "file")
is_pointer_like_symbol :: proc(symbol: Symbol) -> bool {
	if symbol.pointers > 0 {
		return true
	}
	#partial switch v in symbol.value {
	case SymbolMultiPointerValue, SymbolProcedureValue:
		return true
	case SymbolBasicValue:
		if v.ident != nil {
			switch v.ident.name {
			case "rawptr", "cstring", "cstring16":
				return true
			}
		}
	}
	return false
}

@(private = "file")
union_layout :: proc(ctx: ^AstContext, v: SymbolUnionValue, depth: int) -> (Type_Layout, bool) {
    if !ctx.layout_active {return union_layout_uncached(ctx,v,depth)}
    key := Layout_Cache_Key{types=cast(rawptr)v.origin if v.origin!=nil else raw_data(v.types),args=v.instantiation,count=len(v.types),align=v.align,flags=u32(v.kind),is_union=true,target=ctx.layout_target}
    if found, ok := ctx.layout_cache[key]; ok {return found.layout, found.ok && !found.busy}
    ctx.layout_cache[key]={busy=true}
    layout, ok := union_layout_uncached(ctx,v,depth)
    ctx.layout_cache[key]={layout=layout,ok=ok}
    return layout,ok
}

@(private = "file")
union_layout_uncached :: proc(ast_context: ^AstContext, v: SymbolUnionValue, depth: int) -> (result: Type_Layout, ok: bool) {
    scope:=Layout_Bindings{origin=v.origin,poly=v.poly,parent=ast_context.layout_bindings}
    if v.instantiation!=nil {scope.args=v.instantiation.args}
    if scope.poly!=nil && len(scope.args)>0 {ast_context.layout_bindings=&scope}
    defer ast_context.layout_bindings=scope.parent
	if len(v.types) == 0 {
		return type_layout(0, 1, false, false), true
	}

	custom_align: i64 = 0
	if v.align != nil {
		custom_align = eval_const_int(ast_context, v.align, depth + 1) or_return
	}

	max_size, max_align: i64 = 0, 1
    dependent := layout_expr_dependent(ast_context,v.align,depth+1)
	for t in v.types {
		l := type_expr_layout(ast_context, t, depth + 1) or_return
        dependent = dependent || l.size_dependent || l.align_dependent
		max_size = max(max_size, l.size)
		max_align = max(max_align, l.align)
	}

	align := custom_align > 0 ? custom_align : max_align

	if len(v.types) == 1 {
		if symbol, resolved := resolve_type_expression(ast_context, v.types[0]);
		   resolved && is_pointer_like_symbol(symbol) {
			return type_layout(align_forward(max_size, align), align, dependent, dependent), true
		}
	}

	// Mirrors `union_tag_size` in the compiler.
	tag_size: i64 = 1
	if len(v.types) >= 1 << 16 {
		tag_size = 4
	} else if len(v.types) >= 1 << 8 {
		tag_size = 2
	}
	tag_size = max(tag_size, align)
	tag_size = min(tag_size, target_abi(ast_context).max_align, 8)

    size:=align_forward(max_size,tag_size)
    if size<0 || size>max(i64)-tag_size {return {},false}
    size+=tag_size
	return type_layout(align_forward(size, align), align, dependent, dependent), true
}

struct_layout :: proc(ctx: ^AstContext, v: SymbolStructValue, depth := 0, fields: ^[dynamic]Field_Layout = nil) -> (Type_Layout, bool) {
    if !ctx.layout_active || fields != nil {return struct_layout_uncached(ctx, v, depth, fields)}
    key := Layout_Cache_Key{types=cast(rawptr)v.origin if v.origin!=nil else raw_data(v.types),
        args=cast(rawptr)v.instantiation if v.origin!=nil else raw_data(v.args),count=len(v.types),
        align=v.align,min_align=v.min_field_align,max_align=v.max_field_align,tags=v.tags,target=ctx.layout_target}
    if found, ok := ctx.layout_cache[key]; ok {return found.layout, found.ok && !found.busy}
    ctx.layout_cache[key] = {busy=true}
    layout, ok := struct_layout_uncached(ctx, v, depth, fields)
    ctx.layout_cache[key] = {layout=layout,ok=ok}
    return layout, ok
}

struct_layout_uncached :: proc(
	ast_context: ^AstContext,
	v: SymbolStructValue,
	depth := 0,
	fields: ^[dynamic]Field_Layout = nil,
) -> (
	result: Type_Layout,
	ok: bool,
) {
	if depth > LAYOUT_MAX_DEPTH {
		return {}, false
	}
    scope:=Layout_Bindings{origin=v.origin,poly=v.poly,args=v.args,parent=ast_context.layout_bindings}
    if scope.poly!=nil && len(scope.args)>0 {ast_context.layout_bindings=&scope}
    defer ast_context.layout_bindings=scope.parent

	is_packed := .Is_Packed in v.tags
	is_raw_union := .Is_Raw_Union in v.tags

	min_field_align, max_field_align: i64 = 0, 0
	if v.min_field_align != nil {
		min_field_align = eval_const_int(ast_context, v.min_field_align, depth + 1) or_return
	}
	if v.max_field_align != nil {
		max_field_align = eval_const_int(ast_context, v.max_field_align, depth + 1) or_return
	}

	offset, size, align: i64 = 0, 0, 1
    size_dependent, align_dependent := false, false
    for attribute in ([]^ast.Expr{v.align,v.min_field_align,v.max_field_align}) {
        if layout_expr_dependent(ast_context, attribute, depth+1) {size_dependent,align_dependent=true,true}
    }
	for name, i in v.names {
		if i < len(v.from_usings) && v.from_usings[i] != -1 {
			continue
		}

		field := type_expr_layout(ast_context, v.types[i], depth + 1) or_return
        if field.size<0 || field.align<=0 {return {},false}
		field_align := field.align
        size_dependent = size_dependent || field.size_dependent || (!is_packed && field.align_dependent)
        align_dependent = align_dependent || (!is_packed && field.align_dependent)
		if is_packed {
			field_align = 1
		} else {
			if min_field_align > 0 {
				field_align = max(field_align, min_field_align)
			}
			if max_field_align > 0 {
				field_align = min(field_align, max_field_align)
			}
		}

		field_offset: i64 = 0
		if is_raw_union {
			size = max(size, field.size)
		} else {
			offset = align_forward(offset, field_align)
            if offset<0 || offset>max(i64)-field.size {return {},false}
			field_offset = offset
			offset += field.size
			size = offset
		}
		align = max(align, field_align)

		if fields != nil {
			append(fields, Field_Layout{name = name, offset = field_offset, layout = field})
		}
	}

    // Field-alignment directives also affect an empty record's alignment.
    if !is_packed {
        if min_field_align>0 {
            align=max(align,min_field_align)
        }
        if max_field_align>0 {
            align=min(align,max_field_align)
        }
    }

	if v.align != nil {
		align = eval_const_int(ast_context, v.align, depth + 1) or_return
	}
	return type_layout(align_forward(size, align), align, size_dependent, align_dependent), true
}

@(private = "file")
soa_element_fields :: proc(
	ast_context: ^AstContext,
	elem: ^ast.Expr,
	depth: int,
) -> (
	layouts: [dynamic]Type_Layout,
	ok: bool,
) {
	layouts = make([dynamic]Type_Layout, context.temp_allocator)
	symbol := resolve_type_expression(ast_context, elem) or_return
	#partial switch v in symbol.value {
	case SymbolStructValue:
		for _, i in v.names {
			if i < len(v.from_usings) && v.from_usings[i] != -1 {
				continue
			}
			append(&layouts, type_expr_layout(ast_context, v.types[i], depth + 1) or_return)
		}
		return layouts, true
	case SymbolFixedArrayValue:
		n := array_count(ast_context, v.len, depth + 1) or_return
		l := type_expr_layout(ast_context, v.expr, depth + 1) or_return
        if n < 0 || n > HOVER_LAYOUT_BUDGET {return nil, false}
		for _ in 0 ..< n {
			append(&layouts, l)
		}
		return layouts, true
	}
	return layouts, false
}

type_expr_layout :: proc(ast_context: ^AstContext, expr: ^ast.Expr, depth := 0) -> (result: Type_Layout, ok: bool) {
	if expr == nil || depth > LAYOUT_MAX_DEPTH {
		return {}, false
	}
    if ident,ok:=expr.derived.(^ast.Ident); ok {
        if bound,found:=layout_bound_expression(ast_context,ident); found {return type_expr_layout(ast_context,bound,depth+1)}
    }
	symbol := resolve_type_expression(ast_context, expr) or_return
	return symbol_layout(ast_context, symbol, depth + 1)
}

symbol_layout :: proc(ast_context: ^AstContext, symbol: Symbol, depth := 0) -> (result: Type_Layout, ok: bool) {
	if depth > LAYOUT_MAX_DEPTH {return {}, false}
    if ast_context.layout_active {
        if ast_context.layout_budget <= 0 {return {}, false}
        ast_context.layout_budget -= 1
    }

	ptr := target_pointer_size(ast_context)
	word := target_int_size(ast_context)

	if symbol.pointers > 0 {
		if .SoaPointer in symbol.flags {
			return type_layout(2 * word, word, true, true), true
		}
		return type_layout(ptr, ptr, true, true), true
	}

	switch v in symbol.value {
	case SymbolBasicValue:
		if v.ident == nil {
			return {}, false
		}
		return basic_type_layout(ast_context,v.ident.name)
	case SymbolStructValue:
		return struct_layout(ast_context, v, depth + 1)
	case SymbolUnionValue:
		return union_layout(ast_context, v, depth + 1)
	case SymbolEnumValue:
		if v.base_type != nil {
			return type_expr_layout(ast_context, v.base_type, depth + 1)
		}
		return type_layout(word, word, true, true), true
	case SymbolBitSetValue:
		return bit_set_layout(ast_context, v, depth + 1)
	case SymbolBitFieldValue:
		return type_expr_layout(ast_context, v.backing_type, depth + 1)
	case SymbolFixedArrayValue:
		n := array_count(ast_context, v.len, depth + 1) or_return
		if .Soa in symbol.flags {
			fields := soa_element_fields(ast_context, v.expr, depth + 1) or_return
			size, align: i64 = 0, 1
            dependent := layout_expr_dependent(ast_context,v.len,depth+1)
            align_dependent:=false
			for f in fields {
                chunk := layout_product(f.size,n) or_return
                size = align_forward(size,f.align)
                if size<0 || size>max(i64)-chunk {return {},false}
                dependent = dependent || f.size_dependent || f.align_dependent
                align_dependent=align_dependent || f.align_dependent
				size += chunk
				align = max(align, f.align)
			}
			return type_layout(align_forward(size, align), align, dependent, align_dependent), true
		}
		elem := type_expr_layout(ast_context, v.expr, depth + 1) or_return
		if .Simd in symbol.flags {
            if n<=0 || n & (n-1) != 0 {return {},false}
			size := layout_product(elem.size,n) or_return
            align: i64 = 1
            for align < size && align < target_abi(ast_context).simd_align {align *= 2}
            dependent := elem.size_dependent || layout_expr_dependent(ast_context,v.len,depth+1)
			return type_layout(size, align, dependent, size>8 || dependent), true
		}
        if n<0 || elem.size<0 || elem.align<=0 {return {},false}
        size: i64
        if n>0 {
            size = layout_product(align_forward(elem.size,elem.align),n-1) or_return
            if size>max(i64)-elem.size {return {},false}
            size += elem.size
        }
		return type_layout(size, elem.align, n>0 && (elem.size_dependent || n>1 && elem.align_dependent) || layout_expr_dependent(ast_context,v.len,depth+1), elem.align_dependent), true
	case SymbolDynamicArrayValue:
		if .Soa in symbol.flags {
			fields := soa_element_fields(ast_context, v.expr, depth + 1) or_return
			return type_layout(i64(len(fields)) * ptr + 2 * word + allocator_layout(ast_context).size, max(ptr, word), true, true), true
		}
		if v.cap != nil {
			n := eval_const_int(ast_context, v.cap, depth + 1) or_return
			elem := type_expr_layout(ast_context, v.expr, depth + 1) or_return
			align := max(elem.align, word)
            size := layout_product(elem.size,n) or_return
            size = align_forward(size,word)
            if size<0 || size>max(i64)-word {return {},false}
			return type_layout(align_forward(size + word, align), align, true, true), true
		}
		return type_layout(3 * word + 2 * ptr, max(ptr, word), true, true), true
	case SymbolSliceValue:
		if .Soa in symbol.flags {
			fields := soa_element_fields(ast_context, v.expr, depth + 1) or_return
			return type_layout(i64(len(fields)) * ptr + word, max(ptr, word), true, true), true
		}
		return type_layout(2 * word, word, true, true), true
	case SymbolMultiPointerValue:
		return type_layout(ptr, ptr, true, true), true
	case SymbolMapValue:
		return type_layout(4 * ptr, ptr, true, true), true
	case SymbolMatrixValue:
		rows := eval_const_int(ast_context, v.x, depth + 1) or_return
		cols := eval_const_int(ast_context, v.y, depth + 1) or_return
		elem := type_expr_layout(ast_context, v.expr, depth + 1) or_return
        major_count := cols if v.row_major else rows
        stride := layout_product(major_count,elem.size) or_return
        size := layout_product(layout_product(rows,cols) or_return,elem.size) or_return
        align := clamp(stride & -stride,elem.align,min(LAYOUT_MAX_SIMD_ALIGN,target_abi(ast_context).simd_align)) if stride>0 else elem.align
        size_dependent := elem.size_dependent || layout_expr_dependent(ast_context,v.x,depth+1) || layout_expr_dependent(ast_context,v.y,depth+1)
        align_dependent:=elem.align_dependent || stride>0 && stride%16==0 || size_dependent
		return type_layout(size,align,size_dependent,align_dependent),true
	case SymbolProcedureValue:
		return type_layout(ptr, ptr, true, true), true
    case SymbolUntypedValue: return untyped_storage_layout(ast_context,v.type)
	case SymbolPackageValue,
	     SymbolGenericValue,
	     SymbolProcedureGroupValue,
	     SymbolAggregateValue,
	     SymbolPolyTypeValue,
		 SymbolLabelValue:
		return {}, false
	}
	return {}, false
}

construct_symbol_layout :: proc(
	ast_context: ^AstContext,
	symbol: Symbol,
	allocator := context.temp_allocator,
) -> string {
	#partial switch symbol.type {
	case .Function, .Package, .Keyword, .EnumMember, .Unresolved, .Type_Function:
		return ""
	}
	#partial switch v in symbol.value {
	case SymbolProcedureValue:
		if symbol.type != .Variable && symbol.type != .Field {
			return ""
		}
	case SymbolUntypedValue, SymbolProcedureGroupValue, SymbolAggregateValue, SymbolPackageValue:
		return ""
	}

	is_type := symbol.type != .Variable && symbol.type != .Field && symbol.type != .Constant

	if v, is_struct := symbol.value.(SymbolStructValue); is_struct && is_type && symbol.pointers == 0 {
		fields := make([dynamic]Field_Layout, context.temp_allocator)
		layout, ok := struct_layout(ast_context, v, 0, &fields)
		if !ok {
			return ""
		}
		padding := struct_padding(layout, fields[:], .Is_Raw_Union in v.tags)
		if padding > 0 {
			return fmt.aprintf(
				"size=%v, align=%v, padding=%v",
				layout.size,
				layout.align,
				padding,
				allocator = allocator,
			)
		}
		return fmt.aprintf("size=%v, align=%v", layout.size, layout.align, allocator = allocator)
	}

	layout, ok := symbol_layout(ast_context, symbol)
	if !ok {
		return ""
	}
	return fmt.aprintf("size=%v, align=%v", layout.size, layout.align, allocator = allocator)
}

construct_field_layout :: proc(
	ast_context: ^AstContext,
	parent: SymbolStructValue,
	field_index: int,
	allocator := context.temp_allocator,
) -> string {
	if field_index < 0 || field_index >= len(parent.names) {
		return ""
	}

	if field_index < len(parent.from_usings) && parent.from_usings[field_index] != -1 {
		layout, ok := type_expr_layout(ast_context, parent.types[field_index])
		if !ok {
			return ""
		}
		return fmt.aprintf("size=%v, align=%v", layout.size, layout.align, allocator = allocator)
	}

	comments, ok := struct_field_layout_comments(ast_context, parent, allocator)
	if !ok {
		return ""
	}
	return comments[field_index] or_else ""
}

struct_field_layout_comments :: proc(
	ast_context: ^AstContext,
	v: SymbolStructValue,
	allocator := context.temp_allocator,
) -> (
	comments: map[int]string,
	ok: bool,
) {
	fields := make([dynamic]Field_Layout, context.temp_allocator)
	layout := struct_layout(ast_context, v, 0, &fields) or_return
	is_raw_union := .Is_Raw_Union in v.tags

	comments = make(map[int]string, allocator)
	field := 0
	for name, i in v.names {
		if i < len(v.from_usings) && v.from_usings[i] != -1 {
			continue
		}
		if field >= len(fields) || fields[field].name != name {
			return comments, false
		}
		f := fields[field]
		field += 1

		padding: i64 = 0
		if !is_raw_union {
			next := layout.size
			if field < len(fields) {
				next = fields[field].offset
			}
			padding = next - (f.offset + f.layout.size)
		}

		if padding > 0 {
			comments[i] = fmt.aprintf(
				"size=%v, offset=%v (+%v padding)",
				f.layout.size,
				f.offset,
				padding,
				allocator = allocator,
			)
		} else {
			comments[i] = fmt.aprintf("size=%v, offset=%v", f.layout.size, f.offset, allocator = allocator)
		}
	}
	return comments, true
}

@(private = "file")
struct_padding :: proc(layout: Type_Layout, fields: []Field_Layout, is_raw_union: bool) -> i64 {
	padding: i64 = 0
	end: i64 = 0
	for f in fields {
		if !is_raw_union && f.offset > end {
			padding += f.offset - end
		}
		end = max(end, f.offset + f.layout.size)
	}
	if layout.size > end {
		padding += layout.size - end
	}
	return padding
}

append_first_line_comment :: proc(text, comment: string, allocator := context.temp_allocator) -> string {
	if newline := strings.index_byte(text, '\n'); newline >= 0 {
		return strings.concatenate({text[:newline], " // ", comment, text[newline:]}, allocator)
	}
	return strings.concatenate({text, " // ", comment}, allocator)
}

LAYOUT_COMMENT_MARKER :: '\x01'

align_layout_comments :: proc(text: string, allocator := context.temp_allocator) -> string {
	column := 0
	for line in strings.split_lines(text, context.temp_allocator) {
		if i := strings.index_byte(line, u8(LAYOUT_COMMENT_MARKER)); i >= 0 {
			column = max(column, i)
		}
	}

	sb := strings.builder_make(allocator)
	lines := strings.split_lines(text, context.temp_allocator)
	for line, n in lines {
		if i := strings.index_byte(line, u8(LAYOUT_COMMENT_MARKER)); i >= 0 {
			strings.write_string(&sb, line[:i])
			for _ in i ..= column {
				strings.write_byte(&sb, ' ')
			}
			strings.write_string(&sb, "// ")
			strings.write_string(&sb, line[i + 1:])
		} else {
			strings.write_string(&sb, line)
		}
		if n < len(lines) - 1 {
			strings.write_byte(&sb, '\n')
		}
	}
	return strings.to_string(sb)
}

untyped_storage_layout :: proc(ast_context: ^AstContext, kind: SymbolUntypedValueType) -> (Type_Layout,bool) {
    switch kind {
    case .Integer: return basic_type_layout(ast_context,"int")
    case .Float: return basic_type_layout(ast_context,"f64")
    case .Complex: return basic_type_layout(ast_context,"complex128")
    case .Quaternion: return basic_type_layout(ast_context,"quaternion256")
    case .String: return basic_type_layout(ast_context,"string")
    case .Rune: return basic_type_layout(ast_context,"rune")
    case .Bool: return basic_type_layout(ast_context,"bool")
    }
    return {},false
}

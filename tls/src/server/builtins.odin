package server

import "core:fmt"
import "core:odin/ast"
import "core:odin/tokenizer"
import "core:strconv"
import "core:strings"

// Alias names and packages change during lookup. The original builtin identity
// belongs to the resolved declaration, including for parenthesized re-exports.
builtin_proc_name :: proc(symbol: Symbol) -> string {
	if procedure, found := symbol.value.(SymbolProcedureValue); found && procedure.builtin_name != "" {
		return procedure.builtin_name
	}
	if symbol.pkg == "$builtin" { return symbol.name }
	return ""
}

resolve_type_of_call :: proc(ast_context: ^AstContext, call: ^ast.Call_Expr) -> (Symbol, bool) {
	if len(call.args) != 1 { return {}, false }
	argument_context := ast_context^
	argument_context.call = nil
	argument_context.use_locals = true
	argument_context.current_package = get_package_from_node(call)
	symbol, found := resolve_value_type_expression(&argument_context, call.args[0])
	if !found { return {}, false }
	symbol.flags -= {.Variable, .Mutable, .Immutable, .Parameter}
	symbol.type = .Type
	#partial switch _ in symbol.value {
	case SymbolStructValue, SymbolBitFieldValue: symbol.type = .Struct
	case SymbolEnumValue, SymbolUnionValue, SymbolBitSetValue: symbol.type = .Enum
	case SymbolProcedureValue: symbol.type = .Type_Function
	}
	return symbol, true
}

// Preserve the original call AST: only Thor's builtin spread forms gain an
// element result and, for '..', an optional boolean result.
thor_extrema_return :: proc(ast_context: ^AstContext, symbol: Symbol, call: ^ast.Call_Expr) -> (element: ^ast.Expr, optional: bool, ok: bool) {
    if !strings.has_suffix(ast_context.fullpath, ".thor") ||
       (symbol.pkg != "$builtin" && .Builtin not_in symbol.flags) { return }
    name := symbol.name
    if procedure, found := symbol.value.(SymbolProcedureValue); found && procedure.builtin_name != "" { name = procedure.builtin_name }
    if name != "min" && name != "max" && symbol.value_expr != nil {
        #partial switch original in ast.unparen_expr(symbol.value_expr).derived {
        case ^ast.Ident: name = original.name
        case ^ast.Selector_Expr:
            if member, found := original.field.derived.(^ast.Ident); found { name = member.name }
        }
    }
    if name != "min" && name != "max" { return }
    optional = call.ellipsis.pos.line != 0
    for arg in call.args {
        value := ast.unparen_expr(arg)
        expanded := false
        if unary, found := value.derived.(^ast.Unary_Expr); found && unary.op.kind == .Mul_Mul {
            value, expanded = unary.expr, true
        } else if optional && arg.pos.offset >= call.ellipsis.pos.offset+2 {
            expanded = true
        }
        if !expanded { continue }
        argument_context := ast_context^
        argument_context.use_locals = true
        container, found := resolve_type_expression(&argument_context, value)
        if !found { return }
        #partial switch item in container.value {
        case SymbolSliceValue: return item.expr, optional, true
        case SymbolFixedArrayValue: return item.expr, optional, true
        }
        return
    }
    return
}

// Attempts to resolve the type of the builtin proc by following the rules of the odin type checker
// defined in `check_builtin.cpp`.
// We don't need to worry about whether the inputs to the procs are valid which eliminates most edge cases.
// The basic rules are as follows:
//    - For values not known at compile time (eg values return from procs), just return that type.
//        The correct value will either be that type or a compiler error.
//    - If all values are known at compile time, then we essentially compute the relevant value
//        and return that type.
// There is a difference in the returned types between constants and variables. Constants will use an untyped
// value whereas variables will be typed.
check_builtin_proc_return_type :: proc(
	ast_context: ^AstContext,
	symbol: Symbol,
	call: ^ast.Call_Expr,
	is_mutable: bool,
) -> (
	^ast.Expr,
	bool,
) {
	if element, _, found := thor_extrema_return(ast_context, symbol, call); found { return element, true }
	name := builtin_proc_name(symbol)
	if name != "" {
		// Resolving an imported callee changes package/local lookup settings;
		// its arguments still belong to the caller's lexical scope.
		argument_context := ast_context^
		argument_context.use_locals = true
		argument_context.current_package = get_package_from_node(call)
		ast_context := &argument_context
		switch name {
		case "soa_zip":
			return soa_zip_return_type(ast_context, call)
		case "soa_unzip":
			return soa_unzip_return_type(ast_context, call)
		case "swizzle":
			if len(call.args) < 3 { return nil, false }
			column, found := resolve_value_type_expression(ast_context, call.args[0])
			if !found || column.pointers != 0 { return nil, false }
			array, found_array := column.value.(SymbolFixedArrayValue)
			if !found_array { return nil, false }
			if .Simd not_in column.flags {
				length := get_return_expr(ast_context, array.len, false)
				if literal, found := length.derived.(^ast.Basic_Lit); found {
					if count, parsed := strconv.parse_int(literal.tok.text, 0); parsed && count == len(call.args)-1 {
						return get_return_expr(ast_context, call.args[0], is_mutable), true
					}
				}
			}
			result := new_type(ast.Array_Type, call.pos, call.end, ast_context.allocator)
			result.elem = array.expr
			count := new_type(ast.Basic_Lit, call.pos, call.end, ast_context.allocator)
			count.tok = {kind=.Integer, text=fmt.aprintf("%d", len(call.args)-1, allocator=ast_context.allocator)}
			result.len = count
			if .Simd in column.flags {
				tag := new_type(ast.Basic_Directive, call.pos, call.pos, ast_context.allocator)
				tag.name = "simd"
				result.tag = tag
			}
			return result, true
		case "type_info_of":
			return make_symbol_type_reference(ast_context, Symbol{name="Type_Info", pkg=indexer.runtime_package, pointers=1}, call), true
		case "type_of":
			if len(call.args) == 1 { return get_return_expr(ast_context, call.args[0], true), true }
		case "max", "min":
			if len(call.args) == 1 {
				if value, found := resolve_value_type_expression(ast_context, call.args[0]); found {
					if array, found_array := value.value.(SymbolFixedArrayValue); found_array {
						if index, found_index := resolve_value_type_expression(ast_context, array.len); found_index {
							if _, is_enum := index.value.(SymbolEnumValue); is_enum { return array.len, true }
						}
					}
				}
			}
			curr_candidate: ^ast.Basic_Lit
			curr_value := 0.0
			for arg, i in call.args {
				if lit, value, ok := get_basic_lit_value(arg); ok {
					if i != 0 {
						if compare_basic_lit_value(value, curr_value, name) {
							curr_candidate = lit
							curr_value = value
						}
					} else {
						curr_candidate = lit
						curr_value = value
					}
				} else {
					return get_return_expr(ast_context, arg, is_mutable), true
				}
			}
			if curr_candidate != nil {
				return convert_candidate(curr_candidate, is_mutable), true
			}
		case "abs":
			for arg in call.args {
				if lit, _, ok := get_basic_lit_value(arg); ok {
					return convert_candidate(lit, is_mutable), true
				}
				expr := get_return_expr(ast_context, arg, is_mutable)
				if ident, ok := expr.derived.(^ast.Ident); ok {
					if component := builtin_numeric_component(ident.name); component != "" {
						return make_ident_ast(ast_context, call.pos, call.end, component), true
					}
				}
				return expr, true
			}
		case "clamp":
			if len(call.args) == 3 {
				value_lit, value_value, value_ok := get_basic_lit_value(call.args[0])
				if !value_ok {
					return get_return_expr(ast_context, call.args[0], is_mutable), true
				}

				minimum_lit, minimum_value, minimum_ok := get_basic_lit_value(call.args[1])
				if !minimum_ok {
					return get_return_expr(ast_context, call.args[1], is_mutable), true
				}

				maximum_lit, maximum_value, maximum_ok := get_basic_lit_value(call.args[2])
				if !maximum_ok {
					return get_return_expr(ast_context, call.args[2], is_mutable), true
				}

				if value_value < minimum_value {
					return convert_candidate(minimum_lit, is_mutable), true
				}
				if value_value > maximum_value {
					return convert_candidate(maximum_lit, is_mutable), true
				}

				return convert_candidate(value_lit, is_mutable), true
			}
		case "conj":
			if len(call.args) == 1 { return get_return_expr(ast_context, call.args[0], is_mutable), true }
		case "real", "imag", "jmag", "kmag":
			if len(call.args) == 1 {
				expr := get_return_expr(ast_context, call.args[0], true)
				if ident, ok := expr.derived.(^ast.Ident); ok {
					component := builtin_numeric_component(ident.name)
					if component != "" { return make_ident_ast(ast_context, call.pos, call.end, component), true }
				}
			}
		case "complex":
			candidate: ^ast.Basic_Lit
			for arg in call.args {
				if lit, _, ok := get_basic_lit_value(arg); ok {
					candidate = lit
				} else {
					expr := get_complex_return_expr(ast_context, arg)
					if ident, ok := expr.derived.(^ast.Ident); ok {
						switch ident.name {
						case "f16":
							return make_ident_ast(ast_context, call.pos, call.end, "complex32"), true
						case "f32":
							return make_ident_ast(ast_context, call.pos, call.end, "complex64"), true
						case "f64":
							return make_ident_ast(ast_context, call.pos, call.end, "complex128"), true
						}
					}
				}
			}
			if candidate != nil {
				return convert_complex_candidate(candidate, is_mutable), true
			}
		case "quaternion":
			candidate: ^ast.Basic_Lit
			for arg in call.args {
				if lit, _, ok := get_basic_lit_value(arg); ok {
					candidate = lit
				} else {
					expr := get_quaternion_return_expr(ast_context, arg)
					if ident, ok := expr.derived.(^ast.Ident); ok {
						switch ident.name {
						case "f16":
							return make_ident_ast(ast_context, call.pos, call.end, "quaternion64"), true
						case "f32":
							return make_ident_ast(ast_context, call.pos, call.end, "quaternion128"), true
						case "f64":
							return make_ident_ast(ast_context, call.pos, call.end, "quaternion256"), true
						}
					}
				}
			}
			if candidate != nil {
				return convert_quaternion_candidate(candidate, is_mutable), true
			}
		case "expand_values":
			if len(call.args) != 1 {
				return nil, false
			}

			return expand_values(ast_context, call.args[0])
		case "compress_values":
			args_len := len(call.args)
			flattened_len := args_len
			contains_proc_return := false

			if args_len == 0 {
				return nil, false
			}

			exprs := make([]^ast.Expr, args_len, context.temp_allocator)
			types := make([]string, args_len, context.temp_allocator)

			for i in 0 ..< args_len {
				if expr, typ, r_amt, ok := get_expr_return_value(ast_context, call.args[i]); ok {
					if r_amt >= 2 {
						contains_proc_return = true
						flattened_len += r_amt-1
					}
					exprs[i] = expr
					types[i] = typ
				} else {
					return nil, false
				}
			}

			// single expr
			if flattened_len == 1 {
				return exprs[0], true
			}

			all_same := true
			for i in 1 ..< args_len {
				if types[i] != types[0] {
					all_same = false
					break
				}
			}

			// if return type is an array
			if all_same && !contains_proc_return {
				arr := new_type(ast.Array_Type, call.pos, call.end, context.temp_allocator)
				arr.elem = exprs[0]

				len_lit := new_type(ast.Basic_Lit, call.pos, call.end, context.temp_allocator)
				len_lit.tok.kind = .Integer
				len_lit.tok.text = fmt.tprintf("%d", args_len)
				arr.len = len_lit

				return arr, true
			}

			st := new_type(ast.Struct_Type, call.pos, call.end, context.temp_allocator)
			fl := new_type(ast.Field_List, call.pos, call.end, context.temp_allocator)
			fl.list = make([]^ast.Field, flattened_len, context.temp_allocator)
			flattened_exprs: []^ast.Expr

			if flattened_len == args_len {
				flattened_exprs = exprs
			} else {
				flattened_exprs = make([]^ast.Expr, flattened_len, context.temp_allocator)
				ind := 0
				for &i in exprs {
					if v, v_ok := i.derived.(^ast.Comp_Lit); v_ok {
						for &elem in v.elems {
							flattened_exprs[ind] = elem
							ind += 1
						}
					} else {
						flattened_exprs[ind] = i
						ind += 1
					}
				}
			}

			for i in 0 ..< flattened_len {
				f := new_type(ast.Field, call.pos, call.end, context.temp_allocator)
				name_ident := new_type(ast.Ident, call.pos, call.end, context.temp_allocator)
				name_ident.name = fmt.tprintf("v%v", i)
				f.names = make([]^ast.Expr, 1, context.temp_allocator)
				f.names[0] = name_ident
				f.type = flattened_exprs[i]
				fl.list[i] = f
			}

			st.fields = fl
			return st, true
		}
	}

	return nil, false
}

@(private = "file")
soa_zip_return_type :: proc(ast_context: ^AstContext, call: ^ast.Call_Expr) -> (^ast.Expr, bool) {
	if len(call.args) == 0 { return nil, false }
	row := new_type(ast.Struct_Type, call.pos, call.end, ast_context.allocator)
	row.fields = new_type(ast.Field_List, call.pos, call.end, ast_context.allocator)
	row.fields.list = make([]^ast.Field, len(call.args), ast_context.allocator)
	for argument, index in call.args {
		value := ast.unparen_expr(argument)
		name: ^ast.Ident
		if named, found := value.derived.(^ast.Field_Value); found {
			field, valid := named.field.derived.(^ast.Ident)
			if !valid { return nil, false }
			name = field
			value = named.value
		} else {
			name = new_type(ast.Ident, argument.pos, argument.end, ast_context.allocator)
			name.name = fmt.aprintf("_%d", index, allocator = ast_context.allocator)
		}
		column_context := ast_context^
		if column_call, found := get_completed_call(value); found { column_context.call = column_call }
		column, found := resolve_value_type_expression(&column_context, value)
		if !found || column.pointers != 0 || .Soa in column.flags { return nil, false }
		slice, is_slice := column.value.(SymbolSliceValue)
		if !is_slice { return nil, false }
		field := new_type(ast.Field, argument.pos, argument.end, ast_context.allocator)
		field.names = make([]^ast.Expr, 1, ast_context.allocator)
		field.names[0] = name
		// Preserve the column element's original file and generic specialization.
		field.type = slice.expr
		row.fields.list[index] = field
	}
	result := new_type(ast.Array_Type, call.pos, call.end, ast_context.allocator)
	result.elem = row
	tag := new_type(ast.Basic_Directive, call.pos, call.pos, ast_context.allocator)
	tag.name = "soa"
	result.tag = tag
	return result, true
}

@(private = "file")
soa_unzip_return_type :: proc(ast_context: ^AstContext, call: ^ast.Call_Expr) -> (^ast.Expr, bool) {
	if len(call.args) != 1 { return nil, false }
	container, found := resolve_value_type_expression(ast_context, call.args[0])
	if !found || .Soa not_in container.flags || container.pointers != 0 { return nil, false }
	slice, found_slice := container.value.(SymbolSliceValue)
	if !found_slice { return nil, false }
	row: Symbol
	found_row := internal_resolve_type_expression(ast_context, slice.expr, &row)
	if !found_row { return nil, false }
	fields, found_fields := row.value.(SymbolStructValue)
	if !found_fields { return nil, false }
	results := make([dynamic]^ast.Expr, ast_context.allocator)
	for field, index in fields.types {
		if index < len(fields.from_usings) && fields.from_usings[index] != -1 { continue }
		column := new_type(ast.Array_Type, call.pos, call.end, ast_context.allocator)
		column.elem = field
		append(&results, column)
	}
	return make_result_call(call, results[:], ast_context.allocator), true
}

@(private = "file")
make_result_call :: proc(source: ^ast.Expr, results: []^ast.Expr, allocator := context.temp_allocator) -> ^ast.Call_Expr {
	call := new_type(ast.Call_Expr, source.pos, source.end, allocator)
	type := new_type(ast.Proc_Type, source.pos, source.end, allocator)
	type.results = new_type(ast.Field_List, source.pos, source.end, allocator)
	type.results.list = make([]^ast.Field, len(results), allocator)
	for result, index in results {
		field := new_type(ast.Field, source.pos, source.end, allocator)
		field.type = result
		type.results.list[index] = field
	}
	call.expr = type
	return call
}

@(private = "file")
builtin_numeric_component :: proc(name: string) -> string {
	switch name {
	case "complex32", "quaternion64": return "f16"
	case "complex64", "quaternion128": return "f32"
	case "complex128", "quaternion256": return "f64"
	}
	return ""
}

@(private = "file")
get_return_expr :: proc(ast_context: ^AstContext, expr: ^ast.Expr, is_mutable: bool) -> ^ast.Expr {
	if v, ok := expr.derived.(^ast.Field_Value); ok {
		return get_return_expr(ast_context, v.value, is_mutable)
	}
	if symbol, ok := resolve_value_type_expression(ast_context, expr); ok {
		#partial switch _ in symbol.value {
		case SymbolFixedArrayValue, SymbolMatrixValue:
			if symbol.type_expr != nil { return symbol.type_expr }
			if symbol.value_expr != nil {
				if literal, found := symbol.value_expr.derived.(^ast.Comp_Lit); found && literal.type != nil { return literal.type }
			}
		}
		if v, ok := symbol.value.(SymbolBasicValue); ok {
			return v.ident
		} else if v, ok := symbol.value.(SymbolUntypedValue); ok {
			lit := new_type(ast.Basic_Lit, expr.pos, expr.end, context.temp_allocator)
			lit.tok = v.tok
			return convert_candidate(lit, is_mutable)
		}
	}
	return expr
}

@(private = "file")
convert_candidate :: proc(candidate: ^ast.Basic_Lit, is_mutable: bool) -> ^ast.Expr {
	if is_mutable {
		ident := new_type(ast.Ident, candidate.pos, candidate.end, context.temp_allocator)
		ident.name = token_kind_to_str(candidate.tok)
		return ident
	}

	return candidate
}

@(private = "file")
get_complex_return_expr :: proc(ast_context: ^AstContext, expr: ^ast.Expr) -> ^ast.Expr {
	if v, ok := expr.derived.(^ast.Field_Value); ok {
		return get_complex_return_expr(ast_context, v.value)
	}
	if symbol, ok := resolve_value_type_expression(ast_context, expr); ok {
		if v, ok := symbol.value.(SymbolBasicValue); ok {
			return v.ident
		} else if v, ok := symbol.value.(SymbolUntypedValue); ok {
			// There isn't a token for `Complex` so we just set it to `f64` instead
			ident := new_type(ast.Ident, expr.pos, expr.end, context.temp_allocator)
			ident.name = "f64"
			return ident
		}
	}
	return expr
}

@(private = "file")
convert_complex_candidate :: proc(candidate: ^ast.Basic_Lit, is_mutable: bool) -> ^ast.Expr {
	if is_mutable {
		ident := new_type(ast.Ident, candidate.pos, candidate.end, context.temp_allocator)
		ident.name = "complex128"
		return ident
	}

	return candidate
}

@(private = "file")
get_quaternion_return_expr :: proc(ast_context: ^AstContext, expr: ^ast.Expr) -> ^ast.Expr {
	if v, ok := expr.derived.(^ast.Field_Value); ok {
		return get_quaternion_return_expr(ast_context, v.value)
	}
	if symbol, ok := resolve_value_type_expression(ast_context, expr); ok {
		if v, ok := symbol.value.(SymbolBasicValue); ok {
			return v.ident
		} else if v, ok := symbol.value.(SymbolUntypedValue); ok {
			// There isn't a token for `Quaternion` so we just set it to `quaternion256` instead
			ident := new_type(ast.Ident, expr.pos, expr.end, context.temp_allocator)
			ident.name = "f64"
			return ident
		}
	}
	return expr
}

@(private = "file")
convert_quaternion_candidate :: proc(candidate: ^ast.Basic_Lit, is_mutable: bool) -> ^ast.Expr {
	if is_mutable {
		ident := new_type(ast.Ident, candidate.pos, candidate.end, context.temp_allocator)
		ident.name = "quaternion256"
		return ident
	}

	return candidate
}

@(private = "file")
get_basic_lit_value :: proc(n: ^ast.Expr) -> (^ast.Basic_Lit, f64, bool) {
	n := n
	if v, ok := n.derived.(^ast.Field_Value); ok {
		return get_basic_lit_value(v.value)
	}

	op := ""
	if u, ok := n.derived.(^ast.Unary_Expr); ok {
		op = u.op.text
		n = u.expr
	}

	if lit, ok := n.derived.(^ast.Basic_Lit); ok {
		text := lit.tok.text
		if op != "" {
			text = fmt.tprintf("%s%s", op, text)
		}
		value, ok := strconv.parse_f64(text)
		if !ok {
			return nil, 0, false
		}

		return lit, value, true
	}

	return nil, 0, false
}

@(private = "file")
compare_basic_lit_value :: proc(a, b: f64, name: string) -> bool {
	if name == "max" {
		return a > b
	} else if name == "min" {
		return a < b
	}
	return a > b
}

@(private = "file")
token_kind_to_str :: proc(tok: tokenizer.Token) -> string {
	#partial switch tok.kind {
	case .Imag:
		if strings.has_suffix(tok.text, "j") || strings.has_suffix(tok.text, "k") { return "quaternion256" }
		return "complex128"
	case .Integer:
		return "int"
	case .Float:
		return "f64"
	case .String:
		return "string"
	case .Rune:
		return "rune"
	case .Invalid:
		return "bool"
	}

	return ""
}

@(private = "file")
get_expr_return_value :: proc(
	ast_context: ^AstContext,
	n: ^ast.Expr,
	allocator := context.temp_allocator,
) -> (
	^ast.Expr,
	string,
	int,
	bool,
) {
	#partial switch i in n.derived {
	case ^ast.Call_Expr:
		call_context := ast_context^
		call_context.call = i
		callee, found := resolve_call_expr(&call_context, i)
		if !found { return nil, "", 0, false }
		if _, procedure := callee.value.(SymbolProcedureValue); !procedure {
			return get_expr_return_value(&call_context, i.expr, allocator)
		}
		results := get_proc_return_types(&call_context, callee, i, true)
		if len(results) == 0 { return nil, "", 0, false }
		for &result in results {
			if value, found := resolve_value_type_expression(&call_context, result); found {
				if untyped, found_untyped := value.value.(SymbolUntypedValue); found_untyped {
					ident := new_type(ast.Ident, result.pos, result.end, allocator)
					ident.name = token_kind_to_str(untyped.tok)
					result = ident
				}
			}
		}
		if len(results) == 1 {
			return results[0], node_to_string(results[0]), 1, true
		}
		tuple := new_type(ast.Comp_Lit, n.pos, n.end, allocator)
		tuple.elems = results
		return tuple, node_to_string(tuple), len(results), true

	case ^ast.Basic_Lit:
		ident := new_type(ast.Ident, n.pos, n.end, allocator)
		ident.name = token_kind_to_str(i.tok)
		return ident, fmt.tprintf("u %v", ident.name), 0, true

	case:
		if v, v_ok := resolve_type_expression(ast_context, n); v_ok {
			return get_symbol_return_value(ast_context, n, v, allocator)
		}
	}

	return nil, "", 0, false
}

@(private = "file")
get_symbol_return_value :: proc(
	ast_context: ^AstContext,
	n: ^ast.Expr,
	symbol: Symbol,
	allocator := context.temp_allocator,
) -> (
	expr: ^ast.Expr,
	str: string,
	return_len: int,
	ok: bool,
) {
	untyped := false
	ast_context.use_locals = true

	#partial switch sym_val in symbol.value {
	case SymbolBasicValue:
		expr = wrap_pointer(sym_val.ident, symbol.pointers)
		return expr, node_to_string(expr), 0, true

	case SymbolUntypedValue:
		if sym_val.type == .Bool {
			untyped = .Mutable in symbol.flags == false ? false : true
			ident := new_type(ast.Ident, n.pos, n.end, allocator)
			ident.name = "bool"
			expr = wrap_pointer(ident, symbol.pointers)
			name := untyped ? fmt.tprintf("u %v", node_to_string(&expr.expr_base)) : node_to_string(&expr.expr_base)
			return expr, name, 0, true
		}

		untyped = .Variable in symbol.flags && .Mutable in symbol.flags == false ? true : false
		ident := new_type(ast.Ident, n.pos, n.end, allocator)
		ident.name = token_kind_to_str(sym_val.tok)
		expr = wrap_pointer(ident, symbol.pointers)
		name := untyped ? fmt.tprintf("u %v", node_to_string(&expr.expr_base)) : node_to_string(&expr.expr_base)
		return expr, name, 0, true


	case SymbolProcedureValue:
		expr = symbol_to_expr(symbol, ast_context.fullpath, allocator)
		return expr, node_to_string(expr), 0, true

	case SymbolFixedArrayValue,
	     SymbolSliceValue,
	     SymbolMapValue,
	     SymbolDynamicArrayValue,
	     SymbolMatrixValue,
	     SymbolMultiPointerValue:
		if symbol.type_expr == nil {
			expr = wrap_pointer(symbol_to_expr(symbol, ast_context.fullpath), symbol.pointers)
			return expr, node_to_string(expr), 0, true
		}

		expr = wrap_pointer(symbol.type_expr, symbol.pointers)
		return expr, node_to_string(expr), 0, true

	case:
		ident := new_type(ast.Ident, n.pos, n.end, allocator)
		ident.name = symbol.name
		expr = wrap_pointer(ident, symbol.pointers)
		return expr, node_to_string(&expr.expr_base), 0, true
	}

	return nil, "", 0, false
}

expand_values :: proc(
	ast_context: ^AstContext,
	expr: ^ast.Expr,
	allocator := context.temp_allocator,
) -> (
	ce: ^ast.Call_Expr,
	ok: bool,
) {
	value, found := resolve_value_type_expression(ast_context, expr)
	if !found || value.pointers != 0 { return nil, false }
	expanded_exprs := make([dynamic]^ast.Expr, allocator)

	#partial switch v in value.value {
	case SymbolFixedArrayValue:
		length_expr := get_return_expr(ast_context, v.len, false)
		if literal, found := length_expr.derived.(^ast.Basic_Lit); found {
			length := strconv.parse_int(literal.tok.text, 0) or_return
			for _ in 0 ..< length {
				append(&expanded_exprs, v.expr)
			}
		} else {
			return nil, false
		}
	case SymbolStructValue:
		for field, index in v.types {
			if index < len(v.from_usings) && v.from_usings[index] != -1 { continue }
			append(&expanded_exprs, field)
		}
	case:
		return nil, false
	}

	return make_result_call(expr, expanded_exprs[:], allocator), true
}

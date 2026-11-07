/*

LSP Reference:
https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_semanticTokens

*/
package server

import "core:odin/ast"
import "core:odin/tokenizer"
import "core:slice"
import "core:strings"

import "src:common"
import "src:spall"

SemanticTokenTypes :: enum u32 {
	Namespace,
	Type,
	Enum,
	Struct,
	Parameter,
	Variable,
	EnumMember,
	Function,
	Member,
	Keyword,
	Modifier,
	Comment,
	String,
	Number,
	Operator,
	Property,
	Method,
	TypeParameter,
	Label,
}
// Need to be in the same order as SemanticTokenTypes
semantic_token_type_names: []string = {
	"namespace",
	"type",
	"enum",
	"struct",
	"parameter",
	"variable",
	"enumMember",
	"function",
	"member",
	"keyword",
	"modifier",
	"comment",
	"string",
	"number",
	"operator",
	"property",
	"method",
	"typeParameter",
	"label",
}

SemanticTokenModifier :: enum u8 {
	Declaration,
	DefaultLibrary,
	Definition,
	Deprecated,
	ReadOnly,
	CompileTime,
}
// Need to be in the same order as SemanticTokenModifier
semantic_token_modifier_names: []string = {"declaration", "defaultLibrary", "definition", "deprecated", "readonly", "thorCompileTime"}
SemanticTokenModifiers :: bit_set[SemanticTokenModifier;u32]

SemanticTokensRequest :: struct {
	range: bool,
}

SemanticTokensClientCapabilities :: struct {
	requests:                SemanticTokensRequest,
	tokenTypes:              []string,
	tokenModifiers:          []string,
	formats:                 []string,
	overlappingTokenSupport: bool,
	multilineTokenSupport:   bool,
}

SemanticTokensLegend :: struct {
	tokenTypes:     []string,
	tokenModifiers: []string,
}

SemanticTokensOptions :: struct {
	legend: SemanticTokensLegend,
	range:  bool,
	full:   bool,
}

SemanticTokensParams :: struct {
	textDocument: TextDocumentIdentifier,
}

SemanticTokensRangeParams :: struct {
	textDocument: TextDocumentIdentifier,
	range:        common.Range,
}

SemanticToken :: struct {
	// token line number, relative to the previous token
	delta_line: u32,
	// token start character, relative to the previous token
	// (relative to 0 or the previous token’s start if they are on the same line)
	delta_char: u32,
	len:        u32,
	type:       SemanticTokenTypes,
	modifiers:  SemanticTokenModifiers,
}
#assert(size_of(SemanticToken) == 5 * size_of(u32))

SemanticTokensResponseParams :: struct {
	data: []u32,
}

SemanticTokenBuilder :: struct {
	current_start: int,
	tokens:        [dynamic]SemanticToken,
	entries:       [dynamic]SemanticTokenEntry,
	symbols:       SymbolAndNodeMap,
	src:           string,
	call_target:   ^ast.Node,
}

SemanticTokenEntry :: struct {
	start, end: int,
	type: SemanticTokenTypes,
	modifiers: SemanticTokenModifiers,
}

semantic_tokens_to_response_params :: proc(tokens: []SemanticToken) -> SemanticTokensResponseParams {
	return {data = (cast([^]u32)raw_data(tokens))[:len(tokens) * 5]}
}

get_semantic_tokens :: proc(
	document: ^Document,
	range: common.Range,
	symbols: SymbolAndNodeMap,
) -> []SemanticToken {
	spall.trace(#procedure, document.fullpath)

	ast_context := make_ast_context(
		document.ast,
		document.imports,
		document.package_name,
		document.uri.uri,
		document.fullpath,
	)
	ast_context.current_package = ast_context.document_package

	builder: SemanticTokenBuilder = {
		tokens  = make([dynamic]SemanticToken, 0, 2000, context.temp_allocator),
		entries = make([dynamic]SemanticTokenEntry, 0, 2000, context.temp_allocator),
		symbols = symbols,
		src     = ast_context.file.src,
	}

	margin := 20

	for decl in document.ast.decls {
		//Look for declarations that overlap with range
		if range.start.line - margin <= decl.end.line && decl.pos.line <= range.end.line + margin {
			visit_node(decl, &builder)
		}
	}

	// Adapted switches revisit selectors and store arms out of source order.
	// LSP deltas require source order; duplicated
	// synthetic nodes must not shift highlighting onto unrelated source text.
	slice.sort_by(builder.entries[:],proc(a,b:SemanticTokenEntry)->bool {
		return a.start<b.start || a.start==b.start && a.end<b.end
	})
	previous_end := -1
	for entry in builder.entries {
		if entry.start<previous_end {continue}
		position:=common.get_relative_token_position(entry.start,transmute([]u8)builder.src,builder.current_start)
		length:=common.get_character_offset_u8_to_u16(entry.end-entry.start,transmute([]u8)builder.src[entry.start:entry.end])
		append(&builder.tokens,SemanticToken{cast(u32)position.line,cast(u32)position.character,cast(u32)length,entry.type,entry.modifiers})
		builder.current_start=entry.start
		previous_end=entry.end
	}
	return builder.tokens[:]
}

write_semantic_at_pos :: proc(
	builder: ^SemanticTokenBuilder,
	pos: int,
	byte_length: int,
	type: SemanticTokenTypes,
	modifiers: SemanticTokenModifiers = {},
) {
	if pos<0 || byte_length<=0 || pos+byte_length>len(builder.src) {return}
	text:=builder.src[pos:pos+byte_length]
	if strings.contains_any(text,"\r\n") {return}
	append(&builder.entries,SemanticTokenEntry{pos,pos+byte_length,type,modifiers})
}

write_semantic_node :: proc(
	builder: ^SemanticTokenBuilder,
	node: ^ast.Node,
	type: SemanticTokenTypes,
	modifiers: SemanticTokenModifiers = {},
) {
	//Sometimes odin ast uses "_" for empty params.
	if ident, ok := node.derived.(^ast.Ident); ok && ident.name == "_" {
		return
	}

	if node.pos.offset<0 || node.end.offset>len(builder.src) || node.end.offset<=node.pos.offset {return}
	name := get_ast_node_string(node, builder.src)
	if ident,ok:=node.derived.(^ast.Ident);ok && name!=ident.name {return}

	write_semantic_at_pos(builder, node.pos.offset, len(name), type, modifiers)
}

write_semantic_token :: proc(
	builder: ^SemanticTokenBuilder,
	token: tokenizer.Token,
	type: SemanticTokenTypes,
	modifiers: SemanticTokenModifiers = {},
) {
	write_semantic_at_pos(builder, token.pos.offset, len(token.text), type, modifiers)
}

visit_nodes :: proc(array: []$T/^ast.Node, builder: ^SemanticTokenBuilder) {
	for elem in array {
		visit_node(elem, builder)
	}
}

visit_node :: proc(node: ^ast.Node, builder: ^SemanticTokenBuilder) {
	if node == nil {
		return
	}

	#partial switch n in node.derived {
	case ^ast.Ellipsis:
		visit_node(n.expr, builder)
	case ^ast.Ident:
		visit_ident(n, n, {}, builder)
	case ^ast.Selector_Expr:
		visit_node(n.expr, builder)
		visit_ident(n.field, n, {}, builder)
	case ^ast.When_Stmt:
		visit_node(n.cond, builder)
		visit_node(n.body, builder)
		visit_node(n.else_stmt, builder)
	case ^ast.Pointer_Type:
		visit_node(n.elem, builder)
	case ^ast.Value_Decl:
		if thor_is_import_alias(n) {
			// Only the selected name is source text. The adapted namespace and
			// alias declaration overlap it and must never emit competing tokens.
			selector := n.values[0].derived.(^ast.Selector_Expr)
			visit_ident(selector.field, selector, {}, builder)
			return
		}
		visit_value_decl(n^, builder)
	case ^ast.Block_Stmt:
		visit_label(n.label, builder)
		visit_nodes(n.stmts, builder)
	case ^ast.Foreign_Block_Decl:
		visit_nodes(n.attributes[:], builder)
		visit_node(n.body, builder)
	case ^ast.Expr_Stmt:
		visit_node(n.expr, builder)
	case ^ast.Matrix_Type:
		visit_node(n.row_count, builder)
		visit_node(n.column_count, builder)
		visit_node(n.elem, builder)
	case ^ast.Matrix_Index_Expr:
		visit_node(n.expr, builder)
		visit_node(n.row_index, builder)
		visit_node(n.column_index, builder)
	case ^ast.Poly_Type:
		write_semantic_node(builder, n.type, .TypeParameter)
		visit_node(n.specialization, builder)
	case ^ast.Range_Stmt:
		visit_label(n.label, builder)
		for val in n.vals {
			visit_range_binding(val, builder)
		}

		visit_node(n.expr, builder)
		visit_node(n.body, builder)
	case ^ast.If_Stmt:
		visit_label(n.label, builder)
		visit_node(n.init, builder)
		visit_node(n.cond, builder)
		visit_node(n.body, builder)

		if n.else_stmt != nil {
			visit_node(n.else_stmt, builder)
		}
	case ^ast.For_Stmt:
		visit_label(n.label, builder)
		visit_node(n.init, builder)
		visit_node(n.cond, builder)
		visit_node(n.post, builder)
		visit_node(n.body, builder)
	case ^ast.Unroll_Range_Stmt:
		visit_label(n.label, builder)
		visit_range_binding(n.val0, builder)
		visit_range_binding(n.val1, builder)
		visit_node(n.expr, builder)
		visit_node(n.body, builder)
	case ^ast.Switch_Stmt:
		visit_label(n.label, builder)
		visit_node(n.init, builder)
		visit_node(n.cond, builder)
		visit_node(n.body, builder)
	case ^ast.Type_Switch_Stmt:
		visit_label(n.label, builder)
		if n.tag != nil {
			if tag, ok := n.tag.derived.(^ast.Assign_Stmt); ok {
				for value in tag.lhs { visit_range_binding(value, builder) }
				visit_nodes(tag.rhs, builder)
			} else {
				visit_node(n.tag, builder)
			}
		}
		visit_node(n.expr, builder)
		visit_node(n.body, builder)
	case ^ast.Assign_Stmt:
		for l in n.lhs {
			// Invalid writes still refer to the original readonly binding.
			visit_node(l, builder)
		}
		visit_nodes(n.rhs, builder)
	case ^ast.Case_Clause:
		visit_nodes(n.list, builder)
		visit_nodes(n.body, builder)
	case ^ast.Call_Expr:
		previous := builder.call_target
		callee := n.expr
		unwrap: for callee != nil {
			#partial switch value in callee.derived {
			case ^ast.Paren_Expr: callee = value.expr
			case ^ast.Deref_Expr: callee = value.expr
			case: break unwrap
			}
		}
		builder.call_target = callee
		if name,ok:=n.expr.derived.(^ast.Ident);!ok||name.pos.offset!=name.end.offset{visit_node(n.expr, builder)}
		builder.call_target = previous
		visit_nodes(n.args, builder)
	case ^ast.Implicit_Selector_Expr:
		write_semantic_node(builder, n.field, .EnumMember)
	case ^ast.Array_Type:
		visit_node(n.len, builder)
		visit_node(n.elem, builder)
	case ^ast.Binary_Expr:
		visit_node(n.left, builder)
		visit_node(n.right, builder)
	case ^ast.Comp_Lit:
		visit_node(n.type, builder)
		visit_nodes(n.elems, builder)
	case ^ast.Struct_Type:
		visit_poly_params(n.poly_params, builder)
		visit_struct_fields(n^, builder)
	case ^ast.Type_Assertion:
		visit_node(n.expr, builder)
		visit_node(n.type, builder)
	case ^ast.Type_Cast:
		visit_node(n.type, builder)
		visit_node(n.expr, builder)
	case ^ast.Paren_Expr:
		visit_node(n.expr, builder)
	case ^ast.Deref_Expr:
		visit_node(n.expr, builder)
	case ^ast.Return_Stmt:
		visit_nodes(n.results, builder)
	case ^ast.Dynamic_Array_Type:
		visit_node(n.elem, builder)
	case ^ast.Fixed_Capacity_Dynamic_Array_Type:
		visit_node(n.capacity, builder)
		visit_node(n.elem, builder)
	case ^ast.Multi_Pointer_Type:
		visit_node(n.elem, builder)
	case ^ast.Field_Value:
		if ident, ok := n.field.derived.(^ast.Ident); ok {
			write_semantic_node(builder, n.field, .Property)
		} else {
			visit_node(n.field, builder)
		}

		visit_node(n.value, builder)
	case ^ast.Index_Expr:
		visit_node(n.expr, builder)
		visit_node(n.index, builder)
	case ^ast.Unary_Expr:
		visit_node(n.expr, builder)
	case ^ast.Implicit:
	case ^ast.Slice_Expr:
		visit_node(n.expr, builder)
		visit_node(n.low, builder)
		visit_node(n.high, builder)
	case ^ast.Using_Stmt:
		visit_nodes(n.list, builder)
	case ^ast.Map_Type:
		visit_node(n.key, builder)
		visit_node(n.value, builder)
	case ^ast.Bit_Set_Type:
		visit_node(n.elem, builder)
		visit_node(n.underlying, builder)
	case ^ast.Defer_Stmt:
		visit_node(n.stmt, builder)
	case ^ast.Import_Decl:
		visit_import_decl(n, builder)
	case ^ast.Or_Return_Expr:
		visit_node(n.expr, builder)
	case ^ast.Or_Else_Expr:
		visit_node(n.x, builder)
		visit_node(n.y, builder)
	case ^ast.Or_Branch_Expr:
		visit_node(n.expr, builder)
		visit_label(n.label, builder)
	case ^ast.Branch_Stmt:
		visit_label(n.label, builder)
	case ^ast.Ternary_If_Expr:
		if n.op1.text == "if" {
			visit_node(n.x, builder)
			visit_node(n.cond, builder)
			visit_node(n.y, builder)
		} else {
			visit_node(n.cond, builder)
			visit_node(n.x, builder)
			visit_node(n.y, builder)
		}
	case ^ast.Ternary_When_Expr:
		visit_node(n.x, builder)
		visit_node(n.cond, builder)
		visit_node(n.y, builder)
	case ^ast.Union_Type:
		visit_poly_params(n.poly_params, builder)
		visit_nodes(n.variants, builder)
	case ^ast.Enum_Type:
		visit_node(n.base_type, builder)
		visit_enum_fields(n^, builder)
	case ^ast.Proc_Type:
		visit_proc_type(n, builder)
	case ^ast.Proc_Lit:
		if n.type==nil||n.type.tok.text!="__thor_comprehension_body" {visit_proc_type(n.type, builder)}
		visit_nodes(n.where_clauses, builder)
		visit_node(n.body, builder)
	case ^ast.Proc_Group:
		visit_nodes(n.args, builder)
	case ^ast.Bit_Field_Type:
		visit_bit_field_fields(n^, builder)
	case ^ast.Helper_Type:
		visit_node(n.type, builder)
	case ^ast.Distinct_Type:
		visit_node(n.type, builder)
	case ^ast.Attribute:
		for elem in n.elems {
			// `@(private, disabled=FOO)`
			//                       ^- only field values are symbols
			#partial switch e in elem.derived {
			case ^ast.Ident:
				write_semantic_node(builder, e, .Property, {.ReadOnly})
			case ^ast.Field_Value:
				write_semantic_node(builder, e.field, .Property, {.ReadOnly})
				visit_node(e.value, builder)
			}
		}
	case:
	}
}

visit_value_decl :: proc(value_decl: ast.Value_Decl, builder: ^SemanticTokenBuilder) {
	modifiers: SemanticTokenModifiers = value_decl.is_mutable && !thor_is_immutable(value_decl.attributes[:]) ? {} : {.ReadOnly}

	for attribute in value_decl.attributes {
		if !thor_is_immutable([]^ast.Attribute{attribute}) { visit_node(attribute, builder) }
	}

	for name in value_decl.names {
		ident := name.derived.(^ast.Ident) or_continue
		visit_ident(ident, ident, modifiers, builder)
	}

	visit_node(value_decl.type, builder)
	visit_nodes(value_decl.values, builder)
}

visit_label :: proc(label: ^ast.Expr, builder: ^SemanticTokenBuilder) {
	if label != nil { write_semantic_node(builder, label, .Label, {.ReadOnly}) }
}

visit_range_binding :: proc(value: ^ast.Expr, builder: ^SemanticTokenBuilder) {
	if value == nil { return }
    if typed,ok:=value.derived.(^ast.Field_Value);ok {
        visit_node(typed.value,builder)
        visit_range_binding(typed.field,builder)
        return
    }
	if ident, ok := unwrap_ident(value); ok {
		modifiers: SemanticTokenModifiers
		if .ReadOnlyBinding in range_binding_flags(value) { modifiers += {.ReadOnly} }
		write_semantic_node(builder, ident, .Variable, modifiers)
	}
}

visit_proc_type :: proc(node: ^ast.Proc_Type, builder: ^SemanticTokenBuilder) {
	if node == nil {
		return
	}

	if node.params != nil {
		for param in node.params.list {
			for name in param.names {
				if ident, ok := name.derived.(^ast.Ident); ok {
					write_semantic_node(builder, name, .Parameter, {.ReadOnly})
				}
			}

			visit_node(param.type, builder)
			visit_node(param.default_value, builder)
		}
	}

	if node.results != nil {
		for result in node.results.list {
			visit_nodes(result.names, builder)
			visit_node(result.type, builder)
		}
	}
}

visit_enum_fields :: proc(node: ast.Enum_Type, builder: ^SemanticTokenBuilder) {
	if node.fields == nil {
		return
	}

	for field in node.fields {
		if ident, ok := field.derived.(^ast.Ident); ok {
			write_semantic_node(builder, field, .EnumMember)
		} else if f, ok := field.derived.(^ast.Field_Value); ok {
			if _, ok := f.field.derived.(^ast.Ident); ok {
				write_semantic_node(builder, f.field, .EnumMember)
			}
			visit_node(f.value, builder)
		}
	}
}

visit_struct_fields :: proc(node: ast.Struct_Type, builder: ^SemanticTokenBuilder) {
	if node.fields == nil {
		return
	}

	for field in node.fields.list {
		for name in field.names {
			if ident, ok := name.derived.(^ast.Ident); ok {
				write_semantic_node(builder, ident, .Property)
			}
		}

		visit_node(field.type, builder)
	}
}

visit_poly_params :: proc(params: ^ast.Field_List, builder: ^SemanticTokenBuilder) {
	if params == nil {
		return
	}

	for param in params.list {
		for name in param.names {
			if poly, ok := name.derived.(^ast.Poly_Type); ok {
				write_semantic_node(builder, poly.type, .TypeParameter)
			}
		}
		visit_node(param.type, builder)
	}
}

visit_bit_field_fields :: proc(node: ast.Bit_Field_Type, builder: ^SemanticTokenBuilder) {
	if node.fields == nil {
		return
	}

	for field in node.fields {
		if ident, ok := field.name.derived.(^ast.Ident); ok {
			write_semantic_node(builder, ident, .Property)
		}

		visit_node(field.type, builder)
		visit_node(field.bit_size, builder)
	}
}

visit_import_decl :: proc(decl: ^ast.Import_Decl, builder: ^SemanticTokenBuilder) {
	visit_nodes(decl.attributes[:], builder)

	// Import paths stay string-colored throughout. Only an explicit alias
	// outside the quotes is a namespace token: import foo "core:fmt".
	if decl.name.text != "" {
		write_semantic_token(builder, decl.name, .Namespace)
	}
}

// Follow value storage until a shallow reference boundary. This only supplies
// presentation metadata; it must not change type checking or binding lookup.
semantic_field_is_readonly :: proc(
	ast_context: ^AstContext,
	expr: ^ast.Expr,
	require_immutable := false,
	depth := 0,
) -> bool {
	if expr == nil || depth >= 64 { return false }
	// Resolving a field's global type can disable local lookup. Each path
	// component is a new value lookup in the original lexical scope.
	previous_use_locals := ast_context.use_locals
	ast_context.use_locals = true
	defer ast_context.use_locals = previous_use_locals
	#partial switch n in expr.derived {
	case ^ast.Paren_Expr:
		return semantic_field_is_readonly(ast_context, n.expr, require_immutable, depth+1)
	case ^ast.Ident:
		symbol: Symbol
		if !internal_resolve_type_expression(ast_context, expr, &symbol) { return false }
		if .Immutable in symbol.flags { return true }
		return !require_immutable && (.ReadOnlyBinding in symbol.flags || .Mutable not_in symbol.flags)
	case ^ast.Selector_Expr:
		base: Symbol
		if !internal_resolve_type_expression(ast_context, n.expr, &base) || base.pointers > 0 { return false }
		if record, ok := base.value.(SymbolStructValue); ok {
			for name, i in record.names {
				if n.field == nil || name != n.field.name { continue }
				// Promoted fields can cross an implicit `using ^Record` pointer.
				for index := i; index < len(record.from_usings) && record.from_usings[index] != -1; {
					index = record.from_usings[index]
					if index < 0 || index >= len(record.types) { return false }
					parent: Symbol
					if !internal_resolve_type_expression(ast_context, record.types[index], &parent) || parent.pointers > 0 { return false }
				}
				break
			}
		}
		return semantic_field_is_readonly(ast_context, n.expr, require_immutable, depth+1)
	case ^ast.Index_Expr:
		base: Symbol
		if !internal_resolve_type_expression(ast_context, n.expr, &base) || base.pointers > 0 { return false }
		#partial switch _ in base.value {
		case SymbolFixedArrayValue, SymbolMatrixValue:
			return semantic_field_is_readonly(ast_context, n.expr, require_immutable, depth+1)
		case SymbolMapValue:
			// Thor :> protects map entries; an ordinary readonly map header does not.
			return semantic_field_is_readonly(ast_context, n.expr, true, depth+1)
		}
	case ^ast.Matrix_Index_Expr:
		base: Symbol
		if !internal_resolve_type_expression(ast_context, n.expr, &base) || base.pointers > 0 { return false }
		return semantic_field_is_readonly(ast_context, n.expr, require_immutable, depth+1)
	// Explicit dereferences, slices, dynamic arrays, multipointers and call
	// results do not inherit readonly storage from a containing binding.
	}
	return false
}

visit_ident :: proc(
	ident: ^ast.Ident,
	symbol_ptr: rawptr,
	modifiers: SemanticTokenModifiers,
	builder: ^SemanticTokenBuilder,
) {
	if ident.end.offset<=ident.pos.offset{return}
	symbol_and_node, in_symbols := builder.symbols[cast(uintptr)symbol_ptr]
	if !in_symbols {
		return
	}
	symbol := symbol_and_node.symbol
	if _, ok := symbol.value.(SymbolLabelValue); ok {
		write_semantic_node(builder, ident, .Label, {.ReadOnly})
		return
	}

	modifiers := modifiers

	if .Builtin in symbol.flags {
		modifiers += {.DefaultLibrary}
	}

	if .Mutable not_in symbol.flags || .Immutable in symbol.flags || .ReadOnlyBinding in symbol.flags {
		modifiers += {.ReadOnly}
	}

	if .PolyType in symbol.flags {
		write_semantic_node(builder, ident, .TypeParameter, modifiers)
		return
	}

	callable := false
	#partial switch _ in symbol.value {
	case SymbolProcedureValue, SymbolProcedureGroupValue, SymbolAggregateValue:
		callable = true
	}
	called := symbol_ptr == cast(rawptr)builder.call_target
	value_modifiers := modifiers
	if .CompileTime in symbol.flags && .Immutable not_in symbol.flags &&
	   .Mutable not_in symbol.flags && .Parameter not_in symbol.flags && !callable {
		value_modifiers += {.CompileTime}
	}
	if callable && !called &&
	   (symbol.type == .Variable || symbol.type == .Constant || symbol.type == .Function) &&
	   (.Parameter in symbol.flags || .Mutable in symbol.flags || .Variable in symbol.flags) {
		// An inferred procedure default is still a parameter value. Keep its
		// references blue when passed/stored, and function-colored when called.
		kind := SemanticTokenTypes.Parameter if .Parameter in symbol.flags else .Variable
		write_semantic_node(builder, ident, kind, modifiers)
		return
	}
	if .Variable in symbol.flags && !(callable && called) {
		write_semantic_node(builder, ident, .Variable, value_modifiers)
		return
	}

	/* variable idents */
	#partial switch symbol.type {
	case .Variable, .Constant, .Function:
		#partial switch _ in symbol.value {
		case SymbolProcedureValue, SymbolProcedureGroupValue, SymbolAggregateValue:
			write_semantic_node(builder, ident, .Function, modifiers)
		case:
			if .Parameter in symbol.flags {
				write_semantic_node(builder, ident, .Parameter, modifiers)
			} else {
				write_semantic_node(builder, ident, .Variable, value_modifiers)
			}
		}
	case .EnumMember:
		write_semantic_node(builder, ident, .EnumMember, modifiers)
	case .Field:
		write_semantic_node(builder, ident, .Property, modifiers)
	case:
		/* type idents */
		switch v in symbol.value {
		case SymbolPackageValue:
			write_semantic_node(builder, ident, .Namespace, modifiers)
		case SymbolStructValue, SymbolBitFieldValue:
			write_semantic_node(builder, ident, .Struct, modifiers)
		case SymbolEnumValue, SymbolUnionValue:
			write_semantic_node(builder, ident, .Enum, modifiers)
		case SymbolPolyTypeValue:
			write_semantic_node(builder, ident, .TypeParameter, modifiers)
		case SymbolProcedureValue,
		     SymbolMatrixValue,
		     SymbolBitSetValue,
		     SymbolDynamicArrayValue,
		     SymbolFixedArrayValue,
		     SymbolSliceValue,
		     SymbolMapValue,
		     SymbolMultiPointerValue,
		     SymbolBasicValue:
			write_semantic_node(builder, ident, .Type, modifiers)
		case SymbolUntypedValue:
		// handled by static syntax highlighting
		case SymbolGenericValue, SymbolProcedureGroupValue, SymbolAggregateValue, SymbolLabelValue:
		// unused
		case:
		}
	}
}

// Rename edits are always validated against original source, never adapted text.
package server

import "core:fmt"
import "core:odin/ast"
import "core:os"
import "core:path/filepath"
import "core:slice"
import "core:strings"
import "src:common"
import parser "thor:parser"
import lexer "thor:tokenizer"

Thor_Rename_Token :: struct {
	name:       string,
	range:      common.Range,
	start, end: int,
}
Thor_Rename_Target :: struct {
	symbol:      Symbol,
	flag:        ResolveReferenceFlag,
	token:       Thor_Rename_Token,
	declaration: common.Range,
	owner:       ^Document,
	namespace:   bool,
	import_:     Package,
	parameter:   bool,
}

thor_rename_refresh_index :: proc() {
	// Cross-file locations must use the same unsaved snapshots as edit ranges.
	for _, &document in document_storage.documents {
		if document.client_owned && document.ast.syntax_error_count == 0 {
			_ = index_file(document.uri, string(document.text[:document.used_text]))
		}
	}
}

thor_rename_range_text :: proc(document: ^Document, range: common.Range) -> string {
	span, ok := common.get_absolute_range(range, document.text[:document.used_text])
	if !ok || span.start < 0 || span.end < span.start || int(span.end) > document.used_text {return ""}
	return string(document.text[int(span.start):int(span.end)])
}

thor_rename_token :: proc(document: ^Document, position: common.Position) -> (Thor_Rename_Token, bool) {
	absolute, ok := common.get_absolute_position(position, document.text[:document.used_text])
	if !ok {return {}, false}
	State :: struct {
		document: ^Document,
		offset:   int,
		token:    Thor_Rename_Token,
		found:    bool,
	}
	state := State {
		document = document,
		offset   = int(absolute),
	}
	visit :: proc(v: ^ast.Visitor, node: ^ast.Node) -> ^ast.Visitor {
		if node == nil {return v}
		state := cast(^State)v.data
		ident, ok := node.derived.(^ast.Ident)
		if !ok {return v}
		if ident.pos.offset <= state.offset &&
		   state.offset < ident.end.offset &&
		   ident.pos.offset >= 0 &&
		   ident.end.offset <= state.document.used_text &&
		   state.document.ast.src[ident.pos.offset:ident.end.offset] == ident.name {
			state.token = {
				ident.name,
				common.get_token_range(ident, state.document.ast.src),
				ident.pos.offset,
				ident.end.offset,
			}
			state.found = true
		}
		return v
	}
	visitor := ast.Visitor {
		visit = visit,
		data  = &state,
	}
	for decl in document.ast.decls {ast.walk(&visitor, decl)}
	for imp in document.imports {
		token := imp.import_decl.name
		if token.text != "" &&
		   token.pos.offset <= int(absolute) &&
		   int(absolute) < token.pos.offset + len(token.text) {
			return {token.text, imp.range, token.pos.offset, token.pos.offset + len(token.text)}, true
		}
	}
	return state.token, state.found
}

thor_rename_open_document :: proc(fullpath: string) -> ^Document {
	for _, &document in document_storage.documents {
		if document.client_owned && document.fullpath == fullpath {return &document}
	}
	return nil
}

thor_rename_load :: proc(fullpath: string) -> ^Document {
	if document := thor_rename_open_document(fullpath); document != nil {return document}
	data, err := os.read_entire_file(fullpath, context.temp_allocator)
	if err != nil {return nil}
	document := new(Document, context.temp_allocator)
	document.uri = common.create_uri(fullpath, context.temp_allocator)
	document.text = data
	document.used_text = len(data)
	document_setup(document)
	pkg := new(ast.Package, context.temp_allocator)
	pkg.kind = .Normal
	pkg.fullpath = fullpath
	pkg.name = filepath.base(filepath.dir(fullpath))
	document.ast = {
		fullpath = fullpath,
		src      = string(data),
		pkg      = pkg,
	}
	p := parser.Parser {
		flags = {.Optional_Semicolons},
		err   = log_error_handler,
		warn  = log_warning_handler,
	}
	_ = parse_file(&p, &document.ast)
	parse_imports(document, &common.config, p.selections[:])
	return document
}

thor_rename_in_directory :: proc(file, directory: string) -> bool {
	separator := "/"
	when ODIN_OS == .Windows {separator = "\\"}
	return(
		file == directory ||
		strings.has_prefix(
			file,
			strings.concatenate({strings.trim_suffix(directory, separator), separator}, context.temp_allocator),
		) \
	)
}

thor_rename_editable :: proc(document, current: ^Document) -> bool {
	if document == current || document.client_owned {return true}
	for workspace in common.config.workspace_folders {
		if uri, ok := common.parse_uri(workspace.uri, context.temp_allocator);
		   ok && thor_rename_in_directory(document.fullpath, uri.path) {return true}
	}
	return false
}

thor_rename_target :: proc(document: ^Document, position: common.Position) -> (Thor_Rename_Target, string) {
	if document.ast.syntax_error_count > 0 {return {}, "Fix syntax errors in this file before renaming a symbol."}
	token, ok := thor_rename_token(document, position)
	if !ok {return {}, "Select an identifier to rename."}
	ctx := make_ast_context(
		document.ast,
		document.imports,
		document.package_name,
		document.uri.uri,
		document.fullpath,
		context.temp_allocator,
	)
	pos, found := get_document_position_context(document, position, .Hover)
	if !found {return {}, "Cannot resolve this identifier."}
	ctx.position_hint = pos.hint
	get_globals(document.ast, &ctx)
	get_locals(&ctx, &pos)
	target := Thor_Rename_Target {
		token = token,
	}
	resolved := false
	for imp in document.imports {
		if imp.import_decl.name.text == token.name && imp.range == token.range {
			target.namespace = true
			target.import_ = imp
			resolved = true
			break
		}
	}
	if !resolved {
		// A selected import's declaration is an adapted selector whose fake
		// namespace overlaps the real selected name. Resolve its origin directly.
		for decl in document.ast.decls {
			value, ok := decl.derived.(^ast.Value_Decl)
			if !ok || !thor_is_import_alias(value) {continue}
			if decl.pos.offset == token.start && decl.end.offset == token.end {
				target.symbol, resolved = resolve_location_selector(&ctx, value.values[0])
				target.flag = .Field
				break
			}
		}
		if !resolved {target.symbol, target.flag, resolved = prepare_references(document, &ctx, &pos)}
		if !resolved {return {}, "Cannot resolve this identifier to a declaration."}
		if target.symbol.type == .Package {
			for imp in document.imports {
				if imp.base == token.name && imp.range == target.symbol.range && imp.name == target.symbol.pkg {
					target.namespace = true
					target.import_ = imp
					break
				}
			}
		}
	}
	if target.namespace {
		target.symbol = Symbol {
			type  = .Package,
			pkg   = target.import_.name,
			uri   = document.uri.uri,
			range = target.import_.range,
			flags = {.Local},
		}
		target.flag = .Identifier
		target.owner = document
		return target, ""
	}
	if target.symbol.uri == "" {target.symbol.uri = document.uri.uri}
	uri, valid := common.parse_uri(target.symbol.uri, context.temp_allocator)
	if !valid {return {}, "This symbol has no editable source declaration."}
	owner := document if target.symbol.uri == document.uri.uri else thor_rename_load(uri.path)
	if owner == nil ||
	   !thor_rename_editable(owner, document) {return {}, "The declaration is outside the editable workspace."}
	target.owner = owner
	target.declaration = target.symbol.range
	declared := thor_rename_range_text(owner, target.declaration)
	if strings.has_prefix(declared, "$") && declared[1:] == token.name {target.declaration.start.character += 1
		declared = token.name}
	if declared != token.name {return {}, "Cannot identify an original source declaration for this symbol."}
	State :: struct {
		range:     common.Range,
		src:       string,
		parameter: bool,
	}
	state := State {
		range = target.symbol.range,
		src   = owner.ast.src,
	}
	visit :: proc(v: ^ast.Visitor, node: ^ast.Node) -> ^ast.Visitor {
		if node == nil {return v}; state := cast(^State)v.data
		if proc_type, ok := node.derived.(^ast.Proc_Type); ok && proc_type.params != nil {
			for field in proc_type.params.list {for name in field.names {
					if common.get_token_range(name, state.src) == state.range {state.parameter = true}
				}}
		}
		return v
	}
	visitor := ast.Visitor {
		visit = visit,
		data  = &state,
	}
	for decl in owner.ast.decls {ast.walk(&visitor, decl)}
	target.parameter = state.parameter
	return target, ""
}

thor_prepare_rename :: proc(document: ^Document, position: common.Position) -> (common.Range, bool) {
	context.allocator = context.temp_allocator
	thor_rename_refresh_index()
	target, error := thor_rename_target(document, position)
	return target.token.range, error == ""
}

thor_rename_valid_name :: proc(name: string) -> bool {
	if name == "" || name == "_" {return false}
	scanner: lexer.Tokenizer
	lexer.init(&scanner, name, "", nil)
	scanner.thor = true
	first := lexer.scan(&scanner)
	second := lexer.scan(&scanner)
	return scanner.error_count == 0 && first.kind == .Ident && first.text == name && second.kind == .EOF
}

thor_rename_same_symbol :: proc(a, b: Symbol) -> bool {return a.uri == b.uri && a.range == b.range}

thor_rename_source_tokens :: proc(
	document: ^Document,
	name: string,
) -> (
	map[common.Range]bool,
	map[common.Range]bool,
) {
	State :: struct {
		document:             ^Document,
		name:                 string,
		tokens, declarations: map[common.Range]bool,
	}
	state := State {
		document,
		name,
		make(map[common.Range]bool, context.temp_allocator),
		make(map[common.Range]bool, context.temp_allocator),
	}
	visit :: proc(v: ^ast.Visitor, node: ^ast.Node) -> ^ast.Visitor {
		if node == nil {return v}; s := cast(^State)v.data
		if ident, ok := node.derived.(^ast.Ident); ok && ident.name == s.name {
			range := common.get_token_range(ident, s.document.ast.src)
			if thor_rename_range_text(s.document, range) == s.name {s.tokens[range] = true}
		}
		names: []^ast.Expr
		#partial switch n in node.derived {
		case ^ast.Value_Decl:
			if !thor_is_import_alias(n) {names = n.names}
		case ^ast.Field:
			names = n.names
		case ^ast.Range_Stmt:
			names = n.vals
		case ^ast.Enum_Type:
			names = n.fields
		}
		for name in names {
			candidate := name
			if poly, ok := name.derived.(^ast.Poly_Type); ok {candidate = poly.type}
			if field, ok := name.derived.(^ast.Field_Value); ok {candidate = field.field}
			ident, ok := candidate.derived.(^ast.Ident)
			if !ok || ident.name != s.name {continue}
			range := common.get_token_range(ident, s.document.ast.src)
			if thor_rename_range_text(s.document, range) == s.name {s.declarations[range] = true}
		}
		return v
	}
	visitor := ast.Visitor {
		visit = visit,
		data  = &state,
	}
	for decl in document.ast.decls {ast.walk(&visitor, decl)}
	return state.tokens, state.declarations
}

thor_rename_scope :: proc(document: ^Document, range: common.Range) -> [2]int {
	span, ok := common.get_absolute_range(range, document.text[:document.used_text])
	if !ok {return {-1, -1}}
	State :: struct {
		start, end: int,
		scope:      [2]int,
	}
	state := State{int(span.start), int(span.end), {0, document.used_text}}
	visit :: proc(v: ^ast.Visitor, node: ^ast.Node) -> ^ast.Visitor {
		if node == nil {return v}; state := cast(^State)v.data
		#partial switch _ in node.derived {
		case ^ast.Block_Stmt,
		     ^ast.Range_Stmt,
		     ^ast.For_Stmt,
		     ^ast.Case_Clause,
		     ^ast.Proc_Lit,
		     ^ast.Struct_Type,
		     ^ast.Enum_Type,
		     ^ast.Union_Type:
			if node.pos.offset <= state.start &&
			   state.end <= node.end.offset &&
			   node.end.offset - node.pos.offset < state.scope[1] - state.scope[0] {
				state.scope = {node.pos.offset, node.end.offset}
			}
		}
		return v
	}
	visitor := ast.Visitor {
		visit = visit,
		data  = &state,
	}
	for decl in document.ast.decls {ast.walk(&visitor, decl)}
	return state.scope
}

thor_rename_collision :: proc(
	document: ^Document,
	range: common.Range,
	target: Thor_Rename_Target,
	new_name: string,
) -> bool {
	pos, ok := get_document_position_context(document, range.start, .Hover)
	if !ok {return true}
	// Qualified and implicit member names do not capture local identifiers.
	if pos.implicit ||
	   pos.selector_expr != nil && pos.field != nil && position_in_node(pos.field, pos.position) {return false}
	ctx := make_ast_context(
		document.ast,
		document.imports,
		document.package_name,
		document.uri.uri,
		document.fullpath,
		context.temp_allocator,
	)
	get_globals(document.ast, &ctx)
	get_locals(&ctx, &pos)
	ident := thor_ident(new_name, thor_rename_pos(document, range))
	existing, found := resolve_location_identifier(&ctx, ident^)
	return found && !thor_rename_same_symbol(existing, target.symbol)
}

thor_rename_pos :: proc(document: ^Document, range: common.Range) -> lexer.Pos {
	offset, _ := common.get_absolute_position(range.start, document.text[:document.used_text])
	return {
		file = document.fullpath,
		offset = int(offset),
		line = range.start.line + 1,
		column = range.start.character + 1,
	}
}

thor_rename_binding_conflict :: proc(document: ^Document, target: Thor_Rename_Target, new_name: string) -> bool {
	owner_scope := thor_rename_scope(target.owner, target.declaration)
	global := owner_scope == [2]int{0, target.owner.used_text}
	State :: struct {
		document: ^Document,
		target:   Thor_Rename_Target,
		name:     string,
		scope:    [2]int,
		global:   bool,
		conflict: bool,
	}
	state := State{document, target, new_name, owner_scope, global, false}
	visit :: proc(v: ^ast.Visitor, node: ^ast.Node) -> ^ast.Visitor {
		if node == nil {return v}; s := cast(^State)v.data
		names: []^ast.Expr
		#partial switch n in node.derived {
		case ^ast.Value_Decl:
			names = n.names
		case ^ast.Field:
			names = n.names
		case ^ast.Range_Stmt:
			names = n.vals
		case ^ast.Enum_Type:
			names = n.fields
		}
		for name in names {
			candidate := name
			if poly, ok := name.derived.(^ast.Poly_Type); ok {candidate = poly.type}
			if field, ok := name.derived.(^ast.Field_Value); ok {candidate = field.field}
			ident, ok := candidate.derived.(^ast.Ident)
			if !ok || ident.name != s.name {continue}
			range := common.get_token_range(ident, s.document.ast.src)
			if thor_rename_range_text(s.document, range) != s.name {continue}
			scope := thor_rename_scope(s.document, range)
			if s.global {
				if s.document.package_name == s.target.owner.package_name &&
				   (scope == [2]int{0, s.document.used_text}) {s.conflict = true}
			} else if s.document == s.target.owner && scope == s.scope {s.conflict = true}
		}
		return v
	}
	visitor := ast.Visitor {
		visit = visit,
		data  = &state,
	}
	for decl in document.ast.decls {ast.walk(&visitor, decl)}
	return state.conflict
}

thor_rename_files :: proc(target: Thor_Rename_Target, current: ^Document) -> []string {
	files := make(map[string]bool, context.temp_allocator)
	files[current.fullpath] = true
	files[target.owner.fullpath] = true
	if target.namespace || .Local in target.symbol.flags && !target.parameter || .PrivateFile in target.symbol.flags {
		result := make([]string, 1, context.temp_allocator)
		result[0] = current.fullpath
		return result
	}
	for _, &document in document_storage.documents {if document.client_owned {files[document.fullpath] = true}}
	for workspace in common.config.workspace_folders {
		uri, ok := common.parse_uri(workspace.uri, context.temp_allocator)
		if !ok {continue}
		walker := os.walker_create(uri.path)
		defer os.walker_destroy(&walker)
		for info in os.walker_walk(&walker) {
			if info.type == .Directory {
				manifest, _ := filepath.join({info.fullpath, "thor-manifest.json"}, context.temp_allocator)
				if slice.contains(
					   []string{".git", "node_modules", ".thor-build", ".thor-cache", ".vscode"},
					   info.name,
				   ) ||
				   os.is_file(manifest) {os.walker_skip_dir(&walker)}
				continue
			}
			if thor_source_file(info.fullpath) {files[strings.clone(info.fullpath, context.temp_allocator)] = true}
		}
	}
	result := make([dynamic]string, context.temp_allocator)
	for file in files {append(&result, file)}
	slice.sort(result[:])
	return result[:]
}

thor_rename :: proc(document: ^Document, new_name: string, position: common.Position) -> (WorkspaceEdit, string) {
	context.allocator = context.temp_allocator
	if !thor_rename_valid_name(new_name) {return {}, "The new name must be a non-keyword identifier other than _."}
	thor_rename_refresh_index()
	target, error := thor_rename_target(document, position)
	if error != "" {return {}, error}
	workspace := WorkspaceEdit {
		changes = make(map[string][]TextEdit, context.temp_allocator),
	}
	if new_name == target.token.name {return workspace, ""}
	for file in thor_rename_files(target, document) {
		candidate := document if file == document.fullpath else thor_rename_load(file)
		if candidate == nil {continue}
		if !strings.contains(candidate.ast.src, target.token.name) &&
		   !strings.contains(candidate.ast.src, new_name) {continue}
		related := candidate.package_name == target.owner.package_name
		for imp in candidate.imports {if imp.name == target.owner.package_name {related = true}}
		if candidate.ast.syntax_error_count > 0 {
			if related {return {}, fmt.tprintf("Fix syntax errors in %s before renaming this symbol.", file)}
			continue
		}
		if thor_rename_binding_conflict(
			candidate,
			target,
			new_name,
		) {return {}, fmt.tprintf("The name '%s' is already declared in this scope.", new_name)}
		symbols := resolve_entire_file_for_references(
			candidate,
			context.temp_allocator,
			target.flag,
			target.token.name,
		)
		edits := make([dynamic]TextEdit, context.temp_allocator)
		seen := make(map[common.Range]bool, context.temp_allocator)
		tokens, covered := thor_rename_source_tokens(candidate, target.token.name)
		for _, entry in symbols {
			if entry.node == nil {continue}
			range := common.get_token_range(entry.node^, candidate.ast.src)
			if _, implicit := entry.node.derived.(^ast.Implicit_Selector_Expr); implicit {range.start.character += 1}
			if thor_rename_range_text(candidate, range) != target.token.name {continue}
			covered[range] = true
			if entry.symbol.uri == target.symbol.uri {
				origin := thor_rename_range_text(target.owner, entry.symbol.range)
				if origin != target.token.name &&
				   !(strings.has_prefix(origin, "$") && origin[1:] == target.token.name) {
					return {}, fmt.tprintf("A source location for '%s' could not be verified. Rename was cancelled.", target.token.name)
				}
			}
			matches := thor_rename_same_symbol(entry.symbol, target.symbol)
			if target.namespace {matches = entry.symbol.type == .Package && entry.symbol.pkg == target.symbol.pkg && entry.symbol.range == target.symbol.range}
			if !matches || seen[range] {continue}
			if thor_rename_collision(
				candidate,
				range,
				target,
				new_name,
			) {return {}, fmt.tprintf("Renaming to '%s' would conflict with another binding in %s.", new_name, file)}
			seen[range] = true
			append(&edits, TextEdit{range = range, newText = new_name})
		}
		if candidate == target.owner && !target.namespace && !seen[target.declaration] {
			if thor_rename_collision(
				candidate,
				target.declaration,
				target,
				new_name,
			) {return {}, fmt.tprintf("The name '%s' is already bound here.", new_name)}
			append(&edits, TextEdit{range = target.declaration, newText = new_name})
		}
		if related || len(edits) > 0 {
			for range in tokens {
				if !covered[range] {return {}, fmt.tprintf("Cannot safely resolve every use of '%s' in %s. Rename was cancelled.", target.token.name, file)}
			}
		}
		if target.namespace && candidate == document {
			imp := target.import_.import_decl
			if imp.name.text == "" {
				start := common.token_pos_to_position(imp.relpath.pos, document.ast.src)
				append(
					&edits,
					TextEdit {
						range = {start, start},
						newText = strings.concatenate({new_name, " "}, context.temp_allocator),
					},
				)
			} else if !seen[target.import_.range] {append(&edits, TextEdit{range = target.import_.range, newText = new_name})}
		}
		if len(edits) > 0 {workspace.changes[candidate.uri.uri] = edits[:]}
	}
	if len(workspace.changes) == 0 {return {}, "No original-source references could be resolved for this symbol."}
	return workspace, ""
}

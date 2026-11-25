package server

import "base:intrinsics"
import "base:runtime"

import "core:os"
import "core:fmt"
import "core:log"
import "core:mem/virtual"
import "core:odin/ast"
import parser "thor:parser"
import lexer "thor:tokenizer"
import "core:odin/tokenizer"
import "core:path/filepath"
import path "core:path/slashpath"
import "core:strings"

import "src:common"
import "src:spall"

ParserError :: struct {
	message: string,
	line:    int,
	column:  int,
	file:    string,
	offset:  int,
}

Package :: struct {
	wildcard:      bool,
	name:          string, //the entire absolute path to the directory
	base:          string,
	base_original: string,
	original:      string,
	range:         common.Range,
	declaration_range: common.Range, // Full source import, including Thor's selected names.
	import_decl:   ^ast.Import_Decl,
}

Document :: struct {
	uri:                common.Uri,
	fullpath:           string,
	text:               []u8,
	used_text:          int, //allow for the text to be reallocated with more data than needed
	client_owned:       bool,
	diagnosed_errors:   bool,
	ast:                ast.File,
	imports:            []Package,
	package_name:       string,
	allocator:          ^virtual.Arena, //because parser does not support freeing I use arena allocators for each document
	symbol_cache_arena: ^virtual.Arena, // Separate so invalidation can reclaim caches without discarding the AST
	operating_on:       int, //atomic
	version:            Maybe(int),
	symbols:            Maybe(SymbolAndNodeMap), // Cache resolved symbols for open documents, cleared on change
}


DocumentStorage :: struct {
	documents:       map[string]Document,
	free_allocators: [dynamic]^virtual.Arena,
}

document_storage: DocumentStorage

@(private = "file")
document_get_new_symbol_cache_arena :: proc() -> ^virtual.Arena {
	arena := new(virtual.Arena)
	_ = virtual.arena_init_growing(arena)
	return arena
}

@(private = "file")
invalidate_document_symbol_cache :: proc(document: ^Document) {
	if document.symbol_cache_arena != nil {
		virtual.arena_free_all(document.symbol_cache_arena)
	}
	document.symbols = nil
}

invalidate_document_symbol_caches :: proc() {
	for _, &document in document_storage.documents {
		invalidate_document_symbol_cache(&document)
	}
}

@(private = "file")
destroy_document_symbol_cache :: proc(document: ^Document) {
	if document.symbol_cache_arena != nil {
		virtual.arena_destroy(document.symbol_cache_arena)
		free(document.symbol_cache_arena)
		document.symbol_cache_arena = nil
	}
	document.symbols = nil
}

document_storage_shutdown :: proc() {
	thor_delete_slice_info()
	for k, &v in document_storage.documents {
		if v.allocator != nil {
			virtual.arena_destroy(v.allocator)
			free(v.allocator)
		}
		destroy_document_symbol_cache(&v)
		delete(k)
	}

	for alloc in document_storage.free_allocators {
		virtual.arena_destroy(alloc)
		free(alloc)
	}

	delete(document_storage.free_allocators)
	delete(document_storage.documents)
}

document_get_new_allocator :: proc() -> ^virtual.Arena {
	if len(document_storage.free_allocators) > 0 {
		return pop(&document_storage.free_allocators)
	} else {
		allocator := new(virtual.Arena)
		_ = virtual.arena_init_growing(allocator)
		return allocator
	}
}
document_allocator :: proc(document: Document) -> (runtime.Allocator, bool) #optional_ok {
	if document.symbol_cache_arena == nil do return {}, false
	return virtual.arena_allocator(document.symbol_cache_arena), true
}
document_free_allocator :: proc(allocator: ^virtual.Arena) {
	virtual.arena_free_all(allocator)
	append(&document_storage.free_allocators, allocator)
}

document_get :: proc(uri_string: string) -> ^Document {

	uri, parsed_ok := common.parse_uri(uri_string, context.temp_allocator)
	if !parsed_ok {
		return nil
	}

	document := &document_storage.documents[uri.path]

	if document == nil {
		log.errorf("Failed to get document %v", uri.path)
		return nil
	}

	intrinsics.atomic_add(&document.operating_on, 1)

	return document
}

document_release :: proc(document: ^Document) {
	if document != nil {
		intrinsics.atomic_sub(&document.operating_on, 1)
	}
}

/*
	Client opens a document with transferred text
*/

document_open :: proc(uri_string: string, text: string, config: ^common.Config, writer: ^Writer) -> common.Error {

	spall.trace(#procedure, uri_string)

	uri, parsed_ok := common.parse_uri(uri_string, context.allocator)
	if !parsed_ok {
		log.error("Failed to parse uri")
		return .ParseError
	}

	if document := &document_storage.documents[uri.path]; document != nil {
		if document.client_owned {
			log.errorf("Client called open on an already open document: %v ", document.uri.path)
			return .InvalidRequest
		}

		document.uri = uri
		document.client_owned = true
		document.text = transmute([]u8)text
		document.used_text = len(document.text)
		document.allocator = document_get_new_allocator()
		document.symbol_cache_arena = document_get_new_symbol_cache_arena()

		document_setup(document)

		if err := document_refresh(document, config, writer); err != .None {
			return err
		}
	} else {
		document := Document {
			uri                = uri,
			text               = transmute([]u8)text,
			client_owned       = true,
			used_text          = len(text),
			allocator          = document_get_new_allocator(),
			symbol_cache_arena = document_get_new_symbol_cache_arena(),
		}

		document_setup(&document)

		if err := document_refresh(&document, config, writer); err != .None {
			return err
		}

		document_storage.documents[strings.clone(uri.path)] = document
	}

	return .None
}

document_setup :: proc(document: ^Document) {
	//Right now not all clients return the case correct windows path, and that causes issues with indexing, so we ensure that it's case correct.
	when ODIN_OS == .Windows {
		package_name := path.dir(document.uri.path, context.temp_allocator)
		forward, _ := filepath.replace_separators(
			common.get_case_sensitive_path(package_name),
			'/',
			context.temp_allocator,
		)
		if forward == "" {
			document.package_name = package_name
		} else {
			document.package_name = strings.clone(forward, context.allocator)
		}
	} else {
		document.package_name = path.dir(document.uri.path, context.allocator)
	}

	when ODIN_OS == .Windows {
		correct := common.get_case_sensitive_path(document.uri.path)
		fullpath: string
		if correct == "" {
			//This is basically here to handle the tests where the physical file doesn't actual exist.
			document.fullpath, _ = filepath.replace_separators(document.uri.path, '/', context.allocator)
		} else {
			document.fullpath, _ = filepath.replace_separators(correct, '/', context.allocator)
		}
	} else {
		document.fullpath = document.uri.path
	}
}

/*
	Function that applies changes to the given document through incremental syncronization
*/
document_apply_changes :: proc(
	uri_string: string,
	changes: [dynamic]TextDocumentContentChangeEvent,
	version: Maybe(int),
	config: ^common.Config,
	writer: ^Writer,
) -> common.Error {

	spall.trace(#procedure, uri_string)

	uri, parsed_ok := common.parse_uri(uri_string, context.temp_allocator)

	if !parsed_ok {
		return .ParseError
	}

	document := &document_storage.documents[uri.path]

	document.version = version

	if !document.client_owned {
		log.errorf("Client called change on an document not opened: %v ", document.uri.path)
		return .InvalidRequest
	}

	for change in changes {
		//for some reason sublime doesn't seem to care even if i tell it to do incremental sync
		if range, ok := change.range.(common.Range); ok {
			absolute_range, ok := common.get_absolute_range(range, document.text[:document.used_text])

			if !ok {
				return .ParseError
			}

			//lower bound is before the change
			lower := document.text[:absolute_range.start]

			//new change between lower and upper
			middle := change.text

			//upper bound is after the change
			upper := document.text[absolute_range.end:document.used_text]

			//total new size needed
			document.used_text = len(lower) + len(change.text) + len(upper)

			//Reduce the amount of allocation by allocating more memory than needed
			if document.used_text > len(document.text) {
				new_text := make([]u8, document.used_text * 2)

				//join the 3 splices into the text
				copy(new_text, lower)
				copy(new_text[len(lower):], middle)
				copy(new_text[len(lower) + len(middle):], upper)

				delete(document.text)

				document.text = new_text
			} else {
				//order matters here, we need to make sure we swap the data already in the text before the middle
				copy(document.text, lower)
				copy(document.text[len(lower) + len(middle):], upper)
				copy(document.text[len(lower):], middle)
			}
		} else {
			document.used_text = len(change.text)

			if document.used_text > len(document.text) {
				new_text := make([]u8, document.used_text * 2)
				copy(new_text, change.text)
				delete(document.text)
				document.text = new_text
			} else {
				copy(document.text, change.text)
			}
		}
	}

	return document_refresh(document, config, writer)
}

document_close :: proc(uri_string: string) -> common.Error {
	log.infof("document_close: %v", uri_string)

	uri, parsed_ok := common.parse_uri(uri_string, context.temp_allocator)

	if !parsed_ok {
		return .ParseError
	}

	document := &document_storage.documents[uri.path]

	if document == nil || !document.client_owned {
		log.errorf("Client called close on a document that was never opened: %v ", uri.path)
		return .InvalidRequest
	}
	thor_invalidate_slice_info()
	// Discarding an unsaved buffer restores the declarations from disk.
	if data, err := os.read_entire_file(document.fullpath, context.temp_allocator); err == nil {
		_ = index_file(document.uri, string(data))
	}

	document_free_allocator(document.allocator)
	document.allocator = nil
	destroy_document_symbol_cache(document)
	document.client_owned = false
	remove_diagnostics(.Declaration, document.uri.uri)
	remove_diagnostics(.Unused, document.uri.uri)

	common.delete_uri(document.uri)

	delete(document.text)
	delete(document.package_name)
	when ODIN_OS == .Windows {
		// Only on Windows fullpath is allocated (replace_separators in document_setup); elsewhere it's an alias of uri.path
		delete(document.fullpath)
	}

	document.used_text = 0

	return .None
}

document_refresh :: proc(document: ^Document, config: ^common.Config, writer: ^Writer) -> common.Error {

	spall.trace(#procedure, document.fullpath)

	errors, ok := parse_document(document, config)

	if !ok {
		return .ParseError
	}

	if is_ols_builtin_file(document.uri.uri) {
		return .None
	}

	uri := document.uri

	remove_diagnostics(.Syntax, uri.uri)
	remove_diagnostics(.Declaration, uri.uri)
	remove_diagnostics(.Unused, uri.uri)

	if writer != nil && config.enable_parser_errors {
		document.diagnosed_errors = true

		for error, i in errors {
			add_diagnostics(
				.Syntax,
				uri.uri,
				Diagnostic {
					range = parser_error_range(document,error),
					severity = DiagnosticSeverity.Error,
					code = "Syntax",
					message = error.message,
				},
			)
		}

	}
	if writer!=nil && config.enable_diagnostics {
		if len(errors)==0 {
			for duplicate in parser.file_redeclarations(&document.ast,context.temp_allocator) {
				add_diagnostics(.Declaration,uri.uri,Diagnostic{
					range=common.get_token_range(duplicate.name,document.ast.src),
					severity=DiagnosticSeverity.Error,code="Declaration",message=duplicate.message,
				})
			}
		}
		push_diagnostics(writer)
	}

	return .None
}

// Parsing/indexing can run on different workers. Each parse's callback must
// append to its own diagnostic buffer, never another thread's scratch array.
@(thread_local)
current_errors: [dynamic]ParserError

parser_error_range :: proc(document:^Document,error:ParserError)->common.Range {
	// Rescanning an f-string may allocate interpolation parts. Keep those in
	// request scratch, rather than adding a leak on each incomplete edit.
	context.allocator=context.temp_allocator
	text:=document.text[:document.used_text]
	start:=clamp(error.offset,0,len(text))
	end:=start
	if start<len(text) {
		scanner:lexer.Tokenizer
		lexer.init(&scanner,string(text),document.fullpath,common.parser_warning_handler)
		scanner.thor=filepath.ext(document.fullpath)==".thor"
		lexer.seek(&scanner,start)
		token:=lexer.scan(&scanner)
		if token.pos.offset==start {end=min(len(text),start+len(token.text))}
	}
	return {start=common.get_relative_token_position(start,text,0),end=common.get_relative_token_position(end,text,0)}
}

parser_error_handler :: proc(pos: tokenizer.Pos, msg: string, args: ..any) {
	error := ParserError {
		line    = pos.line,
		column  = pos.column,
		file    = pos.file,
		offset  = pos.offset,
		message = fmt.tprintf(msg, ..args),
	}
	append(&current_errors, error)
}

parse_document :: proc(document: ^Document, config: ^common.Config) -> ([]ParserError, bool) {

	spall.trace(#procedure, document.fullpath)
	// Permission facts also depend on imported/other open files, whose edits
	// do not change this document's own source hash.
	if document.client_owned { thor_invalidate_slice_info() }

	p := parser.Parser {
		err   = parser_error_handler,
		warn  = common.parser_warning_handler,
		flags = {.Optional_Semicolons},
	}

	current_errors = make([dynamic]ParserError, context.temp_allocator)

	invalidate_document_symbol_cache(document)
	virtual.arena_free_all(document.allocator)

	context.allocator = virtual.arena_allocator(document.allocator)

	dir := filepath.base(filepath.dir(document.fullpath))
	pkg := new(ast.Package)
	pkg.kind = .Normal
	pkg.fullpath = document.fullpath
	pkg.name = dir

	if dir == "runtime" || strings.contains(document.fullpath, "base/runtime") {
		pkg.kind = .Runtime
	}

	document.ast = ast.File {
		fullpath = document.fullpath,
		src      = string(document.text[:document.used_text]),
		pkg      = pkg,
	}

	parse_file(&p, &document.ast)

	parse_imports(document, config, p.selections[:])

	folder := filepath.dir(document.fullpath)
	if strings.equal_fold(folder, config.builtin_path) {
		return nil, true
	}
	// Only publish registered editor buffers. Transient documents can be used
	// with an isolated resolver/index and must not replace global declarations.
	if document.client_owned && document.ast.syntax_error_count == 0 &&
	   &document_storage.documents[document.uri.path] == document {
		_ = index_parsed_file(document.ast)
	}

	return current_errors[:], true
}

parse_imports :: proc(document: ^Document, config: ^common.Config, selections: []parser.Selection = nil) {

	spall.trace(#procedure, document.fullpath)

	imports := make([dynamic]Package)
	selection_ends := make(map[int]common.Position, context.temp_allocator)
	selection_namespaces := make(map[int]string, context.temp_allocator)
	wildcards := make(map[int]bool, context.temp_allocator)
	for selection in selections {
		if selection.wildcard {
			for imp in document.ast.imports {
				if imp.pos.offset <= selection.start && selection.start < imp.end.offset { wildcards[imp.pos.offset] = true }
			}
			continue
		}
		import_decl: ^ast.Import_Decl
		for imp in document.ast.imports {
			if imp.end.offset <= selection.start && (import_decl == nil || imp.end.offset > import_decl.end.offset) {
				import_decl = imp
			}
		}
		if import_decl != nil && len(selection.names) > 0 {
			if selection.package_name == "_" {
				selection_namespaces[import_decl.pos.offset] = thor_selection_namespace(selection, document.ast)
			}
			// The exclusive end can be a newline (or EOF). Token-position
			// conversion would then read columns from the following line.
			selection_ends[import_decl.pos.offset] = common.get_relative_token_position(
				selection.end, transmute([]u8)document.ast.src, 0)
		}
	}

	for imp, index in document.ast.imports {
		if i := strings.index(imp.fullpath, "\""); i == -1 {
			continue
		}
		// TODO: Breakdown this range like with semantic tokens
		range := get_import_range(imp, string(document.text))
		declaration_range := common.get_token_range(imp, document.ast.src)
		if end, selected := selection_ends[imp.pos.offset]; selected { declaration_range.end = end }

		//collection specified
		if i := strings.index(imp.fullpath, ":"); i != -1 && i > 1 && i < len(imp.fullpath) - 1 {
			if len(imp.fullpath) < 2 {
				continue
			}

			collection := imp.fullpath[1:i]
			p := imp.fullpath[i + 1:len(imp.fullpath) - 1]

			dir, ok := config.collections[collection]

			if !ok {
				continue
			}

			import_: Package
			import_.original = imp.fullpath
			import_.name = strings.clone(path.join(elems = {dir, p}, allocator = context.temp_allocator))
			import_.range = range
			import_.declaration_range = declaration_range
			import_.import_decl = imp
			import_.wildcard = wildcards[imp.pos.offset]

			if imp.name.text != "" {
				import_.base = imp.name.text
				import_.base_original = path.base(import_.name, false)
			} else {
				import_.base = path.base(import_.name, false)
			}

			if namespace, hidden := selection_namespaces[imp.pos.offset]; hidden { import_.base = namespace }
			append(&imports, import_)
		} else {
			//relative
			if len(imp.fullpath) < 2 {
				continue
			}

			import_: Package
			import_.original = imp.fullpath
			import_.name = path.join(
				elems = {document.package_name, imp.fullpath[1:len(imp.fullpath) - 1]},
				allocator = context.temp_allocator,
			)
			import_.name = path.clean(import_.name)
			import_.range = range
			import_.declaration_range = declaration_range
			import_.import_decl = imp
			import_.wildcard = wildcards[imp.pos.offset]

			if imp.name.text != "" {
				import_.base = imp.name.text
				import_.base_original = path.base(import_.name, false)
			} else {
				import_.base = path.base(import_.name, false)
			}

			if namespace, hidden := selection_namespaces[imp.pos.offset]; hidden { import_.base = namespace }
			append(&imports, import_)
		}
	}

	for imp in imports {
		try_build_package(imp.name)
	}

	try_build_package(document.package_name)

	document.imports = imports[:]
}

get_import_range :: proc(imp: ^ast.Import_Decl, src: string) -> common.Range {
	if imp.name.text != "" {
		start := common.token_pos_to_position(imp.name.pos, src)
		end := start
		end.character += len(imp.name.text)
		return {start = start, end = end}
	}

	start := common.token_pos_to_position(imp.relpath.pos, src)
	end := start
	text_len := len(imp.relpath.text)
	end.character += text_len
	return {start = start, end = end}
}

is_ols_builtin_file :: proc(path: string) -> bool {
	p := path
	when ODIN_OS == .Windows {
		p, _ = os.replace_path_separators(p, '/', context.temp_allocator)
	}
	return(
		strings.has_suffix(p, "/builtin/builtin.odin") ||
		strings.has_suffix(p, "/builtin/intrinsics.odin") ||
		strings.has_suffix(p, "/intrinsics/intrinsics.odin") \
	)
}

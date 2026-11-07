package server

import "base:runtime"
import "base:intrinsics"
import "core:encoding/json"
import "core:fmt"
import "core:hash"
import "core:log"
import "core:mem"
import "core:os"
import "core:path/filepath"
import path "core:path/slashpath"
import "core:slice"
import "core:strings"
import "core:sync/chan"
import "core:thread"
import "core:time"

import "src:common"
import "src:spall"

Json_Error :: struct {
	type: string,
	stage: string,
	pos:  Json_Type_Error,
	msgs: []string,
	related: []Json_Related_Error,
}

Json_Related_Error :: struct {
	pos: Json_Type_Error,
	message: string,
}

Json_Type_Error :: struct {
	file:       string,
	offset:     int,
	line:       int,
	column:     int,
	end_column: int,
	end_line:   int,
}

Json_Errors :: struct {
	error_count: int,
	errors:      []Json_Error,
	check_paths: []string, // Local provenance, supplied after reading the compiler's JSON.
	slice_access: []Thor_Slice_Info,
}

Check_Mode :: enum {
	Saved,
	Workspace,
}

Check_Request :: struct {
	check_mode: Check_Mode,
	path:       string,
	config:     ^common.Config,
	buffers:    []Check_Buffer,
	generation: int,
}

Check_Buffer :: struct {
	path: string,
	canonical: string,
	text: []u8,
}

Checker :: struct {
	allocator:              mem.Allocator,
	send:                   chan.Chan(Check_Request, .Send),
	odin_without_workspace: string,
	overlay:                Check_Overlay,
	generation:             int,
	worker:                 ^thread.Thread,
}

Check_Overlay :: struct {
	dir:   string,
	files: map[string]Check_Overlay_File, // by path
	json:  []u8,                          // as last written
}

Check_Overlay_File :: struct {
	file:    string,
	hash:    u64, // unchanged documents are not written again based of the text last written to `file`
	written: bool,
}

@(private = "file")
checker: Checker

queue_check_request :: proc(mode: Check_Mode, path: string, config: ^common.Config) {
	if is_thor_check(path) && config.thor_command == "" { return }
	if !config.enable_diagnostics {
		return
	}
	path := strings.clone(path, checker.allocator)

	buffers := make([dynamic]Check_Buffer, 0, len(document_storage.documents), checker.allocator)
	for _, &document in document_storage.documents {
		if !document.client_owned || is_ols_builtin_file(document.fullpath) {
			continue
		}
		append(
			&buffers,
			Check_Buffer {
				path = strings.clone(document.fullpath, checker.allocator),
				canonical = os.get_absolute_path(document.fullpath, checker.allocator) or_else strings.clone(document.fullpath, checker.allocator),
				text = slice.clone(document.text[:document.used_text], checker.allocator),
			},
		)
	}

	generation := intrinsics.atomic_add(&checker.generation, 1) + 1
	ok := chan.send(checker.send, Check_Request{check_mode = mode, path = path, config = config, buffers = buffers[:], generation = generation})
	if !ok {
		log.errorf("Failed to queue check request for path %q", path)
		delete_check_buffers(buffers[:])
	}
}

@(private = "file")
is_thor_check :: proc(p: string, buffers: []Check_Buffer = nil) -> bool {
	if filepath.ext(p) == ".thor" { return true }
	directory := p
	if filepath.ext(p) == ".odin" { directory = filepath.dir(p) }
	for b in buffers {
		if filepath.ext(b.path) == ".thor" && filepath.dir(b.path) == directory { return true }
	}
	files, _ := filepath.glob(fmt.tprintf("%s/*.thor", directory), context.temp_allocator)
	return len(files) > 0
}

delete_check_buffers :: proc(buffers: []Check_Buffer) {
	for b in buffers {
		delete(b.path, checker.allocator)
		delete(b.canonical, checker.allocator)
		delete(b.text, checker.allocator)
	}
	delete(buffers, checker.allocator)
}

stop_check_worker :: proc() {
	// Invalidate both the active snapshot and any queued work before joining.
	intrinsics.atomic_add(&checker.generation, 1)
	chan.close(checker.send)
	thread.destroy(checker.worker)
	if checker.overlay.dir != "" {
		_ = os.remove_all(checker.overlay.dir)
	}
}

create_and_start_check_worker :: proc(writer: ^Writer) {
	allocator := runtime.heap_allocator()
	check_chan, _ := chan.create(chan.Chan(Check_Request), 8, context.allocator)
	check_send := chan.as_send(check_chan)
	checker = Checker {
		allocator = runtime.heap_allocator(),
		send      = check_send,
	}
	check_recv := chan.as_recv(check_chan)
	checker.worker = thread.create_and_start_with_poly_data(
		Consumer{logger = context.logger, ch = check_recv, w = writer},
		run_check_consumer,
	)
}

Consumer :: struct {
	logger: log.Logger,
	ch:     chan.Chan(Check_Request, .Recv),
	w:      ^Writer,
}

run_check_consumer :: proc(c: Consumer) {
	context.logger = c.logger
	for {
		request, ok := chan.recv(c.ch)
		if !ok {
			break
		}
		paths := make([dynamic]string, allocator = context.temp_allocator)
		append(&paths, request.path)

		buffers := request.buffers
		generation := request.generation
		for later in chan.try_recv(c.ch) {
			append(&paths, later.path)
			delete_check_buffers(buffers)
			buffers = later.buffers
			generation = later.generation
		}

		if generation == intrinsics.atomic_load(&checker.generation) {
			check(request.check_mode, paths[:], buffers, request.config, generation)
		}

		if generation == intrinsics.atomic_load(&checker.generation) {
			push_diagnostics(c.w)
			if request.config.inlay_refresh_support {
				send_request(RequestMessage{jsonrpc="2.0", method="workspace/inlayHint/refresh",
					id=fmt.tprintf("THOR_SLICE_REFRESH_%d",generation)}, c.w)
			}
			if request.config.semantic_refresh_support {
				send_request(RequestMessage{jsonrpc="2.0", method="workspace/semanticTokens/refresh",
					id=fmt.tprintf("THOR_SLICE_TOKENS_%d",generation)}, c.w)
			}
		}
		for path in paths {
			delete(path, checker.allocator)
		}
		delete_check_buffers(buffers)

		free_all(context.temp_allocator)
	}
	free_all(context.temp_allocator)
}

//If the user does not specify where to call odin check, it'll just find all directory with odin, and call them seperately.
fallback_find_odin_directories :: proc(config: ^common.Config) -> []string {
	data := make([dynamic]string, context.temp_allocator)

	for workspace in config.workspace_folders {
		if uri, ok := common.parse_uri(workspace.uri, context.temp_allocator); ok {
			append_packages(uri.path, &data, config.checker_skip_packages, context.temp_allocator)
		}
	}

	return data[:]
}

check_unused_imports :: proc(document: ^Document, config: ^common.Config) {
	if !config.enable_unused_imports_reporting || !config.enable_diagnostics {
		return
	}

	spall.trace(#procedure, document.fullpath)

	path := document.uri.path

	when ODIN_OS == .Windows {
		path = common.get_case_sensitive_path(path, context.temp_allocator)
	}

	uri := common.create_uri(path, context.temp_allocator)

	remove_diagnostics(.Unused, uri.uri)
	if len(document.imports) == 0 {
		return
	}

	usage := find_import_usage(document, context.temp_allocator)

	for imp in usage.unused_packages {
		add_diagnostics(
			.Unused,
			uri.uri,
			Diagnostic {
				range = imp.declaration_range,
				severity = DiagnosticSeverity.Hint,
				code = "Unused",
				message = "unused import",
				tags = {.Unnecessary},
			},
		)
	}
	for name in usage.unused_selections {
		add_diagnostics(.Unused, uri.uri, Diagnostic {
			range = common.get_token_range(name, document.ast.src),
			severity = DiagnosticSeverity.Hint,
			code = "Unused",
			message = "unused selected import",
			tags = {.Unnecessary},
		})
	}
}

resolve_check_paths :: proc(mode: Check_Mode, paths: []string, config: ^common.Config) -> []string {
	if len(config.profile.checker_path) > 0 {
		return config.profile.checker_path[:]
	}

	if mode == .Saved || config.enable_checker_only_saved {
		results := make([dynamic]string, context.temp_allocator)
		for p in paths {
			if p == "" {
				continue
			}
			dir := path.dir(p, context.temp_allocator)
			if dir not_in config.checker_skip_packages && !slice.contains(results[:], dir) {
				append(&results, dir)
			}
		}
		return results[:]
	}

	if mode == .Workspace && config.enable_checker_workspace_diagnostics {
		return fallback_find_odin_directories(config)
	}

	return {}
}

CheckProcess :: struct {
	process:  os.Process,
	reader:   ^os.File,
	finished: bool,
	buffer:   [dynamic]u8,
	check_paths: []string,
}

// A native diagnostic can lack a filename entirely. Keep that error with the
// job that produced it, rather than choosing an arbitrary open editor buffer.
compiler_error_fallback_path :: proc(check_paths: []string, buffers: []Check_Buffer) -> string {
	for requested in check_paths {
		checked := os.get_absolute_path(requested, context.temp_allocator) or_else requested
		is_file := filepath.ext(checked) == ".thor" || filepath.ext(checked) == ".odin"
		selected := ""
		for b in buffers {
			matches := b.path == checked || b.canonical == checked if is_file else
				filepath.dir(b.path) == checked || filepath.dir(b.canonical) == checked
			if matches && (selected == "" || strings.compare(b.path, selected) < 0) { selected = b.path }
		}
		if selected != "" { return selected }
		if is_file { return checked }
		files, err := thor_source_files(checked)
		if err == nil {
			for file in files {
				if selected == "" || strings.compare(file, selected) < 0 { selected = file }
			}
		}
		if selected != "" { return selected }
	}
	return ""
}

stop_check_processes :: proc(processes:[]CheckProcess) {
	for &p in processes {
		if p.finished {continue}
		_ = os.process_terminate(p.process)
		state,err:=os.process_wait(p.process,2*time.Second)
		if err!=nil || !state.exited {
			_ = os.process_kill(p.process)
			_,_ = os.process_wait(p.process)
		}
		p.finished=true
	}
}

check :: proc(mode: Check_Mode, check_paths: []string, buffers: []Check_Buffer, config: ^common.Config, generation: int) {
	write_overlay :: proc(o: ^Check_Overlay, buffers: []Check_Buffer) -> string {
		if o.dir == "" {
			temp, err := os.temp_directory(context.temp_allocator)
			if err != nil {
				log.errorf("Failed to find the temporary directory for the overlay: %v", err)
				return ""
			}
			o.dir = fmt.aprintf("%s/ols-%d", temp, os.get_pid(), allocator = checker.allocator)
			_ = os.make_directory(o.dir)
		}

		Overlay :: struct {
			replace: map[string]string `json:"Replace"`,
		}
		overlay := Overlay {
			replace = make(map[string]string, len(buffers), context.temp_allocator),
		}
		if o.files == nil {
			o.files = make(map[string]Check_Overlay_File, 16, checker.allocator)
		}
		for b in buffers {
			entry := &o.files[b.path]
			if entry == nil {
				path := strings.clone(b.path, checker.allocator)
				o.files[path] = {file = fmt.aprintf("%s/%d.odin", o.dir, len(o.files), allocator = checker.allocator)}
				entry = &o.files[path]
			}
			h := hash.fnv64a(b.text)
			if !entry.written || entry.hash != h {
				if err := os.write_entire_file(entry.file, b.text); err != nil {
					log.errorf("Failed to write the overlay file %q: %v", entry.file, err)
					return ""
				}
				entry.hash = h
				entry.written = true
			}
			overlay.replace[b.path] = entry.file
		}

		data, err := json.marshal(overlay, {sort_maps_by_key = true}, context.temp_allocator)
		if err != nil {
			log.errorf("Failed to make the overlay: %v", err)
			return ""
		}
		path := fmt.tprintf("%s/overlay.json", o.dir)
		if !slice.equal(data, o.json) {
			if err := os.write_entire_file(path, data); err != nil {
				log.errorf("Failed to write the overlay %q: %v", path, err)
				return ""
			}
			delete(o.json, checker.allocator)
			o.json = slice.clone(data, checker.allocator)
		}
		return path
	}

	paths := resolve_check_paths(mode, check_paths, config)

	if len(paths) == 0 {
		return
	}

	command := config.odin_command
	if command == "" {
		command = "odin"
	}

	collections := make([dynamic]string, context.temp_allocator)

	for k, v in common.config.collections {
		if k == "" || k == "core" || k == "vendor" || k == "base" {
			continue
		}
		append(&collections, fmt.aprintf("-collection:%v=%v", k, v))
	}

	jobs := make([dynamic][]string, 0, len(paths), context.temp_allocator)
	overlay := ""
	use_workspace := command != checker.odin_without_workspace
	if use_workspace {
		overlay = write_overlay(&checker.overlay, buffers)

		// NOTE(bill): This is here just to keep the command line well within Windows' limit of 32767 characters
		MAX_WORKSPACE_PATHS_LEN :: 16384

		dirs := make([dynamic]string, 0, len(paths), context.temp_allocator)
		dirs_len := 0
		for p, i in paths {
			if is_thor_check(p, buffers) {
				append(&jobs, paths[i:i + 1])
				continue
			}
			if filepath.ext(p) == ".odin" || filepath.ext(p) == ".thor" {
				append(&jobs, paths[i:i + 1])
				continue
			}
			if dirs_len + len(p) > MAX_WORKSPACE_PATHS_LEN && len(dirs) > 0 {
				append(&jobs, dirs[:])
				dirs = make([dynamic]string, 0, len(paths), context.temp_allocator)
				dirs_len = 0
			}
			append(&dirs, p)
			dirs_len += len(p) + 3
		}
		if len(dirs) > 0 {
			append(&jobs, dirs[:])
		}
	} else {
		for _, i in paths {
			append(&jobs, paths[i:i + 1])
		}
	}

	max_concurrent_checks := max(1, os.get_processor_core_count())
	processes := make([dynamic]CheckProcess, 0, len(jobs), context.temp_allocator)
	defer {for p in processes {if p.reader!=nil {os.close(p.reader)}}}

	errors := make([dynamic]Json_Errors, 0, len(jobs), context.temp_allocator)
	lacks_workspace := false

	next_index := 0
	running_count := 0
	start := time.now()
	deadline := 20 * time.Second
	// Thor may compile several native evaluation helpers before final checking.
	for p in paths {
		if is_thor_check(p,buffers) { deadline = 120 * time.Second; break }
	}

	for running_count > 0 || next_index < len(jobs) {
		if generation!=intrinsics.atomic_load(&checker.generation) {
			stop_check_processes(processes[:])
			return
		}
		for running_count < max_concurrent_checks && next_index < len(jobs) {
			p, ok := start_check_process(command, jobs[next_index], collections[:], overlay, config, buffers)
			next_index += 1
			if !ok {
				continue
			}
			append(&processes, p)
			running_count += 1
		}

		if time.since(start) > deadline {
			log.error("`odin check` timed out")
			stop_check_processes(processes[:])
			append(&errors,Json_Errors{error_count=1,check_paths=paths,errors=[]Json_Error{{type="error",stage="toolchain",msgs=[]string{"Compiler check timed out; reduce compile-time work or check from the command line."}}}})
			break
		}

		for &p in processes {
			if p.finished {
				continue
			}

			buf: [1024]u8
			if ready,_:=os.pipe_has_data(p.reader);ready {
				n,_:=os.read(p.reader,buf[:])
				if n>0 {_,_=append(&p.buffer,..buf[:n])}
			}

			state, err := os.process_wait(p.process, 0)
			if err != nil {
				continue
			}

			if !state.exited {
				continue
			}

			p.finished = true
			running_count -= 1

			for {
				if ready,_:=os.pipe_has_data(p.reader);!ready {break}
				n, read_err := os.read(p.reader, buf[:])
				if n > 0 {
					_, _ = append(&p.buffer, ..buf[:n])
				}
				if read_err != nil {
					break
				}
			}

			os.close(p.reader)
			p.reader = nil

			output := string(p.buffer[:])
			if use_workspace && (strings.contains(output, "Unknown flag: 'workspace'") || strings.contains(output, "Unknown flag: 'overlay'")) {
				lacks_workspace = true
				continue
			}

			if len(p.buffer) > 0 {
				json_errors: Json_Errors
				if res := json.unmarshal(
					p.buffer[:],
					&json_errors,
					json.DEFAULT_SPECIFICATION,
					context.temp_allocator,
				); res != nil {
					log.errorf("Failed to unmarshal check results: %v, %v", res, string(p.buffer[:]))
					continue
				}
				json_errors.check_paths = p.check_paths
				append(&errors, json_errors)
			}
		}

		if running_count > 0 || next_index < len(jobs) {
			time.sleep(1 * time.Millisecond)
		}
	}

	if lacks_workspace {
		log.infof("`%s` has no `-workspace` or `-overlay`, so packages are checked one at a time from disk", command)
		delete(checker.odin_without_workspace, checker.allocator)
		checker.odin_without_workspace = strings.clone(command, checker.allocator)
		check(mode, check_paths, buffers, config, generation)
		return
	}
	// Ignore results from an older snapshot after another edit arrived.
	if generation != intrinsics.atomic_load(&checker.generation) { return }
	clear_diagnostics(.Check)
	for result in errors {
		thor_update_slice_info(result.slice_access)
		for info in result.slice_access {
			for buffer in buffers {
				if buffer.canonical != info.file { continue }
				ensure_diagnostic_path(.Check, common.create_uri(buffer.path, context.temp_allocator).uri)
				if buffer.path != info.file {
					alias := info; alias.file = buffer.path
					thor_update_slice_info([]Thor_Slice_Info{alias})
				}
			}
		}
	}

	DiagnosticKey :: struct {
		path:    string,
		message: string,
		line:    int,
		column:  int,
	}

	diagnostics := make(map[DiagnosticKey]struct{}, context.temp_allocator)
	for e in errors {
		for error in e.errors {
			if len(error.msgs) == 0 {
				continue
			}
			if error.stage == "declaration" || error.stage == "parser" && config.enable_parser_errors {
				// The shared frontend already diagnoses open buffers immediately.
				// Closed dependencies still need these compiler diagnostics.
				open := false
				for b in buffers { if b.path == error.pos.file || b.canonical == error.pos.file { open = true; break } }
				if open { continue }
			}

			message := strings.join(error.msgs, "\n", context.temp_allocator)

			if strings.contains(message, "Redeclaration of 'main' in this scope") {
				continue
			}

			path := error.pos.file
			// Keep the URI the editor opened, including symlinked workspaces.
			for b in buffers { if b.canonical == path { path = b.path; break } }
			if path == "" {
				path = compiler_error_fallback_path(e.check_paths, buffers)
				if path == "" { log.errorf("Compiler: %s", message); continue }
				message = fmt.tprintf("%s\nThe compiler did not provide a source location; this diagnostic is attached to a file in the package being checked.", message)
			}

			when ODIN_OS == .Windows {
				path = common.get_case_sensitive_path(path, context.temp_allocator)
				path, _ = filepath.replace_separators(path, '/', context.temp_allocator)
			}

			key := DiagnosticKey {
				path    = path,
				message = message,
				line    = error.pos.line,
				column  = error.pos.column,
			}
			if key in diagnostics {
				continue
			}

			diagnostics[key] = {}

			if is_ols_builtin_file(path) {
				continue
			}

			uri := common.create_uri(path, context.temp_allocator)

			diagnostic_severity := DiagnosticSeverity.Error
			if strings.equal_fold(error.type, "warning") {
				diagnostic_severity = .Warning
			}
			related := make([dynamic]Diagnostic_Related_Information, context.temp_allocator)
			for note in error.related {
				note_path := note.pos.file
				if note_path == "" { continue }
				for b in buffers { if b.canonical == note_path { note_path = b.path; break } }
				when ODIN_OS == .Windows {
					note_path = common.get_case_sensitive_path(note_path, context.temp_allocator)
					note_path, _ = filepath.replace_separators(note_path, '/', context.temp_allocator)
				}
				note_uri := common.create_uri(note_path, context.temp_allocator)
				append(&related, Diagnostic_Related_Information{
					location = {uri = note_uri.uri, range = compiler_error_range(note.pos)},
					message = note.message,
				})
			}

			add_diagnostics(
				.Check,
				uri.uri,
				Diagnostic {
					code = "thor",
					severity = diagnostic_severity,
					range = compiler_error_range(error.pos),
					message = message,
					relatedInformation = related[:],
				},
			)
		}

	}

}

@(private = "file")
compiler_error_range :: proc(pos: Json_Type_Error) -> common.Range {
	// Native Odin can report column zero; Thor positions are already UTF-16.
	return {
		start = {character = max(pos.column-1, 0), line = max(pos.line-1, 0)},
		end = {character = max(pos.end_column-1, 0), line = max((pos.end_line > 0 ? pos.end_line : pos.line)-1, 0)},
	}
}
@(private = "file")
start_check_process :: proc(
	command: string,
	check_paths: []string,
	collections: []string,
	overlay: string,
	config: ^common.Config,
	buffers: []Check_Buffer,
) -> (
	CheckProcess,
	bool,
) {
	entry_point_opt := filepath.ext(check_paths[0]) == ".odin" ? "-file" : "-no-entry-point"
	cmd := make([dynamic]string, context.temp_allocator)
	thor := is_thor_check(check_paths[0], buffers) && config.thor_command != ""
	compiler := config.thor_command if thor else command
	append(&cmd, compiler, "check")
	append(&cmd, ..check_paths)
	for c in collections {
		append(&cmd, c)
	}
	for k, v in config.profile.defines {
		append(&cmd, fmt.tprintf("-define:%s=%s", k, v))
	}
	append(&cmd, entry_point_opt, "-json-errors")
	if thor {
		append(&cmd, "--editor-info")
		odin := config.odin_command
		if odin == "" { odin = "odin" }
		append(&cmd, "--odin", odin, "--output-dir", fmt.tprintf("%s/thor-%x", checker.overlay.dir, hash.fnv64a(transmute([]u8)check_paths[0])))
	}
	if len(check_paths) > 1 {
		// TODO(bill): is this a good idea to change the limit to 1000?
		append(&cmd, "-workspace", "-max-error-count:1000")
	}
	if overlay != "" {
		if thor { append(&cmd, "--overlay", overlay) }
		else { append(&cmd, fmt.tprintf("-overlay:%s", overlay)) }
	}
	args, _ := strings.split(config.checker_args, " ", context.temp_allocator)
	for arg in args {
		if arg != "" {
			append(&cmd, arg)
		}
	}

	r, w, err := os.pipe()
	if err != nil {
		log.errorf("failed to create pipe for `odin check`: %v\n", err)
		return CheckProcess{}, false
	}
	defer os.close(w)

	desc := os.Process_Desc {
		command = cmd[:],
		stdout  = w,
		stderr  = w,
	}

	p, perr := os.process_start(desc)
	if perr != nil {
		os.close(r)
		log.errorf("failed to start process for `odin check`: %v\n", perr)
		return CheckProcess{}, false
	}

	buffer := make([dynamic]u8, 0, mem.Kilobyte * 200, context.temp_allocator)
	return CheckProcess{process = p, reader = r, buffer = buffer, check_paths = check_paths}, true
}

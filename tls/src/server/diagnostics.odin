package server

import "src:common"
import "core:log"
import "core:slice"
import "core:strings"
import "core:sync"

import "src:spall"

DiagnosticType :: enum {
	Syntax,
	Declaration,
	Unused,
	Check,
}

diagnostics: [DiagnosticType]map[string][dynamic]Diagnostic
diagnostic_mutex: sync.Mutex

// VS Code normalizes Windows drive letters. Separate keys for C: and c: would
// publish two replacement lists for the same document, allowing an empty list
// from one diagnostic channel to erase errors from another. Preserve component
// case and editor aliases; only normalize the drive letter in file URIs.
diagnostic_uri :: proc(uri:string)->string {
	when ODIN_OS==.Windows {
		if len(uri)>9 && strings.has_prefix(uri,"file:///") && uri[8]>='a' && uri[8]<='z' &&
		   (uri[9]==':' || strings.has_prefix(uri[9:],"%3A") || strings.has_prefix(uri[9:],"%3a")) {
			copy:=strings.clone(uri,context.temp_allocator)
			bytes:=transmute([]u8)copy
			bytes[8]&=~u8(32)
			return copy
		}
	}
	return uri
}

@(private = "file")
remove_diagnostics_locked :: proc(type: DiagnosticType, uri: string) {
	spall.trace(#procedure, uri)
	uri:=diagnostic_uri(uri)

	diagnostic_type := &diagnostics[type]
	if diagnostic_type == nil {
		log.errorf("Diagnostic type did not exist: %v", type)
		return
	}

	diagnostic_array := &diagnostic_type[uri]

	if diagnostic_array == nil {
		return
	}

	for diagnostic in diagnostic_array {
		delete(diagnostic.message)
		delete(diagnostic.code)
		delete_vet_fix(diagnostic.data)
		delete_related_diagnostics(diagnostic.relatedInformation)
		if diagnostic.tags != nil {
			delete(diagnostic.tags)
		}
	}

	clear(diagnostic_array)
}

add_diagnostics :: proc(type: DiagnosticType, uri: string, diagnostic: Diagnostic) {
	spall.trace(#procedure, uri)
	uri:=diagnostic_uri(uri)

	if !common.config.enable_diagnostics {
		return
	}

	sync.lock(&diagnostic_mutex)
	defer sync.unlock(&diagnostic_mutex)

	diagnostic_type := &diagnostics[type]

	if diagnostic_type == nil {
		log.errorf("Diagnostic type did not exist: %v", type)
		return
	}

	diagnostic_array := &diagnostic_type[uri]

	if diagnostic_array == nil {
		diagnostic_type[strings.clone(uri)] = make([dynamic]Diagnostic)
		diagnostic_array = &diagnostic_type[uri]
	}

	diagnostic := diagnostic

	diagnostic.message = strings.clone(diagnostic.message)
	diagnostic.code = strings.clone(diagnostic.code)
	diagnostic.data.text = strings.clone(diagnostic.data.text)
	diagnostic.data.snapshot = strings.clone(diagnostic.data.snapshot)
	if diagnostic.tags != nil {
		diagnostic.tags = slice.clone(diagnostic.tags)
	}
	if diagnostic.relatedInformation != nil {
		diagnostic.relatedInformation = slice.clone(diagnostic.relatedInformation)
		for &related in diagnostic.relatedInformation {
			related.location.uri = strings.clone(related.location.uri)
			related.message = strings.clone(related.message)
		}
	}

	append(diagnostic_array, diagnostic)
}

ensure_diagnostic_path :: proc(type: DiagnosticType, uri: string) {
	uri:=diagnostic_uri(uri)
	sync.lock(&diagnostic_mutex)
	defer sync.unlock(&diagnostic_mutex)
	if _, found := diagnostics[type][uri]; !found {
		diagnostics[type][strings.clone(uri)] = make([dynamic]Diagnostic)
	}
}

remove_diagnostics :: proc(type: DiagnosticType, uri: string) {
	sync.lock(&diagnostic_mutex)
	defer sync.unlock(&diagnostic_mutex)

	remove_diagnostics_locked(type, uri)
}

clear_diagnostics :: proc(type: DiagnosticType) {
	sync.lock(&diagnostic_mutex)
	defer sync.unlock(&diagnostic_mutex)

	diagnostic_type := &diagnostics[type]

	if diagnostic_type == nil {
		log.errorf("Diagnostic type did not exist: %v", type)
		return
	}

	for _, &diagnostic_array in diagnostic_type {
		for diagnostic in diagnostic_array {
			delete(diagnostic.message)
			delete(diagnostic.code)
			delete_vet_fix(diagnostic.data)
			delete(diagnostic.tags)
			delete_related_diagnostics(diagnostic.relatedInformation)
		}
		clear(&diagnostic_array)
	}
}

@(private = "file")
delete_vet_fix :: proc(fix: Thor_Vet_Fix) {
	delete(fix.text)
	delete(fix.snapshot)
}

@(private = "file")
delete_related_diagnostics :: proc(related: []Diagnostic_Related_Information) {
	for note in related {
		delete(note.location.uri)
		delete(note.message)
	}
	delete(related)
}

get_merged_diagnostics :: proc() -> map[string][dynamic]Diagnostic {
	sync.lock(&diagnostic_mutex)
	defer sync.unlock(&diagnostic_mutex)

	merged_diagnostics := make(map[string][dynamic]Diagnostic, context.temp_allocator)

	for diagnostic_type in diagnostics {
		for k, v in diagnostic_type {
			diagnostic_array := &merged_diagnostics[k]

			if diagnostic_array == nil {
				merged_diagnostics[k] = make([dynamic]Diagnostic, context.temp_allocator)
				diagnostic_array = &merged_diagnostics[k]
			}

			append(diagnostic_array, ..v[:])
		}
	}
	return merged_diagnostics
}

push_diagnostics :: proc(writer: ^Writer) {
	merged_diagnostics := get_merged_diagnostics()

	for k, v in merged_diagnostics {
		params := NotificationPublishDiagnosticsParams {
			uri         = k,
			diagnostics = v[:],
		}

		notification := Notification {
			jsonrpc = "2.0",
			method  = "textDocument/publishDiagnostics",
			params  = params,
		}

		if writer != nil {
			send_notification(notification, writer)
		}
	}

}

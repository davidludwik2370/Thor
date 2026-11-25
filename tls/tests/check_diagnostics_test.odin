package tests

import "core:path/filepath"
import "core:hash"
import "core:testing"
import "src:server"
import "src:common"

@(test)
rename_workspace_boundaries_keep_native_and_editor_separators :: proc(t:^testing.T) {
	testing.expect(t,server.thor_rename_in_directory("/work/héllo/main.thor","/work/héllo"))
	testing.expect(t,!server.thor_rename_in_directory("/work/héllo-other/main.thor","/work/héllo"))
	testing.expect(t,!server.thor_rename_in_directory("/work/main.thor",""))
	when ODIN_OS==.Windows {
		testing.expect(t,server.thor_rename_in_directory("C:/work/héllo/main.thor",`c:\work\héllo\`))
		testing.expect(t,server.thor_rename_in_directory(`c:\work\héllo\main.thor`,"C:/work/héllo"))
		testing.expect(t,!server.thor_rename_in_directory("C:/work/Héllo/main.thor","C:/work/héllo"))
	}
}

@(test)
windows_diagnostic_channels_share_one_drive_uri :: proc(t:^testing.T) {
	when ODIN_OS!=.Windows {return}
	previous_enabled:=common.config.enable_diagnostics
	common.config.enable_diagnostics=true
	defer {common.config.enable_diagnostics=previous_enabled}
	previous:=server.diagnostics
	server.diagnostics={}
	for kind in server.DiagnosticType {server.diagnostics[kind]=make(map[string][dynamic]server.Diagnostic)}
	defer {
		for kind in server.DiagnosticType {
			server.clear_diagnostics(kind)
			for key,values in server.diagnostics[kind] {delete(key);delete(values)}
			delete(server.diagnostics[kind])
		}
		server.diagnostics=previous
	}
	upper:="file:///C%3A/héllo/Name.thor"
	lower:="file:///c%3A/héllo/Name.thor"
	server.ensure_diagnostic_path(.Syntax,upper)
	server.add_diagnostics(.Unused,upper,{message="unused import"})
	server.add_diagnostics(.Check,lower,{message="integer type required"})
	merged:=server.get_merged_diagnostics()
	testing.expect_value(t,len(merged),1)
	testing.expect_value(t,len(merged[upper]),2)
	server.clear_diagnostics(.Check)
	server.remove_diagnostics(.Unused,lower)
	cleared:=server.get_merged_diagnostics()
	testing.expect_value(t,len(cleared),1)
	testing.expect_value(t,len(cleared[upper]),0)
	testing.expect_value(t,server.diagnostic_uri("file:///c:/héllo/Name.thor"),"file:///C:/héllo/Name.thor")
	testing.expect_value(t,server.diagnostic_uri("file://Server/share/Name.thor"),"file://Server/share/Name.thor")
}

@(test)
combined_checker_requests_retain_document_checks_without_workspace_diagnostics :: proc(t:^testing.T) {
	config:=common.Config{}
	file:="/outside-workspace/main.thor"
	saved:=server.resolve_check_paths(.Saved,{file},&config)
	combined:=server.resolve_check_paths(.Combined,{"",file,""},&config)
	testing.expect_value(t,len(saved),1)
	testing.expect_value(t,len(combined),1)
	if len(saved)==1 && len(combined)==1 {testing.expect_value(t,saved[0],combined[0])}
	workspace:=server.resolve_check_paths(.Workspace,{""},&config,{{canonical=file}})
	testing.expect_value(t,len(workspace),1)
	if len(saved)==1 && len(workspace)==1 {testing.expect_value(t,saved[0],workspace[0])}
}

@(test)
compiler_paths_preserve_unicode_and_component_case :: proc(t:^testing.T) {
	when ODIN_OS==.Windows {
		testing.expect(t,server.compiler_path_equal("C:/héllo/main.thor",`c:\héllo\main.thor`))
		testing.expect(t,!server.compiler_path_equal("C:/héllo/Name.thor","c:/héllo/name.thor"))
	} else {
		testing.expect(t,!server.compiler_path_equal("C:/héllo/main.thor",`c:\héllo\main.thor`))
	}
}

@(test)
compiler_slice_inlays_follow_editor_path_spelling :: proc(t:^testing.T) {
	when ODIN_OS!=.Windows {return}
	canonical:=`C:\héllo\main.thor`
	editor:="c:/héllo/main.thor"
	fullpath:="C:/héllo/main.thor"
	source:="package main\nmodify::proc(values:[]int){}\n"
	text:=transmute([]u8)source
	previous:=server.diagnostics[.Check]
	server.diagnostics[.Check]=make(map[string][dynamic]server.Diagnostic)
	defer {
		server.clear_diagnostics(.Check)
		for key,values in server.diagnostics[.Check] {delete(key);delete(values)}
		delete(server.diagnostics[.Check])
		server.diagnostics[.Check]=previous
	}
	document:=server.Document{fullpath=fullpath,text=text,used_text=len(text)}
	info:=server.Thor_Slice_Info{file=canonical,source_hash=hash.fnv64a(text),
		parameters={server.Thor_Slice_Parameter{file=canonical,offset=27,end=33,name="values"}}}
	revision:=server.thor_current_slice_revision()
	server.compiler_update_slice_info({info},{{path=editor,fullpath=fullpath,canonical=canonical}},revision)
	defer server.thor_delete_slice_info()
	hints:=server.thor_slice_access_hints(&document,{{},{line=99}})
	testing.expect_value(t,len(hints),1)
	testing.expect(t,server.thor_slice_parameter_mut(editor,27))
	testing.expect(t,server.thor_slice_parameter_mut(fullpath,27))
}

@(test)
compiler_diagnostic_paths_keep_editor_aliases_and_unsaved_names :: proc(t:^testing.T) {
	root,_:=filepath.abs(".",context.temp_allocator)
	canonical,_:=filepath.join({root,"physical","new.thor"},context.temp_allocator)
	editor,_:=filepath.join({root,"editor-link","new.thor"},context.temp_allocator)
	buffers:=[]server.Check_Buffer{{path=editor,canonical=canonical}}
	actual:=server.compiler_diagnostic_path(canonical,buffers)
	direct:=server.compiler_diagnostic_path(editor,buffers)
	testing.expect_value(t,actual,editor)
	testing.expect_value(t,direct,editor)
}

@(test)
compiler_locationless_error_stays_in_its_checked_package :: proc(t: ^testing.T) {
	root, _ := filepath.abs(".", context.temp_allocator)
	good, _ := filepath.join({root, "demo", "main.thor"}, context.temp_allocator)
	bad, _ := filepath.join({root, "basic", "main.thor"}, context.temp_allocator)
	bad_package := filepath.dir(bad)
	buffers := []server.Check_Buffer{{path=good, canonical=good}, {path=bad, canonical=bad}}
	testing.expect_value(t, server.compiler_error_fallback_path({bad_package}, buffers), bad)
	testing.expect_value(t, server.compiler_error_fallback_path({bad}, buffers), bad)
}

@(test)
compiler_locationless_error_preserves_editor_spelling_and_unsaved_files :: proc(t: ^testing.T) {
	root, _ := filepath.abs(".", context.temp_allocator)
	canonical, _ := filepath.join({root, "actual", "new.thor"}, context.temp_allocator)
	editor, _ := filepath.join({root, "linked", "new.thor"}, context.temp_allocator)
	buffers := []server.Check_Buffer{{path=editor, canonical=canonical}}
	testing.expect_value(t, server.compiler_error_fallback_path({canonical}, buffers), editor)
	testing.expect_value(t, server.compiler_error_fallback_path({filepath.dir(canonical)}, buffers), editor)
}

@(test)
compiler_locationless_error_does_not_fall_back_to_an_unrelated_buffer :: proc(t: ^testing.T) {
	root, _ := filepath.abs(".", context.temp_allocator)
	missing, _ := filepath.join({root, "missing-package"}, context.temp_allocator)
	unrelated, _ := filepath.join({root, "demo", "main.thor"}, context.temp_allocator)
	buffers := []server.Check_Buffer{{path=unrelated, canonical=unrelated}}
	testing.expect_value(t, server.compiler_error_fallback_path({missing}, buffers), "")
}

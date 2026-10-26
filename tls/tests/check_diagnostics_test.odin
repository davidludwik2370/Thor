package tests

import "core:path/filepath"
import "core:testing"
import "src:server"

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

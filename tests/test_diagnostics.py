"""Source-map and diagnostic regressions, checked against the real backend."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from thorlang.sourcemap import MappingBuilder, SourceMap, position

ROOT = Path(__file__).resolve().parents[1]


class MapTests(unittest.TestCase):
    def test_nested_copied_and_synthetic_fragments(self):
        b = MappingBuilder()
        text = b.wrap("generated(" + b.wrap("é😀", "/source.thor", 10, 16, exact=True) + ")",
                      "/source.thor", 0, 20, "array")
        code, mapping = b.finish(text)
        self.assertEqual(code, "generated(é😀)")
        self.assertEqual(mapping.lookup(10, 2).start, 10)
        self.assertEqual(mapping.lookup(12, 4).start, 12)
        self.assertEqual(mapping.lookup(16).kind, "array")
        self.assertEqual(mapping.lookup(16).end, 20)

    def test_duplicated_reordered_fragments_keep_origins(self):
        b = MappingBuilder()
        a = b.wrap("a", "/a.thor", 3, 4, exact=True)
        c = b.wrap("c", "/c.thor", 9, 10, exact=True)
        code, mapping = b.finish(b.wrap(c + a + c, "/a.thor", 0, 4))
        self.assertEqual(code, "cac")
        self.assertEqual([mapping.lookup(i).file for i in range(3)], ["/c.thor", "/a.thor", "/c.thor"])

    def test_utf16_positions_crlf_and_eof(self):
        raw = "one\r\n\té😀x\n".encode()
        self.assertEqual(position(raw, raw.index(b"x")), (2, 5))
        self.assertEqual(position(raw, len(raw)), (3, 1))
        self.assertEqual(position(raw, 1000), (3, 1))


class DiagnosticTests(unittest.TestCase):
    def invoke(self, source, *, files=None, flags=(), command="check", overlays=None, success=False):
        temporary = tempfile.TemporaryDirectory(prefix="thor-diagnostics-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        entry = root / "src" / "main.thor"
        entry.parent.mkdir()
        entry.write_bytes(source.encode())
        for name, text in (files or {}).items():
            path = entry.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        overlay_flags = []
        if overlays:
            replacements = {}
            for i, (name, text) in enumerate(overlays.items()):
                replacement = root / f"buffer-{i}"
                replacement.write_text(text)
                replacements[str(entry.parent / name)] = str(replacement)
            overlay = root / "overlay.json"
            overlay.write_text(json.dumps({"Replace": replacements}))
            overlay_flags = ["--overlay", str(overlay)]
        result = subprocess.run([str(ROOT / "thor"), command, str(entry.parent), "--output-dir", str(root / "out"),
                                 "--diagnostic-format=json", *overlay_flags, *flags], capture_output=True, text=True, timeout=40)
        data = json.loads(result.stderr)
        self.assertEqual(result.returncode == 0, success, data)
        return entry, root / "out", data["errors"], result

    def at(self, source, needle, *, files=None, flags=(), overlays=None, message=None):
        entry, output, errors, _ = self.invoke(source, files=files, flags=flags, overlays=overlays)
        matching = [e for e in errors if message is None or message in "\n".join(e["msgs"])]
        self.assertTrue(matching, errors)
        error = matching[0]
        raw = (overlays or {}).get("main.thor", source).encode()
        offset = raw.index(needle.encode())
        self.assertEqual(error["pos"]["file"], str(entry), error)
        self.assertEqual(error["pos"]["offset"], offset, error)
        self.assertEqual((error["pos"]["line"], error["pos"]["column"]), position(raw, offset))
        self.assertNotIn(str(output), json.dumps(errors))
        self.assertNotIn("__thor_", json.dumps(errors))
        return errors

    def test_ordinary_error_after_expansions_and_import_removal(self):
        self.at('package main\nimport "core:math" /\n sin,\n cos\nmain :: proc(){ a := [1,2]; _ = sin(f64(0)); _ = cos(f64(0)); _ = a; _ = missing }\n', 'missing')

    def test_array_element_type_error_exact_range(self):
        source = 'package main\nmain :: proc(){ a := [1, "wrong"]; _ = a }\n'
        errors = self.at(source, '"wrong"')
        self.assertEqual(errors[0]['pos']['end_offset'] - errors[0]['pos']['offset'], len('"wrong"'))

    def test_array_used_for_scalar_hides_type_intrinsics(self):
        errors = self.at('package main\nmain :: proc(){ a:int = [1,2]; _ = a }\n', '[1,2]')
        self.assertIn('[1,2]', errors[0]['msgs'][0])
        self.assertNotIn('Thor expression', json.dumps(errors))
        self.assertNotIn('type_elem_type', json.dumps(errors))

    def test_discarding_array_literal_does_not_query_blank_type(self):
        self.invoke('package main\nmain :: proc(){ _ = [1,2]; _ = true ? [1,2] : [3,4] }\n', success=True)

    def test_nested_array_error(self):
        self.at('package main\nmain :: proc(){ a := [[1,2], [3,"wrong"]]; _ = a }\n', '"wrong"')

    def test_multiline_ternary_error(self):
        self.at('package main\nmain :: proc(){ a := (true ?\n [1, missing]\n : [2,3])\n _ = a }\n', 'missing')

    def test_multiline_ternary_later_error(self):
        self.at('package main\nmain :: proc(){ a := (true ?\n 1\n : 2)\n _ = a\n _ = missing }\n', 'missing')

    def test_unicode_crlf_tabs(self):
        self.at('package main\r\nmain :: proc(){\r\n\t_ = "é😀"; a := [1,2]; _ = a; _ = missing\r\n}\r\n', 'missing')

    def test_parser_error_uses_same_schema_and_location(self):
        self.at('package main\nmain :: proc(){ x := ; }\n', ';', message='expected an operand')

    def test_selective_import_validation_points_to_name(self):
        self.at('package main\nimport "core:math" /\n sin,\n DOES_NOT_EXIST\nmain :: proc(){}\n', 'DOES_NOT_EXIST')

    def test_transpiler_conflict_has_a_real_range(self):
        source = 'package main\nimport "core:math" / sin\nsin :: 3\nmain :: proc(){}\n'
        _, _, errors, _ = self.invoke(source)
        self.assertEqual(errors[0]['pos']['offset'], source.rindex('sin ::'))
        self.assertIn('conflicts', errors[0]['msgs'][0])

    def test_invalid_membership_hides_overload_implementation(self):
        self.at('package main\nmain :: proc(){ a := 1 in "abc"; _ = a }\n', '1 in "abc"', message='Invalid operands for membership')

    def test_helper_failure_uses_actual_call_site(self):
        source = 'package main\nmain :: proc(){ _ = 1 in [1]; s := []int{1}\n _ = s in [s] }\n'
        self.at(source, 's in [s]', message='Membership requires comparable elements')

    def test_helper_instantiation_column_after_unicode(self):
        self.at('package main\nmain :: proc(){ _ = "😀"; s := []int{1}; _ = s in [s] }\n',
                's in [s]', message='Membership requires comparable elements')

    def test_unix_style_helper_instantiation_uses_actual_call_site(self):
        source = 'package main\nmain :: proc(){ _ = 1 in [1]; s := []int{1}\n _ = s in [s] }\n'
        self.at(source, 's in [s]', flags=['-error-pos-style:unix'], message='Membership requires comparable elements')

    def test_user_names_resembling_generated_names_remain_intact(self):
        for name in ['__thor_user_missing', 'array_literal_needs_element_type']:
            _, _, errors, _ = self.invoke(f'package main\nmain :: proc(){{_ = {name}}}\n')
            self.assertIn(f'Undeclared name: {name}', errors[0]['msgs'][0])

    def test_warnings_are_mapped_once_and_obey_flags(self):
        source = 'package main\nS :: struct #field_align(4) {x:int}\nmain :: proc(){}\n'
        _, _, errors, _ = self.invoke(source, success=True)
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]['type'], 'warning')
        self.assertEqual(errors[0]['pos']['offset'], source.index('field_align'))
        _, _, errors, _ = self.invoke(source, flags=['-ignore-warnings'], success=True)
        self.assertEqual(errors, [])
        _, _, errors, _ = self.invoke(source, flags=['-warnings-as-errors'])
        self.assertTrue(any(e['type'] == 'error' for e in errors), errors)

    def test_terse_and_raw_diagnostic_options(self):
        entry, output, _, _ = self.invoke('package main\nmain :: proc(){_ = missing}\n')
        for flag in ['-terse-errors', '--raw-diagnostics']:
            with self.subTest(flag=flag):
                result = subprocess.run([str(ROOT / 'thor'), 'check', str(entry), '--output-dir', str(output), flag], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                if flag == '-terse-errors':
                    self.assertIn(str(entry), result.stderr)
                    self.assertNotIn('^', result.stderr)
                else:
                    self.assertIn(str(output / 'main.odin'), result.stderr)

    def test_missing_import_has_a_source_range(self):
        self.at('package main\nimport "nonexistent"\nmain :: proc(){}\n', 'import', message='imported package does not exist')

    def test_empty_array_context_message(self):
        self.at('package main\nmain :: proc(){ a := []; _ = a }\n', '[]', message='needs an element type')

    def test_repeated_inferred_expression_error_deduplicates(self):
        errors = self.at('package main\nmain :: proc(){ a := [missing,2]; _ = a }\n', 'missing')
        self.assertEqual(len([e for e in errors if 'Undeclared name: missing' in e['msgs'][0]]), 1)

    def test_generic_constraint_and_embedded_caller_location(self):
        source = 'package main\ng :: proc(x:$T)->T where T in [int,f64] {return x}\nmain :: proc(){_ = g("bad")}\n'
        errors = self.at(source, 'T in [int,f64]', message="'where' clause")
        messages = '\n'.join(m for e in errors for m in e['msgs'])
        self.assertIn('T in [int,f64]', messages)
        self.assertIn('main.thor(3:', messages)
        self.assertNotIn('((T)', messages)

    def test_unix_style_generic_caller_locations_are_mapped(self):
        source = 'package main\ng :: proc(x:$T)->T where T in [int,f64] {return x}\nmain :: proc(){_ = g("bad")}\n'
        errors = self.at(source, 'T in [int,f64]', flags=['-error-pos-style:unix'], message="'where' clause")
        self.assertIn('main.thor:3:', '\n'.join(m for e in errors for m in e['msgs']))

    def test_imported_thor_and_native_odin_errors(self):
        for extension in ['thor', 'odin']:
            with self.subTest(extension=extension):
                _, _, errors, _ = self.invoke('package main\nimport "lib"\nmain :: proc(){lib.f()}\n',
                    files={f'lib/lib.{extension}': 'package lib\nf :: proc(){ a := 1; _ = a; _ = missing }\n'})
                error = next(e for e in errors if 'missing' in e['msgs'][0])
                self.assertTrue(error['pos']['file'].endswith(f'/lib/lib.{extension}'), error)
                self.assertEqual(error['pos']['line'], 2)

    def test_unsaved_overlay_lowered_and_reported_at_original_path(self):
        source = 'package main\nmain :: proc(){}\n'
        changed = 'package main\nmain :: proc(){ a := [1,2]; _ = a; _ = missing }\n'
        self.at(source, 'missing', overlays={'main.thor': changed})

    def test_unsaved_imported_overlay(self):
        source = 'package main\nimport "lib"\nmain :: proc(){lib.f()}\n'
        entry, _, errors, _ = self.invoke(source, files={'lib/lib.thor': 'package lib\nf :: proc(){}\n'},
                    overlays={'lib/lib.thor': 'package lib\nf :: proc(){ _ = missing }\n'})
        self.assertEqual(errors[0]['pos']['file'], str(entry.parent / 'lib/lib.thor'))

    def test_inactive_branch_does_not_create_diagnostics(self):
        self.invoke('package main\nmain :: proc(){ #if false { a := []; _ = a; _ = missing } }\n', success=True)

    def test_source_map_sidecar_is_complete_and_round_trips(self):
        entry, output, _, _ = self.invoke('package main\nmain :: proc(){ a := [1,2]; _ = a }\n', success=True)
        data = json.loads((output / 'main.odin.map.json').read_text())
        self.assertEqual(data['version'], 1)
        self.assertEqual(data['sources'][str(entry)], entry.read_text())
        raw = (output / 'main.odin').read_bytes()
        segments = data['segments']
        self.assertEqual(segments[0][0], 0)
        self.assertEqual(segments[-1][1], len(raw))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(segments, segments[1:])))
        mapping = SourceMap(segments)
        for a, b, file, start, end, _, exact in segments:
            self.assertEqual(mapping.lookup(a).file, file)
            if exact:
                self.assertEqual(raw[a:b], data['sources'][file].encode()[start:end])

    def test_text_output_and_unix_locations(self):
        entry, output, _, _ = self.invoke('package main\nmain :: proc(){ _ = missing }\n')
        result = subprocess.run([str(ROOT / 'thor'), 'check', str(entry), '--output-dir', str(output), '-error-pos-style:unix'], capture_output=True, text=True)
        self.assertIn(f'{entry}:2:', result.stderr)
        self.assertIn('_ = missing', result.stderr)
        self.assertIn('^^^^^^^', result.stderr)
        self.assertNotIn('.odin', result.stderr)

    def test_runtime_stderr_is_not_treated_as_a_compiler_error(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        entry = Path(temporary.name) / 'main.thor'
        entry.write_text('package main\nimport "core:fmt"\nimport "core:os"\nmain :: proc(){ fmt.eprintln("not JSON: compiler text"); os.exit(7) }\n')
        result = subprocess.run([str(ROOT / 'thor'), 'run', str(entry), '--output-dir', str(entry.parent / 'out')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr, 'not JSON: compiler text\n')


if __name__ == '__main__':
    unittest.main()

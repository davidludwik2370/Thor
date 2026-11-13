"""Cached Thor syntax preserves protocol, overlays and parser cache controls."""
import json
import os
import unittest
from tests import test_rust_compiler as native


class RustSyntaxCacheTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def profile(self, flags=(), command='ast'):
        path = self.root/'profile.json'
        result = self.invoke('rust', command=command, flags=flags,
                             env={**os.environ, 'THOR_PROFILE': str(path)})
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return result, json.loads(path.read_text())

    def syntax_files(self):
        return list((self.root/'cache/rust-syntax-v1').glob('*/*.bin'))

    def test_cross_feature_protocol_matches_warm_and_uncached_sources(self):
        for name in ['examples/comprehensions/demo.thor', 'examples/slice_access/demo.thor']:
            with self.subTest(name=name):
                self.source.write_text((native.ROOT/name).read_text())
                cold, _ = self.profile()
                warm, profile = self.profile()
                uncached, _ = self.profile(['--no-cache'])
                self.assertEqual(cold.stdout, warm.stdout)
                self.assertEqual(cold.stdout, uncached.stdout)
                self.assertGreater(profile['counters'].get('parser.syntax_cache_hits', 0), 0)
                self.assertEqual(profile['counters'].get('parser.processes', 0), 0)

    def test_syntax_corruption_uses_raw_packets_and_both_layers_can_be_rebuilt(self):
        self.source.write_text(self.program('value:>7;_=value'))
        original, _ = self.profile()
        files = self.syntax_files()
        self.assertTrue(files)
        for path in files:
            path.write_bytes(b'broken syntax')
        recovered, profile = self.profile()
        self.assertEqual(original.stdout, recovered.stdout)
        self.assertEqual(profile['counters'].get('parser.processes', 0), 0)
        self.assertGreater(profile['counters'].get('parser.cache_hits', 0), 0)
        for path in self.syntax_files():
            path.write_bytes(b'broken syntax')
        for path in (self.root/'cache/rust-parse-v1').glob('*.json'):
            path.write_bytes(b'broken parser packet')
        rebuilt, profile = self.profile()
        self.assertEqual(original.stdout, rebuilt.stdout)
        self.assertGreater(profile['counters'].get('parser.processes', 0), 0)

    def test_legacy_raw_cache_populates_the_compact_cache(self):
        self.source.write_text(self.program('value:>7;_=value'))
        original, _ = self.profile()
        for path in self.syntax_files():
            path.unlink()
        migrated, profile = self.profile()
        self.assertEqual(original.stdout, migrated.stdout)
        self.assertEqual(profile['counters'].get('parser.processes', 0), 0)
        self.assertGreater(profile['counters'].get('parser.cache_hits', 0), 0)
        self.assertTrue(self.syntax_files())

    def test_disk_and_unsaved_overlay_edits_keep_current_spans_and_immutability(self):
        original = self.program('/* 😀 */ value:>7;_=value')
        changed = original.replace('value:>7', 'value:=8')
        self.source.write_text(original)
        first, _ = self.profile()
        self.source.write_text(changed)
        disk, _ = self.profile()
        replacement = self.root/'unsaved.thor'
        replacement.write_text(original)
        overlay = self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace': {str(self.source): str(replacement)}}))
        unsaved, _ = self.profile(['--overlay', str(overlay)])
        warm, _ = self.profile(['--overlay', str(overlay)])
        self.assertNotEqual(first.stdout, disk.stdout)
        self.assertEqual(first.stdout, unsaved.stdout)
        self.assertEqual(first.stdout, warm.stdout)
        self.assertEqual(self.source.read_text(), changed)

    def test_clear_cache_removes_managed_entries_and_preserves_other_files(self):
        self.source.write_text(self.program('value:>7;_=value'))
        self.profile()
        raw = self.root/'cache/rust-parse-v1'
        syntax = self.root/'cache/rust-syntax-v1'/('a'*64)
        syntax.mkdir(parents=True, exist_ok=True)
        stale_raw = raw/(('b'*64)+'.json')
        stale_syntax = syntax/(('b'*64)+'.bin')
        stale_raw.write_bytes(b'old raw cache')
        stale_syntax.write_bytes(b'old syntax cache')
        notes = syntax/'notes.txt'
        notes.write_text('keep')
        _, profile = self.profile(['--clear-cache'])
        self.assertGreater(profile['counters'].get('parser.processes', 0), 0)
        self.assertFalse(stale_raw.exists())
        self.assertFalse(stale_syntax.exists())
        self.assertEqual(notes.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()

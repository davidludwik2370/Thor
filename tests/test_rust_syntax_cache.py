"""Cached Thor syntax preserves protocol, overlays and parser cache controls."""
import json
import os
import unittest
from tests import test_rust_compiler as native
from tests.cache_paths import entries


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
        return entries(self.root/'cache','syntax-*')

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

    def test_syntax_corruption_recomputes_without_retaining_a_duplicate_raw_cache(self):
        self.source.write_text(self.program('value:>7;_=value'))
        original, _ = self.profile()
        files = self.syntax_files()
        self.assertTrue(files)
        for path in files:
            path.write_bytes(b'broken syntax')
        recovered, profile = self.profile()
        self.assertEqual(original.stdout, recovered.stdout)
        self.assertGreater(profile['counters'].get('parser.processes', 0), 0)
        self.assertFalse(entries(self.root/'cache','parser-v1'))

    def test_legacy_raw_cache_populates_the_bounded_cache(self):
        import hashlib
        import subprocess
        self.source.write_text(self.program('value:>7;_=value'))
        original, _ = self.profile()
        # Create a genuine packet using the same frontend/arguments as Thor.
        odin_root=subprocess.check_output(['odin','root'],text=True).strip()
        argv=[str(self.source),'--odin-root='+odin_root,'--stdin']
        key=self.syntax_files()[0].stem.rsplit('-',1)[1]
        identity=json.dumps(argv,separators=(',',':')).encode()+self.source.read_bytes()
        parser=next(p for p in (native.ROOT/'.thor-cache').glob('frontend-*')
                    if hashlib.sha256(hashlib.sha256(p.read_bytes()).hexdigest().encode()+identity).hexdigest()==key)
        payload=subprocess.check_output([str(parser),*argv],input=self.source.read_bytes()).strip()
        legacy=self.root/'cache/rust-parse-v1';legacy.mkdir(exist_ok=True)
        # Preserve the raw bytes used for checksum rather than reserializing.
        (legacy/(key+'.json')).write_bytes(b'{"version":1,"schema":"odin-frontend-raw-v1","sha256":"'+hashlib.sha256(payload).hexdigest().encode()+b'","payload":'+payload+b'}')
        for path in self.syntax_files():path.unlink()
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
        raw.mkdir(exist_ok=True)
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

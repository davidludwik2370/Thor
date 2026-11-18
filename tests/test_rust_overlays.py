"""Unsaved source files participate in native discovery without being created."""
import json
import os
from pathlib import Path
import subprocess
import unittest
import uuid

from tests import test_rust_compiler as native


class RustOverlayTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp

    def overlay(self, values):
        replacements = {}
        for index, (original, text) in enumerate(values.items()):
            snapshot = self.root / f'snapshot-{index}.txt'
            snapshot.write_text(text)
            replacements[str(original)] = str(snapshot)
        packet = self.root / 'overlay.json'
        packet.write_text(json.dumps({'Replace': replacements}))
        return packet

    def invoke(self, engine, entry, overlay, *, command='check', flags=()):
        compiler = native.PYTHON if engine == 'python' else native.RUST
        return subprocess.run([
            str(compiler), command, str(entry), '--overlay', str(overlay),
            '--output-dir', str(self.root / engine),
            '--cache-dir', str(self.root / ('cache-' + engine)), *flags,
        ], cwd=self.root, capture_output=True, text=True, timeout=60)

    def test_new_file_and_package_errors_keep_the_unsaved_unicode_location(self):
        text = '// café 🌩\npackage main\nmain::proc(){a:=[1,"wrong"];_=a}\n'
        overlay = self.overlay({self.source: text})
        for entry in [self.source, self.app]:
            for flags in [[], native.UNCHECKED]:
                for engine in ['python', 'rust']:
                    with self.subTest(entry=entry, flags=flags, engine=engine):
                        result = self.invoke(engine, entry, overlay, command='build' if flags else 'check',
                                             flags=[*flags, '-json-errors'])
                        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                        packet = json.loads(result.stderr)
                        error = next(e for e in packet['errors'] if 'Cannot convert' in str(e['msgs']))
                        self.assertEqual(error['pos']['file'], str(self.source))
                        self.assertEqual(error['pos']['offset'], text.encode().index(b'"wrong"'))
                        self.assertFalse(self.source.exists())

    def test_saved_and_new_package_members_and_imported_members_merge_once(self):
        saved = 'package main\nmain::proc(){missing()}\n'
        self.source.write_text(saved)
        sibling = self.app / 'helper.thor'
        library = self.app / 'library'
        library.mkdir()
        imported = library / 'only-in-editor.thor'
        overlay = self.overlay({
            self.source: 'package main\nimport "library"\nmain::proc(){if helper()+library.answer()!=12{panic("overlay")}}\n',
            sibling: 'package main\nhelper::proc()->int{return 5}\n',
            imported: 'package library\nanswer::proc()->int{return 7}\n',
        })
        for flags in [[], native.UNCHECKED]:
            for engine in ['python', 'rust']:
                with self.subTest(engine=engine, flags=flags):
                    result = self.invoke(engine, self.app, overlay, command='run', flags=flags)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(self.source.read_text(), saved)
                    self.assertFalse(sibling.exists())
                    self.assertFalse(imported.exists())

    def test_test_files_and_other_packages_remain_excluded_until_requested(self):
        self.source.write_text('package main\nmain::proc(){}\n')
        test_file = self.app / 'unsaved_test.thor'
        unrelated = self.root / 'unrelated'
        unrelated.mkdir()
        other = unrelated / 'other.thor'
        overlay = self.overlay({
            test_file: '#+test\npackage main\ncheck::proc(){missing_overlay_test()}\n',
            self.app / 'ignored.txt': 'not Thor source',
            other: 'package other\ninvalid::missing_other_package\n',
        })
        for engine in ['python', 'rust']:
            normal = self.invoke(engine, self.app, overlay)
            self.assertEqual(normal.returncode, 0, normal.stdout + normal.stderr)
            tests = self.invoke(engine, self.app, overlay, command='test')
            self.assertNotEqual(tests.returncode, 0, tests.stdout + tests.stderr)
            self.assertIn('missing_overlay_test', tests.stderr)
            self.assertNotIn('missing_other_package', tests.stderr)
            self.assertFalse(test_file.exists())
            self.assertFalse(other.exists())

    def test_explicit_file_entry_does_not_pull_in_unsaved_siblings(self):
        sibling = self.app / 'unused.thor'
        overlay = self.overlay({
            self.source: 'package main\nmain::proc(){}\n',
            sibling: 'package main\ninvalid::missing_unsaved_sibling\n',
        })
        for engine in ['python', 'rust']:
            direct = self.invoke(engine, self.source, overlay)
            self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
            package = self.invoke(engine, self.app, overlay)
            self.assertNotEqual(package.returncode, 0, package.stdout + package.stderr)
            self.assertIn('missing_unsaved_sibling', package.stderr)
            self.assertFalse(self.source.exists())
            self.assertFalse(sibling.exists())

    def test_unsaved_compile_time_dependency_edits_replace_cached_values(self):
        for factor in [2, 3, 2]:
            text = ('package main\nimport "core:fmt"\n'
                    f'calculate::fn(n:int)->int{{return n*{factor}}}\n'
                    'VALUE::#run calculate(7)\nmain::proc(){fmt.println(VALUE)}\n')
            overlay = self.overlay({self.source: text})
            for engine in ['python', 'rust']:
                with self.subTest(engine=engine, factor=factor):
                    result = self.invoke(engine, self.source, overlay, command='run', flags=native.UNCHECKED)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stdout.strip(), str(7 * factor))
                    self.assertFalse(self.source.exists())

    def test_virtual_file_paths_resolve_through_an_existing_directory_symlink(self):
        link = self.root / 'linked'
        link.symlink_to(self.app, target_is_directory=True)
        alias = link / self.source.name
        overlay = self.overlay({alias: 'package main\nmain::proc(){}\n'})
        for engine in ['python', 'rust']:
            result = self.invoke(engine, alias, overlay)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(alias.exists())
            self.assertFalse(self.source.exists())

    def test_unsaved_sdk_member_and_body_edits_reach_pure_helpers(self):
        odin = os.environ.get('THOR_ODIN', 'odin')
        sdk = Path(subprocess.check_output([odin, 'root'], text=True).strip()).resolve()
        name = 'thor_audit_unsaved_' + uuid.uuid4().hex
        virtual = sdk / 'core' / 'math' / (name + '.odin')
        self.assertFalse(virtual.exists())
        for increment in [1, 2]:
            text = ('package main\nimport "core:math"\n'
                    f'work::fn(n:int)->int{{return math.{name}(n)}}\n'
                    'VALUE::#run work(7)\n'
                    f'main::proc(){{if VALUE!={7+increment}||work(8)!={8+increment}{{panic("SDK overlay")}}}}\n')
            overlay = self.overlay({
                self.source: text,
                virtual: f'package math\n{name}::proc(n:int)->int{{return n+{increment}}}\n',
            })
            for engine in ['python', 'rust']:
                with self.subTest(engine=engine, increment=increment):
                    result = self.invoke(engine, self.source, overlay, command='run', flags=native.UNCHECKED)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse(virtual.exists())
                    self.assertFalse(self.source.exists())

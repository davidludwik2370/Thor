"""Exercise a staged native toolchain with Python and Cargo unavailable.

Keep the reference launcher intact. Installing the prebuilt Rust executable as
`thor` lets existing project tasks use it without changing their command paths.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import test_rust_compiler as native


@unittest.skipIf(os.name == 'nt', 'POSIX installation and dependency sentinels')
class RustInstallationTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-native-install-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.toolchain = self.root/'toolchain'
        self.toolchain.mkdir()
        self.compiler = self.toolchain/'thor'
        shutil.copy2(native.RUST, self.compiler)
        shutil.copy2(native.ROOT/'mkthor', self.toolchain/'mkthor')
        for name in ('frontend', 'runtime', 'templates'):
            shutil.copytree(native.ROOT/name, self.toolchain/name)
        blocked = self.root/'blocked-tools'
        blocked.mkdir()
        self.log = self.root/'unexpected-dependency.log'
        for name in ('python', 'python3', 'cargo', 'rustc'):
            sentinel = blocked/name
            sentinel.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$THOR_TEST_DEPENDENCY_LOG"\nexit 97\n')
            sentinel.chmod(0o755)
        self.environment = {
            **os.environ,
            'THOR_ROOT': str(self.toolchain),
            'PATH': str(blocked)+os.pathsep+os.environ.get('PATH', ''),
            'THOR_TEST_DEPENDENCY_LOG': str(self.log),
        }
        self.app = self.root/'sample'

    def invoke(self, *arguments, cwd=None):
        result = subprocess.run([str(self.compiler), *arguments], cwd=cwd or self.root,
                                env=self.environment, capture_output=True, text=True,
                                timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else '')
        return result

    def write_program(self):
        self.invoke('new', 'sample')
        (self.app/'sample.thor').write_text('''package main
import "thor:basic" / print
import "core:slice"
twice :: fn(n:int) -> int {
    values := make([]int, 2)
    values[0] = n
    return values[0]*2
}
VALUE :: #run twice(21)
main :: proc() {
    values := [x*x for x in 0..<3]
    defer delete(values)
    view :> values
    copy := slice.clone(view)
    defer delete(copy)
    copy[0] = 9
    if VALUE != 42 || view[0] != 0 || copy[0] != 9 { panic("native installation") }
    number: Integer = 123456789012345678901234567890
    defer delete(number)
    print(f"native {VALUE}: {number}")
}
''')

    def test_native_named_thor_runs_builds_and_uses_the_project_debug_task(self):
        self.write_program()
        expected = 'native 42: 123456789012345678901234567890\n'
        for flags in (['-debug'], native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result = self.invoke('run', '.', *flags, cwd=self.app)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, '')
        self.invoke('build', '.', cwd=self.app)
        executable = self.app/self.app.name
        run = subprocess.run([str(executable)], env=self.environment,
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, expected)
        task = json.loads((self.app/'.vscode/tasks.json').read_text())['tasks'][0]
        self.assertEqual(task['command'], str(self.compiler))
        args = [arg.replace('${workspaceFolder}', str(self.app)) for arg in task['args']]
        self.invoke(*args, cwd=self.app)
        manifest = json.loads((self.app/'.vscode/bin/generated/thor-manifest.json').read_text())
        self.assertEqual(manifest['engine'], 'rust')
        self.assertTrue(list((self.app/'.vscode/bin/generated').rglob('*.map.json')))

    def test_native_named_thor_checks_tests_vets_and_publishes(self):
        self.write_program()
        (self.app/'sample_test.thor').write_text('''package main
import "core:testing"
@(test)
works :: proc(t:^testing.T) { testing.expect(t, VALUE == 42) }
''')
        self.invoke('check', '.', cwd=self.app)
        self.invoke('test', '.', cwd=self.app)
        self.invoke('vet', '.', cwd=self.app)
        self.invoke('publish', '.', cwd=self.app)
        if sys.platform == 'darwin':
            self.assertTrue((self.app/'sample.app/Contents/MacOS/sample').is_file())
        self.assertFalse(self.log.exists())

    def test_mkthor_uses_the_installed_compiler_without_python(self):
        result = subprocess.run([str(self.toolchain/'mkthor'), 'sample',
                                 '--parent', str(self.root), '--no-open'],
                                env=self.environment, cwd=self.root,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((self.app/'sample.thor').read_bytes(),
                         (self.toolchain/'templates/main.thor').read_bytes())
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()

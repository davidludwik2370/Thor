"""Omitted sources select cwd without consuming options or program arguments."""
from tests.test_rust_compiler import compiler_engines
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import unittest

from tests import test_rust_compiler as native


class DefaultSourceTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp

    def write(self, text=None):
        self.source.write_text(text or '''package main
import "core:fmt"
import "core:os"
import "core:testing"
main::proc(){fmt.println("default-source")}
@(test) smoke::proc(t:^testing.T){
    _=t
    err:>os.write_entire_file("test-ran.txt","ok")
    if err!=nil{panic("test marker")}
}
''')

    def invoke(self, engine, command, arguments=(), *, cwd=None, status=0, env=None):
        compiler = native.PYTHON if engine == 'python' else native.RUST
        result = subprocess.run([str(compiler), command, *map(str, arguments)],
                                cwd=cwd or self.app, capture_output=True, text=True,
                                env=env, timeout=90)
        self.assertEqual(result.returncode, status, result.stdout+result.stderr)
        return result

    def test_bare_source_commands_use_cwd_and_preserve_sources(self):
        self.write()
        original = self.source.read_bytes()
        for engine in compiler_engines():
            for command in ('transpile', 'check', 'build', 'run', 'test', 'vet'):
                with self.subTest(engine=engine, command=command):
                    marker = self.app/'test-ran.txt'
                    marker.unlink(missing_ok=True)
                    result = self.invoke(engine, command)
                    if command == 'transpile':
                        self.assertTrue(Path(result.stdout.strip()).exists(), result.stdout)
                    elif command == 'build':
                        executable = self.app/(self.app.name+('.exe' if os.name == 'nt' else ''))
                        launched = subprocess.run([str(executable)], cwd=self.app,
                                                  capture_output=True, text=True, timeout=20)
                        self.assertEqual((launched.returncode, launched.stdout), (0, 'default-source\n'))
                    elif command == 'run':
                        self.assertEqual(result.stdout, 'default-source\n')
                    elif command == 'test':
                        self.assertEqual(marker.read_bytes(), b'ok')
                    elif command == 'vet':
                        self.assertEqual(result.stdout,'')
                        self.assertTrue(not result.stderr or result.stderr.startswith('Unverified analysis:'),result.stderr)
                    else:
                        self.assertEqual((result.stdout, result.stderr), ('', ''))
                    self.assertEqual(self.source.read_bytes(), original)

    def test_flags_and_runtime_arguments_keep_their_meaning_with_or_without_a_source(self):
        self.write('''package main
import "core:fmt"
import "core:os"
main::proc(){for argument in os.args[1:]{fmt.println(argument)}}
''')
        arguments = ['first', '--help', '--output-dir=literal', '--cache-dir=value', '--', 'two words']
        for engine in compiler_engines():
            for source in ([], ['.']):
                for flags in (['-debug'], native.UNCHECKED):
                    with self.subTest(engine=engine, source=source, flags=flags):
                        result = self.invoke(engine, 'run', [
                            *source, '--output-dir', self.root/'generated',
                            '--cache-dir='+str(self.root/'cache'), *flags, '--', *arguments])
                        self.assertEqual(result.stdout.splitlines(), arguments)
                        self.assertEqual(result.stderr, '')

    def test_explicit_paths_override_cwd_before_and_after_options(self):
        self.write()
        (self.root/'invalid.thor').write_text('This is not a Thor project.\n')
        forms = [
            ['-debug', 'input'],
            ['--output-dir', self.root/'generated with spaces', self.source],
            ['--no-cache', '-o:speed', self.app],
            [self.app, '--diagnostic-format=json', '--output-dir='+str(self.root/'generated')],
        ]
        for engine in compiler_engines():
            for arguments in forms:
                with self.subTest(engine=engine, arguments=arguments):
                    result = self.invoke(engine, 'run', arguments, cwd=self.root)
                    self.assertEqual(result.stdout, 'default-source\n')

    def test_usage_errors_still_precede_toolchain_lookup(self):
        environment = {**os.environ, 'THOR_ODIN': 'missing-thor-test-odin'}
        cases = [
            ('new', []), ('new', ['--no-open']), ('unknown-command', []),
            ('build', ['--output-dir']), ('check', ['--run-timeout']),
            ('run', ['--track-allocations']), ('check', ['.', '.']),
            ('build', ['--', 'argument']),
        ]
        for engine in compiler_engines():
            for command, arguments in cases:
                with self.subTest(engine=engine, command=command, arguments=arguments):
                    result = self.invoke(engine, command, arguments, status=2, env=environment)
                    self.assertIn('usage:', result.stderr.lower())
                    self.assertNotIn('Odin compiler not found', result.stderr)

    def test_empty_cwd_does_not_search_a_parent_project(self):
        self.write()
        empty = self.app/'empty'
        empty.mkdir()
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                result = self.invoke(engine, 'build', cwd=empty, status=1)
                self.assertNotIn('expected a source', result.stderr)
                self.assertFalse((empty/('empty.exe' if os.name == 'nt' else 'empty')).exists())

    def test_vet_apply_defaults_to_the_current_project(self):
        text = 'package main\nimport "core:fmt"\nmain::proc(){value:=1;fmt.println(value)}\n'
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                result = self.invoke(engine, 'vet', ['--apply'])
                self.assertIn('Applied 1 correction', result.stdout)
                self.assertEqual(self.source.read_text(), text.replace('value:=', 'value:>'))

    def test_implicit_and_explicit_dot_diagnostics_use_the_same_original_file(self):
        self.write('package main\nmain::proc(){/* 😀 */value:int="wrong";_=value}\n')
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                implicit = self.invoke(engine, 'check', ['-json-errors'], status=1)
                explicit = self.invoke(engine, 'check', ['.', '-json-errors'], status=1)
                errors = json.loads(implicit.stderr)['errors']
                self.assertEqual(errors, json.loads(explicit.stderr)['errors'])
                self.assertTrue(errors)
                self.assertTrue(all(error['pos']['file'] == str(self.source) for error in errors))

    def test_ast_defaults_to_the_same_package_as_explicit_dot(self):
        self.write()
        implicit = self.invoke('rust', 'ast', ['--no-cache'])
        explicit = self.invoke('rust', 'ast', ['.', '--no-cache'])
        self.assertEqual(json.loads(implicit.stdout), json.loads(explicit.stdout))
        self.assertEqual([source['path'] for source in json.loads(implicit.stdout)['sources']],
                         [str(self.source)])

    @unittest.skipUnless(sys.platform == 'darwin', 'Native macOS app publishing')
    def test_publish_defaults_to_cwd_with_separate_option_values(self):
        self.write()
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                result = self.invoke(engine, 'publish', [
                    '--app-name', 'Implicit Source', '--app-type', 'console',
                    '--publish-dir', self.root/engine, '-o:speed'])
                artifact = Path(result.stdout.strip())
                self.assertTrue(artifact.is_dir(), result.stdout)
                metadata = plistlib.loads((artifact/'Contents/Info.plist').read_bytes())
                self.assertEqual(metadata['CFBundleName'], 'Implicit Source')
                self.assertEqual(metadata['CFBundleExecutable'], 'ThorConsole')
                command = artifact/'Contents/Resources/ThorConsole.command'
                launched = subprocess.run(['sh', str(command)], cwd=self.root,
                                          capture_output=True, text=True, timeout=20)
                self.assertEqual((launched.returncode, launched.stdout), (0, 'default-source\n'), launched.stderr)


if __name__ == '__main__':
    unittest.main()

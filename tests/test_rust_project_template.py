"""Native scaffolding matches the reference without requiring Odin or Python."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tests import test_rust_compiler as native

ROOT = native.ROOT


class RustProjectTemplateTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-rust-new-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.template = self.root/'custom.thor'
        self.starter = '// café 😀\r\npackage main\r\nmain::proc(){}\r\n'.encode()
        self.template.write_bytes(self.starter)

    def invoke(self, engine, args, *, cwd=None, env=None):
        command = [str(native.RUST)] if engine == 'rust' else [sys.executable, str(ROOT/'thor')]
        return subprocess.run([*command, 'new', *args], cwd=cwd or self.root,
                              env=env, capture_output=True, text=True, timeout=30)

    def files(self, project):
        return {p.relative_to(project).as_posix(): json.loads(p.read_bytes())
                if p.suffix == '.json' else p.read_bytes()
                for p in project.rglob('*') if p.is_file()}

    def test_default_and_custom_scaffolds_match_reference(self):
        cases = [[], ['--gitignore'], ['--no-open'], ['--odin', 'custom tools/odin'],
                 ['--gitignore', '--no-open']]
        for index, flags in enumerate(cases):
            with self.subTest(flags=flags):
                projects = []
                for engine in ['python', 'rust']:
                    parent = self.root/f'{engine}-{index}'
                    result = self.invoke(engine, ['sample', '--parent', str(parent),
                        '--template', str(self.template), *flags])
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    projects.append(self.files(parent/'sample'))
                self.assertEqual(*projects)
                self.assertEqual(projects[1]['sample.thor'], self.starter)
        result = self.invoke('rust', ['default_project'])
        self.assertEqual(result.returncode, 0, result.stderr)
        files = self.files(self.root/'default_project')
        self.assertEqual(set(files), {'default_project.thor', '.vscode/tasks.json', '.vscode/launch.json'})
        self.assertEqual(files['default_project.thor'], (ROOT/'templates/main.thor').read_bytes())
        task = files['.vscode/tasks.json']['tasks'][0]
        self.assertEqual(Path(task['command']), ROOT/'thor')
        self.assertEqual(task['args'][:4], ['build', '.', '-debug', '-o:none'])
        self.assertEqual(files['.vscode/launch.json']['configurations'][0]['type'], 'thor-lldb')

    def test_creation_and_help_do_not_require_toolchain_or_python(self):
        environment = {**os.environ, 'PATH': str(self.root/'no-tools')}
        result = self.invoke('rust', ['no_tools'], env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/'no_tools/no_tools.thor').read_bytes(),
                         (ROOT/'templates/main.thor').read_bytes())
        self.assertFalse((self.root/'no_tools/.thor-cache').exists())
        result = self.invoke('rust', ['--help'], env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('new', result.stdout)

    def test_invalid_names_existing_projects_and_dangling_symlinks_are_preserved(self):
        for name in ['../escape', '.', '..', 'bad/name', 'two words', 'café', '.hidden']:
            for engine in ['python', 'rust']:
                with self.subTest(name=name, engine=engine):
                    result = self.invoke(engine, [name, '--parent', str(self.root/'invalid')])
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('project name must contain', result.stderr)
                    self.assertFalse((self.root/'invalid').exists())
        for engine in ['python', 'rust']:
            parent = self.root/engine
            parent.mkdir()
            (parent/'existing').symlink_to(parent/'missing', target_is_directory=True)
            result = self.invoke(engine, ['existing', '--parent', str(parent)])
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('already exists', result.stderr)
            self.assertTrue((parent/'existing').is_symlink())
            result = self.invoke(engine, ['sample', '--parent', str(parent)])
            self.assertEqual(result.returncode, 0, result.stderr)
            before = self.files(parent/'sample')
            result = self.invoke(engine, ['sample', '--parent', str(parent)])
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(self.files(parent/'sample'), before)

    def test_invalid_templates_and_options_leave_no_project(self):
        invalid = self.root/'invalid.thor'
        invalid.write_bytes(b'\xff')
        cases = [(['--template', str(invalid)], 'UTF-8 template'),
                 (['--template', str(self.root/'missing')], 'UTF-8 template'),
                 (['--unknown'], 'unrecognized arguments'),
                 (['--open', '--no-open'], 'not allowed'),
                 (['--template'], 'expected one argument')]
        for engine in ['python', 'rust']:
            for flags, message in cases:
                with self.subTest(engine=engine, flags=flags):
                    result = self.invoke(engine, ['fresh', *flags])
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(message, result.stderr)
                    self.assertFalse((self.root/'fresh').exists())

    def test_explicit_odin_paths_and_platform_suffix_warning(self):
        tools = self.root/'custom tools'
        tools.mkdir()
        odin = tools/'odin'
        odin.write_text('#!/bin/sh\nexit 99\n')
        odin.chmod(0o755)
        for engine in ['python', 'rust']:
            parent = self.root/engine
            result = self.invoke(engine, ['sample_linux', '--parent='+str(parent),
                '--odin=custom tools/odin', '--template='+str(self.template)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('filename filtering', result.stderr)
            task = self.files(parent/'sample_linux')['.vscode/tasks.json']['tasks'][0]
            self.assertEqual(Path(task['args'][-1]), odin)

    @unittest.skipIf(os.name == 'nt', 'POSIX command shim')
    def test_open_flag_receives_named_source_and_reports_editor_failure(self):
        tools = self.root/'tools'
        tools.mkdir()
        code = tools/'code'
        code.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$THOR_TEST_EDITOR_LOG"\nexit 7\n')
        code.chmod(0o755)
        for engine in ['python', 'rust']:
            log = self.root/f'{engine}.log'
            environment = {**os.environ, 'PATH': str(tools), 'THOR_TEST_EDITOR_LOG': str(log)}
            parent = self.root/engine
            result = self.invoke(engine, ['plain', '--parent', str(parent)], env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(log.exists())
            result = self.invoke(engine, ['opened', '--parent', str(parent), '--open'], env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('VS Code exited with status 7', result.stderr)
            self.assertEqual(log.read_text().splitlines(), ['--new-window', str(parent/'opened'),
                                                           str(parent/'opened/opened.thor')])

    def test_created_project_runs_on_both_engines(self):
        result = self.invoke('rust', ['runnable', '--template', str(self.template)])
        self.assertEqual(result.returncode, 0, result.stderr)
        project = self.root/'runnable'
        for compiler in [ROOT/'thor', native.RUST]:
            result = subprocess.run([str(compiler), 'run', str(project)], cwd=project,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertEqual(result.stdout, '')

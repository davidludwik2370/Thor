"""Project creation is self-contained, repeatable and preserves existing files."""
import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
import os
import sys
from unittest.mock import patch

from thorlang import project_template

ROOT=Path(__file__).resolve().parents[1]


class ProjectTemplateTests(unittest.TestCase):
    def test_debug_build_identity_is_invalidated_by_a_later_check(self):
        with tempfile.TemporaryDirectory(prefix='thor-debug-identity-') as temporary:
            root=Path(temporary)
            source=root/'main.thor';output=root/'generated'
            source.write_text('package main\nmain::proc(){value:=1;assert(value==1)}\n')
            args=[str(source),'-debug','-o:none','--output-dir',str(output)]
            result=subprocess.run([str(ROOT/'thor'),'build',*args],cwd=root,capture_output=True,text=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            manifest=json.loads((output/'thor-manifest.json').read_text())
            self.assertEqual({Path(path).name for path in manifest['debug_roots']},{'runtime','base','core','vendor'})
            executable=Path(manifest['debug_program']['path'])
            self.assertEqual(manifest['debug_program']['sha256'],hashlib.sha256(executable.read_bytes()).hexdigest())
            source.write_text('package main\nmain::proc(){value:=2;assert(value==2)}\n')
            result=subprocess.run([str(ROOT/'thor'),'check',*[arg for arg in args if arg!='-o:none']],capture_output=True,text=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertNotIn('debug_program',json.loads((output/'thor-manifest.json').read_text()))
            self.assertTrue(executable.exists())

    def test_project_has_only_named_source_and_run_debug_configuration(self):
        with tempfile.TemporaryDirectory(prefix='mkthor-test-') as temporary:
            parent=Path(temporary)/'parent with spaces'
            result=subprocess.run([str(ROOT/'mkthor'),'example','--parent',str(parent),'--no-open'],
                capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            project=parent/'example'
            source=(project/'example.thor').read_bytes()
            self.assertEqual(source,(ROOT/'templates/main.thor').read_bytes())
            self.assertEqual({p.relative_to(project).as_posix() for p in project.rglob('*') if p.is_file()},
                             {'example.thor','.vscode/tasks.json','.vscode/launch.json'})
            config=json.loads((project/'.vscode/launch.json').read_text())['configurations'][0]
            tasks=json.loads((project/'.vscode/tasks.json').read_text())['tasks']
            task=next(t for t in tasks if t['label']=='build-thor-debug')
            self.assertEqual(config['type'],'thor-lldb')
            self.assertEqual(config['preLaunchTask'],task['label'])
            self.assertIn('-debug',task['args'])
            self.assertIn('-o:none',task['args'])
            self.assertEqual(Path(task['command']),ROOT/'thor')
            self.assertEqual([t['label'] for t in tasks],['build-thor-debug'])
            again=subprocess.run([str(ROOT/'mkthor'),'example','--parent',str(parent),'--no-open'],capture_output=True,text=True)
            self.assertNotEqual(again.returncode,0)
            self.assertIn('already exists',again.stderr)
            self.assertEqual((project/'example.thor').read_bytes(),source)
            check=subprocess.run([str(ROOT/'thor'),'run',str(project)],cwd=project,capture_output=True,text=True,timeout=40)
            self.assertEqual(check.returncode,0,check.stdout+check.stderr)

    def test_platform_suffix_preserves_requested_filename_and_reports_native_filtering(self):
        with tempfile.TemporaryDirectory(prefix='mkthor-suffix-') as temporary:
            result=subprocess.run([str(ROOT/'mkthor'),'example_linux','--parent',temporary,'--no-open'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            project=Path(temporary)/'example_linux'
            self.assertTrue((project/'example_linux.thor').is_file())
            self.assertIn('filename filtering',result.stderr)

    def test_invalid_names_cannot_escape_parent_or_replace_symlinks(self):
        with tempfile.TemporaryDirectory(prefix='mkthor-test-') as temporary:
            parent=Path(temporary)
            for name in ['../escape','.hidden','/absolute','two words','a/b']:
                result=subprocess.run([str(ROOT/'mkthor'),name,'--parent',temporary,'--no-open'],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
            self.assertEqual(list(parent.iterdir()),[])
            (parent/'existing').symlink_to(parent/'missing')
            result=subprocess.run([str(ROOT/'mkthor'),'existing','--parent',temporary,'--no-open'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertTrue((parent/'existing').is_symlink())

    def test_new_creates_in_current_directory_without_odin_or_extra_files(self):
        with tempfile.TemporaryDirectory(prefix='thor-new-') as temporary:
            root=Path(temporary)
            result=subprocess.run([sys.executable,str(ROOT/'thor-python'),'new','my-project'],cwd=root,
                env={**os.environ,'PATH':str(root/'no-tools')},capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            project=root/'my-project'
            self.assertEqual({p.relative_to(project).as_posix() for p in project.rglob('*') if p.is_file()},
                             {'my-project.thor','.vscode/tasks.json','.vscode/launch.json'})
            self.assertFalse((project/'.thor-cache').exists())

    def test_custom_template_bytes_gitignore_and_explicit_odin(self):
        with tempfile.TemporaryDirectory(prefix='thor-new-') as temporary:
            root=Path(temporary)
            template=root/'custom source.thor'
            content='// 😀 custom\r\npackage main\r\nmain::proc(){}\r\n'.encode()
            template.write_bytes(content)
            odin=str(root/'tools with spaces/odin')
            result=subprocess.run([str(ROOT/'thor'),'new','custom','--parent',str(root/'parent with spaces'),
                '--template',str(template),'--gitignore','--odin',odin],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            project=root/'parent with spaces/custom'
            self.assertEqual((project/'custom.thor').read_bytes(),content)
            self.assertIn('/custom.exe',(project/'.gitignore').read_text())
            self.assertIn('.thor-build/',(project/'.gitignore').read_text())
            self.assertIn('.thor-rust-build/',(project/'.gitignore').read_text())
            for task in json.loads((project/'.vscode/tasks.json').read_text())['tasks']:
                self.assertEqual(task['args'][-2:],['--odin',odin])

    def test_bad_template_and_cli_inputs_leave_no_project(self):
        with tempfile.TemporaryDirectory(prefix='thor-new-') as temporary:
            root=Path(temporary)
            invalid=root/'invalid.thor';invalid.write_bytes(b'\xff')
            for flags in [['--template',str(root/'missing')],['--template',str(invalid)],
                          ['--unknown'],['--open','--no-open']]:
                with self.subTest(flags=flags):
                    result=subprocess.run([str(ROOT/'thor'),'new','fresh',*flags],cwd=root,capture_output=True,text=True)
                    self.assertNotEqual(result.returncode,0)
                    self.assertFalse((root/'fresh').exists())

    def test_relative_odin_path_is_anchored_to_the_creation_directory(self):
        with tempfile.TemporaryDirectory(prefix='thor-new-') as temporary:
            root=Path(temporary)
            compiler=root/'custom tools/odin'
            compiler.parent.mkdir()
            compiler.write_text('#!/bin/sh\nexit 99\n')
            compiler.chmod(0o755)
            result=subprocess.run([str(ROOT/'thor'),'new','example','--odin','custom tools/odin'],
                                  cwd=root,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            for task in json.loads((root/'example/.vscode/tasks.json').read_text())['tasks']:
                self.assertEqual(Path(task['args'][-1]),compiler.resolve())

    def test_new_default_does_not_open_and_open_flag_opens_named_source(self):
        with tempfile.TemporaryDirectory(prefix='thor-new-') as temporary:
            root=Path(temporary)
            with patch.object(project_template,'open_project') as opened:
                self.assertEqual(project_template.main(['plain','--parent',str(root)]),0)
                opened.assert_not_called()
                self.assertEqual(project_template.main(['opened','--parent',str(root),'--open']),0)
                opened.assert_called_once_with(root.resolve()/'opened')
            with patch.object(project_template.shutil,'which',return_value='/tools/code'),patch.object(project_template.subprocess,'run') as run:
                run.return_value.returncode=0
                project_template.open_project(root/'opened')
                self.assertEqual(run.call_args.args[0],['/tools/code','--new-window',str(root/'opened'),str(root/'opened/opened.thor')])

    def test_mkthor_delegates_to_new_with_thor_parent_and_editor_opening(self):
        from thorlang import driver
        with patch.object(driver,'main',return_value=0) as new:
            self.assertEqual(project_template.mkthor_main(['example']),0)
            new.assert_called_once_with(['new','--parent',str(Path.home()/'Code/Thor'),'--open','example'])
            new.reset_mock()
            project_template.mkthor_main(['example','--no-open','--parent','/tmp/elsewhere'])
            self.assertNotIn('--open',new.call_args.args[0])
            self.assertEqual(new.call_args.args[0][-2:],['--parent','/tmp/elsewhere'])

    def test_new_help_is_available_without_compiling(self):
        for args in [['new','--help'],['--help']]:
            result=subprocess.run([str(ROOT/'thor'),*args],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('new',result.stdout)

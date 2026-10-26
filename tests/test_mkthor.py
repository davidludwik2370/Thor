"""Project creation is self-contained, repeatable and preserves existing files."""
import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ProjectTemplateTests(unittest.TestCase):
    def test_debug_build_identity_is_invalidated_by_a_later_check(self):
        with tempfile.TemporaryDirectory(prefix='thor-debug-identity-') as temporary:
            root=Path(temporary)
            source=root/'main.thor';output=root/'generated'
            source.write_text('package main\nmain::proc(){value:=1;assert(value==1)}\n')
            args=[str(source),'-debug','-o:none','--output-dir',str(output)]
            result=subprocess.run([str(ROOT/'thor'),'build',*args],capture_output=True,text=True,timeout=40)
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

    def test_project_has_build_launch_tls_and_original_source(self):
        with tempfile.TemporaryDirectory(prefix='mkthor-test-') as temporary:
            parent=Path(temporary)/'parent with spaces'
            result=subprocess.run([str(ROOT/'mkthor'),'example','--parent',str(parent),'--no-open'],
                capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            project=parent/'example'
            source=(project/'main.thor').read_text()
            self.assertIn('double :: fn',source)
            config=json.loads((project/'.vscode/launch.json').read_text())['configurations'][0]
            task=json.loads((project/'.vscode/tasks.json').read_text())['tasks'][0]
            self.assertEqual(config['type'],'thor-lldb')
            self.assertEqual(config['preLaunchTask'],task['label'])
            self.assertIn('-debug',task['args'])
            self.assertIn('-o:none',task['args'])
            self.assertEqual(Path(task['command']),ROOT/'thor')
            settings=json.loads((project/'.vscode/settings.json').read_text())
            self.assertEqual(Path(settings['tls.compiler.path']),ROOT/'thor')
            self.assertIn('.vscode/bin/',(project/'.gitignore').read_text())
            again=subprocess.run([str(ROOT/'mkthor'),'example','--parent',str(parent),'--no-open'],capture_output=True,text=True)
            self.assertNotEqual(again.returncode,0)
            self.assertIn('already exists',again.stderr)
            self.assertEqual((project/'main.thor').read_text(),source)

    def test_project_name_does_not_turn_starter_into_a_platform_specific_file(self):
        with tempfile.TemporaryDirectory(prefix='mkthor-suffix-') as temporary:
            result=subprocess.run([str(ROOT/'mkthor'),'example_linux','--parent',temporary,'--no-open'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            project=Path(temporary)/'example_linux'
            self.assertTrue((project/'main.thor').is_file())
            result=subprocess.run([str(ROOT/'thor'),'check',str(project),'--output-dir',str(Path(temporary)/'generated')],capture_output=True,text=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

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

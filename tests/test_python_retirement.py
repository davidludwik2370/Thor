"""Frozen compiler integrity and independent native test/build entry points."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests import test_rust_compiler as native
from tools.check_python_reference import differences
from tools.test_compiler import native_only


class PythonRetirementTests(unittest.TestCase):
    def test_frozen_source_manifest_matches(self):
        self.assertEqual(differences(), [])

    def test_manifest_detects_changes_additions_and_missing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'thorlang').mkdir();(root/'docs').mkdir()
            source=root/'thorlang/reference.py';source.write_text('value = 1\n')
            launcher=root/'thor-python';launcher.write_text('# historical\n')
            recorded={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (source,launcher)}
            (root/'docs/python-reference-frozen.json').write_text(json.dumps({'sha256':recorded}))
            self.assertEqual(differences(root),[])
            source.write_text('value = 2\n');self.assertEqual(differences(root),['thorlang/reference.py'])
            source.unlink();self.assertEqual(differences(root),['thorlang/reference.py'])
            (root/'thorlang/new.py').write_text('')
            self.assertEqual(differences(root),['thorlang/new.py','thorlang/reference.py'])

    def test_reference_execution_is_explicit(self):
        with patch.dict(os.environ,{'THOR_TEST_PYTHON_REFERENCE':'0'}):
            self.assertEqual(native.compiler_engines(),('rust',))
        with patch.dict(os.environ,{'THOR_TEST_PYTHON_REFERENCE':'1'}):
            self.assertEqual(native.compiler_engines(),('python','rust'))
        with native_only():
            with self.assertRaisesRegex(RuntimeError,'frozen Python compiler'):
                subprocess.Popen([str(native.PYTHON),'--version'])

    def test_runtime_preparation_does_not_import_the_python_compiler(self):
        with tempfile.TemporaryDirectory() as directory:
            trap=Path(directory)/'sitecustomize.py'
            trap.write_text('''import sys
class Block:
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='thorlang' or fullname.startswith('thorlang.'):
   raise ImportError('frozen compiler imported by production runtime preparation')
sys.meta_path.insert(0,Block())
''')
            result=subprocess.run([sys.executable,str(native.ROOT/'tools/build_runtime.py'),
                '--compiler',str(native.RUST),'--odin',os.environ.get('THOR_ODIN','odin')],
                env={**os.environ,'PYTHONPATH':directory},capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            view=Path(result.stdout.strip())
            self.assertTrue(view.is_dir(),result.stdout)
            self.assertTrue(view.name.startswith('runtime-rust-'),view)


if __name__=='__main__':
    unittest.main()

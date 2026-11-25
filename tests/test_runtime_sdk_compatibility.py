"""RTTI consumers work with the installed SDK and an optional second revision."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.test_thor import ROOT
from tests.native_paths import EXE_SUFFIX


class RuntimeSdkCompatibilityTests(unittest.TestCase):
    def test_checked_and_unchecked_rtti_consumers(self):
        compilers = [os.environ.get('THOR_ODIN', shutil.which('odin')),
                     os.environ.get('THOR_TEST_SECOND_ODIN')]
        compilers = list(dict.fromkeys(str(Path(path).resolve()) for path in compilers if path))
        if not compilers:
            self.skipTest('Odin compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='thor-rtti-sdk-') as temporary:
            for compiler in compilers:
                for package in ['pure', 'eval', 'graph', 'hash']:
                    for flags in [[], ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']]:
                        with self.subTest(compiler=compiler, package=package, flags=flags):
                            result = subprocess.run([compiler, 'test', str(ROOT/'runtime'/package),
                                '-collection:thor='+str(ROOT/'runtime'), '-thread-count:1',
                                '-define:ODIN_TEST_THREADS=1', '-warnings-as-errors',
                                '-out:'+str(Path(temporary)/('tests'+EXE_SUFFIX)), *flags],
                                capture_output=True, text=True, timeout=90)
                            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                            self.assertRegex(result.stdout+result.stderr, r'Finished \d+ tests')

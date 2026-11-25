"""Run the playground table tests using Odin directly, including memory checks."""
from tests.native_paths import EXE_SUFFIX
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OdinTableTests(unittest.TestCase):
    def test_native_odin_table_with_allocation_tracking(self):
        odin = os.environ.get('THOR_ODIN', 'odin')
        if not shutil.which(odin):
            self.skipTest('Odin is required')
        # Isolate the real library from unrelated playground main/spec edits.
        with tempfile.TemporaryDirectory(prefix='odin-table-tests-') as temporary:
            directory = Path(temporary)
            shutil.copyfile(ROOT / 'odin-playground' / 'Library.odin', directory / 'Library.odin')
            shutil.copyfile(ROOT / 'tests' / 'odin' / 'table_test.odin', directory / 'table_test.odin')
            result = subprocess.run([odin, 'test', str(directory),
                f'-out:{directory / ("table-tests"+EXE_SUFFIX)}', '-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'],
                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

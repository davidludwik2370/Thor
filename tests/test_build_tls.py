"""A TLS rebuild must leave the published server available until success."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT=Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name=='nt','The test compiler uses a Unix executable script')
class TLSBuildTests(unittest.TestCase):
    def test_published_server_survives_in_progress_and_failed_rebuilds(self):
        with tempfile.TemporaryDirectory(prefix='thor-tls-build-') as temporary:
            root=Path(temporary)
            compiler=root/'odin'
            compiler.write_text('#!'+sys.executable+'\n'+'''
from pathlib import Path
import sys,time
root=Path(__file__).parent
output=Path(next(flag[5:] for flag in sys.argv if flag.startswith('-out:')))
output.unlink(missing_ok=True)
output.write_text('new server')
(root/'started').write_text('ready')
while not (root/'release').exists():time.sleep(.01)
raise SystemExit(1 if (root/'fail').exists() else 0)
''')
            compiler.chmod(0o755)
            # Run the actual build script with an isolated installation root.
            runner=root/'run.py'
            runner.write_text('''import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('build_tls',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.ROOT=Path(sys.argv[2])
sys.argv=[sys.argv[0],'--odin',str(module.ROOT/'odin')]
raise SystemExit(module.main())
''')
            (root/'tls/bin').mkdir(parents=True)
            published=root/'tls/bin/tls'
            for fail in [False,True]:
                with self.subTest(fail=fail):
                    published.write_text('old server')
                    for name in ['started','release','fail']:(root/name).unlink(missing_ok=True)
                    if fail:(root/'fail').touch()
                    process=subprocess.Popen([sys.executable,str(runner),str(ROOT/'tools/build_tls.py'),str(root)],
                                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                    try:
                        deadline=time.monotonic()+10
                        while not (root/'started').exists() and process.poll() is None and time.monotonic()<deadline:
                            time.sleep(.01)
                        self.assertTrue((root/'started').exists())
                        self.assertEqual(published.read_text(),'old server')
                        (root/'release').touch()
                        stdout,stderr=process.communicate(timeout=10)
                        self.assertEqual(process.returncode,1 if fail else 0,stdout+stderr)
                        self.assertEqual(published.read_text(),'old server' if fail else 'new server')
                        self.assertEqual(list((root/'tls/bin').glob('tls-build-*')),[])
                    finally:
                        if process.poll() is None:process.kill()
                        process.communicate()


if __name__=='__main__':unittest.main()

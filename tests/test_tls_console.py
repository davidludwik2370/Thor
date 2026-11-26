"""Windows checker cancellation must not interrupt its caller's console."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=='nt','Windows console control-event regression')
class CheckerConsoleTests(unittest.TestCase):
    def test_cancellation_preserves_the_callers_shared_console(self):
        # Keep a regression in this behavior from cancelling the outer suite.
        # A real console is important: detached GUI processes mask the bug.
        script='''import signal,unittest
signals=[]
signal.signal(signal.SIGINT,lambda *args:signals.append('interrupt'))
names=['tests.test_tls.TLSTests.test_shutdown_cancels_inflight_compiler_check',
       'tests.test_tls.TLSTests.test_superseded_silent_check_is_cancelled_before_latest_snapshot']
suite=unittest.defaultTestLoader.loadTestsFromNames(names)
result=unittest.TextTestRunner(verbosity=2).run(suite)
if signals:raise SystemExit('Compiler cancellation interrupted its caller: '+str(signals))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,capture_output=True,text=True,
            creationflags=subprocess.CREATE_NEW_CONSOLE,startupinfo=startup,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

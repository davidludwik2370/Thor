"""The desktop validator must restore discovery state even when editor tests fail."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.test_install_desktop import mac_editor_registration, owned_editor_pids


class DesktopRegistrationTests(unittest.TestCase):
    def test_cleanup_includes_only_owned_editor_and_debugger_trees(self):
        rows = '''
100 1 /Applications/Visual Studio Code.app/Contents/MacOS/Code --user-data-dir /tmp/test/home/code-data
101 100 Code Helper --type=renderer
102 101 helper
200 1 /Applications/Visual Studio Code.app/Contents/MacOS/Code --user-data-dir /Users/david/Library/Code
201 200 Code Helper --type=renderer
300 1 /tmp/test/home/.vscode/extensions/vadimcn.vscode-lldb-1/adapter/codelldb --connect 12
301 300 /usr/libexec/debugserver
400 1 /tmp/test/project/.vscode/bin/debug
500 1 /System/SecurityAgent
600 1 /Applications/chrome_crashpad_handler --database=/tmp/test/home/code-data/Crashpad
700 1 /usr/bin/python3 tools/test_install_desktop.py --output /tmp/test
'''
        self.assertEqual(owned_editor_pids('/tmp/test', rows), [100, 101, 102, 300, 301, 400, 600])

    def test_cleanup_does_not_match_neighboring_temporary_directory(self):
        rows = '100 1 /tmp/test-other/project/.vscode/bin/debug\n200 1 /tmp/test-other/home/.vscode/extensions/x/adapter/codelldb --connect 1'
        self.assertEqual(owned_editor_pids('/tmp/test', rows), [])

    def fixture(self, root):
        actual, private = root/'actual', root/'private'
        target = actual/'Library/Application Support/Thor/installation.json'
        source = private/'Library/Application Support/Thor/installation.json'
        target.parent.mkdir(parents=True); source.parent.mkdir(parents=True)
        target.write_bytes(b'{"original":"preserved"}\n')
        target.chmod(0o600)
        source.write_bytes(b'{"test":"temporary"}\n')
        return actual, private, target, source

    def test_original_record_restored_after_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);actual,private,target,source=self.fixture(root)
                os.utime(target,ns=(1500000000000000000,1500000000000000000))
                info=target.stat();before=target.read_bytes()
                with patch.object(Path,'home',return_value=actual):
                    try:
                        with mac_editor_registration(private,root):
                            self.assertEqual(target.read_bytes(),source.read_bytes())
                            backup=next(root.glob('macos-record-recovery-*'))/'original.json'
                            self.assertEqual(backup.read_bytes(),before)
                            if fail:raise ValueError('intentional test failure')
                    except ValueError:
                        if not fail:raise
                self.assertEqual(target.read_bytes(),before)
                self.assertEqual(target.stat().st_mtime_ns,info.st_mtime_ns)
                self.assertEqual(list(root.glob('macos-record-recovery-*')),[])

    def test_concurrent_user_edit_is_preserved_with_recovery_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);actual,private,target,source=self.fixture(root)
            before=target.read_bytes()
            with patch.object(Path,'home',return_value=actual):
                with self.assertRaisesRegex(RuntimeError,'concurrent changes were preserved'):
                    with mac_editor_registration(private,root):target.write_bytes(b'user changed this')
            self.assertEqual(target.read_bytes(),b'user changed this')
            backup=next(root.glob('macos-record-recovery-*'))/'original.json'
            self.assertEqual(backup.read_bytes(),before)

    def test_absent_registration_remains_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);actual,private,target,source=self.fixture(root);target.unlink()
            with patch.object(Path,'home',return_value=actual):
                with mac_editor_registration(private,root):self.assertEqual(target.read_bytes(),source.read_bytes())
            self.assertFalse(target.exists())


if __name__=='__main__':unittest.main()

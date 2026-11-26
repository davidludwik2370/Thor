"""The desktop validator must restore discovery state even when editor tests fail."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.test_install_desktop import mac_editor_registration


class DesktopRegistrationTests(unittest.TestCase):
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

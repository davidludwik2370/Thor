import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tools.windows_validation import Validation, source_identity


class WindowsValidationTests(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-windows-runner-')
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        source=root/'example.thor'
        source.write_bytes(b'package main\n')
        (root/'snapshot-manifest.json').write_text(json.dumps({'files':{
            source.name:hashlib.sha256(source.read_bytes()).hexdigest()}}))
        return root,source

    def test_source_snapshot_rejects_incomplete_transfers(self):
        root,source=self.fixture()
        before=source_identity(root)
        self.assertEqual(len(before),64)
        source.write_bytes(b'changed\n')
        with self.assertRaisesRegex(RuntimeError,'hash mismatch'):
            source_identity(root)

    def test_result_records_real_exit_status_and_stdout_oracle(self):
        root,_=self.fixture()
        run=Validation(root,'probe')
        run.command('success',[sys.executable,'-c','print("ok")'],expected='ok')
        self.assertTrue(run.records[0]['success'])
        with self.assertRaisesRegex(RuntimeError,'failed'):
            run.command('wrong-output',[sys.executable,'-c','print("wrong")'],expected='ok')
        self.assertFalse(run.records[1]['success'])
        saved=json.loads((root/'windows-results/probe.json').read_text())
        self.assertEqual(saved['commands'][1]['exit_code'],0)
        self.assertFalse(saved['commands'][1]['success'])

    def test_manifest_cannot_read_outside_the_snapshot(self):
        root,_=self.fixture()
        (root/'snapshot-manifest.json').write_text(json.dumps({'files':{'../outside':'unused'}}))
        with self.assertRaisesRegex(RuntimeError,'escapes the workspace'):
            source_identity(root)

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def test_failed_stage_cleans_generated_output_and_records_real_failure(self):
        root,source=self.fixture()
        run=Validation(root,'probe')
        generated=root/'.thor-cache/sdk-parser/old.bin'
        def stage():
            generated.parent.mkdir(parents=True)
            generated.write_bytes(b'obsolete cache')
            run.command('failure',[sys.executable,'-c','print("test failed");raise SystemExit(3)'])
        with patch.object(run,'run_stage',side_effect=stage):
            with self.assertRaisesRegex(RuntimeError,'failure failed'):
                run.run()
        saved=json.loads((root/'windows-results/probe.json').read_text())
        self.assertFalse(generated.exists());self.assertTrue(source.exists())
        self.assertFalse(saved['success']);self.assertEqual(saved['commands'][0]['exit_code'],3)
        self.assertEqual([r['phase'] for r in saved['maintenance']],['before','after'])
        self.assertIn('test failed',(root/'windows-results/probe-failure.log').read_text())

    def test_successful_stage_removes_native_outputs_but_keeps_source_and_log(self):
        root,source=self.fixture();run=Validation(root,'probe')
        executable=root/'windows-results/generated.exe'
        def stage():
            executable.write_bytes(b'MZdisposable')
            run.command('success',[sys.executable,'-c','print("ok")'],expected='ok')
            run.packet['success']=True;run.write()
        with patch.object(run,'run_stage',side_effect=stage):
            run.run()
        saved=json.loads((root/'windows-results/probe.json').read_text())
        self.assertFalse(executable.exists());self.assertTrue(source.exists())
        self.assertTrue(saved['success'])
        self.assertEqual((root/'windows-results/probe-success.log').read_text().strip(),'ok')

    def test_interrupted_stage_still_cleans_and_records_the_interrupt(self):
        root,_=self.fixture();run=Validation(root,'probe')
        generated=root/'.thor-cache/sdk-parser/old.bin'
        def stage():
            generated.parent.mkdir(parents=True);generated.write_bytes(b'cache')
            raise KeyboardInterrupt('cancelled')
        with patch.object(run,'run_stage',side_effect=stage):
            with self.assertRaises(KeyboardInterrupt):run.run()
        saved=json.loads((root/'windows-results/probe.json').read_text())
        self.assertFalse(generated.exists());self.assertFalse(saved['success'])
        self.assertEqual(saved['error'],'cancelled')

    def test_cleanup_error_preserves_the_original_test_failure(self):
        root,_=self.fixture();run=Validation(root,'probe')
        with patch('tools.windows_validation.prune',side_effect=[{'removed':[]},RuntimeError('locked output')]), \
                patch.object(run,'run_stage',side_effect=RuntimeError('test failed')):
            with self.assertRaisesRegex(RuntimeError,'test failed'):run.run()
        saved=json.loads((root/'windows-results/probe.json').read_text())
        self.assertEqual(saved['error'],'test failed')
        self.assertEqual(saved['maintenance'][-1]['error'],'locked output')

    def test_cleanup_error_cannot_leave_a_false_success_verdict(self):
        root,_=self.fixture();run=Validation(root,'probe')
        def stage():run.packet.update(success=True);run.write()
        with patch('tools.windows_validation.prune',side_effect=[{'removed':[]},RuntimeError('locked output')]), \
                patch.object(run,'run_stage',side_effect=stage):
            with self.assertRaisesRegex(RuntimeError,'locked output'):run.run()
        saved=json.loads((root/'windows-results/probe.json').read_text())
        self.assertFalse(saved['success']);self.assertIn('Cleanup failed',saved['error'])

    def test_unwritable_report_does_not_skip_cleanup_or_replace_test_failure(self):
        root,source=self.fixture();run=Validation(root,'probe')
        generated=root/'.thor-cache/sdk-parser/old.bin'
        def stage():
            generated.parent.mkdir(parents=True);generated.write_bytes(b'cache')
            raise RuntimeError('original test failure')
        original_write=run.write
        def write():
            if generated.exists():raise OSError('disk full')
            original_write()
        with patch.object(run,'run_stage',side_effect=stage), \
                patch.object(run,'write',side_effect=write):
            with self.assertRaisesRegex(RuntimeError,'original test failure'):run.run()
        run.write()
        saved=json.loads((root/'windows-results/probe.json').read_text())
        self.assertFalse(generated.exists());self.assertTrue(source.exists())
        self.assertEqual(saved['report_error'],'disk full')
        self.assertEqual(saved['error'],'original test failure')

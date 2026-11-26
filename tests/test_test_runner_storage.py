"""The real suite runner releases generated storage on all exit paths."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from tools import storage, test_compiler


class TestRunnerStorageTests(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-test-runner-storage-')
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        (root/'tests').mkdir();(root/'tests/test_probe.py').write_text('# loader fixture\n')
        source=root/'source.thor';source.write_bytes(b'package main\n')
        (root/'snapshot-manifest.json').write_text(json.dumps({'files':{
            'source.thor':hashlib.sha256(source.read_bytes()).hexdigest()}}))
        return root,source,root/'results/report.json',root/'.thor-cache/sdk-parser/old.bin'

    def invoke(self, root, output, operation, *, runner_error=None, prune_error=None, write_error=False, recover_write=False):
        class Probe(unittest.TestCase):
            def test_probe(self):operation()
        module=types.ModuleType('storage_probe');module.Probe=Probe
        stream=io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(test_compiler,'ROOT',root))
            stack.enter_context(patch.object(test_compiler,'inventory',return_value={'modules':{},'tests':{}}))
            stack.enter_context(patch.object(test_compiler.importlib,'import_module',return_value=module))
            stack.enter_context(patch.object(test_compiler,'native_only',contextlib.nullcontext))
            stack.enter_context(patch.object(sys,'argv',['test_compiler.py','--output',str(output)]))
            stack.enter_context(contextlib.redirect_stdout(stream))
            stack.enter_context(contextlib.redirect_stderr(stream))
            if runner_error is not None:
                def interrupted(*args):operation();raise runner_error
                stack.enter_context(patch.object(test_compiler.unittest.TextTestRunner,'run',side_effect=interrupted))
            if prune_error is not None:
                stack.enter_context(patch.object(storage,'prune',side_effect=[{'removed':[]},prune_error]))
            if write_error:
                original=Path.write_text
                writes=0
                def write(path,*args,**kwargs):
                    nonlocal writes
                    if path.name=='report.json.tmp':
                        writes+=1
                        if not recover_write or writes==1:raise OSError('disk full')
                    return original(path,*args,**kwargs)
                stack.enter_context(patch.object(Path,'write_text',write))
            return test_compiler.main()

    def generated(self, path):
        path.parent.mkdir(parents=True);path.write_bytes(b'generated cache')

    def test_success_saves_results_and_cleans_generated_storage(self):
        root,source,output,generated=self.fixture()
        code=self.invoke(root,output,lambda:self.generated(generated))
        saved=json.loads(output.read_text())
        self.assertEqual(code,0);self.assertTrue(saved['success'])
        self.assertFalse(generated.exists());self.assertTrue(source.exists())
        self.assertEqual(saved['cleanup_removed'],[{'path':str(Path('.thor-cache/sdk-parser'))}])

    def test_failed_tests_keep_failure_details_and_release_generated_storage(self):
        root,source,output,generated=self.fixture()
        def operation():
            self.generated(generated)
            raise AssertionError('intentional regression verdict')
        code=self.invoke(root,output,operation)
        saved=json.loads(output.read_text())
        self.assertEqual(code,1);self.assertFalse(saved['success'])
        self.assertIn('intentional regression verdict',saved['failures'][0]['detail'])
        self.assertFalse(generated.exists());self.assertTrue(source.exists())

    def test_interrupt_saves_an_aborted_report_and_releases_generated_storage(self):
        root,source,output,generated=self.fixture()
        with self.assertRaisesRegex(KeyboardInterrupt,'cancelled'):
            self.invoke(root,output,lambda:self.generated(generated),runner_error=KeyboardInterrupt('cancelled'))
        saved=json.loads(output.read_text())
        self.assertTrue(saved['aborted']);self.assertFalse(saved['success'])
        self.assertIn('KeyboardInterrupt',saved['error'])
        self.assertFalse(generated.exists());self.assertTrue(source.exists())

    def test_report_write_failure_does_not_skip_cleanup_or_return_success(self):
        root,source,output,generated=self.fixture()
        code=self.invoke(root,output,lambda:self.generated(generated),write_error=True)
        self.assertEqual(code,1);self.assertFalse(generated.exists());self.assertTrue(source.exists())

    def test_cleanup_failure_is_reported_and_cannot_return_false_success(self):
        root,source,output,_=self.fixture()
        code=self.invoke(root,output,lambda:None,prune_error=RuntimeError('locked output'))
        saved=json.loads(output.read_text())
        self.assertEqual(code,1);self.assertFalse(saved['success'])
        self.assertEqual(saved['cleanup_error'],'locked output');self.assertTrue(source.exists())

    def test_recovered_report_write_records_the_initial_failure(self):
        root,source,output,generated=self.fixture()
        code=self.invoke(root,output,lambda:self.generated(generated),write_error=True,recover_write=True)
        saved=json.loads(output.read_text())
        self.assertEqual(code,1);self.assertFalse(saved['success'])
        self.assertIn('before cleanup',saved['report_error'])
        self.assertFalse(generated.exists());self.assertTrue(source.exists())

    def test_cleanup_failure_does_not_replace_an_original_interrupt(self):
        root,_,output,_=self.fixture()
        with self.assertRaisesRegex(KeyboardInterrupt,'cancelled'):
            self.invoke(root,output,lambda:None,runner_error=KeyboardInterrupt('cancelled'),
                        prune_error=RuntimeError('locked output'))
        saved=json.loads(output.read_text())
        self.assertTrue(saved['aborted']);self.assertEqual(saved['cleanup_error'],'locked output')


if __name__=='__main__':unittest.main()

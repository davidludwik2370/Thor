"""Frontend cache observes content, parser configuration and binary identity."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from thorlang import parsecache


class ParseCacheTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-parser-cache-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.frontend=self.root/'frontend'
        self.frontend.write_bytes(b'parser binary')
        self.args=[str(self.frontend),str(self.root/'main.thor'),'--stdin']
        self.output=b'{"nodes":[],"excluded":true}\n'

    def produce(self, args=None, raw=b'package main', enabled=True):
        args=self.args if args is None else args
        completed=subprocess.CompletedProcess(args,0,self.output,b'')
        with patch.object(parsecache.subprocess,'run',return_value=completed) as run:
            result=parsecache.run(args,raw,enabled=enabled)
        self.assertEqual(result.stdout,self.output)
        return run.call_count

    def test_unchanged_content_reuses_frontend_output(self):
        self.assertEqual(self.produce(),1)
        self.assertEqual(self.produce(),0)

    def test_text_flags_file_identity_and_frontend_binary_invalidate(self):
        self.produce()
        self.assertEqual(self.produce(raw=b'package demo'),1)
        self.assertEqual(self.produce(args=[*self.args,'--odin']),1)
        self.assertEqual(self.produce(args=[*self.args,'--test']),1)
        self.assertEqual(self.produce(args=[*self.args,'--target-os=linux']),1)
        self.assertEqual(self.produce(args=[str(self.frontend),str(self.root/'other.thor'),'--stdin']),1)
        self.frontend.write_bytes(b'updated parser binary')
        self.assertEqual(self.produce(),1)

    def test_corruption_bypass_and_clear_recompute(self):
        self.produce()
        packet=next((self.root/'parse-v1').glob('*.ast'))
        packet.write_bytes(b'partial cache packet')
        self.assertEqual(self.produce(),1)
        self.assertEqual(self.produce(enabled=False),1)
        keep=packet.parent/'mine.ast';keep.write_bytes(b'keep')
        parsecache.clear(self.frontend)
        self.assertEqual(keep.read_bytes(),b'keep')
        self.assertEqual(self.produce(),1)

    def test_unavailable_storage_is_optional_and_errors_are_not_cached(self):
        (self.root/'parse-v1').write_bytes(b'not a directory')
        self.assertEqual(self.produce(),1)
        self.assertEqual(self.produce(),1)
        (self.root/'parse-v1').unlink()
        error=subprocess.CompletedProcess(self.args,1,b'bad source',b'parser error')
        with patch.object(parsecache.subprocess,'run',return_value=error) as run:
            for _ in range(2):
                self.assertEqual(parsecache.run(self.args,b'bad').returncode,1)
        self.assertEqual(run.call_count,2)
        self.assertFalse((self.root/'parse-v1').exists())


if __name__=='__main__':
    unittest.main()

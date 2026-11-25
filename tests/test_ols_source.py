import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from tools.ols_source import stage_source

class OLSSourceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'source';self.source.mkdir();self.dest=self.root/'dest'
        self.files={'src/main.odin':b'package main\n','builtin/builtin.odin':b'package builtin\n','LICENSE':b'upstream license'}
        self.metadata={'revision':'abc','files':{name:hashlib.sha256(data).hexdigest() for name,data in self.files.items()}}
        for name,data in self.files.items():
            path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    def archive(self,kind=tarfile.REGTYPE):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w:gz') as tar:
            for name,data in self.files.items():
                info=tarfile.TarInfo('ols-abc/'+name);info.type=kind;info.size=len(data) if kind==tarfile.REGTYPE else 0
                info.linkname='/outside';tar.addfile(info,io.BytesIO(data) if kind==tarfile.REGTYPE else None)
        return out.getvalue()
    def test_local_source_and_archive_have_identical_payloads(self):
        stage_source(self.dest,self.source,metadata=self.metadata)
        second=self.root/'second';stage_source(second,metadata=self.metadata,archive=self.archive())
        for name,data in self.files.items():
            self.assertEqual((self.dest/name).read_bytes(),data);self.assertEqual((second/name).read_bytes(),data)
        self.assertEqual(json.loads((self.dest/'UPSTREAM.json').read_text()),self.metadata)
    def test_modified_source_is_rejected_before_staging(self):
        (self.source/'src/main.odin').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'modified'):stage_source(self.dest,self.source,metadata=self.metadata)
        self.assertFalse(self.dest.exists())
    def test_links_and_unsafe_paths_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'Unsupported'):stage_source(self.dest,metadata=self.metadata,archive=self.archive(tarfile.SYMTYPE))
        with self.assertRaisesRegex(ValueError,'Unsafe'):stage_source(self.dest,self.source,metadata={'files':{'../outside':'a'}})

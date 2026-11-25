"""Development cleanup preserves source, evidence and unrelated/linked files."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from tools import storage

class StorageCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-storage-cleanup-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        subprocess.run(['git','init','-q',str(self.root)],check=True)
    def write(self,name,data=b'data'):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path
    def test_dry_run_and_apply_remove_only_generated_files(self):
        generated=[self.write('.thor-cache/sdk-parser/old.bin'),self.write('examples/demo/.thor-cache/parse-v1/old.json'),
                   self.write('rust/target/debug/deps/old.o'),self.write('thor_test/artifacts/program',b'\x7fELFcompiled'),
                   self.write('dist/before-editor-update/archive.tar.gz')]
        kept=[self.write('main.thor'),self.write('.thor-cache/editor-backups/settings.json'),
              self.write('thor_test/artifacts/report.json',b'{}'),self.write('rust/target/release/thor-rust'),
              self.write('dist/unknown-project/source.thor')]
        subprocess.run(['git','-C',str(self.root),'add','main.thor'],check=True)
        storage.prune(self.root,reset_builds=True)
        self.assertTrue(all(p.exists() for p in generated+kept))
        storage.prune(self.root,apply=True,reset_builds=True)
        self.assertTrue(all(not p.exists() for p in generated))
        self.assertTrue(all(p.exists() for p in kept))
    def test_tracked_generated_looking_file_prevents_any_removal(self):
        path=self.write('rust/target/debug/source.thor')
        subprocess.run(['git','-C',str(self.root),'add',str(path)],check=True)
        other=self.write('.thor-cache/sdk-parser/old.json')
        with self.assertRaisesRegex(RuntimeError,'tracked source'):storage.prune(self.root,apply=True,reset_builds=True)
        self.assertTrue(other.exists());self.assertTrue(path.exists())
    def test_directory_links_and_unknown_files_are_preserved(self):
        outside=self.root/'external';outside.mkdir();target=outside/'report';target.write_text('keep')
        linked=self.root/'.thor-cache/sdk-parser';linked.parent.mkdir();linked.symlink_to(outside,target_is_directory=True)
        storage.prune(self.root,apply=True,reset_builds=True)
        self.assertTrue(linked.is_symlink());self.assertEqual(target.read_text(),'keep')
    def test_only_two_distribution_versions_per_platform_are_retained(self):
        for n in range(4):self.write(f'dist/thor-0.{n}-macos-aarch64.tar.gz')
        linux=self.write('dist/thor-0.1-linux-aarch64.tar.gz')
        storage.prune(self.root,apply=True)
        self.assertEqual(len(list((self.root/'dist').glob('*macos*.tar.gz'))),2)
        self.assertTrue(linux.exists())

if __name__=='__main__':unittest.main()

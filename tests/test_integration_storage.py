"""Integration source archives verify content and restore independent files."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from tools import integration_storage as storage
from tests.native_paths import directory_alias, is_directory_alias

class IntegrationStorageTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-integration-storage-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
    def write(self,name,text):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);return path
    def test_round_trip_preserves_all_source_modes_links_and_independent_copies(self):
        first=self.write('migrated/app/main.thor','package main\n')
        second=self.write('vetted/app/main.thor','package main\n')
        script=self.write('toolchains/odin','tool');script.chmod(0o755)
        original_mode=script.stat().st_mode&0o777
        reports=self.write('results/report.json','{}');access=self.write('windows-access/notes','keep')
        original=storage.inventory(self.root,['migrated','toolchains','vetted'])
        result=storage.archive_workspaces(self.root)
        self.assertFalse(first.exists());self.assertEqual(reports.read_text(),'{}');self.assertEqual(access.read_text(),'keep')
        storage.verify(self.root/'archived-workspaces.tar.gz')
        storage.restore_workspaces(self.root)
        self.assertEqual(storage.inventory(self.root,result['archived']),original)
        first.write_text('changed')
        self.assertEqual(second.read_text(),'package main\n')
        self.assertEqual(script.stat().st_mode&0o777,original_mode)
    def test_round_trip_preserves_file_symlinks_when_available(self):
        first=self.write('migrated/app/main.thor','package main\n');link=first.parent/'alias.thor'
        try:link.symlink_to('main.thor')
        except OSError as error:
            if os.name=='nt' and error.winerror==1314:self.skipTest('This account cannot create file symlinks; ordinary archive round trips remain covered.')
            raise
        storage.archive_workspaces(self.root);storage.restore_workspaces(self.root)
        self.assertTrue(link.is_symlink());self.assertEqual(link.read_text(),first.read_text())
    def test_restore_refuses_to_overwrite_existing_workspaces(self):
        self.write('migrated/main.thor','original');storage.archive_workspaces(self.root)
        new=self.write('migrated/main.thor','new work')
        with self.assertRaisesRegex(RuntimeError,'Existing workspace'):storage.restore_workspaces(self.root)
        self.assertEqual(new.read_text(),'new work')
    def test_corrupt_archive_never_removes_or_overwrites_source(self):
        source=self.write('migrated/main.thor','source')
        with patch.object(storage,'verify',side_effect=RuntimeError('invalid checksum')):
            with self.assertRaisesRegex(RuntimeError,'checksum'):storage.archive_workspaces(self.root)
        self.assertEqual(source.read_text(),'source')
        self.assertFalse((self.root/'archived-workspaces.tar.gz').exists())
    def test_changed_sources_abort_archiving_without_removing_workspaces(self):
        source=self.write('vetted/main.thor','source');original=storage.verify
        def verify(path):
            result=original(path);source.write_text('edited during archive');return result
        with patch.object(storage,'verify',side_effect=verify):
            with self.assertRaisesRegex(RuntimeError,'changed'):storage.archive_workspaces(self.root)
        self.assertEqual(source.read_text(),'edited during archive')
    def test_partial_restoration_cannot_replace_an_archive_of_additional_sources(self):
        self.write('migrated/main.thor','first');self.write('vetted/main.thor','second');storage.archive_workspaces(self.root)
        before=(self.root/'archived-workspaces.tar.gz').read_bytes();source=self.write('migrated/main.thor','new first')
        with self.assertRaisesRegex(RuntimeError,'Restore all'):storage.archive_workspaces(self.root)
        self.assertEqual((self.root/'archived-workspaces.tar.gz').read_bytes(),before)
        self.assertEqual(source.read_text(),'new first')
    def test_unsafe_archive_paths_are_rejected_before_restoration(self):
        archive=self.root/'archived-workspaces.tar.gz'
        manifest={'schema':1,'roots':['migrated'],'entries':{'migrated/../../outside':{'kind':'file','mode':0o644,'mtime_ns':0,'size':1,'sha256':'0'*64}}}
        with tarfile.open(archive,'w:gz') as tar:
            data=json.dumps(manifest).encode();info=tarfile.TarInfo(storage.MANIFEST);info.size=len(data);tar.addfile(info,io.BytesIO(data))
        with self.assertRaisesRegex(RuntimeError,'Unsafe workspace'):storage.restore_workspaces(self.root)
        self.assertFalse((self.root/'migrated').exists())
    def test_directory_links_are_preserved_instead_of_archived(self):
        outside=self.root/'outside';outside.mkdir();source=outside/'main.thor';source.write_text('keep')
        directory_alias(self.root/'migrated',outside)
        with self.assertRaisesRegex(RuntimeError,'Linked workspace'):storage.archive_workspaces(self.root)
        self.assertEqual(source.read_text(),'keep');self.assertTrue(is_directory_alias(self.root/'migrated'))
    @unittest.skipUnless(os.name=='nt','Windows junction traversal protection')
    def test_nested_junction_is_preserved_instead_of_archiving_its_target(self):
        source=self.write('migrated/app/main.thor','source');outside=self.root/'outside';outside.mkdir()
        target=outside/'keep.thor';target.write_text('keep');alias=source.parent/'linked'
        directory_alias(alias,outside)
        with self.assertRaisesRegex(RuntimeError,'Linked workspace'):storage.archive_workspaces(self.root)
        self.assertEqual(target.read_text(),'keep');self.assertEqual(source.read_text(),'source')
        self.assertTrue(is_directory_alias(alias));self.assertFalse((self.root/'archived-workspaces.tar.gz').exists())
    @unittest.skipUnless(os.name=='nt','Windows dangling junction restoration guard')
    def test_restore_preserves_a_dangling_workspace_junction(self):
        self.write('migrated/main.thor','source');storage.archive_workspaces(self.root)
        target=self.root/'missing';target.mkdir();alias=self.root/'migrated';directory_alias(alias,target);target.rmdir()
        with self.assertRaisesRegex(RuntimeError,'Existing workspace'):storage.restore_workspaces(self.root)
        self.assertTrue(is_directory_alias(alias))

if __name__=='__main__':unittest.main()

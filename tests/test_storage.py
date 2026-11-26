"""Development cleanup preserves source, evidence and unrelated/linked files."""
from tests.native_paths import directory_alias, is_directory_alias
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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
    def test_source_walk_does_not_visit_a_linked_tree(self):
        outside=self.root/'outside';(outside/'.thor-cache/sdk-parser').mkdir(parents=True)
        kept=outside/'.thor-cache/sdk-parser/kept';kept.write_text('outside')
        source=self.root/'source';source.mkdir();alias=source/'linked'
        directory_alias(alias,outside)
        candidates=storage.candidates(source)
        self.assertEqual(candidates,[])
        self.assertTrue(is_directory_alias(alias));self.assertEqual(kept.read_text(),'outside')

    def test_directory_links_and_unknown_files_are_preserved(self):
        outside=self.root/'external';outside.mkdir();target=outside/'report';target.write_text('keep')
        linked=self.root/'.thor-cache/sdk-parser';linked.parent.mkdir();directory_alias(linked,outside)
        storage.prune(self.root,apply=True,reset_builds=True)
        self.assertTrue(is_directory_alias(linked));self.assertEqual(target.read_text(),'keep')
    def test_only_two_distribution_versions_per_platform_are_retained(self):
        for n in range(4):self.write(f'dist/thor-0.{n}-macos-aarch64.tar.gz')
        linux=self.write('dist/thor-0.1-linux-aarch64.tar.gz')
        storage.prune(self.root,apply=True)
        self.assertEqual(len(list((self.root/'dist').glob('*macos*.tar.gz'))),2)
        self.assertTrue(linux.exists())

    def test_gitless_snapshot_prunes_generated_output_and_keeps_reports(self):
        root=self.root/'snapshot';root.mkdir()
        source=self.write('snapshot/main.thor')
        manifest=self.write('snapshot/snapshot-manifest.json',json.dumps({'files':{'main.thor':'source-hash'}}).encode())
        generated=[self.write('snapshot/.thor-cache/sdk-parser/old.bin'),
                   self.write('snapshot/rust/target/debug/deps/old.o'),
                   self.write('snapshot/windows-results/smoke/program.exe',b'MZcompiled'),
                   self.write('snapshot/windows-results/smoke/program.pdb',b'Microsoft C/C++ MSF 7.00\r\n')]
        report=self.write('snapshot/windows-results/failure.json',b'{"success":false}')
        log=self.write('snapshot/windows-results/failure.log',b'test failed')
        quoted_signature=self.write('snapshot/windows-results/quoted-signature.log',b'Microsoft C/C++ MSF diagnostic text')
        source_fixture=self.write('snapshot/windows-results/source.thor',b'MZ :: 1\n')
        with patch.object(storage.subprocess,'check_output',side_effect=AssertionError('Git must not be required')):
            storage.prune(root,apply=True,reset_builds=True)
        self.assertTrue(all(not path.exists() for path in generated))
        self.assertTrue(all(path.exists() for path in [source,manifest,report,log,quoted_signature,source_fixture]))

    def test_snapshot_sources_are_protected_even_with_generated_names(self):
        root=self.root/'snapshot';root.mkdir()
        source=self.write('snapshot/rust/target/debug/source.thor')
        self.write('snapshot/snapshot-manifest.json',json.dumps({'files':{
            'nested/../rust/target/debug/source.thor':'source-hash'}}).encode())
        other=self.write('snapshot/.thor-cache/sdk-parser/old.json')
        with self.assertRaisesRegex(RuntimeError,'tracked source'):
            storage.prune(root,apply=True,reset_builds=True)
        self.assertTrue(source.exists());self.assertTrue(other.exists())

    def test_invalid_snapshot_inventory_aborts_before_removing_anything(self):
        root=self.root/'snapshot';root.mkdir()
        generated=self.write('snapshot/.thor-cache/sdk-parser/old.json')
        for files in [{},[],{'../outside':'hash'}]:
            with self.subTest(files=files):
                self.write('snapshot/snapshot-manifest.json',json.dumps({'files':files}).encode())
                with self.assertRaisesRegex(RuntimeError,'cleanup refused'):
                    storage.prune(root,apply=True)
                self.assertTrue(generated.exists())

    def test_validation_results_do_not_follow_directory_links(self):
        outside=self.root/'outside';outside.mkdir()
        binary=self.write('outside/kept.exe',b'MZkeep')
        results=self.root/'windows-results';results.mkdir()
        directory_alias(results/'external',outside)
        storage.prune(self.root,apply=True)
        self.assertTrue(is_directory_alias(results/'external'))
        self.assertEqual(binary.read_bytes(),b'MZkeep')

    def test_cargo_cleanup_cannot_be_redirected_to_an_unrelated_target_directory(self):
        self.write('rust/Cargo.toml')
        generated=self.write('rust/target/debug/old.o')
        unrelated=self.write('other-target/debug/keep.o')
        original_run=subprocess.run
        def cargo(args,**options):
            if args[0]!='cargo':return original_run(args,**options)
            # Cargo honors CARGO_TARGET_DIR unless --target-dir overrides it.
            selected=Path(args[args.index('--target-dir')+1]) if '--target-dir' in args else Path(os.environ['CARGO_TARGET_DIR'])
            shutil.rmtree(selected/'debug')
        with patch.dict(os.environ,{'CARGO_TARGET_DIR':str(unrelated.parent.parent)}), \
                patch.object(storage.subprocess,'run',side_effect=cargo):
            storage.prune(self.root,apply=True,reset_builds=True)
        self.assertFalse(generated.exists());self.assertTrue(unrelated.exists())

if __name__=='__main__':unittest.main()

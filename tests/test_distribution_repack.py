import tempfile
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from tools import build_distribution as distribution


@unittest.skipIf(os.name=='nt','Linux SDK relocation uses POSIX executables')
class RepackTests(unittest.TestCase):
    def test_installed_sdk_dependencies_are_resolved_at_the_original_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'installed';stage=root/'stage'
            source=old/'odin/odin';source.parent.mkdir(parents=True);source.write_bytes(b'compiler')
            (stage/'odin').mkdir(parents=True);(stage/'odin/odin').write_bytes(source.read_bytes())
            library=old/'odin/lib/libLLVM-18.so.1';library.parent.mkdir();library.write_bytes(b'library')
            (old/'THOR-DISTRIBUTION.json').write_text('{}')
            notice=old/'licenses/native/llvm/copyright';notice.parent.mkdir(parents=True);notice.write_text('retained notice')
            calls=[]
            def capture(command,path):
                self.assertEqual(command,'ldd');calls.append(Path(path))
                return 'libLLVM-18.so.1 => '+str(library)+' (0x123)\n' if Path(path)==source else ''
            with patch.object(distribution.platform,'system',return_value='Linux'),patch.object(distribution,'capture',side_effect=capture),patch.object(distribution.subprocess,'run') as run:
                distribution.relocate_odin(stage,source)
            self.assertEqual(calls[0],source)
            self.assertEqual((stage/'odin/lib/libLLVM-18.so.1').read_bytes(),b'library')
            self.assertEqual((stage/'licenses/native/llvm/copyright').read_text(),'retained notice')
            self.assertEqual(source.read_bytes(),b'compiler')
            self.assertTrue(any(call.args[0][-1]==str(stage/'odin/odin') for call in run.call_args_list))


if __name__=='__main__':unittest.main()


class BorrowedMacToolsTests(unittest.TestCase):
    def test_packaging_uses_verified_sdk_tools_without_host_llvm(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);sdk=root/'sdk';stage=root/'stage'
            files={'libexec/llvm-symbolizer':b'reviewed symbolizer','libexec/dsymutil':b'reviewed dsymutil','clang-resource/lib/darwin/libclang_rt.asan_osx_dynamic.dylib':b'reviewed sanitizer'}
            for name,data in files.items():
                p=sdk/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            with patch.object(distribution.shutil,'which',side_effect=AssertionError('Host LLVM must not be selected')):
                distribution.stage_macos_tools(stage,sdk)
            for name,data in files.items():self.assertEqual((stage/'odin'/name).read_bytes(),data)
            self.assertEqual((sdk/'libexec/llvm-symbolizer').read_bytes(),files['libexec/llvm-symbolizer'])

    def test_incomplete_borrowed_tools_fail_instead_of_mixing_host_llvm(self):
        for missing in ('libexec/dsymutil','clang-resource/lib/darwin/libclang_rt.asan_osx_dynamic.dylib'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);sdk=root/'sdk'
                for name in ('libexec/llvm-symbolizer','libexec/dsymutil','clang-resource/lib/darwin/libclang_rt.asan_osx_dynamic.dylib'):
                    if name==missing:continue
                    p=sdk/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'reviewed tool')
                with patch.object(distribution.shutil,'which',side_effect=AssertionError('Host LLVM must not be selected')):
                    with self.assertRaises(ValueError):distribution.stage_macos_tools(root/'stage',sdk)


class VersionOnlyPatchTests(unittest.TestCase):
    def test_installer_refresh_cannot_replace_a_published_or_other_sources_release(self):
        from tools.rebuild_patch_release import validate_base_release
        source='a'*40
        validate_base_release({'isDraft':True,'targetCommitish':source},True,source)
        for release in ({'isDraft':False,'targetCommitish':source},{'isDraft':True,'targetCommitish':'b'*40}):
            with self.assertRaisesRegex(ValueError,'unpublished draft'):validate_base_release(release,True,source)
        with self.assertRaisesRegex(ValueError,'published'):validate_base_release({'isDraft':True},False,source)

    def test_only_the_unique_package_version_may_change(self):
        from tools.rebuild_patch_release import version_only
        previous='name = "compiler"\nversion = "0.41.11"\ndependency = "unchanged"\n'
        current=previous.replace('0.41.11','0.41.12')
        allowed=version_only(previous,current,'0.41.11','0.41.12')
        changed_dependency=version_only(previous,current.replace('unchanged','changed'),'0.41.11','0.41.12')
        duplicate=version_only(previous+previous,current+current,'0.41.11','0.41.12')
        absent=version_only('no version','no version','0.41.11','0.41.12')
        self.assertTrue(allowed);self.assertFalse(changed_dependency);self.assertFalse(duplicate);self.assertFalse(absent)

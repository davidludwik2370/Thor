"""Private source-build tools: no dependency compilation fallback or global setup."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from install import source_tools as tools


class Response(io.BytesIO):
    def geturl(self):
        return 'https://example.invalid/tool'


class BinaryArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-source-binaries-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def archive(self, entries):
        path = self.root / 'tool.tar.gz'
        with tarfile.open(path, 'w:gz') as output:
            for name, kind, value in entries:
                member = tarfile.TarInfo(name)
                if kind == 'file':
                    member.size = len(value); member.mode = 0o755
                    output.addfile(member, io.BytesIO(value))
                else:
                    member.type = tarfile.SYMTYPE if kind == 'link' else tarfile.LNKTYPE
                    member.linkname = value
                    output.addfile(member)
        return path

    @unittest.skipIf(os.name == 'nt', 'POSIX Node/LLVM archives')
    def test_safe_relative_npm_links_and_component_selection(self):
        archive = self.archive([('tool/bin/npm', 'link', '../lib/npm.js'),
                                ('tool/lib/npm.js', 'file', b'npm'), ('tool/unused', 'file', b'omit')])
        root = tools.extract_tar(archive, self.root / 'output', 'tool', lambda p: p.name != 'unused')
        self.assertEqual((root / 'bin/npm').read_bytes(), b'npm')
        self.assertFalse((root / 'unused').exists())
        self.assertEqual(stat.S_IMODE((root / 'lib/npm.js').stat().st_mode), 0o755)

    @unittest.skipIf(os.name == 'nt', 'POSIX Node/LLVM archives')
    def test_tar_rejects_traversal_duplicate_files_special_links_and_cycles(self):
        cases = [[('../outside', 'file', b'bad')], [('other/file', 'file', b'bad')],
                 [('tool/a', 'file', b'a'), ('tool/a', 'file', b'b')],
                 [('tool/link', 'link', '../../outside')], [('tool/link', 'link', '/tmp/outside')],
                 [('tool/link', 'hard', 'tool/file')],
                 [('tool/a', 'link', 'b'), ('tool/b', 'link', 'a')],
                 [('tool/a', 'link', 'b'), ('tool/a/nested', 'link', '../file')]]
        for number, entries in enumerate(cases):
            with self.subTest(entries=entries):
                archive = self.archive(entries)
                with self.assertRaises((ValueError, OSError, RuntimeError)):
                    tools.extract_tar(archive, self.root / str(number), 'tool')
        self.assertFalse((self.root / 'outside').exists())

    def test_zip_rejects_backslashes_traversal_and_case_collisions(self):
        for number, names in enumerate([['tool/../../outside'], ['tool/..\\outside'],
                                        ['tool/a', 'tool/A'], ['C:/outside']]):
            archive = self.root / f'{number}.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                for name in names:output.writestr(name, b'payload')
            with self.subTest(names=names), self.assertRaises(ValueError):
                tools.extract_zip(archive, self.root / str(number), 'tool')

    def test_zip_regular_payload_is_readable(self):
        archive = self.root / 'tool.zip'
        with zipfile.ZipFile(archive, 'w') as output:output.writestr('tool/node.exe', b'payload')
        root = tools.extract_zip(archive, self.root / 'zip-output', 'tool')
        self.assertEqual((root / 'node.exe').read_bytes(), b'payload')

    def test_download_checks_hash_and_https_before_use(self):
        data = b'official binary'
        for expected, succeeds in [(hashlib.sha256(data).hexdigest(), True), ('0' * 64, False)]:
            destination = self.root / expected
            with patch.object(tools.urllib.request, 'urlopen', return_value=Response(data)):
                if succeeds:
                    tools.download('https://example.invalid/tool', destination, expected)
                    self.assertEqual(destination.read_bytes(), data)
                else:
                    with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                        tools.download('https://example.invalid/tool', destination, expected)
        with patch.object(tools.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                tools.download('http://example.invalid/tool', self.root / 'bad', '0' * 64)
        with patch.object(tools.urllib.request, 'urlopen', return_value=Response(data)), patch.object(tools, 'MAX_DOWNLOAD', 1):
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                tools.download('https://example.invalid/tool', self.root / 'oversized', '0' * 64)


class ToolAcquisitionTests(unittest.TestCase):
    def test_existing_node_requires_both_node_and_npm(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = {'PATH': '/fixture'}
            with patch.object(tools.subprocess, 'check_output', side_effect=['v22.14.0', '10.9.2']), \
                 patch.object(tools, 'download', side_effect=AssertionError('unnecessary download')):
                tools.ensure_node(Path(temporary), env, 'macos-aarch64')
            self.assertEqual(env, {'PATH': '/fixture', 'NODE_COMPILE_CACHE': str(Path(temporary) / 'node-compile-cache')})
            with patch.object(tools.subprocess, 'check_output', side_effect=['v22.14.0', FileNotFoundError()]), \
                 patch.object(tools, 'official_hash', side_effect=ValueError('binary acquisition requested')):
                with self.assertRaisesRegex(ValueError, 'acquisition requested'):
                    tools.ensure_node(Path(temporary), env, 'macos-aarch64')

    def test_existing_supported_rust_and_cargo_are_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = {'PATH': '/fixture'}
            with patch.object(tools.subprocess, 'check_output', side_effect=['rustc 1.89.0 (fixture)', 'cargo 1.89.0']), \
                 patch.object(tools, 'download', side_effect=AssertionError('unnecessary download')):
                tools.ensure_rust(Path(temporary), env, 'linux-aarch64')
            self.assertEqual(env, {'PATH': '/fixture'})

    def test_private_rust_uses_minimal_pinned_toolchain_and_private_homes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {'PATH': '/fixture', 'RUSTUP_HOME': '/user-rust', 'CARGO_HOME': '/user-cargo'}
            calls = []
            def download(url, path, digest):
                self.assertIn('/archive/1.28.2/x86_64-pc-windows-msvc/', url)
                self.assertEqual(digest, 'a' * 64)
                path.write_bytes(b'fixture')
            def run(args, **kwargs):
                calls.append((args, dict(kwargs['env'])))
                return subprocess.CompletedProcess(args, 0)
            with patch.object(tools.subprocess, 'check_output', side_effect=[FileNotFoundError(), 'rustc 1.89.0', 'cargo 1.89.0']), \
                 patch.object(tools.urllib.request, 'urlopen', return_value=Response(('a' * 64).encode())), \
                 patch.object(tools, 'download', side_effect=download), patch.object(tools.subprocess, 'run', side_effect=run):
                tools.ensure_rust(root, env, 'windows-x86_64')
            self.assertEqual(len(calls), 1)
            arguments, child = calls[0]
            self.assertIn('--no-modify-path', arguments)
            self.assertEqual(arguments[arguments.index('--profile') + 1], 'minimal')
            self.assertEqual(arguments[arguments.index('--default-toolchain') + 1], '1.89.0')
            self.assertEqual(child['CARGO_HOME'], str(root / 'cargo'))
            self.assertEqual(child['RUSTUP_HOME'], str(root / 'rustup'))

    def test_llvm_download_failure_never_starts_a_build(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(tools.platform, 'mac_ver', return_value=('13.5.1', (), 'arm64')), \
             patch.object(tools, 'download', side_effect=OSError('network failure')), \
             patch.object(tools.subprocess, 'run') as run:
            with self.assertRaisesRegex(OSError, 'network failure'):
                tools.acquire_macos_llvm(Path(temporary), {}, 'macos-aarch64')
            run.assert_not_called()

    def test_llvm_rejects_a_too_old_host_before_downloading(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(tools.platform, 'mac_ver', return_value=('13.5.1', (), 'x86_64')), \
             patch.object(tools, 'download', side_effect=AssertionError('incompatible download')):
            with self.assertRaisesRegex(ValueError, 'macOS 13.7'):
                tools.acquire_macos_llvm(Path(temporary), {}, 'macos-x86_64')

    def test_static_llvm_notices_survive_sdk_packaging(self):
        from tools.build_distribution import sdk_components
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            llvm = root / 'llvm';(llvm / 'bin').mkdir(parents=True)
            (llvm / 'bin/llvm-config').write_text('fixture')
            (llvm / 'THOR-BINARY-TOOL.json').write_text('{"version":"18.1.8"}')
            (llvm / 'LICENSE-LLVM.txt').write_text('upstream LLVM license')
            (llvm / 'LICENSE-zstd-COPYING.txt').write_text('upstream zstd license')
            sdk = root / 'sdk'
            tools.retain_llvm_notices(llvm / 'bin/llvm-config', sdk)
            stage = root / 'stage'
            sdk_components(sdk, stage)
            self.assertEqual((stage / 'odin/bin/llvm-notices/LICENSE-LLVM.txt').read_text(), 'upstream LLVM license')
            self.assertEqual((stage / 'odin/bin/llvm-notices/LICENSE-zstd-COPYING.txt').read_text(), 'upstream zstd license')
            self.assertFalse((stage / 'odin/bin/llvm-notices/bin/llvm-config').exists())

class MacDebugToolsTests(unittest.TestCase):
    def test_source_llvm_selection_keeps_matching_dsymutil_without_compiling_it(self):
        self.assertTrue(tools.llvm_files(Path('bin/dsymutil')))
        self.assertTrue(tools.llvm_files(Path('bin/llvm-symbolizer')))
        self.assertFalse(tools.llvm_files(Path('bin/clang')))

    def test_distribution_keeps_matching_symbol_tools_and_sanitizer_runtime(self):
        from tools import build_distribution as distribution
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);llvm=root/'LLVM with spaces';(llvm/'bin').mkdir(parents=True)
            symbolizer=llvm/'bin/llvm-symbolizer-18';symbolizer.write_bytes(b'symbolizer')
            (llvm/'bin/dsymutil').write_bytes(b'matching dsymutil')
            runtime=llvm/'lib/clang/18/lib/darwin';runtime.mkdir(parents=True)
            (runtime/'libclang_rt.asan_osx_dynamic.dylib').write_bytes(b'runtime')
            stage=root/'stage'
            with patch.object(distribution.shutil,'which',return_value=str(symbolizer)):
                distribution.stage_macos_tools(stage)
            self.assertEqual((stage/'odin/libexec/dsymutil').read_bytes(),b'matching dsymutil')
            self.assertEqual((stage/'odin/libexec/llvm-symbolizer').read_bytes(),b'symbolizer')
            self.assertEqual((stage/'odin/clang-resource/lib/darwin/libclang_rt.asan_osx_dynamic.dylib').read_bytes(),b'runtime')

    def test_distribution_does_not_substitute_an_unrelated_system_dsymutil(self):
        from tools import build_distribution as distribution
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'llvm/bin').mkdir(parents=True)
            symbolizer=root/'llvm/bin/llvm-symbolizer';symbolizer.write_bytes(b'symbolizer')
            with patch.object(distribution.shutil,'which',return_value=str(symbolizer)):
                with self.assertRaisesRegex(ValueError,'matching dsymutil'):
                    distribution.stage_macos_tools(root/'stage')

    @unittest.skipIf(os.name=='nt','macOS binary relocation')
    def test_distribution_relocates_and_signs_both_private_tools(self):
        from tools import build_distribution as distribution
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'odin/libexec').mkdir(parents=True)
            for name in ['llvm-symbolizer','dsymutil']:(root/'odin/libexec'/name).write_bytes(b'tool')
            calls=[]
            def capture(*args):
                calls.append(tuple(map(str,args)))
                return 'binary:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n'
            with patch.object(distribution.platform,'system',return_value='Darwin'),patch.object(distribution,'capture',side_effect=capture),patch.object(distribution.subprocess,'run') as run:
                distribution.relocate_odin(root)
            for name in ['llvm-symbolizer','dsymutil']:
                helper=str(root/'odin/libexec'/name)
                self.assertIn(('otool','-L',helper),calls)
                self.assertTrue(any(str(call.args[0][-1])==helper for call in run.call_args_list))

class GitLFSToolTests(unittest.TestCase):
    def test_compatible_git_lfs_is_reused_without_global_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            env={'PATH':'/original'}
            with patch.object(tools.subprocess,'check_output',return_value='git-lfs/3.8.1 (GitHub)'),patch.object(tools,'download',side_effect=AssertionError('unnecessary download')):
                tools.ensure_git_lfs(Path(temporary),env,'linux-aarch64')
            self.assertEqual(env['PATH'],'/original');self.assertEqual(list(Path(temporary).iterdir()),[])

    def test_private_binary_uses_pinned_digest_for_each_native_host(self):
        config=json.loads(Path(tools.__file__).with_name('git-lfs-source.json').read_text())
        for target,item in config['targets'].items():
            with self.subTest(target=target),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary).resolve();exe='git-lfs.exe' if target.startswith('windows-') else 'git-lfs';data=b'private binary'
                blob=io.BytesIO()
                if item['url'].endswith('.zip'):
                    with zipfile.ZipFile(blob,'w') as z:z.writestr(config['root']+'/'+exe,data)
                else:
                    with tarfile.open(fileobj=blob,mode='w:gz') as t:
                        info=tarfile.TarInfo(config['root']+'/'+exe);info.size=len(data);info.mode=0o755;t.addfile(info,io.BytesIO(data))
                def download(url,path,digest):
                    self.assertEqual(url,item['url']);self.assertEqual(digest,item['sha256']);path.write_bytes(blob.getvalue())
                env={'PATH':'/original'}
                with patch.object(tools,'download',side_effect=download),patch.object(tools.subprocess,'check_output',side_effect=[FileNotFoundError(),'git-lfs/'+config['version']+' (GitHub)']):
                    tools.ensure_git_lfs(root,env,target)
                private=root/'private-lfs'/config['root']
                self.assertEqual((private/exe).read_bytes(),data);self.assertTrue(env['PATH'].startswith(str(private)+os.pathsep))
                self.assertFalse((root/'git-lfs.zip').exists());self.assertFalse((root/'git-lfs.tar.gz').exists())

    def test_missing_bsd_package_has_no_guessed_binary_download(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(tools.subprocess,'check_output',side_effect=FileNotFoundError()),patch.object(tools,'download',side_effect=AssertionError('foreign binary')):
            with self.assertRaisesRegex(ValueError,'host package manager'):tools.ensure_git_lfs(Path(temporary),{'PATH':'/original'},'freebsd-aarch64')


class SDKPointerTests(unittest.TestCase):
    def test_packaging_rejects_unresolved_large_file_pointers(self):
        from tools.build_distribution import reject_lfs_pointer
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)/'library.a';p.write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:'+b'0'*64+b'\nsize 12345\n')
            with self.assertRaisesRegex(ValueError,'Unresolved Git LFS'):reject_lfs_pointer(p)
            p.write_bytes(b'!<arch>\nreal library payload');reject_lfs_pointer(p)

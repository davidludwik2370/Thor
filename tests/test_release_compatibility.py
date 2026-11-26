"""Release ABI gates exercise real ELF version-chain layouts, without a linker."""
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools import release_compatibility as abi


def elf(versions=(), machine=62, endian='<'):
    strings=b'\0';offsets=[]
    for name in versions:
        offsets.append(len(strings));strings+=name.encode()+b'\0'
    needed=struct.pack(endian+'HHIII',1,len(versions),0,16,0) if versions else b''
    for i,name in enumerate(offsets):
        needed+=struct.pack(endian+'IHHII',0,0,i+2,name,16 if i+1<len(offsets) else 0)
    head=bytearray(64);head[:7]=b'\x7fELF\x02'+bytes([1 if endian=='<' else 2,1])
    struct.pack_into(endian+'HHI',head,16,3,machine,1)
    struct.pack_into(endian+'Q',head,40,64)
    struct.pack_into(endian+'HHH',head,58,64,3,0)
    sections=bytes(64)
    sections+=struct.pack(endian+'IIQQQQIIQQ',0,3,0,0,256,len(strings),0,0,1,0)
    sections+=struct.pack(endian+'IIQQQQIIQQ',0,0x6ffffffe,0,0,256+len(strings),len(needed),1,1 if versions else 0,4,0)
    return bytes(head)+sections+strings+needed


def components(data=None):
    data=elf(['GLIBC_2.35','GLIBCXX_3.4.30','CXXABI_1.3.13']) if data is None else data
    return [(name,data) for name in ('thor','thor-install','odin/odin','tls/bin/tls')]


class ReleaseCompatibilityTests(unittest.TestCase):
    def test_reviewed_boundary_and_numeric_order(self):
        data=elf(['GLIBC_2.9','GLIBC_2.35','GLIBC_2.3','GLIBCXX_3.4.30','CXXABI_1.3.13'])
        result=abi.inspect(components(data),'x86_64',portable=True)
        self.assertEqual(result['required'],{'glibc':'2.35','glibcxx':'3.4.30','cxxabi':'1.3.13'})
        self.assertEqual(len(result['files']),4)

    def test_each_runtime_family_is_enforced_in_support_libraries(self):
        for name in ('GLIBC_2.39','GLIBCXX_3.4.31','CXXABI_1.3.14'):
            with self.subTest(name=name):
                entries=[*components(),('odin/lib/libsupport.so',elf([name]))]
                with self.assertRaisesRegex(ValueError,'libsupport.so requires'):
                    abi.inspect(entries,'x86_64',portable=True)
                result=abi.inspect(entries,'x86_64')
                self.assertFalse(result['portable'])

    def test_relr_feature_also_raises_the_glibc_floor(self):
        data=elf(['GLIBC_2.17','GLIBC_ABI_DT_RELR'])
        self.assertEqual(abi.elf_requirements(data)['requirements']['glibc'],'2.36')
        with self.assertRaisesRegex(ValueError,'glibc 2.36'):
            abi.inspect(components(data),'x86_64',portable=True)

    def test_unknown_and_private_system_abis_are_not_certified(self):
        for name in ('GLIBC_PRIVATE','GLIBC_ABI_FUTURE','CXXABI_FUTURE'):
            with self.subTest(name=name):
                rows=components(elf([name]))
                with self.assertRaisesRegex(ValueError,'unverified system ABI'):
                    abi.inspect(rows,'x86_64',portable=True)
                self.assertIn(name,abi.inspect(rows,'x86_64')['files'][0]['unknown_versions'])

    def test_legacy_cpp_features_and_foreign_sdk_libraries(self):
        rows=[*components(elf(['CXXABI_TM_1','CXXABI_FLOAT128'])),
              ('odin/vendor/foreign.so',elf(['GLIBC_9.99'],machine=183))]
        result=abi.inspect(rows,'x86_64',portable=True)
        self.assertEqual(len(result['files']),4)

    def test_both_architectures_and_byte_orders(self):
        for machine,arch in [(62,'x86_64'),(183,'aarch64')]:
            for order in ('<','>'):
                with self.subTest(arch=arch,order=order):
                    result=abi.inspect(components(elf(['GLIBC_2.35'],machine,order)),arch,portable=True)
                    self.assertEqual(result['required']['glibc'],'2.35')

    def test_missing_shell_and_wrong_architecture_host_components_fail(self):
        for rows in [components()[:-1],components(b'#!/bin/sh\n'),components(elf(machine=183))]:
            with self.subTest(rows=len(rows)):
                with self.assertRaises(ValueError):abi.inspect(rows,'x86_64',portable=True)

    def test_non_elf_files_are_ignored(self):
        self.assertIsNone(abi.elf_requirements(b'ordinary text'))
        self.assertEqual(len(abi.inspect([*components(),('README',b'notes')],'x86_64',portable=True)['files']),4)

    def test_truncation_bad_links_and_bad_chains_fail_closed(self):
        original=elf(['GLIBC_2.35','GLIBC_2.34'])
        broken=[original[:12],original[:-1]]
        for pos,fmt,value in [(58,'H',1),(64+128+40,'I',99),(256+1+len('GLIBC_2.35')+1+len('GLIBC_2.34')+1+8,'I',1)]:
            b=bytearray(original);struct.pack_into('<'+fmt,b,pos,value);broken.append(bytes(b))
        for data in broken:
            with self.subTest(size=len(data)):
                with self.assertRaises(ValueError):abi.elf_requirements(data)

    def test_directory_and_streamed_archive_have_the_same_verdict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'stage';root.mkdir()
            for name,data in components():
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            directory=abi.inspect_directory(root,'x86_64',portable=True)
            archive=Path(temporary)/'release.tar.gz'
            with tarfile.open(archive,'w:gz') as out:
                manifest=json.dumps({'target':'linux-x86_64'}).encode()
                info=tarfile.TarInfo('THOR-DISTRIBUTION.json');info.size=len(manifest)
                out.addfile(info,io.BytesIO(manifest))
                for name,data in components():
                    info=tarfile.TarInfo(name);info.size=len(data);out.addfile(info,io.BytesIO(data))
            streamed=abi.inspect_archive(archive)
            self.assertEqual(streamed['required'],directory['required'])
            self.assertEqual({r['file'] for r in streamed['files']},{r['file'] for r in directory['files']})


class MacReleaseCompatibilityTests(unittest.TestCase):
    def test_legacy_and_modern_slices_are_both_checked(self):
        text='Load command 1\n cmd LC_BUILD_VERSION\n platform MACOS\n minos 11.0\nLoad command 2\n cmd LC_VERSION_MIN_MACOSX\n version 15.0\n'
        self.assertEqual(abi.macos_versions(text),['11.0','15.0'])

    def test_other_apple_platform_is_not_a_macos_requirement(self):
        self.assertEqual(abi.macos_versions('Load command 1\n cmd LC_BUILD_VERSION\n platform IOS\n minos 17.0\n'),[])

    def test_missing_and_malformed_native_floor_is_rejected(self):
        for text in ['nothing','Load command 1\n platform MACOS\n minos unknown\n']:
            with self.assertRaises(ValueError):abi.macos_versions(text)

    def test_portable_gate_and_architecture_validation(self):
        data=bytearray(32);data[:4]=bytes.fromhex('cffaedfe');struct.pack_into('<I',data,4,0x100000c)
        rows=components(bytes(data))
        for floor,passes in [('11.0',True),('13.0',True),('13.0.1',False),('15.0',False)]:
            with self.subTest(floor=floor),patch.object(abi.subprocess,'run',return_value=subprocess_result('Load command 1\n platform MACOS\n minos '+floor+'\n')):
                if passes:
                    result=abi.inspect_macos(rows,'aarch64',portable=True)
                    self.assertEqual(result['required']['macos'],floor)
                else:
                    with self.assertRaisesRegex(ValueError,'requires macOS'):abi.inspect_macos(rows,'aarch64',portable=True)
        with self.assertRaisesRegex(ValueError,'native macOS x86_64'):abi.inspect_macos(rows,'x86_64',portable=True)


def subprocess_result(stdout):
    import subprocess
    return subprocess.CompletedProcess([],0,stdout,'')

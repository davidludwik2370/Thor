"""The installed ASan adapter must work without the frozen Python compiler."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from thorlang.memorymap import encode
from tests.native_paths import rust_binary


class NativeSymbolizerTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-native-symbolizer-')
        self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name)
        self.module=self.directory/'module é with spaces'
        self.backend=self.directory/'llvm-symbolizer'
        self.backend.write_text('''#!/bin/sh
while IFS= read -r request; do
    printf '%s\\n' '__thor_helper' '/generated/main.odin:50:8' ''
done
''')
        self.backend.chmod(0o755)
        self.data={'version':1,'files':['/original/é😀.thor'],'units':[
            {'path':'/generated/main.odin','rows':[[50,5,20,0,7,8,True,'work']]}],
            'aliases':{},'symbolizer':''}

    def write_module(self,data=None,*,damaged=False):
        blob=bytearray(encode(self.data if data is None else data))
        if damaged:blob[-1]^=1
        header=bytearray(32);struct.pack_into('<I',header,0,0xFEEDFACF);struct.pack_into('<II',header,16,1,152)
        command=bytearray(152);struct.pack_into('<II',command,0,0x19,152);struct.pack_into('<I',command,64,1)
        command[72:82]=b'__thor_map';struct.pack_into('<Q',command,72+40,len(blob));struct.pack_into('<I',command,72+48,184)
        self.module.write_bytes(header+command+blob)

    def invoke(self,*requests):
        env={**os.environ,'THOR_LLVM_SYMBOLIZER':str(self.backend)}
        result=subprocess.run([str(rust_binary()),'__symbolize'],input=''.join(r+'\n' for r in requests),env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout

    @unittest.skipIf(os.name=='nt','ASan is currently supported on native macOS only; POSIX backend fixture')
    def test_unicode_quoted_module_and_exact_source_columns(self):
        self.write_module()
        self.assertEqual(self.invoke(f'CODE "{self.module}" 0x42'),'work\n/original/é😀.thor:7:11\n\n')

    @unittest.skipIf(os.name=='nt','POSIX backend fixture')
    def test_data_queries_preserve_symbol_names_and_map_locations(self):
        self.write_module()
        self.assertEqual(self.invoke(f'DATA "{self.module}" 0x42'),'__thor_helper\n/original/é😀.thor:7:11\n\n')

    @unittest.skipIf(os.name=='nt','POSIX backend fixture')
    def test_invalid_maps_are_ignored_without_scanning_other_binary_data(self):
        self.write_module(damaged=True)
        self.assertEqual(self.invoke(f'CODE "{self.module}" 0x42'),'__thor_helper\n/generated/main.odin:50:8\n\n')
        self.module.write_bytes(b'not Mach-O'+encode(self.data))
        self.assertIn('/generated/main.odin:50:8',self.invoke(f'CODE "{self.module}" 0x42'))

    @unittest.skipIf(os.name=='nt','POSIX backend fixture')
    def test_arch_suffix_and_multiple_requests_use_the_same_adapter(self):
        self.write_module()
        result=self.invoke(f'CODE "{self.module}:arm64" 0x42',f'CODE "{self.module}" 0x43')
        self.assertEqual(result.count('work\n'),2)

    @unittest.skipIf(os.name=='nt','POSIX backend fixture')
    def test_aliases_and_ordinary_backend_frames_are_preserved(self):
        data={**self.data,'units':[],'aliases':{'/generated/':'/original/'}}
        self.write_module(data)
        result=self.invoke(f'CODE "{self.module}" 0x42')
        self.assertEqual(result,'__thor_helper\n/original/main.odin:50:8\n\n')


if __name__=='__main__':unittest.main()

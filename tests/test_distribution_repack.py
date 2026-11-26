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

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from tools.build_portable_vendor import raygui_arm_binding
from install import from_source

class PortableVendorTests(unittest.TestCase):
    def test_arm_adapter_preserves_other_platforms_and_procedure_declarations(self):
        text='''package raylib
when ODIN_OS == .Windows {
    foreign import lib "windows/raygui.lib"
} else when ODIN_OS == .Linux  {
\tforeign import lib {
\t\t"linux/libraygui.so" when RAYGUI_SHARED else "linux/libraygui.a",
\t}
} else when ODIN_OS == .Darwin {
    foreign import lib "macos/libraygui.a"
}
foreign { GuiGetState :: proc() -> c.int --- }
'''
        changed=raygui_arm_binding(text)
        self.assertIn('when ODIN_ARCH == .arm64',changed)
        self.assertIn('"linux-arm64/libraygui.so" when RAYGUI_SHARED else "linux-arm64/libraygui.a"',changed)
        self.assertIn('"linux/libraygui.so" when RAYGUI_SHARED else "linux/libraygui.a"',changed)
        self.assertEqual(changed.split('} else when ODIN_OS == .Darwin')[1],text.split('} else when ODIN_OS == .Darwin')[1])
        self.assertEqual(changed.split('} else when ODIN_OS == .Linux')[0],text.split('} else when ODIN_OS == .Linux')[0])
        with self.assertRaisesRegex(ValueError,'import block changed'):raygui_arm_binding(changed)

    def test_missing_or_duplicate_reviewed_block_requires_manual_review(self):
        with self.assertRaises(ValueError):raygui_arm_binding('package changed')

    def test_reused_sdk_cannot_be_modified_by_release_refresh(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(from_source.source_tools,'ensure_rust',side_effect=AssertionError('must fail before build tools')):
            with self.assertRaisesRegex(ValueError,'Reused SDKs are never modified'):
                from_source.build(Path(temporary),[],Path(temporary)/'user-odin',portable=True)
            self.assertEqual(list(Path(temporary).iterdir()),[])

"""Library and syntax-role captures retain the reference's lexical context."""
import unittest
import json
from pathlib import Path
from unittest.mock import patch
from tests import test_thor as base
from tests import test_basic as basic
from tests import test_basic_extensions as extensions
from tests import test_rust_compiler as native


class RustBasicFixtureTests(basic.BasicTests):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def invoke(self, *args, **kwargs):
        with patch.object(base, 'THOR', native.RUST):
            return base.ThorTests.invoke(self, *args, **kwargs)

    def test_native_runtime_parameter_witnesses_are_emitted_with_original_maps(self):
        # Native output deliberately shares unchanged Odin runtime files.
        # Check that layout's actual manifest/maps rather than requiring the
        # reference compiler's per-project runtime-copy directory.
        original=base.ROOT/'runtime/sets/sets.odin'
        before=original.read_bytes()
        code=self.program('values:=basic.to_set([3,1,3,2]);defer delete(values);'
                          'if len(values)!=3 || 2 not_in values {panic("set witness")}',
                          'import "thor:basic"\n')
        for flags in [[],native.UNCHECKED]:
            with self.subTest(flags=flags):
                root,_=self.invoke(code,flags=flags)
                manifest=json.loads((root/'output/thor-manifest.json').read_text())
                self.assertEqual(manifest['engine'],'rust')
                view=next(flag.split('=',1)[1] for flag in manifest['odin_flags']
                          if flag.startswith('-collection:thor='))
                self.assertEqual((Path(view)/'sets/sets.odin').read_bytes(),before)
                generated=[root/'output'/name for name in manifest['generated']]
                self.assertTrue(any(p.name=='basic.odin' for p in generated))
                for mapped in (p for p in generated if p.name.endswith('.map.json')):
                    mapping=json.loads(mapped.read_text())
                    self.assertTrue(mapping['sources'])
                    self.assertNotIn('.thor-runtime/sets/sets.odin',mapping['sources'])
        self.assertEqual(original.read_bytes(),before)


class RustBasicExtensionFixtureTests(extensions.BasicExtensionTests):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def invoke(self, *args, **kwargs):
        with patch.object(base, 'THOR', native.RUST):
            return base.ThorTests.invoke(self, *args, **kwargs)


class RustSyntaxCaptureTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare
    program = native.RustCompilerTests.program

    def test_fields_named_arguments_and_enum_members_are_not_variable_captures(self):
        source=self.program('''
width:=4
row:=Row{width=3}
text:=f"{row.width + width}:{read(width=width)}:{Kind.width}"
if text!="7:4:width" {panic("syntax roles")}
delete(text)
if (switch {case row.width+width==7:row.width;case:0})!=3 {panic("switch capture")}
''','Row::struct{width:int}\nKind::enum{width}\nread::proc(width:int)->int{return width}\n')
        for flags in [[],native.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(source,flags=flags,expected='')

    def test_context_initialized_foreign_callback_can_allocate(self):
        source=self.program('if read()!=7 {panic("callback context")}','''
import "base:runtime"
read::proc "c"()->int{
 context=runtime.default_context()
 text:=f"{7}"
 defer delete(text)
 return int(text[0]-'0')
}
''')
        for flags in [[],native.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(source,flags=flags,expected='')

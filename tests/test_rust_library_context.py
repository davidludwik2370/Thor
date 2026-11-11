"""Library and syntax-role captures retain the reference's lexical context."""
import unittest
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
        original=base.ROOT/'runtime/sets/sets.odin'
        before=original.read_bytes()
        basic.BasicTests.test_native_runtime_parameter_witnesses_are_emitted_with_original_maps(self)
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

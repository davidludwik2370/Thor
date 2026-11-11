"""Native Odin multi-pointer slices retain their slice type after lowering."""
import unittest
from tests import test_rust_compiler as native


class RustMultiPointerSliceTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def test_cast_alias_generic_and_once_only_bounds(self):
        prefix = '''
Bytes :: [^]u8
view::proc(pointer:$P,n:int)->[]u8{return pointer[:n]}
count::proc(calls:^int)->int{calls^+=1;return 3}
'''
        source = self.program('''
storage:[4]u8={1,2,3,4}
calls:=0
bytes:=(cast([^]u8)&storage[0])[:count(&calls)]
alias:=Bytes(&storage[0]);n:=3
other:=alias[:n];generic:=view(alias,n)
if calls!=1||len(bytes)!=3||len(other)!=3||len(generic)!=3{panic("slice shape")}
bytes[1]=9
if other[1]!=9||generic[1]!=9||storage[1]!=9{panic("slice storage")}
''', prefix)
        self.source.write_text(source)
        for flags in [['-debug'], native.UNCHECKED]:
            with self.subTest(flags=flags):
                result = self.invoke('rust', flags=flags)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, '')

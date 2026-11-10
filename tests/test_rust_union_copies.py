"""A union value copy preserves payload permissions without becoming a slice alias."""
import unittest
from tests import test_rust_compiler as native

class RustUnionCopyTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_inline_union_copies_and_stores_are_independent(self):
        prefix='''Cell::union{int,[3]int}
store::proc(destination:^Cell,value:Cell){destination^=value}
'''
        source=self.program('''original:>Cell([3]int{1,2,3})
copy:=original
cells:=[1]Cell{}
cells[0]=original
store(&cells[0],copy)
values,ok:=copy.([3]int)
if !ok{panic("tag")}
values[0]=9
before,valid:=original.([3]int)
if !valid||before[0]!=1||values[0]!=9{panic("union copy")}
''',prefix)
        for flags in [[],native.UNCHECKED]:self.compare(source,flags=flags)

    def test_copying_union_does_not_drop_nested_slice_readonly_access(self):
        prefix='Choice::union{int,[]int}\n'
        for body in ['''original:>Choice([]int{1,2});copy:=original
switch payload in copy{case []int:payload[0]=9;case int:}
''','''original:> []Choice{[]int{1,2}};copy:=original[0]
switch payload in copy{case []int:payload[0]=9;case int:}
''']:
            with self.subTest(body=body):self.compare(self.program(body,prefix),command='check',fail='read-only slice')

    def test_union_alias_can_be_read_without_mutating_its_slice(self):
        prefix='Choice::union{int,[]int}\n'
        self.compare(self.program('''original:>Choice([]int{1,2});copy:=original
switch payload in copy{case []int:if payload[0]!=1{panic("read")};case int:}
''',prefix))

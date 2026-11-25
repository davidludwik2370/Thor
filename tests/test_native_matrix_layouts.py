"""Native matrix-layout wrappers retain literals, expansion and diagnostics."""
from tests.native_paths import EXE_SUFFIX
import os
from pathlib import Path
import subprocess
import unittest
from tests import test_rust_compiler as base

PROGRAM='''package main
Row :: #row_major matrix[2, 3]f32
three :: proc() -> (f32, f32, f32) {return 1, 2, 3}
first :: proc(value: #row_major matrix[$R, $C]$E) -> E {return value[0, 0]}
main :: proc() {
 row := #row_major matrix[2, 3]f32{9, three(), 5, 6}
 column := #column_major matrix[2, 3]f32{9, three(), 5, 6}
 named := Row{9, three(), 5, 6}
 parenthesized := (#row_major matrix[2, 3]f32){9, three(), 5, 6}
 if first(row) != 9 || first(named) != 9 || first(parenthesized) != 9 {panic("layout type")}
 for r in 0..<2 {
  for c in 0..<3 {
   if row[r, c] != column[r, c] || row[r, c] != named[r, c] || row[r, c] != parenthesized[r, c] {panic("layout or expansion")}
  }
 }
 if row[1, 2] != 6 || (#column_major matrix[2, 2]int{1, 2, 3, 4})[0, 0] != 1 {panic("literal selection")}
}
'''
class NativeMatrixLayoutTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke
    compare=base.RustCompilerTests.compare

    def native(self,text,flags=(),command='run'):
        source=self.root/'native.odin'
        source.write_text(text)
        return subprocess.run([os.environ.get('THOR_ODIN','odin'),command,str(source),'-file',
            '-thread-count:1',*(['-out:'+str(self.root/('native-program'+EXE_SUFFIX))]if command!='check'else[]),*flags],cwd=self.root,
            capture_output=True,text=True,timeout=90)

    def test_layout_literals_match_native_with_multi_results_aliases_and_generics(self):
        for flags in [[],base.UNCHECKED]:
            result=self.native(PROGRAM,flags)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.compare(PROGRAM,flags=['-thread-count:1',*flags],expected=result.stdout.rstrip('\n'))

    def test_type_helpers_do_not_gain_non_native_compound_literals(self):
        text='package main\nmain::proc(){_=#type matrix[2,2]f32{1,2,3,4}}\n'
        result=self.native(text,command='check')
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.compare(text,command='check',fail='expected')

    def test_bad_matrix_element_keeps_native_type_error(self):
        text='package main\nmain::proc(){m:=#row_major matrix[2,2]f32{1,false,3,4};_=m}\n'
        result=self.native(text,command='check')
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('bool',result.stderr)
        self.compare(text,command='check',fail='bool')

if __name__=='__main__':unittest.main()

"""Library-only graph copying, public basic exports, and release-safe cleanup."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests import test_thor as base

ROOT = Path(__file__).resolve().parents[1]
TRACKING = ['--track-allocations=basic'] if sys.platform=='darwin' else []


class DeepcloneTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_native_graph_suite_checked_and_unchecked(self):
        configurations = [[], ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']]
        with tempfile.TemporaryDirectory(prefix='thor-graph-native-') as temporary:
            for index, flags in enumerate(configurations):
                with self.subTest(flags=flags):
                    result = subprocess.run(['odin', 'test', str(ROOT/'runtime/graph'),
                        f'-collection:thor={ROOT / "runtime"}', '-out:'+str(Path(temporary)/('tests-'+str(index)+('.exe' if os.name=='nt' else ''))),
                        '-sanitize:address', '-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true',
                        *flags], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    self.assertIn('All tests were successful', result.stdout+result.stderr)

    def test_basic_exports_namespace_wildcard_and_selected(self):
        body = '''
source := Node{value=13}
source.next=&source
copy,err:=CALL_CLONE(&source)
if err!=.None {panic("clone")}
if copy==&source || copy.next!=copy || copy.value!=13 {panic("identity")}
copy.value=27
if source.value!=13 {panic("source changed")}
free_error:ERROR_TYPE=CALL_FREE(copy)
if free_error!=.None {panic("free")}
'''
        for declaration, namespace in [('import "thor:basic"', 'basic.'),
                ('import * "thor:basic"', ''),
                ('import _ "thor:basic" / deepclone, deepfree, Deep_Error', '')]:
            with self.subTest(declaration=declaration):
                text=self.program(body.replace('CALL_CLONE',namespace+'deepclone')
                    .replace('CALL_FREE',namespace+'deepfree').replace('ERROR_TYPE',namespace+'Deep_Error'),
                    declaration+'\nNode::struct{value:int,next:^Node}\n')
                _,result=self.invoke(text,flags=TRACKING)
                self.assertNotIn('still allocated',result.stderr)

    def test_thor_containers_big_numbers_union_and_interior_pointers(self):
        text=self.program('''
values := [10,20,30,40]
source := Data{selected=&values[2],values=values[:],tail=values[2:],
    names=set[string]{"oak","elm"},integer=Integer(123456789012345678901234567890),
    rational=Rational(1,3),variant="hello"}
defer delete(source.names);defer delete(source.integer);defer delete(source.rational)
copy,err:=basic.deepclone(&source)
if err!=.None {panic("clone")}
if copy.selected!=&copy.values[2] || &copy.tail[0]!=copy.selected {panic("aliases")}
copy.selected^=99
if copy.tail[0]!=99 || values[2]!=30 {panic("independence")}
if copy.integer!=source.integer || copy.rational!=source.rational || "elm" not_in copy.names {panic("values")}
if copy.variant.(string)!="hello" {panic("union")}
free_error:=basic.deepfree(copy)
if free_error!=.None {panic("cleanup")}
''','''import "thor:basic"
Data::struct{selected:^int,values,tail:[]int,names:set[string],integer:Integer,rational:Rational,variant:union{int,string}}
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert',*TRACKING]]:
            with self.subTest(flags=flags):
                _,result=self.invoke(text,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

    def test_unsupported_types_return_errors_without_touching_source(self):
        self.invoke(self.program('''
source:=Bad{number=7}
copy,err:=basic.deepclone(&source)
if copy!=nil || err!=.Unsupported_Type || source.number!=7 {panic("unsupported")}
free_error:=basic.deepfree(&source)
if free_error!=.Unsupported_Type {panic("free validation")}
''','import "thor:basic"\nBad::struct{number:int,opaque:rawptr}\n'))

    def test_obvious_unsupported_root_has_library_compile_error_at_thor_call(self):
        for declaration in ['value:rawptr','value:any','value:[^]int','value:proc()']:
            with self.subTest(declaration=declaration):
                _,result=self.invoke(self.program(declaration+'\n_,_=basic.deepclone(&value)',
                    'import "thor:basic"\n'),command='check',fail='deepclone does not support')
                self.assertIn('main.thor',result.stderr)
                self.assertNotIn('Thor expression',result.stderr)

    def test_commented_demo_checked_and_unchecked(self):
        source=(ROOT/'examples/deepclone/demo.thor').read_text()
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert',
                                  *TRACKING]]:
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertNotIn('still allocated',result.stderr)
                self.assertIn('deepclone demo passed',result.stdout)

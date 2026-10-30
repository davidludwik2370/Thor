"""Enable gated using forms in generated Thor files, preserving original text."""
import json
import subprocess
import unittest

from tests import test_thor as base
from tests import test_diagnostics as diagnostic_base
from thorlang.sourcemap import SourceMap


class UsingDefaultTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def generated(self, source, **kwargs):
        root,_ = self.invoke(source, **kwargs)
        return (root/'output/main.odin').read_text()

    def test_standalone_record_pointer_multiple_and_package_statements(self):
        source=self.program('''
value:=Record{4};using value;assert(x==4);x=8;assert(value.x==8)
{
 first,second:=First{3},Second{5};using first,second;assert(a+b==8)
}
{
 pointed:=Record{4};pointer:=&pointed;using pointer;x=9;assert(pointed.x==9)
}
{
 using math;assert(sqrt(4.0)==2.0)
}
''','import "core:math"\nRecord::struct{x:int}\nFirst::struct{a:int}\nSecond::struct{b:int}\n')
        code=self.generated(source)
        self.assertTrue(code.startswith('#+feature using-stmt\n'))
        self.assertEqual(code.count('#+feature using-stmt'),1)

    def test_procedure_parameters_generics_and_procedure_types(self):
        source=self.program('''
record:=Record{4}
assert(read(record)==4&&generic(record)==4)
write(&record);assert(record.x==8)
callback:Callback=read;assert(callback(record)==8)
holder:=Holder{callback=read};assert(holder.callback(record)==8)
anonymous:=proc(using record:Record)->int{return x};assert(anonymous(record)==8)
''','''
Record::struct{x:int}
Callback::proc(using record:Record)->int
Holder::struct{callback:proc(using record:Record)->int}
read::proc(using record:Record)->int{return x}
generic::proc(using record:$T/Record)->int{return x}
write::proc(using record:^Record){x=8}
''')
        code=self.generated(source)
        self.assertTrue(code.startswith('#+feature using-stmt\n'))
        self.assertEqual(code.count('#+feature using-stmt'),1)

    def test_unused_procedure_type_still_needs_feature(self):
        code=self.generated(self.program('_=size_of(Callback)',
            'Record::struct{x:int}\nCallback::proc(using record:Record)->int\n'))
        self.assertTrue(code.startswith('#+feature using-stmt\n'))

    def test_embedded_fields_and_using_declarations_do_not_insert_tag(self):
        code=self.generated(self.program('''
using local:=Record{4};assert(x==4)
holder:=Holder{{8}};assert(holder.x==8)
''','Record::struct{x:int}\nHolder::struct{using record:Record}\n'))
        self.assertNotIn('#+feature using-stmt',code)

    def test_ordinary_fields_comments_and_strings_do_not_insert_tag(self):
        code=self.generated(self.program('''
// using value
// proc(using record:Record)
/* using value; proc(using record:Record) */
text:="using value; proc(using record:Record)";_=text
value:="#+feature using-stmt";_=value
'''))
        self.assertFalse(code.startswith('#+feature using-stmt'))
        self.assertEqual(code.count('#+feature using-stmt'),1) # Only the quoted text.

    def test_explicit_feature_tags_are_preserved_without_duplicate(self):
        for tag in ['#+feature using-stmt', '#+feature dynamic-literals using-stmt',
                    '#+feature using-stmt dynamic-literals', '#+ feature using-stmt']:
            with self.subTest(tag=tag):
                source=tag+'\n'+self.program('value:=Record{4};using value;assert(x==4)',
                                            'Record::struct{x:int}\n')
                code=self.generated(source)
                self.assertTrue(code.startswith(tag+'\n'),code)
                self.assertEqual(code.count('using-stmt'),1)

    def test_feature_looking_comment_does_not_suppress_real_tag(self):
        source='// #+feature using-stmt\n'+self.program('value:=Record{4};using value;assert(x==4)',
                                                                 'Record::struct{x:int}\n')
        code=self.generated(source)
        self.assertTrue(code.startswith('#+feature using-stmt\n// #+feature using-stmt\n'))

    def test_other_file_tags_and_header_comments_are_preserved(self):
        source='// License 😀\n#+feature dynamic-literals\n#+vet !unused\n'+self.program(
            'value:=Record{4};using value;assert(x==4)','Record::struct{x:int}\n')
        code=self.generated(source)
        self.assertTrue(code.startswith('#+feature using-stmt\n'+source[:source.index('package')]))

    def test_explicit_opt_out_is_respected(self):
        source='#+feature !using-stmt\n'+self.program('value:=Record{4};using value;assert(x==4)',
                                                               'Record::struct{x:int}\n')
        code=self.generated(source, command='check',fail="'using' has been disallowed")
        self.assertTrue(code.startswith('#+feature !using-stmt\n'))
        self.assertEqual(code.count('using-stmt'),1)

    def test_using_in_odin_dependencies_retains_native_feature_rules(self):
        self.invoke(self.program('lib.work()','import "lib"\n'),
                    files={'lib/main.odin':'package lib\nR::struct{x:int}\nwork::proc(){r:=R{4};using r;_=x}\n'},
                    command='check',fail="'using' has been disallowed")

    def test_feature_is_per_file_and_imported_thor_packages_work(self):
        source=self.program('assert(lib.read({4})==4)','import "lib"\n')
        root,_=self.invoke(source,files={
            'lib/main.thor':'package lib\nRecord::struct{x:int}\nread::proc(using record:Record)->int{return x}\n',
            'lib/other.thor':'package lib\nHolder::struct{using record:Record}\n'})
        files={path.name:path.read_text() for path in (root/'output').rglob('*.odin') if path.name in {'main.odin','other.odin'}}
        self.assertTrue(any(text.startswith('#+feature using-stmt') for text in files.values()),files)
        self.assertNotIn('#+feature using-stmt',(root/'output/main.odin').read_text())
        other=next(path for path in (root/'output').rglob('other.odin'))
        self.assertNotIn('#+feature using-stmt',other.read_text())

    def test_pure_and_run_helpers_receive_the_feature_automatically(self):
        code=self.generated(self.program('''
record:=Record{4};assert(read(record)==4&&PARAM==4&&BODY==4&&AUTO==4)
''','''
Record::struct{x:int}
read::fn(using record:Record)->int{return x}
ordinary::proc(record:Record)->int{using record;return x}
PARAM::#run read(Record{4})
BODY::#run ordinary(Record{4})
AUTO::read(Record{4})
'''))
        self.assertTrue(code.startswith('#+feature using-stmt\n'))

    def test_source_maps_preserve_original_lines_bytes_and_generated_header(self):
        for bom in ['', '\ufeff']:
            with self.subTest(bom=bool(bom)):
                source=bom+'// 😀 original header\r\npackage main\r\nR::struct{x:int}\r\nmain::proc(){r:=R{4};using r;assert(x==4)}\r\n'
                entry,output,_,_=diagnostic_base.DiagnosticTests.invoke(self,source,success=True,flags=['-debug'])
                raw=(output/'main.odin').read_bytes()
                self.assertEqual(entry.read_bytes(),source.encode())
                self.assertTrue(raw.startswith(bom.encode()+b'#+feature using-stmt\r\n'))
                packet=json.loads((output/'main.odin.map.json').read_text())
                self.assertEqual(packet['sources'][str(entry)],source)
                mapping=SourceMap(packet['segments'])
                self.assertEqual(mapping.lookup(len(bom.encode())).kind,'support')
                for start,end,file,original_start,original_end,kind,exact in packet['segments']:
                    if exact:self.assertEqual(raw[start:end],packet['sources'][file].encode()[original_start:original_end])
                token=raw.index(b'assert')
                self.assertEqual(mapping.lookup(token,6).start,source.encode().index(b'assert'))

    def test_edits_add_and_remove_the_automatic_feature_without_stale_cache(self):
        original=self.program('r:=R{4};assert(r.x==4)','R::struct{x:int}\n')
        root,_=self.invoke(original,command='check')
        entry=root/'input/main.thor';output=root/'output/main.odin'
        using=original.replace('assert(r.x==4)','using r;assert(x==4)')
        for text,enabled in [(using,True),(original,False),(using,True)]:
            entry.write_text(text)
            result=subprocess.run([str(base.THOR),'check',str(entry),'--output-dir',str(root/'output')],
                                  capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(output.read_text().startswith('#+feature using-stmt'),enabled)
            self.assertEqual(entry.read_text(),text)


if __name__=='__main__':
    unittest.main()

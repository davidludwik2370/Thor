"""@(unused) silences unused diagnostics and makes references an error."""
from tests.test_rust_compiler import compiler_engines
import json
import subprocess
import unittest
from tests import test_unused_vet as usage
from tests import test_rust_compiler as native


class UnusedAttributeTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=usage.UnusedVetTests.setUp
    write=usage.UnusedVetTests.write
    invoke=usage.UnusedVetTests.invoke

    def test_variables_constants_types_procedures_groups_and_mixed_attributes(self):
        text='''package main
@(unused) Thing::struct{x:int}
@(unused) Kind::enum{a,b}
@(unused) ANSWER::42
@(unused) first,second::1,2
@(unused) global:int
@(unused) first_global,second_global:int
@(unused,private="file") helper::proc()->int{return 1}
@(unused) pure::fn()->int{return 2}
@(unused) generic::proc(value:$T)->T{return value}
@(unused,export=false) hidden::proc(){}
main::proc(){
 @(unused) local:=10
 @(unused) a,b:=1,2
 @(unused) fixed:>[1,2,3]
 @(unused) Local::struct{x:int}
}
'''
        for engine in compiler_engines():
            for flags in ([],['--vet=unused']):
                with self.subTest(engine=engine,flags=flags):
                    self.write(text)
                    _,errors=self.invoke(engine,*flags,command='check')
                    self.assertEqual(errors,[])
            self.write(text)
            _,warnings=self.invoke(engine,'--vet=unused','--apply')
            self.assertEqual(warnings,[])
            self.assertEqual(self.source.read_text(),text)

    def test_reads_writes_addresses_queries_and_explicit_discards_are_uses(self):
        expressions=['_=value','value=1','value+=1','_= &value','_=type_of(value)',
                     '_=size_of(value)','_=f"{value}"','for _ in 0..<value{}',
                     'if value==0{}','_= [value]',
                     '_= [value for _ in [1,2]]','_= map[int]int{value=1}']
        for body in expressions:
            text='package main\n@(unused) value:int\nmain::proc(){'+body+'}\n'
            # Find the reference separately from the declaration.
            for engine in compiler_engines():
                with self.subTest(engine=engine,body=body):
                    self.write(text)
                    _,errors=self.invoke(engine,command='check',status=1)
                    self.assertEqual(errors[0]['code'],'thor/unused-reference',errors)
                    self.assertEqual(errors[0]['pos']['offset'],text.index('value',text.index('main::')))
                    self.assertEqual(errors[0]['pos']['end_offset']-errors[0]['pos']['offset'],5)

    def test_calls_callbacks_aliases_and_compile_time_references_are_rejected(self):
        for suffix in ['main::proc(){helper()}',
                       'main::proc(){callback:=helper;_=callback}',
                       'alias::helper\nmain::proc(){}',
                       'VALUE::#run helper()\nmain::proc(){_=VALUE}',
                       'main::proc(){_=result_type_of(helper)}']:
            text='package main\n@(unused) helper::fn()->int{return 3}\n'+suffix+'\n'
            for engine in compiler_engines():
                with self.subTest(engine=engine,suffix=suffix):
                    self.write(text)
                    _,errors=self.invoke(engine,command='check',status=1)
                    self.assertEqual(errors[0]['code'],'thor/unused-reference',errors)
                    self.assertEqual(errors[0]['pos']['offset'],text.rindex('helper'))

    def test_type_annotations_literals_and_aliases_are_uses(self):
        for suffix in ['main::proc(){value:Record;_=value}',
                       'Alias::Record\nmain::proc(){}',
                       'main::proc(){_=Record{1}}',
                       'take::proc(value:Record){}\nmain::proc(){}']:
            text='package main\n@(unused) Record::struct{x:int}\n'+suffix+'\n'
            for engine in compiler_engines():
                with self.subTest(engine=engine,suffix=suffix):
                    self.write(text)
                    _,errors=self.invoke(engine,command='check',status=1)
                    self.assertEqual(errors[0]['code'],'thor/unused-reference',errors)
                    self.assertEqual(errors[0]['pos']['offset'],text.rindex('Record'))

    def test_shadowing_field_names_named_arguments_attributes_and_loop_bindings(self):
        text='''package main
@(unused) value::3
@(unused) unused::1
accept::proc(value:int)->int{return value}
main::proc(){
 Record::struct{value:int}
 record:=Record{value=1};_=record.value
 _=accept(value=2)
 {value:=2;_=value}
 for value in [1,2] {_=value}
 _=f"{record.value}"
 _= #config(value, 4)
}
'''
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                _,errors=self.invoke(engine,command='check')
                self.assertEqual(errors,[])

    def test_qualified_selective_wildcard_and_using_imports_resolve_original_declaration(self):
        library=self.app/'library'
        library.mkdir()
        declaration='package library\n@(unused) helper::proc()->int{return 3}\n'
        (library/'main.thor').write_text(declaration)
        for imported,body in [('import "library"','_=library.helper()'),
                              ('import "library" / helper','_=helper()'),
                              ('import * "library"','_=helper()'),
                              ('import "library"','using library;_=helper()')]:
            text='package main\n'+imported+'\nmain::proc(){'+body+'}\n'
            for engine in compiler_engines():
                with self.subTest(engine=engine,imported=imported,body=body):
                    self.write(text)
                    _,errors=self.invoke(engine,command='check',status=1)
                    self.assertEqual(errors[0]['code'],'thor/unused-reference',errors)
                    self.assertEqual(errors[0]['pos']['offset'],text.rindex('helper'))
                    self.assertEqual(errors[0]['related'][0]['pos']['file'],str((library/'main.thor').resolve()))

    def test_known_inactive_when_arms_and_files_do_not_count(self):
        text='''package main
@(unused) value::3
main::proc(){
 when false {_=value}
 when ODIN_DEBUG {_=value}
 when true {_=0} else {_=value}
}
'''
        ignored=self.app/'ignored.thor'
        ignored.write_text('#+build ignore\npackage main\n@(unused) x::1\nuse::x\n')
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                _,errors=self.invoke(engine,command='check')
                self.assertEqual(errors,[])
                _,errors=self.invoke(engine,'-debug',command='check',status=1)
                self.assertEqual(errors[0]['code'],'thor/unused-reference',errors)

    def test_attribute_syntax_and_automatically_used_declarations(self):
        cases=[('@(unused=false) x::1','unused=false'),
               ('@(unused,unused) x::1','unused,unused'),
               ('@(unused) _::1','unused'),
               ('@(unused) main::proc(){}','unused'),
               ('@(unused,require) x::proc(){}','unused'),
               ('@(unused,init) x::proc(){}','unused'),
               ('@(unused,export) x::proc(){}','unused')]
        for body,_ in cases:
            text='package main\n'+body+('' if 'main::' in body else '\nmain::proc(){}')+'\n'
            for engine in compiler_engines():
                with self.subTest(engine=engine,body=body):
                    self.write(text)
                    _,errors=self.invoke(engine,command='check',status=1)
                    self.assertEqual(errors[0]['code'],'thor/unused-attribute',errors)
                    self.assertEqual(errors[0]['pos']['offset'],text.index('unused'))

    def test_compile_time_conditions_are_resolved_before_checking_their_arms(self):
        text='''package main
@(unused) reserved::3
choose::fn()->bool{return false}
SELECTED::#run choose()
main::proc(){when SELECTED {_=reserved}}
'''
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                self.invoke(engine,command='check')
                self.write(text.replace('return false','return true'))
                _,errors=self.invoke(engine,command='check',status=1)
                self.assertEqual(errors[0]['code'],'thor/unused-reference',errors)

    def test_single_declaration_blocks_do_bodies_and_unrolled_loops(self):
        text='''package main
main::proc(){
 if true {@(unused) value:=10}
 if true do @(unused) another:=20
 for _ in 0..<2 {@(unused) value::3}
 #unroll for _ in 0..<2 {@(unused) value:=4}
}
'''
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                _,errors=self.invoke(engine,'-warnings-as-errors',command='check')
                self.assertEqual(errors,[])

    def test_conditional_export_is_only_an_automatic_use_when_enabled(self):
        text='''package main
choose::fn()->bool{return false}
EXPORTED::#run choose()
@(unused,export=EXPORTED) hidden::proc(){}
main::proc(){}
'''
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                self.invoke(engine,command='check')
                self.write(text.replace('return false','return true'))
                _,errors=self.invoke(engine,command='check',status=1)
                self.assertEqual(errors[0]['code'],'thor/unused-attribute',errors)

    def test_tag_does_not_suppress_body_warnings_or_pure_advice(self):
        text='package main\n@(unused) helper::proc(parameter:int)->int{local:=3;return 1}\nmain::proc(){}\n'
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                _,warnings=self.invoke(engine)
                self.assertEqual([item['code'] for item in warnings],['thor/unused','thor/unused','thor/prefer-fn'])
                self.assertIn("'parameter'",warnings[0]['msgs'][0])
                self.assertIn("'local'",warnings[1]['msgs'][0])
                _,warnings=self.invoke(engine,'--apply')
                self.assertEqual([item['code'] for item in warnings],['thor/unused','thor/unused'])
                self.assertEqual(self.source.read_text(),text.replace('helper::proc','helper::fn'))

    def test_initializers_keep_their_effects_in_checked_and_unchecked_builds(self):
        text='''package main
import "core:fmt"
counter:int
create::proc "contextless" ()->int{counter+=1;return counter}
@(unused) global:int
main::proc(){
 @(unused) local:=create()
 @(unused) second:=create()
 if counter!=2 {panic("initializers changed")}
 fmt.println(counter)
}
'''
        for engine in compiler_engines():
            for flags in ([],native.UNCHECKED):
                with self.subTest(engine=engine,flags=flags):
                    self.write(text)
                    result,errors=self.invoke(engine,*flags,command='run')
                    self.assertEqual(errors,[])
                    self.assertEqual(result.stdout.strip(),'2')

    def test_cache_cannot_bypass_new_attribute_and_errors_recover_after_removal(self):
        text='package main\ncalculate::fn()->int{return 42}\nVALUE::#run calculate()\nmain::proc(){_=VALUE}\n'
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                self.write(text)
                self.invoke(engine,command='check')
                self.write(text.replace('calculate::','@(unused) calculate::'))
                _,errors=self.invoke(engine,command='check',status=1)
                self.assertEqual(errors[0]['code'],'thor/unused-reference',errors)
                self.write(text)
                self.invoke(engine,command='check')

    def test_ast_is_still_a_syntax_dump_for_an_invalid_reference(self):
        self.write('package main\n@(unused) value::1\nmain::proc(){_=value}\n')
        for compiler in ([native.PYTHON,native.RUST] if native.reference_enabled() else [native.RUST]):
            with self.subTest(compiler=compiler):
                result=subprocess.run([str(compiler),'ast',str(self.app)],cwd=self.root,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertTrue(json.loads(result.stdout)['sources'])


if __name__=='__main__':unittest.main()

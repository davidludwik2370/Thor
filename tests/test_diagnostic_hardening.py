"""Real-backend regressions for contextual errors and generated-name hygiene."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from tests import test_diagnostics as diagnostics
from tests import test_thor as base
from thorlang.hygiene import qualify
from thorlang.sourcemap import MappingBuilder
from thorlang.sourcemap import SourceMap
from thorlang.diagnostics import Diagnostics


class DiagnosticHardeningTests(unittest.TestCase):
    invoke=diagnostics.DiagnosticTests.invoke
    at=diagnostics.DiagnosticTests.at

    def test_expression_restoration_preserves_literal_values_and_real_type_names(self):
        original=b'folded_constant values[i]'
        emitted=b'8;__generated_access (i)'
        mapping=SourceMap([],[[0,1,'/original.thor',0,15],[2,len(emitted),'/original.thor',16,len(original)]])
        renderer=Diagnostics(maps={'/generated.odin':mapping},generated={'/generated.odin':emitted})
        renderer.prefixes=['__generated_']
        renderer.raw['/original.thor']=original
        native="Cannot convert numeric value '300' to 'i8'"
        restored=renderer.restore_expressions([native,"Cannot assign value '__generated_access(i)' of type 'int' to 'string'"])
        self.assertEqual(restored[0],native)
        self.assertIn("value 'values[i]'",restored[1])

    def test_undeclared_index_operands_keep_only_their_primary_error(self):
        for body in ['i:=1;missing[i]=2', 'i:=1;_=missing[i]',
                     'values:=[1,2];_=values[missing]', 'values:=[1,2];_=values[missing:]',
                     'values:=[1,2];_=values[:missing]', 'values:=[1,2];values[missing]=2',
                     'values:=[1,2];text:=f"{values[missing]}";_=text',
                     'values:=[1,2];_=switch{case true:values[missing];case:1}']:
            with self.subTest(body=body):
                source='package main\nmain::proc(){/* 😀 */ '+body+'}\n'
                errors=self.at(source,'missing',message='Undeclared name: missing')
                self.assertEqual(len(errors),1,errors)

    def test_independent_errors_on_same_line_and_in_same_index_survive(self):
        for body,names in [
            ('i:=1;missing[i]=2;other[i]=3',{'missing','other'}),
            ('values:=[1,2];_=values[missing:other]',{'missing','other'}),
            ('values:=[1,2];_=values[missing];bad:int="wrong";_=bad',{'missing'}),
        ]:
            with self.subTest(body=body):
                _,_,errors,_=self.invoke('package main\nmain::proc(){'+body+'}\n')
                for name in names:self.assertTrue(any('Undeclared name: '+name in '\n'.join(e['msgs']) for e in errors),errors)
                self.assertEqual(len(errors),2,errors)
                self.assertNotIn('Thor expression',json.dumps(errors))
        source='package main\nmain::proc(){values:=[1,2];record:=struct{x:int}{1};_=values[record.missing]}\n'
        errors=self.at(source,'record.missing',message='has no field')
        self.assertEqual(len(errors),1,errors)

    def test_missing_interpolation_operands_do_not_report_generated_type_cascades(self):
        for imports in ['', 'import * "thor:basic"\n']:
            for expression in ['(1-p_loss) * 100','(1-p_loss) * (100)',
                               '(1-p_loss) * -100','(1-p_loss) * (50+50)',
                               '100 * (1-p_loss)','(1-p_loss) ** 2','number_value(100)+p_loss','p_loss+number_value(100)']:
                with self.subTest(imports=imports,expression=expression):
                    source='package main\n'+imports+'number_value::proc(value:int)->int{return value}\nmain::proc(){/* 😀 */ p_win_pad:>len(f"{'+expression+':.2f}");_=p_win_pad}\n'
                    errors=self.at(source,'p_loss',message='Undeclared name: p_loss')
                    self.assertEqual(len(errors),1,errors)
                    self.assertEqual(errors[0]['msgs'],['Undeclared name: p_loss'])
                    self.assertEqual(errors[0]['pos']['end_offset']-errors[0]['pos']['offset'],len('p_loss'))

    def test_missing_members_and_multiple_root_errors_keep_precise_ranges(self):
        prefix='package main\nimport * "thor:basic"\nRecord::struct{x:f64}\n'
        source=prefix+'main::proc(){record:=Record{1};_=f"{(1-record.absent)*100:.2f}"}\n'
        errors=self.at(source,'record.absent',message='has no field')
        self.assertEqual(len(errors),1,errors)
        source=prefix+'main::proc(){_=f"{missing}|{other}"}\n'
        _,_,errors,_=self.invoke(source)
        self.assertEqual({e['msgs'][0] for e in errors},{'Undeclared name: missing','Undeclared name: other'})

    def test_inference_cleanup_preserves_independent_errors_and_field_boundaries(self):
        prefix='package main\nimport * "thor:basic"\nbool_number::proc(value:bool)->int{return 1}\nneed_type::proc($T:typeid)->int{return 0}\n'
        for body,expected in [
            ('_=f"{(1-missing)*100:.2f}";bad:bool=100;_=bad','Cannot convert'),
            ('_=f"{(1-missing)*100:.2f}|{bool_number(100)}"','Cannot convert'),
            ('_=f"{(1-missing)*100:.2f}|{need_type(100)}"','Expected a type'),
            ('_=f"{(1-missing)*100:.2f}|{Rational(1,3):g}"','requires explicit precision'),
        ]:
            with self.subTest(body=body):
                _,_,errors,_=self.invoke(prefix+'main::proc(){'+body+'}\n')
                self.assertEqual(len(errors),2,errors)
                self.assertTrue(any(e['msgs']==['Undeclared name: missing'] for e in errors),errors)
                self.assertTrue(any(expected in '\n'.join(e['msgs']) for e in errors),errors)
                self.assertNotIn('Thor expression',json.dumps(errors))

    def test_independent_argument_error_in_same_arithmetic_expression_survives(self):
        prefix='package main\nbool_number::proc(value:bool)->int{return 1}\nneed_type::proc($T:typeid)->int{return 0}\n'
        for expression,message in [('bool_number(100)+missing',"to 'bool'"),
                                   ('need_type(100)+missing','Expected a type')]:
            with self.subTest(expression=expression):
                _,_,errors,_=self.invoke(prefix+'main::proc(){_=f"{'+expression+'}"}\n')
                self.assertEqual(len(errors),2,errors)
                self.assertTrue(any(e['msgs']==['Undeclared name: missing'] for e in errors),errors)
                self.assertTrue(any(message in e['msgs'][0] for e in errors),errors)
                self.assertNotIn('Thor expression',json.dumps(errors))

    def test_inference_errors_without_a_primary_cause_are_retained(self):
        source='package main\nneed_type::proc($T:typeid)->int{return 0}\nmain::proc(){_=f"{need_type(100)}"}\n'
        errors=self.at(source,'100',message='Expected a type')
        self.assertTrue(errors)
        source='package main\nunused::proc($T:typeid){_=f"{(1-missing)*100:.2f}"}\nmain::proc(){when false{_=f"{(1-missing)*100:.2f}"}}\n'
        self.invoke(source,success=True)

    def test_imported_generic_interpolation_reports_its_root_at_the_definition(self):
        source='package main\nimport "library"\nmain::proc(){_=library.render(1.0)}\n'
        library='// 😀 library\npackage library\nimport * "thor:basic"\nrender::proc(value:$T)->string{return f"{(value-missing)*100:.2f}"}\n'
        entry,_,errors,_=self.invoke(source,files={'library/render.thor':library})
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['msgs'][0],'Undeclared name: missing')
        self.assertEqual(errors[0]['pos']['file'],str(entry.parent/'library/render.thor'))
        self.assertEqual(errors[0]['pos']['offset'],library.encode().index(b'missing'))

    def test_invalid_sequence_bounds_have_their_original_ranges_and_types(self):
        for body,needle,expected in [
            ('values:=[1,2];index:f64=1;_=values[index]','index]','Sequence indices must have an integer type'),
            ('values:=[1,2];bound:="x";_=values[bound:]','bound:]','The lower slice bound must have an integer type'),
            ('values:=[1,2];bound:=false;_=values[:bound]','bound]','The upper slice bound must have an integer type'),
            ('values:=[1,2];_=values[1.5]','1.5','Sequence indices must have an integer type'),
            ('values:=[1,2];low:f64=0;high:="x";_=values[low:high]','low:high','The lower slice bound must have an integer type'),
            ('values:=[1,2];index:Fraction=1;_=values[index]','index]','type \'Fraction\''),
        ]:
            with self.subTest(body=body):
                source='package main\nFraction::distinct f64\nmain::proc(){/* 😀 */ '+body+'}\n'
                errors=self.at(source,needle,message=expected)
                self.assertNotIn('Thor expression',json.dumps(errors))
                self.assertNotIn('runtime/sequence',json.dumps(errors))
                self.assertEqual(len(errors),2 if 'low:high' in body else 1,errors)

    def test_generic_instantiation_keeps_concrete_invalid_index_type(self):
        source='''package main
read::proc(values:$T,index:$I)->int{return values[index]}
main::proc(){values:=[1,2];_=read(values[:],f64(1))}
'''
        errors=self.at(source,'index]}',message="has type 'f64'")
        self.assertEqual(len(errors),1,errors)
        self.assertNotIn('Thor expression',json.dumps(errors))
        source='package main\nA::[2]int{1,2}\nVALUE::A[1.5]\nmain::proc(){_=VALUE}\n'
        errors=self.at(source,'1.5',message='Sequence indices must have an integer type')
        self.assertEqual(len(errors),1,errors)

    def test_result_mismatches_quote_original_expressions(self):
        for body,expected in [
            ('values:=[1,2];i:=1;value:string=values[i];_=value','values[i]'),
            ('values:=[1,2];i:=1;value:string=values[i:];_=value','values[i:]'),
            ('n:=1;value:int=f"n {n}";_=value','f"n {n}"'),
            ('n:=1;value:string=switch n{case 1:2;case:3};_=value',None),
            ('n:=2;value:string=n**3;_=value','n**3'),
        ]:
            with self.subTest(body=body):
                _,_,errors,_=self.invoke('package main\nmain::proc(){'+body+'}\n')
                if expected:self.assertTrue(any(expected in '\n'.join(error['msgs']) for error in errors),errors)
                self.assertNotIn('Thor expression',json.dumps(errors))

    def test_membership_context_retains_each_element_and_points_to_needle(self):
        for op in ['in','not_in']:
            source='package main\nRecord::struct{x:int}\nmain::proc(){/* 😀 */ record:=Record{1};_=record '+op+' [1,2]}\n'
            entry,_,errors,_=self.invoke(source)
            self.assertEqual(len(errors),2,errors)
            for error in errors:
                self.assertIn('Cannot convert',error['msgs'][0])
                self.assertIn("comparable with 'record' (type 'Record')",error['msgs'][1])
                self.assertIn(source.encode()[error['pos']['offset']:error['pos']['end_offset']],(b'1',b'2'))
                note=error['related'][0]
                self.assertEqual(note['pos']['file'],str(entry))
                self.assertEqual(note['pos']['offset'],source.encode().index(b'record '+op.encode()))

    def test_membership_does_not_relabel_failures_inside_explicit_values(self):
        source='package main\nRecord::struct{x:int}\nmain::proc(){record:=Record{1};_=record in [Record{"wrong"}]}\n'
        _,_,errors,_=self.invoke(source)
        wrong=next(error for error in errors if 'wrong' in '\n'.join(error['msgs']))
        self.assertNotIn('In this membership test', '\n'.join(wrong['msgs']))

    def test_inactive_errors_and_valid_generic_map_enum_unsigned_indices(self):
        source='''package main
Index::distinct i8
E::enum{A,B}
read::proc(values:$T,index:$I)->int{return values[index]}
unused::proc($T:typeid){i:=1;missing[i]=2}
main::proc(){
    when false {values:=[1,2];_=values[missing];_=values[f64(1)]}
    values:=[1,2];i:Index = -1;_=read(values[:],i)
    wide:u128=1;_=values[wide]
    keyed:map[f64]int;keyed[-1.5]=7;assert(read(keyed,-1.5)==7);delete(keyed)
    names:map[string]int;names["hi"]=8;value,ok:=names["hi"];assert(value==8 && ok);delete(names)
    enumerated:[E]int;enumerated[.B]=9;assert(read(enumerated,E.B)==9)
}
'''
        self.invoke(source,success=True)


class BuiltinHygieneTests(unittest.TestCase):
    invoke=base.ThorTests.invoke

    def test_user_queries_and_generated_queries_keep_separate_bindings(self):
        source='''package main
import native "base:builtin"
type_of::proc()->[3]int{return {1,2,3}}
len::proc()->int{return 17}
cap::proc()->int{return 23}
main::proc(){
    i:=-1
    native.assert(type_of()[i]==3 && len()==17 && cap()==23)
    values:=[4,5,6];slice:=values[i:];native.assert(native.len(slice)==1 && slice[0]==6)
}
'''
        self.invoke(source)

    def test_local_and_parameter_shadows_keep_original_values(self):
        source='''package main
import native "base:builtin"
read::proc(values:[]int,type_of:proc()->int)->int{return values[type_of()]}
main::proc(){
    type_of:=42;len:=7
    values:=[4,5,6];i:=-1
    native.assert(values[i]==6 && type_of==42 && len==7)
    native.assert(read(values[:],proc()->int{return -1})==6)
}
'''
        self.invoke(source)

    def test_qualified_and_selected_user_imports_are_not_rewritten(self):
        files={'shadow/shadow.thor':'package shadow\ntype_of::proc()->[2]int{return {1,2}}\nlen::proc()->int{return 17}\n'}
        for imports,expr in [('import "shadow"','shadow.type_of()'),
                             ('import _ "shadow" / type_of,len','type_of()'),
                             ('import * "shadow"','type_of()')]:
            with self.subTest(imports=imports):
                self.invoke('package main\nimport native "base:builtin"\n'+imports+'\nmain::proc(){i:=-1;native.assert('+expr+'[i]==2)}\n',files=files)

    def test_other_package_files_can_shadow_generated_builtins(self):
        self.invoke('package main\nimport native "base:builtin"\nmain::proc(){i:=-1;native.assert(type_of()[i]==2)}\n',
                    files={'names.thor':'package main\ntype_of::proc()->[2]int{return {1,2}}\n'},directory=True)

    def test_runtime_operations_strings_comments_and_user_type_names(self):
        source='''package main
import native "base:builtin"
len::struct{x:int}
make::proc()->int{return 8}
delete::proc(x:int){native.assert(x==8)}
main::proc(){
    /* type_of( /* len( */ make( */
    a:len={1};data:=[a];i:=-1;native.assert(data[i].x==1)
    text:=f"type_of( len( make( {make()}"
    native.assert(text=="type_of( len( make( 8")
    native.delete(text);delete(make())
}
'''
        self.invoke(source)

    def test_pure_and_run_queries_keep_hygiene_on_cold_and_cached_paths(self):
        source='''package main
import native "base:builtin"
type_of::proc()->[3]int{return {1,2,3}}
compute::fn(index:int)->int{return type_of()[index]}
VALUE::#run compute(-1)
main::proc(){native.assert(VALUE==3 && compute(-2)==2)}
'''
        root,_=self.invoke(source)
        import subprocess
        result=subprocess.run([str(base.THOR),'run',str(root/'input/main.thor'),'--output-dir',str(root/'output')],capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_no_shadow_needs_no_builtin_import(self):
        _,result=self.invoke('package main\nmain::proc(){values:=[1,2];i:=-1;assert(values[i]==2)}\n',command='transpile')
        self.assertNotIn('base:builtin',Path(result.stdout.strip()).read_text())

    def test_pure_parameter_named_like_builtin_remains_a_value(self):
        self.invoke('''package main
import native "base:builtin"
work::fn(type_of:int)->int{return type_of+1}
VALUE::#run work(2)
main::proc(){native.assert(VALUE==3 && work(7)==8)}
''')

    def test_qualification_preserves_exact_unicode_and_all_generated_coordinates(self):
        builder=MappingBuilder()
        copied=builder.wrap('"😀len("', '/source.thor', 10, 20, exact=True)
        call=builder.wrap('type_of('+copied+')', '/source.thor', 0, 20, 'Index')
        code,mapping=builder.finish(builder.wrap(call, '/source.thor', 0, 20, 'expression-helper'))
        source=SimpleNamespace(path=Path('/source.thor'),prefix='__safe_',imports={'type_of':'user'},selected={},
                               resolver=SimpleNamespace(scopes={}),nodes=[])
        code,mapping=qualify(source,code,mapping)
        self.assertIn('__safe_builtin.type_of("😀len(")',code)
        raw=code.encode()
        literal=raw.index('"😀'.encode())
        self.assertEqual(mapping.lookup(literal).start,10)
        self.assertTrue(mapping.lookup(literal).exact)
        self.assertEqual(mapping.expansions[0][:2],[0,literal+len('"😀len(")'.encode())])
        self.assertEqual(mapping.regions[0][:2],mapping.expansions[0][:2])


if __name__=='__main__':unittest.main()

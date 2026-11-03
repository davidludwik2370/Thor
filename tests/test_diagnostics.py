"""Source-map and diagnostic regressions, checked against the real backend."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from thorlang.sourcemap import MappingBuilder, SourceMap, position

ROOT = Path(__file__).resolve().parents[1]


class MapTests(unittest.TestCase):
    def test_nested_copied_and_synthetic_fragments(self):
        b = MappingBuilder()
        text = b.wrap("generated(" + b.wrap("é😀", "/source.thor", 10, 16, exact=True) + ")",
                      "/source.thor", 0, 20, "array")
        code, mapping = b.finish(text)
        self.assertEqual(code, "generated(é😀)")
        self.assertEqual(mapping.lookup(10, 2).start, 10)
        self.assertEqual(mapping.lookup(12, 4).start, 12)
        self.assertEqual(mapping.lookup(16).kind, "array")
        self.assertEqual(mapping.lookup(16).end, 20)

    def test_duplicated_reordered_fragments_keep_origins(self):
        b = MappingBuilder()
        a = b.wrap("a", "/a.thor", 3, 4, exact=True)
        c = b.wrap("c", "/c.thor", 9, 10, exact=True)
        code, mapping = b.finish(b.wrap(c + a + c, "/a.thor", 0, 4))
        self.assertEqual(code, "cac")
        self.assertEqual([mapping.lookup(i).file for i in range(3)], ["/c.thor", "/a.thor", "/c.thor"])

    def test_utf16_positions_crlf_and_eof(self):
        raw = "one\r\n\té😀x\n".encode()
        self.assertEqual(position(raw, raw.index(b"x")), (2, 5))
        self.assertEqual(position(raw, len(raw)), (3, 1))
        self.assertEqual(position(raw, 1000), (3, 1))


class DiagnosticTests(unittest.TestCase):
    def test_type_used_as_array_has_one_error_and_declaration_note(self):
        for operation in ['_=Rows[0]', 'i:=1;_=Rows[i]', 'i:=1;Rows[i]={1}',
                          'i:=1;Rows[i]+={1}', 'i:=-1;_=Rows[i:]', '_=Rows[:]']:
            with self.subTest(operation=operation):
                source='// 😀 original header\npackage main\nRows :: [3]struct{x:int}\nmain::proc(){'+operation+'}\n'
                entry,output,errors,result=self.invoke(source)
                self.assertEqual(len(errors),1,errors)
                error=errors[0]
                self.assertIn('it names a type, not an array value',error['msgs'][0])
                self.assertIn('Rows: [3]struct{x:int}',error['msgs'][1])
                self.assertEqual(error['pos']['offset'],source.encode().rindex(b'Rows'))
                self.assertEqual(error['pos']['end_offset']-error['pos']['offset'],4)
                note=error['related'][0]
                self.assertEqual(note['pos']['file'],str(entry))
                self.assertEqual(note['pos']['offset'],source.encode().index(b'Rows'))
                self.assertNotIn('Thor expression',json.dumps(errors))
                self.assertNotIn(str(output),json.dumps(errors))

    def test_type_operand_notes_follow_imported_unsaved_aliases(self):
        source='package main\nimport "records"\nmain::proc(){i:=1;_=records.Rows[i]}\n'
        files={'records/records.thor':'package records\nRows::[2]int\n'}
        changed='// 😀 unsaved header\npackage records\nRows::[4]int\n'
        entry,_,errors,_=self.invoke(source,files=files,overlays={'records/records.thor':changed})
        self.assertEqual(len(errors),1,errors)
        error=errors[0]
        self.assertEqual(error['pos']['offset'],source.encode().index(b'records.Rows'))
        self.assertIn('Rows: [4]int',error['msgs'][1])
        note=error['related'][0]['pos']
        self.assertEqual(note['file'],str(entry.parent/'records/records.thor'))
        self.assertEqual((note['line'],note['column']),(3,1))
        self.assertEqual(note['offset'],changed.encode().index(b'Rows'))

    def test_type_operand_error_inside_lowered_expressions_keeps_one_primary(self):
        for body in [
            'Rows::[3]Integer;i:=1;Rows[i]=2',
            'Rows::[3]int;i:=1;text:=f"{Rows[i]}";_=text',
            'Rows::[3]int;i:=1;_=switch{case true:Rows[i];case:1}',
            'Rows::[3]int;i:=1;_=read(Rows[i])',
        ]:
            with self.subTest(body=body):
                source='package main\nread::proc(x:int)->int{return x}\nmain::proc(){'+body+'}\n'
                _,_,errors,_=self.invoke(source)
                self.assertEqual(len(errors),1,errors)
                self.assertIn('it names a type',errors[0]['msgs'][0])

    def test_type_operand_errors_preserve_independent_errors(self):
        source='package main\nRows::[3]struct{x:int}\nmain::proc(){i:=1;Rows[i]={1};_=missing;_=Rows[i];other:int="bad";_=other}\n'
        _,_,errors,_=self.invoke(source)
        self.assertEqual(len(errors),4,errors)
        self.assertEqual(sum('it names a type' in e['msgs'][0] for e in errors),2)
        self.assertTrue(any('Undeclared name: missing' in e['msgs'][0] for e in errors))
        self.assertTrue(any('bad' in '\n'.join(e['msgs']) for e in errors))
        # A fully typed RHS has its own independently invalid field value.
        source='package main\nRows::[3]struct{x:int}\nmain::proc(){i:=1;Rows[i]=struct{x:int}{"wrong"}}\n'
        _,_,errors,_=self.invoke(source)
        self.assertTrue(any('it names a type' in e['msgs'][0] for e in errors))
        self.assertTrue(any('wrong' in '\n'.join(e['msgs']) for e in errors))
        source='package main\nRows::[3]struct{x:int}\nmain::proc(){i:=1;Rows[i]={struct{x:int}{"wrong"}}}\n'
        _,_,errors,_=self.invoke(source)
        self.assertTrue(any('it names a type' in e['msgs'][0] for e in errors))
        self.assertTrue(any('wrong' in '\n'.join(e['msgs']) for e in errors))

    def test_type_operand_diagnostics_preserve_native_laziness_and_valid_values(self):
        source='''package main
Rows::[3]int
read::proc(values:$T,index:int)->int{return values[index]}
unused::proc($T:typeid){i:=1;_=Rows[i]}
main::proc(){
    when false {i:=1;Rows[i]={1};_=Rows[i:]}
    values:=Rows{1,2,3};i:=1;_=values[i];_=read(values,i)
    when true {Choice::Rows;_=Choice{1,2,3}} else {Choice::3;_=Choice}
}
'''
        self.invoke(source,success=True)
        self.invoke('package main\ntype_of::proc()->[2]int{return {1,2}}\nmain::proc(){_=type_of()[0]}\n',success=True)

    def test_type_operand_terminal_note_and_native_raw_escape_hatch(self):
        source='package main\nRows::[2]int\nmain::proc(){i:=1;_=Rows[i]}\n'
        entry,output,errors,_=self.invoke(source)
        result=subprocess.run([str(ROOT/'thor'),'check',str(entry),'--output-dir',str(output)],capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,1)
        self.assertIn('Cannot index',result.stderr)
        self.assertIn('(2:1): Note:',result.stderr)
        self.assertIn('Rows::[2]int',result.stderr)
        raw=subprocess.run([str(ROOT/'thor'),'check',str(entry),'--output-dir',str(output),'--raw-diagnostics'],capture_output=True,text=True,timeout=40)
        self.assertIn('is not an expression but a type',raw.stderr)
        self.assertNotIn('it names a type',raw.stderr)

    def test_typed_loop_contracts_and_bound_errors_point_to_original_annotations(self):
        for body,needle,message in [
            ('values:[129]int;for _,(index:i8) in values{_=index}','i8','indices do not fit'),
            ('values:=[1,2];for _,(index:f64) in values{_=index}','f64','indices require an integer type'),
            ('for value,(index:i8) in "text"{_=value;_=index}','i8','only for arrays and slices'),
            ('limit:int=3;for i:f64 in 1..=limit{_=i}','limit{','Cannot assign'),
            ('for i:string in 1..=3{_=i}','string','integer or floating-point'),
            ('for i:Missing in 1..=3{_=i}','Missing','Undeclared'),
            ('values:=[1,2];for _,(index:Missing) in values{_=index}','Missing','Undeclared'),
        ]:
            with self.subTest(body=body):
                source='package main\nmain::proc(){/* 😀 */ '+body+'}\n'
                if needle=='limit{':
                    # The final reference, not the declaration of limit.
                    entry,_,errors,_=self.invoke(source)
                    error=next(e for e in errors if message in e['msgs'][0])
                    self.assertEqual(error['pos']['offset'],source.encode().index(b'limit{'))
                else:errors=self.at(source,needle,message=message)
                self.assertEqual(len(errors),1,errors)
                self.assertNotIn('Thor expression',json.dumps(errors))
                self.assertNotIn('runtime/sequence',json.dumps(errors))

    def test_typed_loop_following_errors_and_readonly_index_keep_unicode_ranges(self):
        source='package main\nmain::proc(){/* 😀 */ values:=[1,2];for value,(index:i8) in values{_=value;index+=1};_=missing}\n'
        errors=self.at(source,'index+=',message='immutable storage')
        self.assertTrue(any(e['pos']['offset']==source.encode().index(b'missing') for e in errors))

    def test_big_switch_case_type_errors_keep_original_positions(self):
        for selector,case in [('Integer(3)','i64(3)'),('Integer(3)','Rational(3)'),
                              ('Rational(1,3)','f64(0.5)')]:
            source='// original 😀 header\npackage main\nmain::proc(){value:=switch '+selector+' {case '+case+':1;case:0};_=value}\n'
            self.at(source,case,message='Cannot')

    def test_big_literals_properties_and_copy_errors_keep_original_unicode_positions(self):
        for body,needle,message in [
            ('r:=Rational(1,2);r.numerator=1','r.numerator','read-only'),
            ('value:Integer=1.25','1.25','whole number'),
            ('value:=Integer(f64(1))','f64(1)','exact integer inputs'),
            ('value:=Rational(1,0)','0','denominator must not be zero'),
            ('value:=Integer(123);value="wrong"','"wrong"','Cannot'),
            ('VALUE::Integer(123);VALUE+=1','VALUE+=1','read-only'),
        ]:
            source='// original header\npackage main\nmain::proc(){/* 😀 */ '+body+'}\n'
            with self.subTest(body=body):
                errors=self.at(source,needle,message=message)
                self.assertNotIn('big.Integer',json.dumps(errors))
                self.assertNotIn('Thor expression.Integer',json.dumps(errors))

    def test_multi_switch_wrong_result_has_original_position_and_destination_type(self):
        for switch in ['switch',' #switch']:
            source='package main\nKind::enum{A,B}\nmain::proc(){/* 😀 */ a:int;b:Kind;a,b='+switch+'{case true:1,"wrong";case:2,.B};_=a;_=b}\n'
            errors=self.at(source,'"wrong"',message='Cannot')
            self.assertIn('Kind',json.dumps(errors))

    def test_multi_switch_arity_diagnostic_identifies_the_wrong_arm(self):
        source='package main\nmain::proc(){a,b:=switch{case true:1,2;case:3,4,5};_=a;_=b}\n'
        self.at(source,'3,4,5',message='Every switch value arm')

    def test_switch_skip_bad_value_still_has_original_location(self):
        source='package main\nmain::proc(){a:int;b:string;a,b=switch{case true:1,99;case:break};_=a;_=b}\n'
        self.at(source,'99',message='Cannot')

    def test_inferred_multi_switch_incompatible_result_identifies_that_position(self):
        source='package main\nmain::proc(){a,b:=switch{case true:1,99;case:2,"other"};_=a;_=b}\n'
        self.at(source,'99',message='Cannot convert')

    def test_using_promoted_switch_result_errors_have_the_field_type_and_original_range(self):
        source='package main\nKind::enum{A,B}\nRecord::struct{kind:Kind}\nwrite::proc(using record:^Record){/* 😀 */ kind=switch{case true:"wrong";case:.A}}\nmain::proc(){record:=Record{};write(&record)}\n'
        errors=self.at(source,'"wrong"',message='Cannot')
        self.assertIn('Kind',json.dumps(errors))
        self.assertNotIn("to '^Record'",json.dumps(errors))

    def test_automatic_using_header_keeps_runtime_and_parameter_error_locations(self):
        for flags in [(),('-debug',)]:
            with self.subTest(flags=flags):
                self.at('// 😀 source header\npackage main\nR::struct{x:int}\nmain::proc(){r:=R{4};using r;_=missing}\n',
                        'missing',flags=flags,message='Undeclared')
                self.at('package main\nR::struct{x:int}\nread::proc(using value:R)->int{return "wrong"}\nmain::proc(){_=read({4})}\n',
                        '"wrong"',flags=flags,message='Cannot')
        self.at('package main\nmain::proc(){using missing}\n','missing',message='Undeclared')
        self.at('package main\nR::struct{x:int}\nread::proc(using value:R)->int{return x}\nmain::proc(){_=read("wrong")}\n',
                '"wrong"',message='Cannot')

    def test_membership_context_errors_point_to_original_inline_elements(self):
        for typ, array, fragment in [('i8','[4,128]','128'),('u8','[-1,4]','-1'),
                                     ('i8','[4,2.5]','2.5'),('i8','[4,i16(8)]','i16(8)')]:
            with self.subTest(typ=typ,array=array):
                errors=self.at('package main\nmain::proc(){/* 😀 */ value:'+typ+'=4;_=value in '+array+'}\n',
                               fragment)
                self.assertEqual(len([error for error in errors if error['type']=='error']),1,errors)

    def test_string_repetition_errors_point_to_original_unicode_operation(self):
        for expression,message in [('"ab"*(-1)','nonnegative count'),('"ab"*1.5','integer compile-time count'),
                                   ('"ab"*(1<<30)','16 MiB')]:
            with self.subTest(expression=expression):
                errors=self.at('package main\nmain::proc(){/* 😀 */ _='+expression+'}\n',expression,message=message)
                self.assertEqual(len([e for e in errors if e.get('type')=='error']),1)

    def test_odin_compatibility_named_result_error_keeps_original_value(self):
        self.at('package main\nmake_board::proc()->(board:[2]int){i:=-1;/* 😀 */ board[i]="wrong";return}\nmain::proc(){_=make_board()}\n',
                '"wrong"', message='Cannot')

    def test_odin_compatibility_case_and_deferred_body_errors_are_not_helpers(self):
        self.at('package main\nmain::proc(){values:=[1,2];i:=-1;switch{case values[i]==missing:return}}\n',
                'missing', message='Undeclared')
        self.at('package main\nconsume::proc(value:int){}\nmain::proc(){values:=[1,2];defer for i in 0..<2{consume(values[i]);_=missing}}\n',
                'missing', message='Undeclared')

    def test_odin_compatibility_pointer_type_switch_error_keeps_original_value(self):
        self.at('package main\nCell::union{int,[2]bool}\nmain::proc(){cells:[2]Cell;i:=0;cells[i]=[2]bool{};switch &v in cells[i]{case [2]bool:v[i]="wrong"}}\n',
                '"wrong"', message='Cannot')

    def test_owned_do_return_error_is_original_token_not_generated_block(self):
        source='package main\nwork::fn(flag:bool)->string{if flag do return "yes";return 10}\nmain::proc(){flag:=false;text:=work(flag);delete(text)}\n'
        errors=self.at(source,'10',flags=('-debug',),message='Cannot')
        self.assertNotIn('Syntax Error',json.dumps(errors))
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_native_run_overload_error_uses_public_name_and_original_callee(self):
        source='package main\nimport "core:math"\n/* 😀 */ VALUE :: #run math.round("bad")\nmain::proc(){}\n'
        errors=self.at(source,'math.round',message='No procedures or ambiguous call')
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['pos']['end_offset']-errors[0]['pos']['offset'],len('math.round'))
        self.assertNotIn('thor_eval_',json.dumps(errors))
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_native_run_foreign_rejection_highlights_the_run_expression(self):
        self.at('package main\nimport "core:c/libc"\n/* 😀 */ VALUE :: #run libc.rand()\nmain::proc(){}\n',
                '#run libc.rand()',message='foreign')

    def test_wildcard_import_errors_map_to_original_names_and_marker(self):
        self.at('package main\nimport * "lib"\n/* 😀 */ VALUE::8\nmain::proc(){}\n','VALUE::',
            files={'lib/main.odin':'package lib\nVALUE::7\n'},message='conflicts with wildcard import')
        self.at('package main\nimport * "one"\nimport * "two"\nmain::proc(){}\n','* "two"',
            files={'one/main.odin':'package one\nVALUE::7\n','two/main.odin':'package two\nVALUE::8\n'},
            message='Conflicting wildcard import')
        self.at('package main\nimport _ "lib" / work\nmain::proc(){_=work("wrong")}\n','"wrong"',
            files={'lib/main.thor':'package lib\nwork::proc(value:int)->int{return value}\n'},message='Cannot convert')
    def test_spread_extrema_errors_retain_original_argument_ranges(self):
        self.at('package main\nmain::proc(){/* 😀 */ _=min(**[0]int{})}\n', '[0]int{}', message='empty fixed array')
        self.at('package main\nmain::proc(){_=max(..[2]int{1,2})}\n', '[2]int{1,2}', message='must be a slice')
        self.at('package main\nmain::proc(){x:int=3;_=max(x,..[]f64{1,2})}\n', 'x,..', message='Cannot assign')
        errors=self.at('package main\nmain::proc(){_=min(..[]bool{true})}\n', 'min(..', message='Spread min/max requires ordered')
        self.assertNotIn('runtime/sequence',json.dumps(errors))

    def test_bundled_basic_call_error_maps_to_original_argument(self):
        self.at('package main\nimport "thor:basic"\nmain::proc(){data:=basic.sorted([]int{2,1},less=7);delete(data)}\n',
                '7', message='Cannot convert')

    def test_generic_default_callback_body_error_has_its_unicode_source_range(self):
        source = 'package main\n/* 😀 */ work::proc(a:[]$E,less:=(proc(i,j:E)->bool{return "wrong"}))->bool{return less(a[0],a[1])}\nmain::proc(){_=work([]int{1,2})}\n'
        errors = self.at(source, '"wrong"', message='Cannot convert')
        self.assertEqual(len(errors), 1, errors)
        error = errors[0]
        self.assertEqual(error['pos']['end_offset']-error['pos']['offset'], len('"wrong"'.encode()))
        self.assertNotIn('Unspecialized polymorphic procedure', json.dumps(errors))
        self.assertNotIn('__thor_', json.dumps(errors))

    def test_uninferable_generic_defaults_report_each_original_signature(self):
        first = 'proc()->E'
        second = 'proc()->[N]int'
        source = ('package basic\n'
                  f'first::proc(a:[]$E,value:={first}{{return {{}}}}){{}}\n'
                  f'second::proc($N:int,value:={second}{{return {{}}}}){{}}\n')
        entry, _, errors, _ = self.invoke(source, flags=('-no-entry-point',))
        self.assertEqual(len(errors), 2, errors)
        self.assertEqual({e['pos']['offset'] for e in errors},
                         {source.encode().index(first.encode()), source.encode().index(second.encode())})
        self.assertTrue(all(e['pos']['file'] == str(entry) for e in errors))

    def test_uninferable_default_callbacks_and_captures_have_source_ranges(self):
        for parameter, needle, message in [
            ('less:=proc(i,j:$U)->bool{return i<j}', 'proc(i,j:$U)->bool', 'explicit callback parameter type'),
            ('less:=proc(i,j:E)->bool{return N>0 && i<j}', 'proc(i,j:E)->bool', "generic 'N'"),
            ('less:=proc(i,j:E)->bool{return len(a)>0 && i<j}', 'a)>0', "cannot capture enclosing parameter 'a'"),
        ]:
            with self.subTest(parameter=parameter):
                source = 'package basic\nwork::proc($N:int,a:[]$E,'+parameter+'){}\n'
                errors = self.at(source, needle, flags=('-no-entry-point',), message=message)
                self.assertEqual(len(errors), 1, errors)

    def test_generic_default_guard_preserves_native_conditional_checking(self):
        bad = 'work::proc(a:[]$E,value:=proc()->E{return {}})->[]E{return a}'
        for wrapper in ['when false {%s}', 'when false {} else when false {%s}',
                        'outer::proc(a:[]$T){%s}',
                        'when #config(ENABLE_BAD,false) {%s}']:
            with self.subTest(wrapper=wrapper):
                self.invoke('package basic\n'+wrapper % bad+'\n', flags=('-no-entry-point',), success=True)
        self.at('package basic\nwhen #config(ENABLE_BAD,false) {'+bad+'}\n',
                'proc()->E',flags=('-no-entry-point','-define:ENABLE_BAD=true'),message='cannot be inferred')
        self.at('package main\nouter::proc($T:typeid){'+bad+'}\nmain::proc(){outer(int)}\n',
                'proc()->E', message='cannot be inferred')

    def test_valid_default_callbacks_and_enclosing_specializations_are_preserved(self):
        variants = [
            'work::proc(a:[]$E,less:=proc(i,j:int)->bool{return i<j})->[]E{return a}',
            'work::proc(a:[]$E,less:=proc(i,j:int)->bool{_=size_of(E);return i<j})->[]E{return a}',
            'work::proc(a:[]$E,less:=proc(E,j:int)->bool{return E<j})->[]E{return a}',
            'E::int\nwork::proc(a:[]$T,less:=proc(i,j:E)->bool{return i<j})->[]T{return a}',
            'work::proc($N:int,less:=proc(i:[N when false else 2]int)->int{return i[0]}){}',
            'less::proc(i,j:$T)->bool{return i<j}\nwork::proc(a:[]$T,compare:=less)->[]T{_=compare(a[0],a[1]);return a}\nmain::proc(){_=work([]int{1,2})}',
            'outer::proc($E:typeid){inner::proc(a:[]E,less:=proc(i,j:E)->bool{return i<j})->[]E{return a};_=inner([]E{1,2})}\nmain::proc(){outer(int)}',
        ]
        for code in variants:
            with self.subTest(code=code):
                self.invoke('package main\n'+code+'\n', flags=('-no-entry-point',), success=True)

    def test_computed_default_signature_keeps_the_native_type_error(self):
        source = 'package basic\nwork::proc($N:int,less:=proc(i:type_of(N))->int{return i}){}\n'
        errors = self.at(source, 'type_of(N)', flags=('-no-entry-point',), message='is not a type')
        self.assertFalse(any('Anonymous default procedure' in message for error in errors for message in error['msgs']))

    def test_imported_native_callback_and_unsaved_fix_keep_their_actual_source(self):
        source = 'package main\nimport "library"\nmain::proc(){_=library.VALUE}\n'
        library = 'package library\nVALUE::1\n/* 😀 */ sorted::proc(a:[]$E,less:=proc(i,j:E)->bool{return i<j})->[]E{return a}\n'
        entry, _, errors, _ = self.invoke(source, files={'library/main.odin': library})
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]['pos']['file'], str(entry.parent/'library/main.odin'))
        self.assertEqual(errors[0]['pos']['offset'], library.encode().index(b'proc(i,j:E)'))
        fixed = library.replace('less:=proc(i,j:E)->bool{return i<j}', 'less:proc(i,j:E)->bool')
        self.invoke(source, files={'library/main.odin': library}, overlays={'library/main.odin': fixed}, success=True)
        # An imported type named E is independent of the host's generic E.
        self.invoke('package main\nimport "library"\nwork::proc(a:[]$E,less:=proc(i,j:library.E)->bool{return i<j})->[]E{return a}\n',
                    files={'library/main.odin': 'package library\nE::int\n'}, flags=('-no-entry-point',), success=True)

    def test_range_operand_errors_keep_unicode_bounds_without_query_cascades(self):
        source='package main\nmain::proc(){_="😀";value:=3;_=value in "a"..<"z"}\n'
        _,_,errors,_=self.invoke(source)
        self.assertEqual(len(errors),2,errors)
        self.assertEqual({e['pos']['offset'] for e in errors},{source.encode().index(b'"a"'),source.encode().index(b'"z"')})
        self.assertNotIn('ternary',json.dumps(errors))
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_early_redeclaration_has_original_unicode_range_and_prior_line(self):
        source='package main\ncafé::proc(){}\nLABEL::"😀";café::proc(){}\nmain::proc(){}\n'
        errors=self.at(source,'café::proc(){}\nmain',message="Redeclaration of 'café'")
        self.assertEqual(len(errors),1)
        self.assertEqual(errors[0]['stage'],'declaration')
        self.assertIn('previous declaration on line 2',errors[0]['msgs'][0])

    def test_invalid_format_fill_has_its_own_unicode_source_range(self):
        source='package main\nmain::proc(){_="😀";_=f`{"é😀":界^5}`}\n'
        errors=self.at(source,'界^5',message='single-quoted rune')
        error=next(e for e in errors if 'single-quoted rune' in e['msgs'][0])
        self.assertEqual(error['pos']['end_column']-error['pos']['column'],3)
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_automatic_failures_keep_original_unicode_ranges(self):
        source='package main\nwork::fn(n:int)->int{_="😀";return missing}\nVALUE::work(3)\nmain::proc(){_=VALUE}\n'
        errors=self.at(source,'missing',message='Undeclared name: missing')
        self.assertNotIn('__thor_',json.dumps(errors))
        self.assertNotIn('thor-run-',json.dumps(errors))
        source='package main\nwork::fn()->int{assert(false);return 0}\nmain::proc(){_="😀";VALUE::work();_=VALUE}\n'
        errors=self.at(source,'work()',message='Compile-time execution failed')
        self.assertNotIn('thor-run-',json.dumps(errors))

    def test_generic_run_unknown_argument_maps_to_its_expression(self):
        source='package main\nsquare::fn(n:int)->int{return n*n}\nwork::proc(value:$T)->int{_="😀";VALUE::#run square(value);return VALUE}\nmain::proc(){a:=3;_=work(a)}\n'
        errors=self.at(source,'value);return',message='runtime storage is not a constant')
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_allocated_results_preserve_error_locations_and_constant_context(self):
        source='package main\nwork::fn()->[]string{_="😀";return missing}\nmain::proc(){a:=work();_=a}\n'
        errors=self.at(source,'missing',message='Undeclared name: missing')
        self.assertNotIn('__thor_',json.dumps(errors))
        source='package main\nwork::fn()->[]string{return nil}\nmain::proc(){_="😀";VALUE::#run work();_=VALUE}\n'
        errors=self.at(source,'#run work()',message='slice-containing #run result needs a runtime binding')
        self.assertEqual(len(errors),1,errors)
        self.assertNotIn('thor-run-',json.dumps(errors))

    def test_pure_contract_and_borrow_errors_point_to_original_source(self):
        source='package main\nbad::fn(input:[]int)->int{_="😀";alias:=input;alias[0]=7;return 0}\nmain::proc(){a:=[1];_=bad(a[:])}\n'
        errors=self.at(source,'alias[0]=7',message='borrowed from an input')
        self.assertNotIn('runtime/',json.dumps(errors))
        source='package main\nbad::fn()->^int{return nil}\nmain::proc(){_=bad()}\n'
        errors=self.at(source,'^int',message='Unsupported pure value type')
        self.assertNotIn('runtime/',json.dumps(errors))

    def test_pure_reference_iteration_reports_original_loop_without_body_cascades(self):
        for call in ['work()', '#run work()']:
            with self.subTest(call=call):
                source='package main\nwork::fn()->int{_="😀";values:=[1,2];for &value in values{value+=1};return values[0]}\nmain::proc(){_='+call+'}\n'
                errors=self.at(source,'for &value',message='Pure code cannot iterate by mutable reference')
                self.assertEqual(len(errors),1,errors)
                self.assertNotIn('Undeclared name',json.dumps(errors))

    def test_run_helper_errors_runtime_failure_and_unsaved_import(self):
        source='package main\nwork::fn()->int{return missing}\nVALUE::#run work()\nmain::proc(){_=VALUE}\n'
        errors=self.at(source,'missing',message='missing')
        self.assertNotIn('thor-run-',json.dumps(errors))
        source='package main\nwork::fn()->int{assert(false,"oops");return 0}\nVALUE::#run work()\nmain::proc(){_=VALUE}\n'
        errors=self.at(source,'#run work()',message='#run execution failed')
        self.assertNotIn('thor-run-',json.dumps(errors))
        source='package main\nimport "library"\nVALUE::#run library.work()\nmain::proc(){_=VALUE}\n'
        library='package library\nwork::fn()->int{_="😀";return missing}\n'
        entry,_,errors,_=self.invoke(source,files={'library/main.thor':'package library\nwork::fn()->int{return 7}\n'},overlays={'library/main.thor':library})
        error=next(e for e in errors if 'missing' in e['msgs'][0])
        self.assertEqual(error['pos']['file'],str(entry.parent/'library/main.thor'))
        self.assertEqual(error['pos']['offset'],library.encode().index(b'missing'))
        self.assertNotIn('thor-run-',json.dumps(errors))

    def test_anonymous_view_and_pointer_errors_keep_unicode_ranges(self):
        for expr in ['f"abc {1}"[1:]', 'raw_data(f"abc {1}")', '&f"abc {1}"[-1]']:
            with self.subTest(expr=expr):
                source='package main\nmain::proc(){_="😀";view:='+expr+';_=view}\n'
                errors=self.at(source,expr,message='named owner')
                self.assertEqual(len(errors),1,errors)
                self.assertEqual(errors[0]['pos']['end_offset']-errors[0]['pos']['offset'],len(expr.encode()))
                self.assertNotIn('runtime/',json.dumps(errors))

    def test_hardened_generic_xor_error_names_the_user_operator(self):
        source='package main\nf::proc(a:$A,b:$B){_=a~b}\nmain::proc(){a:=set[int]{1};b:=set[string]{"x"};f(a,b)}\n'
        self.at(source,'a~b',message="Invalid operands for '~'")

    def test_c_callback_allocation_without_context_reports_its_expression(self):
        source='package main\nmessage::proc "c"(n:int)->string{return f"value {n}"}\nmain::proc(){_=message(7)}\n'
        errors=self.at(source,'f"value {n}"',message='context')
        self.assertNotIn('runtime/',json.dumps(errors))

    def test_interpolation_field_errors_keep_unicode_source_ranges(self):
        self.at('package main\nmain::proc(){_=f"😀 {missing_value}"}\n','missing_value')
        errors=self.at('package main\nmain::proc(){_=f"😀 {true:,}"}\n','true',message='Grouping requires')
        self.assertEqual(len(errors),1)
        self.assertNotIn('runtime/',json.dumps(errors))

    def test_switch_result_and_duplicate_cases_keep_original_locations(self):
        self.at('package main\nmain::proc(){x:int=switch 1{case 1:"wrong";case:3};_=x}\n','"wrong"')
        source='package main\nmain::proc(){_=switch 1{case 1:2;case 1:3;case:4}}\n'
        _,_,errors,_=self.invoke(source)
        self.assertEqual(errors[0]['pos']['offset'],source.index('case 1:3')+5)
        self.assertIn('main.thor(2:30)',json.dumps(errors))
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_switch_panic_preserves_other_result_and_argument_error_locations(self):
        for arm, fragment, message in [
            ('panic(123)', '123', 'Cannot'),
            ('panic(missing)', 'missing', 'Undeclared'),
            ('work()', 'work()', 'does not return a value'),
        ]:
            with self.subTest(arm=arm):
                errors=self.at('package main\nwork::proc(){}\nmain::proc(){/* 😀 */ value:int=switch{case true:7;case:'+arm+'};_=value}\n',
                               fragment, message=message)
                self.assertNotIn('__thor_',json.dumps(errors))
        self.at('package main\nmain::proc(){/* 😀 */ value:int=switch{case true:"wrong";case:panic("stop")};_=value}\n',
                '"wrong"', message='Cannot')

    def test_set_literal_contract_and_element_errors(self):
        errors=self.at('package main\nmain::proc(){_=set[[]int]{}}\n','set[[]int]',message='valid Odin map keys')
        self.assertEqual(len(errors),1)
        self.assertNotIn('runtime/',json.dumps(errors))
        self.at('package main\nmain::proc(){_=set[int]{"wrong"}}\n','"wrong"')

    def test_expression_lowering_imported_unsaved_buffer(self):
        source='package main\nimport "library"\nmain::proc(){library.work()}\n'
        library='package library\nwork::proc(){s:=f"😀 {missing_field}";delete(s)}\n'
        entry,_,errors,_=self.invoke(source,files={'library/main.thor':'package library\nwork::proc(){}\n'},
                                    overlays={'library/main.thor':library})
        error=next(e for e in errors if 'missing_field' in e['msgs'][0])
        self.assertEqual(error['pos']['file'],str(entry.parent/'library/main.thor'))
        self.assertEqual(error['pos']['offset'],library.encode().index(b'missing_field'))
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_negative_constant_reports_original_bound(self):
        self.at('package main\nmain::proc(){a:=[1,2];_=a[-3]}\n','-3',message='index -3 is out of bounds for length 2')

    def test_generic_dispatch_errors_belong_to_user_operation(self):
        source='package main\nf::proc(a,b:$T)->T{return a|b}\nmain::proc(){_=f(f64(1),f64(2))}\n'
        errors=self.at(source,'a|b')
        self.assertNotIn('runtime/sets/compiler',json.dumps(errors))
        self.assertNotIn('Thor expression',json.dumps(errors))
        source='package main\nf::proc(a:$A,b:$B){_=a|b}\nmain::proc(){a:=set[int]{1};b:=set[string]{"x"};f(a,b)}\n'
        errors=self.at(source,'a|b',message="Invalid operands for '|'")
        self.assertNotIn('compiler_union',json.dumps(errors))

    def test_immutable_address_unicode_range_and_following_native_error(self):
        source='package main\nmain::proc(){_="😀"; x:>[1,2]; _=&x[0]; _=missing}\n'
        errors=self.at(source,'&x[0]',message='mutable address')
        error=next(e for e in errors if 'mutable address' in e['msgs'][0])
        self.assertEqual(error['pos']['end_offset']-error['pos']['offset'],len('&x[0]'))
        self.assertTrue(any(e['pos']['offset']==source.encode().index(b'missing') for e in errors))

    def test_generic_immutable_guard_keeps_call_notes_and_source(self):
        source='package main\nf::proc(value:$T){x:>value; x[0]=7}\nmain::proc(){f([1,2])}\n'
        errors=self.at(source,'x[0]',message="immutable storage 'x'")
        self.assertTrue(any('main.thor(3:' in m for e in errors for m in e['msgs']))
        self.assertNotIn('type_is_array',json.dumps(errors))

    def test_support_contract_failures_point_to_calls_without_cascades(self):
        source='package main\nimport f "thor:format"\nimport s "thor:sets"\nmain::proc(){_=f.group_integer(1.25); bad:s.Set([]int);_=bad}\n'
        errors=self.at(source,'f.group_integer(1.25)',message='Integer grouping')
        self.assertEqual(len(errors),2,errors)
        self.assertTrue(any(e['pos']['offset']==source.index('s.Set([]int)') for e in errors))
        self.assertNotIn('runtime/',json.dumps(errors))

    def test_conditional_generic_guard_after_do_retains_source_columns(self):
        source='package main\nf::proc(value:$T){x:>value; for i:=0;i<1;i+=1 do x[0]=7; _=missing}\nmain::proc(){f([1,2])}\n'
        self.at(source,'missing',message='missing')

    def test_power_mismatch_has_one_original_unicode_operation_error(self):
        source='package main\nmain::proc(){_="😀";a:int=2;b:f64=3;_=a**b}\n'
        errors=self.at(source,'a**b',message='matching operand types')
        self.assertEqual(len(errors),1,errors)
        self.assertNotIn('type_of',json.dumps(errors))
        self.assertNotIn('runtime/',json.dumps(errors))

    def test_power_mismatch_in_immutable_loop_binding_has_no_generated_return_cascade(self):
        source='''// original 😀 header
package main
import * "thor:basic"
Games::enum{roulette}
HOUSE_ODDS_TABLE::[Games]f64{.roulette=.5263157894736842}
main::proc(){
    GAME::Games.roulette
    HOUSE_ODDS::HOUSE_ODDS_TABLE[GAME]
    for i in 1..=10 {
        prob_loss:>HOUSE_ODDS ** i
        _=prob_loss
    }
}
'''
        errors=self.at(source,'HOUSE_ODDS ** i',message='matching operand types')
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['pos']['end_offset']-errors[0]['pos']['offset'],len('HOUSE_ODDS ** i'))
        self.assertIn('f64 vs int',errors[0]['msgs'][0])
        self.assertNotIn('Thor expression',json.dumps(errors))
        self.assertNotIn('return statement',json.dumps(errors))

    def test_generated_power_cascade_filter_preserves_independent_and_real_return_errors(self):
        source='package main\nimport * "thor:basic"\nmain::proc(){HOUSE_ODDS::f64(.52);for i in 1..=10{loss:>HOUSE_ODDS ** i;_=loss};_=missing}\n'
        errors=self.at(source,'HOUSE_ODDS ** i',message='matching operand types')
        self.assertEqual(len(errors),2,errors)
        self.assertTrue(any(e['pos']['offset']==source.index('missing') and 'Undeclared' in e['msgs'][0] for e in errors))
        self.assertNotIn('Thor expression',json.dumps(errors))
        errors=self.at('package main\nbad::proc(value:int)->bool{return value**2}\nmain::proc(){_=bad(2)}\n',
                       'value**2',message='return statement')
        self.assertTrue(any("to 'bool'" in e['msgs'][0] for e in errors),errors)

    def test_power_undefined_operand_and_unsupported_type(self):
        errors=self.at('package main\nmain::proc(){_=2**missing}\n','missing',message='Undeclared')
        self.assertEqual(len(errors),1,errors)
        self.at('package main\nmain::proc(){a:="a";b:="b";_=a**b}\n','a**b',message='requires integer or floating-point operands')

    def test_power_unrepresentable_literal_keeps_native_error_without_cascades(self):
        for value in ['300','3.5']:
            with self.subTest(value=value):
                source=f'package main\nmain::proc(){{a:i8=2;_=a**{value}}}\n'
                errors=self.at(source,'a**'+value,message='truncated to' if value=='3.5' else 'Cannot convert numeric value')
                self.assertEqual(len(errors),1,errors)
                self.assertNotIn('Thor expression',json.dumps(errors))

    def invoke(self, source, *, files=None, flags=(), command="check", overlays=None, success=False):
        temporary = tempfile.TemporaryDirectory(prefix="thor-diagnostics-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        entry = root / "src" / "main.thor"
        entry.parent.mkdir()
        entry.write_bytes(source.encode())
        for name, text in (files or {}).items():
            path = entry.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        overlay_flags = []
        if overlays:
            replacements = {}
            for i, (name, text) in enumerate(overlays.items()):
                replacement = root / f"buffer-{i}"
                replacement.write_text(text)
                replacements[str(entry.parent / name)] = str(replacement)
            overlay = root / "overlay.json"
            overlay.write_text(json.dumps({"Replace": replacements}))
            overlay_flags = ["--overlay", str(overlay)]
        result = subprocess.run([str(ROOT / "thor"), command, str(entry.parent), "--output-dir", str(root / "out"),
                                 "--diagnostic-format=json", *overlay_flags, *flags], capture_output=True, text=True, timeout=40)
        data = json.loads(result.stderr)
        self.assertEqual(result.returncode == 0, success, data)
        return entry, root / "out", data["errors"], result

    def at(self, source, needle, *, files=None, flags=(), overlays=None, message=None):
        entry, output, errors, _ = self.invoke(source, files=files, flags=flags, overlays=overlays)
        matching = [e for e in errors if message is None or message in "\n".join(e["msgs"])]
        self.assertTrue(matching, errors)
        error = matching[0]
        raw = (overlays or {}).get("main.thor", source).encode()
        offset = raw.index(needle.encode())
        self.assertEqual(error["pos"]["file"], str(entry), error)
        self.assertEqual(error["pos"]["offset"], offset, error)
        self.assertEqual((error["pos"]["line"], error["pos"]["column"]), position(raw, offset))
        self.assertNotIn(str(output), json.dumps(errors))
        self.assertNotIn("__thor_", json.dumps(errors))
        return errors

    def test_ordinary_error_after_expansions_and_import_removal(self):
        self.at('package main\nimport "core:math" /\n sin,\n cos\nmain :: proc(){ a := [1,2]; _ = sin(f64(0)); _ = cos(f64(0)); _ = a; _ = missing }\n', 'missing')

    def test_array_element_type_error_exact_range(self):
        source = 'package main\nmain :: proc(){ a := [1, "wrong"]; _ = a }\n'
        errors = self.at(source, '"wrong"')
        self.assertEqual(errors[0]['pos']['end_offset'] - errors[0]['pos']['offset'], len('"wrong"'))

    def test_array_used_for_scalar_hides_type_intrinsics(self):
        errors = self.at('package main\nmain :: proc(){ a:int = [1,2]; _ = a }\n', '[1,2]')
        self.assertIn('[1,2]', errors[0]['msgs'][0])
        self.assertNotIn('Thor expression', json.dumps(errors))
        self.assertNotIn('type_elem_type', json.dumps(errors))

    def test_discarding_array_literal_does_not_query_blank_type(self):
        self.invoke('package main\nmain :: proc(){ _ = [1,2]; _ = true ? [1,2] : [3,4] }\n', success=True)

    def test_nested_array_error(self):
        self.at('package main\nmain :: proc(){ a := [[1,2], [3,"wrong"]]; _ = a }\n', '"wrong"')

    def test_multiline_ternary_error(self):
        self.at('package main\nmain :: proc(){ a := (true ?\n [1, missing]\n : [2,3])\n _ = a }\n', 'missing')

    def test_multiline_ternary_later_error(self):
        self.at('package main\nmain :: proc(){ a := (true ?\n 1\n : 2)\n _ = a\n _ = missing }\n', 'missing')

    def test_unicode_crlf_tabs(self):
        self.at('package main\r\nmain :: proc(){\r\n\t_ = "é😀"; a := [1,2]; _ = a; _ = missing\r\n}\r\n', 'missing')

    def test_parser_error_uses_same_schema_and_location(self):
        self.at('package main\nmain :: proc(){ x := ; }\n', ';', message='expected an operand')

    def test_selective_import_validation_points_to_name(self):
        self.at('package main\nimport "core:math" /\n sin,\n DOES_NOT_EXIST\nmain :: proc(){}\n', 'DOES_NOT_EXIST')

    def test_transpiler_conflict_has_a_real_range(self):
        source = 'package main\nimport "core:math" / sin\nsin :: 3\nmain :: proc(){}\n'
        _, _, errors, _ = self.invoke(source)
        self.assertEqual(errors[0]['pos']['offset'], source.rindex('sin ::'))
        self.assertIn('conflicts', errors[0]['msgs'][0])

    def test_invalid_membership_hides_overload_implementation(self):
        self.at('package main\nmain :: proc(){ a := 1 in "abc"; _ = a }\n', '1 in "abc"', message='Invalid operands for membership')

    def test_helper_failure_uses_actual_call_site(self):
        source = 'package main\nmain :: proc(){ _ = 1 in [1]; s := []int{1}\n _ = s in [s] }\n'
        self.at(source, 's in [s]', message='Membership requires comparable elements')

    def test_helper_instantiation_column_after_unicode(self):
        self.at('package main\nmain :: proc(){ _ = "😀"; s := []int{1}; _ = s in [s] }\n',
                's in [s]', message='Membership requires comparable elements')

    def test_unix_style_helper_instantiation_uses_actual_call_site(self):
        source = 'package main\nmain :: proc(){ _ = 1 in [1]; s := []int{1}\n _ = s in [s] }\n'
        self.at(source, 's in [s]', flags=['-error-pos-style:unix'], message='Membership requires comparable elements')

    def test_user_names_resembling_generated_names_remain_intact(self):
        for name in ['__thor_user_missing', 'array_literal_needs_element_type']:
            _, _, errors, _ = self.invoke(f'package main\nmain :: proc(){{_ = {name}}}\n')
            self.assertIn(f'Undeclared name: {name}', errors[0]['msgs'][0])

    def test_warnings_are_mapped_once_and_obey_flags(self):
        source = 'package main\nS :: struct #field_align(4) {x:int}\nmain :: proc(){}\n'
        _, _, errors, _ = self.invoke(source, success=True)
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]['type'], 'warning')
        self.assertEqual(errors[0]['pos']['offset'], source.index('field_align'))
        _, _, errors, _ = self.invoke(source, flags=['-ignore-warnings'], success=True)
        self.assertEqual(errors, [])
        _, _, errors, _ = self.invoke(source, flags=['-warnings-as-errors'])
        self.assertTrue(any(e['type'] == 'error' for e in errors), errors)

    def test_terse_and_raw_diagnostic_options(self):
        entry, output, _, _ = self.invoke('package main\nmain :: proc(){_ = missing}\n')
        for flag in ['-terse-errors', '--raw-diagnostics']:
            with self.subTest(flag=flag):
                result = subprocess.run([str(ROOT / 'thor'), 'check', str(entry), '--output-dir', str(output), flag], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                if flag == '-terse-errors':
                    self.assertIn(str(entry), result.stderr)
                    self.assertNotIn('^', result.stderr)
                else:
                    self.assertIn(str(output / 'main.odin'), result.stderr)

    def test_missing_import_has_a_source_range(self):
        self.at('package main\nimport "nonexistent"\nmain :: proc(){}\n', 'import', message='imported package does not exist')

    def test_empty_array_context_message(self):
        self.at('package main\nmain :: proc(){ a := []; _ = a }\n', '[]', message='needs an element type')

    def test_repeated_inferred_expression_error_deduplicates(self):
        errors = self.at('package main\nmain :: proc(){ a := [missing,2]; _ = a }\n', 'missing')
        self.assertEqual(len([e for e in errors if 'Undeclared name: missing' in e['msgs'][0]]), 1)

    def test_generic_constraint_and_embedded_caller_location(self):
        source = 'package main\ng :: proc(x:$T)->T where T in [int,f64] {return x}\nmain :: proc(){_ = g("bad")}\n'
        errors = self.at(source, 'T in [int,f64]', message="'where' clause")
        messages = '\n'.join(m for e in errors for m in e['msgs'])
        self.assertIn('T in [int,f64]', messages)
        self.assertIn('main.thor(3:', messages)
        self.assertNotIn('((T)', messages)

    def test_unix_style_generic_caller_locations_are_mapped(self):
        source = 'package main\ng :: proc(x:$T)->T where T in [int,f64] {return x}\nmain :: proc(){_ = g("bad")}\n'
        errors = self.at(source, 'T in [int,f64]', flags=['-error-pos-style:unix'], message="'where' clause")
        self.assertIn('main.thor:3:', '\n'.join(m for e in errors for m in e['msgs']))

    def test_imported_thor_and_native_odin_errors(self):
        for extension in ['thor', 'odin']:
            with self.subTest(extension=extension):
                _, _, errors, _ = self.invoke('package main\nimport "lib"\nmain :: proc(){lib.f()}\n',
                    files={f'lib/lib.{extension}': 'package lib\nf :: proc(){ a := 1; _ = a; _ = missing }\n'})
                error = next(e for e in errors if 'missing' in e['msgs'][0])
                self.assertTrue(error['pos']['file'].endswith(f'/lib/lib.{extension}'), error)
                self.assertEqual(error['pos']['line'], 2)

    def test_unsaved_overlay_lowered_and_reported_at_original_path(self):
        source = 'package main\nmain :: proc(){}\n'
        changed = 'package main\nmain :: proc(){ a := [1,2]; _ = a; _ = missing }\n'
        self.at(source, 'missing', overlays={'main.thor': changed})

    def test_unsaved_imported_overlay(self):
        source = 'package main\nimport "lib"\nmain :: proc(){lib.f()}\n'
        entry, _, errors, _ = self.invoke(source, files={'lib/lib.thor': 'package lib\nf :: proc(){}\n'},
                    overlays={'lib/lib.thor': 'package lib\nf :: proc(){ _ = missing }\n'})
        self.assertEqual(errors[0]['pos']['file'], str(entry.parent / 'lib/lib.thor'))

    def test_inactive_branch_does_not_create_diagnostics(self):
        self.invoke('package main\nmain :: proc(){ when false { a := []; _ = a; _ = missing } }\n', success=True)

    def test_source_map_sidecar_is_complete_and_round_trips(self):
        entry, output, _, _ = self.invoke('package main\nmain :: proc(){ a := [1,2]; _ = a }\n', success=True,flags=['-debug'])
        data = json.loads((output / 'main.odin.map.json').read_text())
        self.assertEqual(data['version'], 1)
        self.assertEqual(data['sources'][str(entry)], entry.read_text())
        raw = (output / 'main.odin').read_bytes()
        segments = data['segments']
        self.assertEqual(segments[0][0], 0)
        self.assertEqual(segments[-1][1], len(raw))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(segments, segments[1:])))
        mapping = SourceMap(segments)
        for a, b, file, start, end, _, exact in segments:
            self.assertEqual(mapping.lookup(a).file, file)
            if exact:
                self.assertEqual(raw[a:b], data['sources'][file].encode()[start:end])

    def test_text_output_and_unix_locations(self):
        entry, output, _, _ = self.invoke('package main\nmain :: proc(){ _ = missing }\n')
        result = subprocess.run([str(ROOT / 'thor'), 'check', str(entry), '--output-dir', str(output), '-error-pos-style:unix'], capture_output=True, text=True)
        self.assertIn(f'{entry}:2:', result.stderr)
        self.assertIn('_ = missing', result.stderr)
        self.assertIn('^^^^^^^', result.stderr)
        self.assertNotIn('.odin', result.stderr)

    def test_runtime_stderr_is_not_treated_as_a_compiler_error(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        entry = Path(temporary.name) / 'main.thor'
        entry.write_text('package main\nimport "core:fmt"\nimport "core:os"\nmain :: proc(){ fmt.eprintln("not JSON: compiler text"); os.exit(7) }\n')
        result = subprocess.run([str(ROOT / 'thor'), 'run', str(entry), '--output-dir', str(entry.parent / 'out')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr, 'not JSON: compiler text\n')


if __name__ == '__main__':
    unittest.main()

"""Run existing convention/apply contracts through the independent compiler."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch
from tests import test_vet as reference
from tests import test_rust_compiler as native

class RustVetTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=reference.VetTests.setUp
    write=reference.VetTests.write
    findings=reference.VetTests.findings

    def invoke(self,*flags,command='vet',input=None,status=0):
        result=subprocess.run([str(native.RUST),command,str(self.app),'--output-dir',str(self.output),*flags],
                              cwd=self.root,capture_output=True,text=True,input=input,timeout=90)
        self.assertEqual(result.returncode,status,result.stdout+result.stderr)
        return result

    def test_shared_portability_graph_is_scanned_once_and_cycles_retain_rejections(self):
        wrappers = '\n'.join(f'wrapper_{index}::proc(x:int)->int{{return shared(x)}}'
                             for index in range(48))
        self.write('''package main
shared::proc(x:int)->int{return x+1}
cycle_a::proc(x:int)->int{return cycle_b(x)}
cycle_b::proc(x:int)->int{when true{return x}else{return cycle_a(x)}}
blocked::proc(x:int)->int{return cycle_a(x)}
''' + wrappers + '\nmain::proc(){}\n')
        profile = self.root/'profile.json'
        result = subprocess.run([str(native.RUST), 'vet', str(self.app), '--vet=pure',
            '-json-errors', '--output-dir', str(self.output)], cwd=self.root,
            env={**os.environ, 'THOR_PROFILE':str(profile)}, capture_output=True,
            text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        findings = json.loads(result.stderr)['errors']
        names = [error['msgs'][0] for error in findings]
        self.assertEqual(len(names), 49, names)
        self.assertTrue(all('cycle_' not in name and "'blocked'" not in name for name in names), names)
        counters = json.loads(profile.read_text())['counters']
        self.assertLessEqual(counters.get('vet.portability_body_scans', 0), 52, counters)
        self.assertGreater(counters.get('vet.portability_body_scans', 0), 48, counters)

    def test_imported_test_packages_participate_in_correction_validation(self):
        self.write('package root\n@(require) import "cases"\n')
        cases=self.app/'cases';cases.mkdir()
        suite=cases/'suite_test.thor'
        suite.write_text('package cases\nimport "core:testing"\n@(test) check_value::proc(t:^testing.T){value:=7;testing.expect_value(t,value,7)}\n')
        def invoke(command,*flags):
            result=subprocess.run([str(native.RUST),command,str(self.source),'-file','-all-packages',
                '--output-dir',str(self.output),*flags],cwd=self.root,capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        profile=self.root/'required-import-profile.json'
        with patch.dict(os.environ,{'THOR_PROFILE':str(profile)}):
            invoke('vet','--vet=immutable,unused','-build-mode:test','--apply')
        self.assertEqual(json.loads(profile.read_text())['counters'].get('vet.validation_checks'),1)
        self.assertIn('value:>7',suite.read_text())
        self.assertIn('@(require) import "cases"',self.source.read_text())
        invoke('test')

    def test_enum_constant_arguments_do_not_poison_unrelated_pure_edits(self):
        self.write('package main\nE::enum{a=5,b=9}\n'
            'identity::proc(c:byte)->bool{return c==0}\n'
            'main::proc(){check::proc($e:E){when e==.a{if int(e)!=5{panic("enum")}}};check(.a);check(.b)}\n')
        profile=self.root/'enum-profile.json'
        with patch.dict(os.environ,{'THOR_PROFILE':str(profile)}):
            self.invoke('--vet=pure','--apply','--comptime=required')
        self.assertIn('identity::fn',self.source.read_text())
        self.assertEqual(json.loads(profile.read_text())['counters'].get('vet.validation_checks'),1)
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='run')

    def test_imported_test_pointer_exposure_is_not_auto_frozen(self):
        self.write('package root\n@(require) import "cases"\n')
        cases=self.app/'cases';cases.mkdir()
        suite=cases/'suite_test.thor'
        suite.write_text('package cases\nimport "core:testing"\nread::proc(b:[]u8)->u8{return ((^u8)(raw_data(b)))^}\n@(test) check_value::proc(t:^testing.T){values:=[]u8{7};testing.expect_value(t,read(values),7)}\n')
        result=subprocess.run([str(native.RUST),'vet',str(self.source),'-file','-all-packages',
            '-build-mode:test','--vet=immutable','--apply','--output-dir',str(self.output)],
            cwd=self.root,capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('values:=',suite.read_text())
        self.invoke('-all-packages',command='test')

    def test_matrix_element_addresses_and_writes_prevent_immutable_edits(self):
        self.write('package main\nread::proc(p:^f32)->f32{return p^}\nmain::proc(){transform:=(matrix[2,2]f32){};transform[0,0]=7;if read(&transform[0,0])!=7{panic("matrix")}}\n')
        result=self.invoke('--vet=immutable','--apply')
        self.assertNotIn("Declare 'transform' immutable",result.stdout+result.stderr)
        self.assertIn('transform:=',self.source.read_text())
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='run')

    def test_options_before_source_and_large_batches(self):
        body='\n'.join(f'value_{index}:={index};if value_{index}!={index}{{panic("result")}}' for index in range(80))
        self.write('package main\nmain::proc(){\n'+body+'\n}\n')
        result=subprocess.run([str(native.RUST),'check','--vet',str(self.app),'-json-errors',
                               '--output-dir',str(self.output)],capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(len(json.loads(result.stderr)['errors']),80)

    def test_reviewed_argument_contracts_bound_portability_work_for_large_batches(self):
        declarations='\n'.join(f'values_{index}:=[]int{{{index}}};show(values_{index})' for index in range(80))
        self.write('package main\nimport "core:fmt"\n'
                   'show::proc(values:[]int){fmt.println(values)}\nmain::proc(){\n'+declarations+'\n}\n')
        profile=self.root/'profile.json'
        result=subprocess.run([str(native.RUST),'vet',str(self.app),'--vet=immutable',
            '-json-errors','--output-dir',str(self.output)],cwd=self.root,
            env={**os.environ,'THOR_PROFILE':str(profile)},capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(len(json.loads(result.stderr)['errors']),80)
        counters=json.loads(profile.read_text())['counters']
        self.assertEqual(counters.get('vet.safety_roots'),1,counters)
        self.assertEqual(counters.get('vet.safety_contexts'),2,counters)
        self.assertEqual(counters.get('vet.validation_checks'),1,counters)

    def test_inline_index_copies_are_immutable_despite_unrelated_output_parameters(self):
        for typ, initial, read in [
            ('Hand', 'first', 'hand[0].num'),
            ('Hand_Alias', 'first', 'hand[0].num'),
            ('[2]Card', '[2]Card{first[0],first[1]}', 'hand[0].num'),
            ('[2]Hand', '[2]Hand{first,first}', 'hand[1][0].num'),
            ('Box(Hand)', 'Box(Hand){first}', 'hand.payload[0].num'),
        ]:
            with self.subTest(typ=typ):
                text='''package main
CHANGE::#config(CHANGE,false)
Suit::enum{club,heart}
Card::struct{num:int,suit:Suit}
Hand::[dynamic;2]Card
Hand_Alias::Hand
Box::struct($T:typeid){payload:T}
Results::map[int]int
make_hand::proc()->(hand:Hand){append(&hand,Card{7,.club});append(&hand,Card{8,.heart});return}
record::proc(out:^Results,deal:[]TYPE,extra:$T){
    _=extra
    when CHANGE {out[99]=99}
    for i in 0..<len(deal) {
        hand := deal[i]
        out[i]=READ
    }
}
main::proc(){
    first:>make_hand()
    deal:=[1]TYPE{INITIAL}
    results:=make(Results);defer delete(results)
    record(&results,deal[:],1)
    record(&results,deal[:],"another specialization")
    if results[0]!=7{panic("copied hand")}
}
'''.replace('TYPE',typ).replace('INITIAL',initial).replace('READ',read)
                self.write(text)
                errors=self.findings('--vet=immutable')
                hand=[error for error in errors if error['msgs'][0]=="Declare 'hand' immutable with ':>'."]
                self.assertEqual(len(hand),1,errors)
                self.assertIn('fix',hand[0])
                self.assertTrue(all(error['code']=='thor/prefer-immutable' for error in errors),errors)
                self.assertEqual(self.source.read_text(),text)
                self.invoke('--vet=immutable','--apply')
                self.assertIn('hand :> deal[i]',self.source.read_text())
                remaining=self.findings('--vet=immutable')
                self.assertTrue(all(error.get('manual_review') for error in remaining),remaining)
                for flags in [[],[*native.UNCHECKED,'-define:CHANGE=true']]:
                    self.invoke(*flags,command='run')

    def test_inline_views_and_reference_elements_keep_escape_guards(self):
        for typ, initializer, projection in [
            ('[2]int','[2]int{7,8}','values[:]'),
            ('Hand','make_hand()','values[:]'),
            ('Box(Hand)','Box(Hand){make_hand()}','values.payload[:]'),
            ('Box([]int)','Box([]int){[]int{7,8}}','values.payload'),
        ]:
            with self.subTest(typ=typ):
                self.write('''package main
Hand::[dynamic;2]int
Box::struct($T:typeid){payload:T}
Row::struct{items:[]int}
make_hand::proc()->(hand:Hand){append(&hand,7);append(&hand,8);return}
store::proc(out:^Row){defer out.items=nil;values:=INITIAL;out.items=PROJECTION}
main::proc(){}
'''.replace('INITIAL',initializer).replace('PROJECTION',projection))
                findings=self.findings('--vet=immutable')
                self.assertTrue(all(error.get('manual_review') and 'fix' not in error for error in findings),findings)
                self.invoke('--vet=immutable','--apply')
                self.assertIn('values:=',self.source.read_text())

        for body in [
            'values:=Box(Hand){make_hand()};using values;out.items=payload[:]',
            'using values:=Box(Hand){make_hand()};out.items=payload[:]',
            'values:=Choice(make_hand());switch &item in values{case Hand:out.items=item[:];case int:out.items=nil}',
        ]:
            with self.subTest(projection=body):
                self.write('''package main
Hand::[dynamic;2]int
Box::struct($T:typeid){payload:T}
Choice::union{Hand,int}
Row::struct{items:[]int}
make_hand::proc()->(hand:Hand){append(&hand,7);return}
store::proc(out:^Row){defer out.items=nil;BODY}
main::proc(){}
'''.replace('BODY',body))
                findings=self.findings('--vet=immutable')
                self.assertTrue(all(error.get('manual_review') and 'fix' not in error for error in findings),findings)
                self.invoke('--vet=immutable','--apply')
                self.assertIn('values:=',self.source.read_text())

        self.write('''package main
CHANGE::#config(CHANGE,false)
Hand::[dynamic;2]int
Results::map[int]int
make_hand::proc()->(hand:Hand){append(&hand,7);return}
write::proc(values:[]int){when CHANGE{values[0]=9}}
record::proc(out:^Results){hand:=make_hand();write(hand[:]);out[0]=hand[0]}
main::proc(){
    results:=make(Results);defer delete(results)
    record(&results)
    expected:>9 when CHANGE else 7
    if results[0]!=expected{panic("conditional view write")}
}
''')
        findings=self.findings('--vet=immutable')
        self.assertEqual(len(findings),1,findings)
        self.assertTrue(findings[0].get('manual_review'))
        self.assertNotIn('fix',findings[0])
        self.invoke('--vet=immutable','--apply')
        self.assertIn('hand:=make_hand()',self.source.read_text())
        self.invoke(command='run')
        self.invoke(*native.UNCHECKED,'-define:CHANGE=true',command='run')

    def test_inline_value_classification_is_shared_across_large_batches(self):
        declarations='\n'.join(f'hand_{i}:=deal[0];out[{i}]=hand_{i}[0]' for i in range(80))
        self.write('package main\nHand::[dynamic;2]int\nResults::map[int]int\n'
            'record::proc(out:^Results,deal:[]Hand){\n'+declarations+'\n}\nmain::proc(){}\n')
        profile=self.root/'profile.json'
        result=subprocess.run([str(native.RUST),'vet',str(self.app),'--vet=immutable',
            '-json-errors','--output-dir',str(self.output)],cwd=self.root,
            env={**os.environ,'THOR_PROFILE':str(profile)},capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(len(json.loads(result.stderr)['errors']),80)
        counters=json.loads(profile.read_text())['counters']
        self.assertLessEqual(counters.get('vet.inline_types_scanned',0),3,counters)
        self.assertGreater(counters.get('vet.inline_types_scanned',0),0,counters)
        self.assertEqual(counters.get('vet.portability_body_scans',0),0,counters)
        self.assertEqual(counters.get('vet.validation_checks'),1,counters)
        self.assertEqual(counters.get('vet.source_snapshots'),1,counters)

    def test_fix_payload_matches_reference_byte_offsets_and_hash(self):
        text='// 😀\npackage main\nget::proc(x:int)->int{return x+1}\nmain::proc(){x/*comment*/:=get(1);if x!=2{panic("result")}}\n'
        self.write(text)
        rust=self.invoke('-json-errors','--editor-info')
        actual=json.loads(rust.stderr)['errors']
        self.assertTrue(actual,rust.stderr)
        if native.reference_enabled():
            reference=subprocess.run([str(native.PYTHON),'vet',str(self.app),'-json-errors',
                '--output-dir',str(self.root/'python')],capture_output=True,text=True,cwd=self.root,timeout=90)
            self.assertEqual(reference.returncode,0,reference.stdout+reference.stderr)
            self.assertEqual(actual,json.loads(reference.stderr)['errors'])
        self.assertEqual(self.source.read_text(),text)
        # Standalone validation also exercises the hash/byte-offset consumer.
        result=self.invoke('--apply')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('x/*comment*/:>',self.source.read_text())

    def test_corrections_reject_unsaved_overlays(self):
        self.write('package main\nmain::proc(){value:=1;_=value}\n')
        replacement=self.root/'replacement.thor';replacement.write_text(self.source.read_text())
        overlay=self.root/'overlay.json';overlay.write_text(json.dumps({'Replace':{str(self.source):str(replacement)}}))
        for flag in ['--apply','--ask']:
            result=self.invoke(flag,'--overlay',str(overlay),status=2)
            self.assertIn('saved files',result.stderr)
        self.assertIn(':=',self.source.read_text())

    def test_ineligible_signatures_and_owned_results_are_not_suggested(self):
        self.write('''package main
import "core:fmt"
Box::struct($T:typeid){value:T}
Choice::union{int,^int}
pointer::proc(value:^int)->int{return value^}
dynamic_values::proc(value:[dynamic]int)->int{return len(value)}
fixed::proc(value:[dynamic;4]int)->int{return len(value)}
mapping::proc(value:map[int]int)->int{return len(value)}
nested::proc(value:Box(^int))->int{return value.value^}
variant::proc(value:Choice)->int{return 1}
callback::proc(value:proc()->int)->int{return value()}
returns_map::proc()->map[int]int{return nil}
returns_inferred_map::proc()->(result:=map[int]int{}){return}
returns_pointer::proc()->^int{return nil}
prints::proc(){fmt.println(1)}
reader::proc(value:[]int)->int{return value[0]}
local_copy::proc(value:Box([2]int))->int{copy:=value;copy.value[0]=9;return copy.value[0]}
main::proc(){}
''')
        errors=self.findings('--vet=pure')
        self.assertEqual([error['msgs'][0] for error in errors],
            ["Procedure 'reader' can be declared with 'fn'.","Procedure 'local_copy' can be declared with 'fn'."])


CASES=[
'default_reports_without_asking_or_editing_and_rules_are_selectable',
'build_flag_warnings_and_strict_enforcement',
'apply_preserves_comments_typed_groups_unicode_crlf_and_file_mode',
'ask_yes_no_empty_and_eof',
'mutation_address_escape_group_and_loop_requirements',
'slice_aliases_calls_and_mutation_contracts_use_full_checker',
'reference_output_parameters_keep_local_ownership_choices_explicit',
'print_readers_preserve_immutable_slice_advice_through_zip_and_aliases',
'transitive_library_readers_keep_formatting_buffers_writable',
'reviewed_calls_preserve_conditional_mutation_and_global_escape_checks',
'inferred_zip_views_retain_reference_sensitive_vet_checks',
'purity_includes_transitive_helpers_input_mutation_and_global_state',
'generic_and_attributed_procedures_and_existing_fn_are_conservative',
'owned_results_keep_aliasing_and_allocation_decisions_explicit',
'prefilter_preserves_supported_types_defaults_and_local_helper_mutation',
'native_dependencies_inactive_files_and_invalid_programs',
'inactive_conditional_declarations_are_not_offered_as_verified_fixes',
'invalid_option_combinations',
]
for case in CASES:setattr(RustVetTests,'test_'+case,getattr(reference.VetTests,'test_'+case))

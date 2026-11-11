"""Run existing convention/apply contracts through the independent compiler."""
import json
import subprocess
import unittest
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

    def test_options_before_source_and_large_batches(self):
        body='\n'.join(f'value_{index}:={index};_=value_{index}' for index in range(80))
        self.write('package main\nmain::proc(){\n'+body+'\n}\n')
        result=subprocess.run([str(native.RUST),'check','--vet',str(self.app),'-json-errors',
                               '--output-dir',str(self.output)],capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(len(json.loads(result.stderr)['errors']),80)

    def test_fix_payload_matches_reference_byte_offsets_and_hash(self):
        text='// 😀\npackage main\nget::proc(x:int)->int{return x+1}\nmain::proc(){x/*comment*/:=get(1);_=x}\n'
        self.write(text)
        python=subprocess.run([str(native.PYTHON),'vet',str(self.app),'-json-errors',
            '--output-dir',str(self.root/'python')],capture_output=True,text=True,cwd=self.root,timeout=90)
        rust=self.invoke('-json-errors','--editor-info')
        self.assertEqual(python.returncode,0,python.stdout+python.stderr)
        expected=json.loads(python.stderr)['errors'];actual=json.loads(rust.stderr)['errors']
        self.assertEqual(actual,expected)
        self.assertEqual(self.source.read_text(),text)

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
'purity_includes_transitive_helpers_input_mutation_and_global_state',
'generic_and_attributed_procedures_and_existing_fn_are_conservative',
'owned_results_keep_aliasing_and_allocation_decisions_explicit',
'prefilter_preserves_supported_types_defaults_and_local_helper_mutation',
'native_dependencies_inactive_files_and_invalid_programs',
'inactive_conditional_declarations_are_not_offered_as_verified_fixes',
'invalid_option_combinations',
]
for case in CASES:setattr(RustVetTests,'test_'+case,getattr(reference.VetTests,'test_'+case))

"""Thor convention warnings and checked, explicitly requested source corrections."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from thorlang import vet
from thorlang.compiler import ThorError
from thorlang.driver import frontend_binary, Project

ROOT = Path(__file__).resolve().parents[1]


class VetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='thor-vet-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.app = self.root/'app'
        self.app.mkdir()
        self.source = self.app/'main.thor'
        self.output = self.root/'output'

    def write(self, text):
        self.source.write_bytes(text.encode())

    def invoke(self, *flags, command='vet', input=None, status=0):
        result = subprocess.run([str(ROOT/'thor'), command, str(self.app), '--output-dir', str(self.output), *flags],
                                cwd=self.root, capture_output=True, text=True, input=input, timeout=90)
        self.assertEqual(result.returncode, status, result.stdout+result.stderr)
        return result

    def findings(self, *flags, **options):
        result = self.invoke(*flags, '-json-errors', **options)
        return json.loads(result.stderr)['errors']

    def test_default_reports_without_asking_or_editing_and_rules_are_selectable(self):
        text = 'package main\nget::proc(x:int)->int{return x+1}\nmain::proc(){value:=get(6);if value!=7{panic("result")}}\n'
        self.write(text)
        errors = self.findings()
        self.assertEqual([item['code'] for item in errors], ['thor/prefer-immutable', 'thor/prefer-fn'])
        self.assertTrue(all(item['type'] == 'warning' for item in errors))
        self.assertEqual(self.source.read_text(), text)
        for check, code in [('immutable','thor/prefer-immutable'),('pure','thor/prefer-fn')]:
            with self.subTest(check=check):
                errors = self.findings('--vet='+check)
                self.assertEqual([item['code'] for item in errors], [code])

    def test_build_flag_warnings_and_strict_enforcement(self):
        self.write('package main\nimport "core:fmt"\nmain::proc(){x:=7;fmt.println(x)}\n')
        errors = self.findings('--vet', command='build', status=1)
        self.assertEqual([item['type'] for item in errors], ['error'])
        ignored = self.findings('--vet', '-ignore-warnings', command='build', status=1)
        self.assertEqual(ignored, errors)
        warnings = self.findings('--vet', command='check')
        self.assertEqual([item['type'] for item in warnings], ['warning'])
        self.write('package main\nimport "core:fmt"\nmain::proc(){x:>7;fmt.println(x)}\n')
        self.assertEqual(self.findings('--vet', command='build'), [])
        executable = self.root/(self.root.name+('.exe' if os.name == 'nt' else ''))
        result = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual((result.returncode,result.stdout.strip()), (0,'7'))
        self.write('package main\nimport "core:fmt"\nmain::proc(){x:=7;fmt.println(x)}\n')
        errors = self.findings('-warnings-as-errors', status=1)
        self.assertTrue(all(item['type'] == 'error' for item in errors))
        self.assertEqual(self.findings('-ignore-warnings'), [])

    def test_apply_preserves_comments_typed_groups_unicode_crlf_and_file_mode(self):
        text = '// 😀 header\r\npackage main\r\nget :: proc(x:int)->int{return x+1}\r\nmain::proc(){\r\n    x /* note */ :=get(2)\r\n    a,b: int = 4,5\r\n    if x+a+b!=12{panic("result")}\r\n}\r\n'
        self.write(text)
        self.source.chmod(0o640)
        result = self.invoke('--apply')
        self.assertIn('Applied 3 corrections.', result.stdout)
        expected = text.replace(':: proc(', ':: fn(').replace(':=get', ':>get').replace(': int =', ': int >')
        self.assertEqual(self.source.read_bytes(), expected.encode())
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o640)
        self.assertEqual(self.findings(), [])
        self.invoke(command='run')
        self.invoke('-o:speed','-disable-assert','-no-bounds-check','-no-type-assert', command='run')
        self.assertEqual(list(self.app.glob('.thor-vet-*')), [])

    def test_ask_yes_no_empty_and_eof(self):
        text = 'package main\nmain::proc(){x:=1;if x!=1{panic("result")}}\n'
        for answer, changes in [('y\n',True),(' YES \n',True),('n\n',False),('\n',False),('',False)]:
            with self.subTest(answer=answer):
                self.write(text)
                result = self.invoke('--ask', input=answer)
                self.assertEqual(result.stdout.count('[y/N]'), 1)
                self.assertEqual(self.source.read_text(), text.replace(':=',':>') if changes else text)

    def test_mutation_address_escape_group_and_loop_requirements(self):
        self.write('''package main
Node::struct{x:int}
set::proc(value:^int){value^=9}
main::proc(){
    changed:=1;changed+=1
    field:=Node{1};field.x=2
    addressed:=3;set(&addressed)
    a,b:=1,2;a=3
    for i:=0;i<2;i+=1 {_=i}
    readable:=4
    _=changed+field.x+addressed+a+b+readable
}
''')
        errors = self.findings('--vet=immutable')
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("'readable'", errors[0]['msgs'][0])

    def test_slice_aliases_calls_and_mutation_contracts_use_full_checker(self):
        text = '''package main
write::proc(#mut values:[]int){values[0]=8}
reader::proc(values:[]int)->int{return values[0]}
main::proc(){
    writable:=[]int{1,2}
    alias:=writable
    write(alias)
    source:=[]int{3,4}
    if reader(source)!=3 || writable[0]!=8{panic("result")}
}
'''
        self.write(text)
        errors = self.findings('--vet=immutable')
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("'source'", errors[0]['msgs'][0])
        self.invoke('--apply')
        self.invoke(command='run')
        changed = self.source.read_text()
        self.assertIn('writable:=', changed)
        self.assertIn('alias:=', changed)
        self.assertIn('source:>', changed)

    def test_reference_output_parameters_keep_local_ownership_choices_explicit(self):
        for signature,store,body in [('^Row','out^=Row{items}','row:Row;store(&row);defer delete(row.items)'),
                                     ('Sink','out.target^=Row{items}','row:Row;store(Sink{&row});defer delete(row.items)')]:
            with self.subTest(signature=signature):
                text='package main\nRow::struct{items:[]int}\nSink::struct{target:^Row}\n'
                text+=f'store::proc(out:{signature}){{items:=[x+7 for x in 0..<1];{store}}}\n'
                text+='main::proc(){'+body+';if row.items[0]!=7{panic("output")}}\n'
                self.write(text)
                self.assertEqual(self.findings('--vet=immutable'),[])
                self.invoke('--vet=immutable','--apply')
                self.assertIn('items:=',self.source.read_text())
                self.invoke(command='run')
        self.write('''package main
store::proc(out:^int){items:=[]int{out^+7};defer delete(items);out^=items[0]}
main::proc(){value:int;store(&value);if value!=7{panic("scalar output")}}
''')
        findings=self.findings('--vet=immutable')
        self.assertEqual(len(findings),1,findings)
        self.assertIn("'items'",findings[0]['msgs'][0])

    def test_print_readers_preserve_immutable_slice_advice_through_zip_and_aliases(self):
        self.write('''package main
import "core:fmt" / println
import "thor:basic" / print
main::proc(){
    zip::soa_zip
    output::println
    basic_output::print
    Gender::enum{male,female}
    names:=[]string{"Tim","Sally","Tom"}
    ages:=[]int{22,5,12,7}
    genders:=[]Gender{.male,.female,.male}
    people:>zip(name=names,age=ages,gender=genders)
    for person in people {
        output(person.name,person.age,person.gender)
        basic_output(f"{person.name} {person.age} {person.gender}")
    }
}
''')
        findings=self.findings('--vet=immutable')
        self.assertEqual([error['msgs'][0] for error in findings],[
            f"Declare '{name}' immutable with ':>'." for name in ('names','ages','genders')])
        self.invoke('--vet=immutable','--apply')
        findings=self.findings('--vet=immutable')
        self.assertEqual(findings,[])
        for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):
                result=self.invoke(*flags,command='run')
                self.assertEqual(result.stdout,'Tim 22 male\nTim 22 male\nSally 5 female\nSally 5 female\nTom 12 male\nTom 12 male\n')

    def test_transitive_library_readers_keep_formatting_buffers_writable(self):
        self.write('''package main
import "core:fmt"
show::proc(values:[]int){fmt.println(values)}
main::proc(){
    values:=[]int{3,4}
    alias:>values
    show(alias)
    buffer:=make([]byte,64)
    defer delete(buffer)
    label:=[]int{7}
    text:>fmt.bprintf(buffer,"%v",label)
    if text!="[7]"{panic("formatted result")}
}
''')
        findings=self.findings('--vet=immutable')
        self.assertEqual([error['msgs'][0] for error in findings],[
            f"Declare '{name}' immutable with ':>'." for name in ('values','label')])
        pure_findings=self.findings('--vet=pure')
        self.assertEqual(pure_findings,[])
        self.invoke('--vet=immutable','--apply')
        self.assertIn('buffer:=',self.source.read_text())
        for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            result=self.invoke(*flags,command='run')
            self.assertEqual(result.stdout,'[3, 4]\n')

    def test_reviewed_calls_preserve_conditional_mutation_and_global_escape_checks(self):
        for helper,action in [
                ('print::proc(values:[]int){when CHANGE{values[0]=9}}','print(values)'),
                ('SHARED:[]int\nchange::proc(){when CHANGE{SHARED[0]=9}}','SHARED=values;change()')]:
            with self.subTest(helper=helper):
                self.write('package main\nimport "core:fmt"\nCHANGE::#config(CHANGE,false)\n'+helper+'''
main::proc(){
    values:=[]int{1}
    fmt.println(values)
    ACTION
}
'''.replace('ACTION',action))
                findings=self.findings('--vet=immutable')
                self.assertEqual(findings,[])
                self.invoke('--vet=immutable','--apply')
                self.assertIn('values:=',self.source.read_text())
                self.invoke('-define:CHANGE=true',command='run')

    def test_inferred_zip_views_retain_reference_sensitive_vet_checks(self):
        self.write('''package main
CHANGE::#config(CHANGE,false)
change::proc(rows:#soa[]$T){when CHANGE{rows.age[0]=9}}
main::proc(){
    ages:=[]int{1}
    people:=soa_zip(age=ages)
    change(people)
}
''')
        findings=self.findings('--vet=immutable')
        self.assertEqual(findings,[])
        self.invoke('--vet=immutable','--apply')
        self.assertIn('people:=',self.source.read_text())
        self.invoke('-define:CHANGE=true',command='run')

    def test_purity_includes_transitive_helpers_input_mutation_and_global_state(self):
        self.write('''package main
import "core:fmt"
GLOBAL:int
mutate::proc(x:^int){x^+=1}
local_work::proc(x:int)->int{y:=x;mutate(&y);return y}
read_global::proc()->int{return GLOBAL}
transitive::proc()->int{return read_global()}
print_value::proc(x:int){fmt.println(x)}
main::proc(){if local_work(2)!=3{panic("result")};print_value(transitive())}
''')
        errors = self.findings('--vet=pure')
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("'local_work'", errors[0]['msgs'][0])
        self.invoke('--apply')
        self.invoke(command='run')

    def test_generic_and_attributed_procedures_and_existing_fn_are_conservative(self):
        self.write('''package main
@(export)
exported::proc(x:int)->int{return x}
generic::proc(x:$T)->T{return x}
already::fn(x:int)->int{return x+1}
main::proc(){_=generic(exported(already(1)))}
''')
        self.assertEqual(self.findings('--vet=pure'), [])

    def test_owned_results_keep_aliasing_and_allocation_decisions_explicit(self):
        self.write('''package main
Holder::struct{values:[]int}
identity::proc(values:[]int)->[]int{return values}
text::proc()->string{return "hello"}
holder::proc(value:Holder)->Holder{return value}
main::proc(){values:=[]int{1,2};_=identity(values);_=text();_=holder(Holder{values})}
''')
        self.assertEqual(self.findings('--vet=pure'), [])

    def test_ineligible_signatures_and_direct_io_skip_purity_graph_analysis(self):
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
        frontend,_=frontend_binary(os.environ.get('THOR_ODIN','odin'))
        project=Project(self.app,frontend,[],cache_directory=self.root/'cache')
        original=project.purity.analyze
        inspected=[]
        def analyze(proc):
            name=proc.parent.group('names')[0].text
            inspected.append(name)
            if name not in {'reader','local_copy'}:
                raise RuntimeError('Ineligible candidate reached expensive provenance analysis: '+name)
            return original(proc)
        with patch.object(project.purity,'analyze',side_effect=analyze):
            fixes=vet.candidates(project,['pure'])
        self.assertEqual([fix.names for fix in fixes],['reader','local_copy'])
        self.assertEqual(inspected,['reader','local_copy'])
        errors=self.findings('--vet=pure')
        self.assertEqual([error['msgs'][0] for error in errors],
                         ["Procedure 'reader' can be declared with 'fn'.",
                          "Procedure 'local_copy' can be declared with 'fn'."])

    def test_prefilter_preserves_supported_types_defaults_and_local_helper_mutation(self):
        self.write('''package main
Record::struct{values:[]int}
Choice::union{int,bool}
increment::proc(value:^int){value^+=1}
work::proc(record:Record,choice:Choice,x:=7)->int{
    local:=record.values[0]+x
    increment(&local)
    _=choice
    return local
}
main::proc(){if work(Record{[]int{1}},Choice(true))!=9{panic("result")}}
''')
        errors=self.findings('--vet=pure')
        self.assertEqual(len(errors),1,errors)
        self.assertIn("'work'",errors[0]['msgs'][0])
        self.invoke('--vet=pure','--apply')
        self.invoke(command='run')
        self.invoke('-o:speed','-disable-assert','-no-bounds-check','-no-type-assert',command='run')

    def test_vet_flag_before_source_and_large_valid_batches(self):
        body = '\n'.join(f'value_{index}:={index};if value_{index}!={index}{{panic("result")}}' for index in range(80))
        self.write('package main\nmain::proc(){\n'+body+'\n}\n')
        result = subprocess.run([str(ROOT/'thor'),'check','--vet',str(self.app),'-json-errors'],
                                capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(len(json.loads(result.stderr)['errors']),80)

    def test_native_dependencies_inactive_files_and_invalid_programs(self):
        (self.app/'lib').mkdir()
        (self.app/'lib/lib.odin').write_text('package lib\nget::proc()->int{return 1}\n')
        inactive = self.app/('ignored_windows.thor' if os.name != 'nt' else 'ignored_linux.thor')
        inactive.write_text('package main\nnot valid\n')
        self.write('package main\nimport "lib"\nmain::proc(){value:=lib.get();if value!=1{panic("result")}}\n')
        errors = self.findings()
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]['code'], 'thor/prefer-immutable')
        self.write('package main\nmain::proc(){value:=missing;_=value}\n')
        before = self.source.read_bytes()
        errors = self.findings('--apply', status=1)
        self.assertTrue(any('Undeclared name' in str(item['msgs']) for item in errors))
        self.assertEqual(self.source.read_bytes(), before)

    def test_inactive_conditional_declarations_are_not_offered_as_verified_fixes(self):
        self.write('package main\nwhen false {hidden::proc()->int{return missing}}\n'
                   'main::proc(){when false {unused:=missing}}\n')
        self.assertEqual(self.findings(), [])

    def test_invalid_option_combinations(self):
        self.write('package main\nmain::proc(){}\n')
        for flags, command in [(['--apply','--ask'],'vet'),(['--apply'],'build'),
                               (['--vet=bogus'],'vet'),(['--vet','--raw-diagnostics'],'check')]:
            with self.subTest(flags=flags):
                self.invoke(*flags, command=command, status=2)

    def test_atomic_updates_rollback_and_reject_concurrent_edits(self):
        text = 'package main\nmain::proc(){a:=1;b:=2;_=a+b}\n'
        self.write(text)
        other = self.app/'other.thor'
        other.write_text('package main\nother::proc(){c:=3;_=c}\n')
        frontend, _ = frontend_binary(os.environ.get('THOR_ODIN','odin'))
        project = Project(self.app, frontend, [])
        fixes = vet.candidates(project, ['immutable'])
        original_replace = os.replace
        count = 0
        def fail_second(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('test replacement failure')
            return original_replace(source, target)
        with patch('thorlang.vet.os.replace', side_effect=fail_second):
            with self.assertRaises(OSError):
                vet.apply(fixes)
        self.assertEqual(self.source.read_text(), text)
        self.assertEqual(other.read_text(), 'package main\nother::proc(){c:=3;_=c}\n')
        other.write_text('package main\nother::proc(){}\n')
        with self.assertRaisesRegex(ThorError, 'Source changed'):
            vet.apply(fixes)
        self.assertEqual(self.source.read_text(), text)
        self.assertEqual(list(self.app.glob('.thor-vet-*')), [])


if __name__ == '__main__':
    unittest.main()

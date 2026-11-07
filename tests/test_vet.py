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
        text = 'package main\nget::proc(x:int)->int{return x+1}\nmain::proc(){value:=get(6);_=value}\n'
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
        self.findings('--vet', command='build')
        executable = self.root/(self.root.name+('.exe' if os.name == 'nt' else ''))
        result = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual((result.returncode,result.stdout.strip()), (0,'7'))
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
        text = 'package main\nmain::proc(){x:=1;_=x}\n'
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

    def test_vet_flag_before_source_and_large_valid_batches(self):
        body = '\n'.join(f'value_{index}:={index};_=value_{index}' for index in range(80))
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
        self.write('package main\nimport "lib"\nmain::proc(){value:=lib.get();_=value}\n')
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

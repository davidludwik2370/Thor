"""Unused binding warnings use original Thor scopes and preserve execution."""
import json
import subprocess
import unittest

from tests import test_rust_compiler as native
from tests import test_vet as reference


class UnusedVetTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = reference.VetTests.setUp
    write = reference.VetTests.write

    def invoke(self, engine, *flags, command='vet', status=0, input=None):
        compiler = native.PYTHON if engine == 'python' else native.RUST
        result = subprocess.run(
            [str(compiler), command, str(self.app), '--output-dir', str(self.output),
             '-json-errors', *flags], cwd=self.root, capture_output=True,
            text=True, input=input, timeout=90)
        self.assertEqual(result.returncode, status, result.stdout+result.stderr)
        return result, json.loads(result.stderr)['errors']

    def check(self, text, expected, *flags):
        self.write(text)
        reports = []
        for engine in ('python', 'rust'):
            with self.subTest(engine=engine):
                _, errors = self.invoke(engine, '--vet=unused', *flags)
                self.assertTrue(all(error['code'] == 'thor/unused' for error in errors), errors)
                names = [error['msgs'][0].split("'")[1] for error in errors]
                self.assertEqual(names, expected, errors)
                self.assertTrue(all(error['type'] == 'warning' for error in errors))
                reports.append(errors)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0], reports[1])
        return reports[0]

    def test_local_parameters_constants_groups_and_reference_loop_names(self):
        self.check('''package main
work::proc(unused_parameter, used_parameter:int)->int {
    unused_mutable:=1
    uninitialized:int
    unused_immutable:>2
    UNUSED_CONSTANT::3
    first, second:=4,5
    _=second
    for value, index in [1,2] {_=value}
    values:=[1,2]
    for &unused_reference in values {_=0}
    return used_parameter
}
main::proc(){_=work(1,2)}
''', ['unused_parameter', 'unused_mutable', 'uninitialized', 'unused_immutable', 'UNUSED_CONSTANT',
      'first', 'index', 'unused_reference'])

    def test_unused_union_switch_and_comprehension_bindings(self):
        self.check('''package main
Choice::union{int,string}
main::proc(){
    value:=Choice(1)
    switch unused_union in value {case int: _=0; case string: _=0}
    values:=[1 for unused_element in [1,2]]
    _=values
    _=[item for item in [1,2]]
}
''', ['unused_union', 'unused_element'])

    def test_assignments_and_simple_self_updates_are_not_uses(self):
        self.check('''package main
import "core:fmt"
main::proc(){
    assigned:=(1+2)*5
    assigned=2
    uninitialized:int
    uninitialized=2
    updated:=0
    updated+=1
    updated*=2
    self_assigned:=1
    self_assigned=(self_assigned+2)*3
    doubled:=1
    doubled+=doubled
    a,b:=1,2
    a,b=a+1,b+1
    source:=3
    destination:=0
    destination+=source
    swapped_a,swapped_b:=1,2
    swapped_a,swapped_b=swapped_b,swapped_a
    discarded:=0
    discarded=2
    _=discarded
    observed:=0
    observed=2
    fmt.println(observed)
}
''', ['assigned', 'uninitialized', 'updated', 'self_assigned', 'doubled', 'a', 'b',
      'destination'])

    def test_calls_are_uses_even_when_the_callee_ignores_its_parameter(self):
        self.check('''package main
ignore::proc(unused_parameter:int){}
identity::proc(value:int)->int{return value}
effect::proc()->bool{return true}
main::proc(){
    passed:=1
    passed=2
    ignore(passed)
    self_call:=1
    self_call=identity(self_call)
    indirect:proc(int)=ignore
    indirect_value:=3
    indirect(indirect_value)
    call_in_update:=1
    call_in_update+=identity(call_in_update)
    conditional_call:=true
    conditional_call=conditional_call && effect()
    deferred:=1
    defer ignore(deferred)
    unused_result:=1
    unused_result=identity(2)
}
''', ['unused_parameter', 'unused_result'])

    def test_reference_writes_and_address_escapes_are_uses(self):
        self.check('''package main
Record::struct{field:int}
write::proc(pointer:^int, #mut values:[]int, mapping:^map[int]int){
    pointer^=1
    values[0]=2
    mapping[0]=3
}
write_field::proc(using pointer:^Record){field=4}
main::proc(){
    addressed:=0
    pointer:=&addressed
    pointer^+=1
    values:=[]int{0}
    defer delete(values)
    mapping:=make(map[int]int)
    defer delete(mapping)
    write(&addressed,values,&mapping)
    record:=Record{0}
    write_field(&record)
    index:=0
    key:=1
    values[index]=3
    mapping[key]=4
    record.field=5
    array:=[1,2]
    array[0]=3
    unused_pointer:^int
    unused_pointer=nil
}
''', ['unused_pointer'])

    def test_control_flow_returns_and_rhs_reads_remain_uses(self):
        self.check('''package main
return_value::proc(value:int)->int{return value}
main::proc(){
    condition:=true
    condition=false
    if condition {_=0}
    for counter:=0;counter<2;counter+=1 {_=0}
    source:=1
    unused_copy:=source
    outer:=1
    {outer:=2;outer=outer+1}
    _=outer
    selected:=1
    selected=2
    switch selected {case 1: _=0; case: _=0}
    returned:=return_value(3)
    _=returned
}
''', ['unused_copy', 'outer'])

    def test_writes_through_reference_loop_and_switch_bindings_are_uses(self):
        self.check('''package main
Choice::union{int,bool}
increase::proc(#mut data:[]int){for &item in data{item+=1}}
main::proc(){
    values:=[1,2]
    increase(values[:])
    for &element in values {element=3}
    choice:Choice=1
    switch &reference in choice {
    case int: reference+=1
    case bool: reference=true
    }
    switch &unused_reference in choice {
    case int: _=0
    case bool: _=0
    }
}
''', ['unused_reference'])

    def test_original_uses_in_interpolations_switches_and_comptime_work(self):
        self.check('''package main
identity::fn(input:int)->int{return input}
main::proc(){
    width:>3
    number:>4
    text:=f"{number:{width}}"
    defer delete(text)
    selected:=switch {case len(text)>0:number; case:width}
    _=selected
    COUNT::2
    RESULT::#run identity(COUNT)
    array:[RESULT]int
    _=array
    unused_big:Integer=123456789012345678901234567890
    unused_array:>[]int{1,2}
    _=0
}
''', ['unused_big', 'unused_array'])

    def test_shadowing_field_names_named_arguments_map_and_array_keys(self):
        self.check('''#+feature dynamic-literals
package main
Record::struct{field:int}
consume::proc(field:int){_=field}
main::proc(){
    field:=1
    record:=Record{field=2}
    _=record.field
    consume(field=3)
    key:=4
    mapping:=map[int]int{key=5}
    defer delete(mapping)
    INDEX::0
    array:=[2]int{INDEX=1}
    _=array
    shadow:=6
    {shadow:=shadow;_=shadow}
}
''', ['field'])

    def test_using_parameters_named_results_callback_prototypes_and_discards(self):
        self.check('''package main
Record::struct{field:int}
read::proc(using record:Record)->int{return field}
callback::proc(value:proc(prototype_name:int)->int)->int{return value(1)}
returns::proc()->(named_result:int){return}
main::proc(){
    Local::struct{field:int}
    _=Local{1}
    _=read(Record{1})
    _=callback(proc(actual:int)->int{return actual})
    _=returns()
    discarded:=1
    _=discarded
    _,other:=2,3
    _=other
    label: for false {break label}
}
''', [])

    def test_active_build_conditions_and_inactive_file_exclusion(self):
        text='''package main
ENABLED::#config(ENABLED,false)
main::proc(){
    debug_value:=1
    when ODIN_DEBUG {_=debug_value; debug_unused:=2; _=0}
    when ENABLED {enabled_unused:=3; _=0} else {disabled_unused:=4; _=0}
    when false {invalid:=missing}
}
'''
        ignored = self.app/'ignored.thor'
        ignored.write_text('#+build ignore\npackage main\ninvalid::proc(unused:int){x:=missing}\n')
        self.check(text, ['debug_value', 'disabled_unused'])
        self.check(text, ['debug_unused', 'enabled_unused'], '-debug', '-define:ENABLED=true')
        self.check('''package main
main::proc(){
    when ODIN_OS==.Linux {linux_unused:>1;_=0}
    else {other_unused:>2;_=0}
}
''', ['linux_unused'], '-target:linux_amd64')

    def test_explicit_comptime_parameters_and_type_uses(self):
        self.check('''package main
get::proc($Count:int,$T:typeid,value:T)->T{return value}
valid::proc($Count:int)->int{return Count}
another::proc($Unused_Type:typeid)->int{return 1}
main::proc(){_=get(3,int,1);_=valid(2);_=another(int)}
''', ['Count', 'Unused_Type'])

    def test_comptime_boolean_results_and_compound_conditions_keep_active_scopes(self):
        text='''package main
identity::fn(value:bool)->bool{return value}
YES::#run identity(true)
main::proc(){
    when YES && !ODIN_DEBUG {active_unused:>1;_=0}
    else {debug_unused:>2;_=0}
}
'''
        self.check(text, ['active_unused'])
        self.check(text, ['debug_unused'], '-debug')

    def test_only_project_thor_bindings_and_parameters_with_implementations(self):
        (self.app/'native.odin').write_text('package main\nNative::struct{field:int}\n')
        library = self.app/'lib'
        library.mkdir()
        (library/'lib.thor').write_text('package lib\nread::proc(unused_parameter:int)->int{return 1}\n')
        self.check('''package main
import "lib"
GLOBAL_UNUSED:int
Record::struct{field:int, callback:proc(prototype_name:int)}
main::proc(){_=Record{};_=lib.read(1)}
''', ['unused_parameter'])

    def test_warning_policy_default_checks_selection_and_duplicate_native_warning(self):
        self.write('package main\nmain::proc(){unused:=1}\n')
        for engine in ('python', 'rust'):
            with self.subTest(engine=engine):
                _, errors = self.invoke(engine)
                unused = [error for error in errors if error.get('code') == 'thor/unused']
                self.assertEqual(len(unused), 1, errors)
                self.assertEqual(sum('declared but not used' in error['msgs'][0] for error in errors), 1)
                _, errors = self.invoke(engine, '--vet', command='check')
                self.assertTrue(any(error.get('code') == 'thor/unused' for error in errors), errors)
                _, errors = self.invoke(engine, '--vet=unused', '-ignore-warnings')
                self.assertEqual(errors, [])
        self.write('package main\nmain::proc(){unused:=1;_=0}\n')
        for engine in ('python', 'rust'):
            _, errors = self.invoke(engine, command='check')
            self.assertEqual(errors, [])
            _, errors = self.invoke(engine, '--vet=unused', '-warnings-as-errors', status=1)
            self.assertEqual([error['code'] for error in errors], ['thor/unused'])
            self.assertEqual(errors[0]['type'], 'error')

    def test_unused_takes_priority_until_a_binding_is_referenced(self):
        unused = ('package main\nimport "core:fmt"\n'
                  'main::proc(){x:=(1+2)*5;fmt.println("ready")}\n')
        used = unused.replace('fmt.println("ready")', 'fmt.println(x)')
        for engine in ('python', 'rust'):
            with self.subTest(engine=engine):
                self.write(unused)
                for flags in ([], ['--vet=immutable,unused']):
                    _, errors = self.invoke(engine, *flags)
                    self.assertEqual([error['code'] for error in errors], ['thor/unused'])
                    self.assertNotIn('fix', errors[0])
                _, errors = self.invoke(engine, '--vet', command='check')
                self.assertEqual([error['code'] for error in errors], ['thor/unused'])
                _, errors = self.invoke(engine, '--vet=immutable')
                self.assertEqual(errors, [])
                self.write(used)
                _, errors = self.invoke(engine)
                self.assertEqual([error['code'] for error in errors], ['thor/prefer-immutable'])
                self.assertEqual(errors[0]['fix']['text'], '>')

    def test_explicit_discard_only_suppresses_immutable_warnings_and_corrections(self):
        text = 'package main\nmain::proc(){x:=(1+2)*5;_=x}\n'
        for engine in ('python', 'rust'):
            for flags in ([], ['--vet=immutable'], ['--vet=unused']):
                with self.subTest(engine=engine, flags=flags):
                    self.write(text)
                    _, errors = self.invoke(engine, *flags, '-warnings-as-errors')
                    self.assertEqual(errors, [])
                    for correction in ('--apply', '--ask'):
                        result, errors = self.invoke(engine, *flags, correction, input='y\n')
                        self.assertEqual(errors, [])
                        self.assertNotIn('[y/N]', result.stdout)
                        self.assertEqual(self.source.read_text(), text)
            _, errors = self.invoke(engine, '--vet', command='check')
            self.assertEqual(errors, [])

    def test_explicit_discard_with_other_uses_keeps_immutable_warnings_and_corrections(self):
        for body in ('_=x;fmt.println(x)', 'fmt.println(x);_=((x))'):
            text = 'package main\nimport "core:fmt"\nmain::proc(){x:=(1+2)*5;'+body+'}\n'
            for engine in ('python', 'rust'):
                for flags in ([], ['--vet=immutable'], ['--vet=unused']):
                    with self.subTest(engine=engine, body=body, flags=flags):
                        expected_codes = [] if flags == ['--vet=unused'] else ['thor/prefer-immutable']
                        self.write(text)
                        _, errors = self.invoke(engine, *flags, '-warnings-as-errors',
                                                status=1 if expected_codes else 0)
                        self.assertEqual([error['code'] for error in errors], expected_codes)
                        for correction in ('--apply', '--ask'):
                            self.write(text)
                            result, errors = self.invoke(engine, *flags, correction, input='y\n')
                            self.assertEqual(errors, [])
                            self.assertEqual('[y/N]' in result.stdout,
                                             correction == '--ask' and bool(expected_codes))
                            expected = text.replace('x:=', 'x:>') if expected_codes else text
                            self.assertEqual(self.source.read_text(), expected)
                self.write(text)
                _, errors = self.invoke(engine, '--vet', command='check')
                self.assertEqual([error['code'] for error in errors], ['thor/prefer-immutable'])

    def test_discard_pairing_preserves_other_uses_and_group_rules(self):
        text='''package main
main::proc(){
    direct:=1
    parenthesized:=2
    _,_ = direct,((parenthesized))
    wrapped:=3
    _=((wrapped))
    mixed_a,mixed_b:=3,4
    _=mixed_a
    if mixed_b!=4{panic("result")}
    used:=5
    _=used
    if used!=5{panic("result")}
    expression:=6
    _=expression+1
    destination:=0
    _,destination=direct,parenthesized
    _=destination
}
'''
        for engine in ('python', 'rust'):
            with self.subTest(engine=engine):
                self.write(text)
                _, errors = self.invoke(engine)
                expected_names = ('parenthesized', 'used', 'expression')
                self.assertEqual([error['code'] for error in errors], ['thor/prefer-immutable']*3)
                self.assertEqual([error['msgs'][0].split("'")[1] for error in errors],
                                 list(expected_names))
                _, errors = self.invoke(engine, '--apply')
                self.assertEqual(errors, [])
                expected = text
                for name in expected_names:
                    expected = expected.replace(name+':=', name+':>')
                self.assertEqual(self.source.read_text(), expected)

    def test_discarded_bindings_use_original_scopes_and_active_branches(self):
        text='''package main
main::proc(){
    value:=1
    {value:=2;_=value}
    _=value+1
    conditional:=3
    _=conditional
    when ODIN_DEBUG {_=conditional+1}
    active:=4
    when ODIN_DEBUG {_=active}
    _=active+1
}
'''
        for engine in ('python', 'rust'):
            for flags, expected in (([], ['value', 'active']),
                                    (['-debug'], ['value', 'conditional', 'active'])):
                with self.subTest(engine=engine, flags=flags):
                    self.write(text)
                    _, errors = self.invoke(engine, *flags)
                    self.assertEqual([error['code'] for error in errors], ['thor/prefer-immutable']*len(expected))
                    self.assertEqual([error['msgs'][0].split("'")[1] for error in errors], expected)
                    self.assertEqual(errors[0]['pos']['offset'], text.encode().index(b'value:=1'))

    def test_discard_rules_keep_initializer_effects_in_all_build_modes(self):
        text='''package main
import "core:fmt"
counter:int
effect::proc()->int{counter+=1;return counter}
main::proc(){x:=effect();_=x;fmt.println(counter)}
'''
        for other_use in (False, True):
            original = text.replace('fmt.println(counter)', 'fmt.println(x,counter)') if other_use else text
            expected = original.replace('x:=', 'x:>') if other_use else original
            for engine in ('python', 'rust'):
                self.write(original)
                _, errors = self.invoke(engine, '--apply')
                self.assertEqual(errors, [])
                self.assertEqual(self.source.read_text(), expected)
                for flags in (['-debug'], native.UNCHECKED):
                    with self.subTest(engine=engine, flags=flags, other_use=other_use):
                        result, errors = self.invoke(engine, '--vet', *flags, command='run')
                        self.assertEqual(errors, [])
                        self.assertEqual(result.stdout, '1 1\n' if other_use else '1\n')

    def test_grouped_declarations_wait_for_all_names_to_be_used(self):
        for body, expected in [('a,b:=1,2;_=b', ['a']),
                               ('a,b:int=1,2;_=0', ['a', 'b']),
                               ('_,b:=1,2;_=0', ['b'])]:
            text = 'package main\nmain::proc(){'+body+'}\n'
            for engine in ('python', 'rust'):
                for correction in ('--apply', '--ask'):
                    with self.subTest(engine=engine, body=body, correction=correction):
                        self.write(text)
                        result, errors = self.invoke(engine, correction, input='y\n')
                        self.assertEqual(self.source.read_text(), text)
                        self.assertNotIn('[y/N]', result.stdout)
                        self.assertTrue(all(error['code'] == 'thor/unused' for error in errors), errors)
                        self.assertEqual([error['msgs'][0].split("'")[1] for error in errors], expected)
        self.write('package main\nmain::proc(){a,b:=1,2;_=a+b}\n')
        for engine in ('python', 'rust'):
            _, errors = self.invoke(engine)
            self.assertEqual([error['code'] for error in errors], ['thor/prefer-immutable'])
            self.assertIn("'a, b'", errors[0]['msgs'][0])

    def test_other_used_declarations_keep_their_immutable_corrections(self):
        text = 'package main\nmain::proc(){dead:=1;live:=2;if live!=2{panic("result")}}\n'
        for engine in ('python', 'rust'):
            with self.subTest(engine=engine):
                self.write(text)
                _, errors = self.invoke(engine)
                self.assertEqual([error['code'] for error in errors],
                                 ['thor/unused', 'thor/prefer-immutable'])
                self.assertIn("'dead'", errors[0]['msgs'][0])
                self.assertIn("'live'", errors[1]['msgs'][0])
                for checks in ([], ['--vet=immutable']):
                    self.write(text)
                    _, errors = self.invoke(engine, *checks, '--apply')
                    self.assertEqual(self.source.read_text(), text.replace('live:=', 'live:>'))
                    self.assertEqual([error['code'] for error in errors],
                                     [] if checks else ['thor/unused'])

    def test_apply_and_ask_preserve_initializer_side_effects_in_checked_and_unchecked_runs(self):
        text='''package main
import "core:fmt"
counter:int
effect::proc()->int{counter+=1;return counter}
main::proc(){unused:=effect();fmt.println(counter)}
'''
        for engine in ('python', 'rust'):
            for correction in ('--apply', '--ask'):
                for checks in ([], ['--vet=unused']):
                    self.write(text)
                    result, errors = self.invoke(engine, *checks, correction, input='y\n')
                    self.assertEqual(self.source.read_text(), text)
                    self.assertNotIn('[y/N]', result.stdout)
                    self.assertEqual([error['code'] for error in errors], ['thor/unused'])
            for flags in (['-debug'], native.UNCHECKED):
                with self.subTest(engine=engine, flags=flags):
                    result, errors = self.invoke(engine, '--vet', *flags, command='run')
                    self.assertEqual(result.stdout, '1\n')
                    self.assertEqual([error['code'] for error in errors], ['thor/unused'])

    def test_write_only_warnings_preserve_assignment_effects_and_warning_policy(self):
        text='''package main
import "core:fmt"
counter:int
effect::proc(amount:int)->int{counter+=amount;return counter}
main::proc(){
    unused:=effect(1)
    unused=effect(2)
    unused+=effect(4)
    fmt.println(counter)
}
'''
        for engine in ('python', 'rust'):
            for correction in ('--apply', '--ask'):
                with self.subTest(engine=engine, correction=correction):
                    self.write(text)
                    result, errors = self.invoke(engine, correction, input='y\n')
                    self.assertEqual(self.source.read_text(), text)
                    self.assertNotIn('[y/N]', result.stdout)
                    self.assertEqual([error['code'] for error in errors], ['thor/unused'])
                    self.assertIn("'unused'", errors[0]['msgs'][0])
                    self.assertNotIn('fix', errors[0])
            _, errors = self.invoke(engine, '--vet=unused', '-warnings-as-errors', status=1)
            self.assertEqual([(error['code'], error['type']) for error in errors],
                             [('thor/unused', 'error')])
            for flags in (['-debug'], native.UNCHECKED):
                with self.subTest(engine=engine, flags=flags):
                    result, errors = self.invoke(engine, '--vet=unused', *flags, command='run')
                    self.assertEqual(result.stdout, '7\n')
                    self.assertEqual([error['code'] for error in errors], ['thor/unused'])

    def test_unused_warning_positions_follow_applied_fn_corrections(self):
        text=('// 😀 header\npackage main\n'
              'helper::proc(unused_parameter:int)->int{return 1}\n'
              'main::proc(){/* 😀 */unused_value:=helper(2)}\n')
        for engine in ('python', 'rust'):
            for correction in ('--apply', '--ask'):
                with self.subTest(engine=engine, correction=correction):
                    self.write(text)
                    _, errors = self.invoke(engine, correction, input='y\n')
                    updated = self.source.read_text()
                    self.assertIn('helper::fn(', updated)
                    self.assertIn('unused_value:=', updated)
                    self.assertEqual([error['code'] for error in errors], ['thor/unused']*2)
                    for error in errors:
                        name = error['msgs'][0].split("'")[1]
                        offset = updated.encode().index(name.encode())
                        before = updated.encode()[:offset].decode()
                        self.assertEqual(error['pos']['offset'], offset)
                        self.assertEqual(error['pos']['line'], before.count('\n')+1)
                        self.assertEqual(error['pos']['column'], len(before.rsplit('\n',1)[-1].encode('utf-16-le'))//2+1)


if __name__ == '__main__':
    unittest.main()

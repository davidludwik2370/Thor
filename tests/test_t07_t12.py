"""T07–T12 end-to-end acceptance: runtime, resolution, projects, immutability."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests import test_thor as base

ROOT, THOR = base.ROOT, base.THOR


class BatchTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_runtime_libraries_with_allocation_tracking(self):
        for package in ['format', 'sets']:
            with self.subTest(package=package):
                result = subprocess.run([str(THOR), 'test', str(ROOT / 'runtime' / package),
                                         '-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'],
                                        capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_owned_lexer_retains_native_tokens_and_unicode_positions(self):
        result=subprocess.run(['odin','test',str(ROOT/'frontend'/'tokenizer'),
                               '-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true',f'-out:{ROOT/".thor-cache"/"tokenizer-tests"}'],capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_format_collection_and_owned_output(self):
        self.invoke(self.program('''
a := f.group_integer(i64(-9223372036854775808))
b := f.format(1234567, grouping=true, width=14, align=.Center, fill='界')
c := f.format("é😀", width=5)
assert(a == "-9,223,372,036,854,775,808")
assert(b == "界界1,234,567界界界")
assert(c == "é😀   ")
delete(a); delete(b); delete(c)
''', 'import f "thor:format"\n'))

    def test_grouping_rejects_non_integer(self):
        self.invoke(self.program('_ = f.group_integer(1.25)', 'import f "thor:format"\n'), fail='Integer grouping requires an integer')

    def test_padding_does_not_borrow_input_storage(self):
        self.invoke(self.program('''
source := "original"
copy := f.format(source)
assert(copy == source && raw_data(copy) != raw_data(source))
delete(copy)
assert(source == "original")
''', 'import f "thor:format"\n'))

    def test_set_support_api_and_membership_in_borrowed_entries(self):
        self.invoke(self.program('''
a: sets.Set(int)
sets.insert(&a,1); sets.insert(&a,1); sets.insert(&a,2)
assert(sets.count(a) == 2 && 2 in sets.entries(a))
sets.remove(&a,99)
b := sets.clone(a)
sets.insert(&b,3)
c := sets.difference(b,a)
assert(sets.count(c)==1 && sets.contains(c,3))
assert(sets.count(a)==2)
for key in sets.entries(a) { assert(key in [1,2]) }
sets.destroy(c); sets.destroy(b); sets.destroy(a)
''', 'import sets "thor:sets"\n'))

    def test_set_invalid_key_type_rejected(self):
        self.invoke(self.program('s: sets.Set([]int); _ = s', 'import sets "thor:sets"\n'), fail='Error:')

    def test_odin_identifiers_fn_and_set_remain_available(self):
        self.invoke(self.program('fn:=2; set:=3; assert(fn+set==5)'))

    def test_immutable_inferred_and_typed_runtime_initializers_run_once(self):
        self.invoke(self.program('''
a :> next()
b: int > next()
c,d :> pair()
assert(a==1 && b==2 && c==3 && d==4 && calls==4)
''', 'calls:int\nnext::proc()->int{calls+=1; return calls}\npair::proc()->(int,int){return next(),next()}\n'))

    def test_immutable_scalar_reassignments(self):
        for operation in ['x=2', 'x+=1', 'x-=1', 'x*=2', 'x/=2', 'x%=2', 'x|=2', 'x&=2', 'x~=2', 'x<<=1', 'x>>=1']:
            with self.subTest(operation=operation):
                self.invoke(self.program('x:>1; '+operation), fail="immutable storage 'x'")

    def test_immutable_multiple_assignment_target(self):
        self.invoke(self.program('x:>1; y:=2; x,y=3,4'), fail="immutable storage 'x'")

    def test_immutable_record_and_nested_array_subobjects(self):
        prefix='Inner::struct{values:[2]int}\nOuter::struct{inner:Inner}\n'
        for operation in ['x.inner=Inner{}', 'x.inner.values=[3,4]', 'x.inner.values[0]=3', 'x.inner.values[1]+=1']:
            with self.subTest(operation=operation):
                self.invoke(self.program('x:>Outer{inner=Inner{values=[1,2]}}; '+operation, prefix), fail="immutable storage 'x'")

    def test_mutable_addresses_of_direct_storage_are_rejected(self):
        for body in ['x:>1; _=&x', 'x:>[1,2]; _=&x[0]',
                     'S::struct{x:int}; x:>S{}; _=&x.x',
                     'S::struct{a:[2]int}; x:>S{}; _=&x.a[0]']:
            with self.subTest(body=body):
                self.invoke(self.program(body), fail='mutable address')

    def test_fixed_array_cannot_escape_as_mutable_slice(self):
        for body in ['x:>[1,2]; _=x[:]', 'x:>[1,2]; _=x[0:1]',
                     'S::struct{a:[2]int}; x:>S{}; _=x.a[:]']:
            with self.subTest(body=body):
                self.invoke(self.program(body), fail='mutable slice')

    def test_direct_array_mutable_iteration_is_rejected(self):
        self.invoke(self.program('x:>[1,2]; for &v in x {v=3}'), fail='mutable reference')

    def test_existing_slice_elements_and_addresses_remain_mutable(self):
        self.invoke(self.program('''
data:=[1,2,3]
x:>data[:]
x[0]=4
p:=&x[1]; p^=5
for &value in x {value+=1}
assert(data[0]==5 && data[1]==6 && data[2]==4)
view:=x[:2]; view[0]=7
assert(data[0]==7)
'''))

    def test_slice_header_cannot_be_reassigned_or_addressed(self):
        for operation in ['x = data[:]', '_=&x']:
            with self.subTest(operation=operation):
                self.invoke(self.program('data:=[1,2]; x:>data[:]; '+operation), fail="immutable storage 'x'")

    def test_existing_pointer_pointee_remains_mutable(self):
        self.invoke(self.program('''
S::struct{a:[2]int}
data:=S{}
p:>&data
p.a[0]=8
p^.a[1]=9
assert(data.a[0]==8 && data.a[1]==9)
'''))

    def test_existing_map_contents_remain_mutable(self):
        self.invoke(self.program('original:=make(map[int]int); original[-1]=7; m:>original; copy:=m; copy[-1]+=1; assert(m[-1]==8); delete(original)'))
        self.invoke(self.program('m:>make(map[int]int); m[0]=7'),fail="immutable storage 'm'")

    def test_dynamic_array_header_mutation_requires_mutable_storage(self):
        self.invoke(self.program('x:>make([dynamic]int); append(&x,1)'), fail='mutable address')

    def test_copy_of_immutable_value_is_mutable(self):
        self.invoke(self.program('S::struct{a:[2]int}; x:>S{a=[1,2]}; y:=x; y.a[0]=9; assert(x.a[0]==1 && y.a[0]==9)'))

    def test_nested_shadowing_parameters_and_loop_bindings(self):
        self.invoke(self.program('''
x:>7
{x:=2; x+=1; assert(x==3)}
f::proc(x:int)->int{return x+1}
for x in [1,2] {assert(x<3)}
for &x in data {x+=1}
assert(f(2)==3 && x==7)
''', 'data:[2]int\n'))

    def test_same_spelling_in_other_procedure_is_mutable(self):
        self.invoke(self.program('assert(f()==3); x:>1; assert(x==1)', 'f::proc()->int{x:=2; x+=1;return x}\n'))

    def test_using_field_retains_immutable_storage_origin(self):
        self.invoke(self.program('S::struct{x:int}; s:>S{}; using s; x=2'), fail="immutable storage 's'")

    def test_using_an_existing_pointer_remains_shallow(self):
        self.invoke('#+feature using-stmt\n'+self.program('S::struct{x:int}; data:=S{}; p:>&data; using p; x=2; assert(data.x==2)'))

    def test_embedded_using_pointer_remains_shallow(self):
        self.invoke(self.program('I::struct{x:int}; O::struct{using p:^I}; i:=I{}; o:>O{p=&i}; o.x=2; assert(i.x==2)'))

    def test_generic_explicit_pointer_path_remains_shallow(self):
        prefix='I::struct{x:int}\nO::struct{using p:^I}\nf::proc(value:$T){o:>value; o.p.x=3}\n'
        self.invoke(self.program('i:=I{};f(O{p=&i});assert(i.x==3)',prefix))

    def test_native_arrow_call_cannot_take_implicit_mutable_address(self):
        prefix='S::struct{x:int,f:proc(s:^S)}\nf::proc(s:^S){s.x=3}\n'
        self.invoke(self.program('s:>S{f=f}; s->f()',prefix), fail='implicit mutable address')
        self.invoke(self.program('s:=S{f=f}; p:>&s; p->f(); assert(s.x==3)',prefix))

    def test_generic_arrow_call_checks_implicit_mutable_address(self):
        prefix='S::struct{x:int,f:proc(s:^S)}\nf::proc(s:^S){s.x=3}\ncall::proc(value:$T){s:>value; s->f()}\n'
        self.invoke(self.program('s:=S{f=f};call(&s);assert(s.x==3)',prefix))
        self.invoke(self.program('call(S{f=f})',prefix),fail='implicit mutable address')

    def test_imported_and_mixed_file_immutable_bindings(self):
        for source_extension in ['thor', 'odin']:
            with self.subTest(extension=source_extension):
                self.invoke(self.program('f()'), directory=True,
                    files={'state.thor':'package main\nSTATE:>1\n',
                           f'write.{source_extension}':'package main\nf::proc(){STATE=2}\n'}, fail="immutable storage 'STATE'")
        self.invoke(self.program('lib.STATE=2','import "lib"\n'),
                    files={'lib/state.thor':'package lib\nSTATE:>1\n'}, fail="immutable storage 'STATE'")

    def test_conditional_immutable_declaration_uses_selected_branch(self):
        prefix='#if #config(IMMUTABLE,false) {x:>1} #else {x:=1}\n'
        self.invoke(self.program('x=2; assert(x==2)',prefix))
        self.invoke(self.program('x=2',prefix),flags=['-define:IMMUTABLE=true'],fail="immutable storage 'x'")

    def test_inactive_immutable_violations_are_unchecked(self):
        self.invoke(self.program('#if false {x:>1; x=2; p:=&x; _=p}\nwhen false {a:>[1,2]; _=a[:]}'))

    def test_generic_shape_checks_distinguish_array_slice_and_map(self):
        prefix='write::proc(value:$T){x:>value; x[0]=7}\n'
        self.invoke(self.program('data:=[1,2]; write(data[:]); assert(data[0]==7)',prefix))
        self.invoke(self.program('data:=make(map[int]int);write(data);delete(data)',prefix),fail="immutable storage 'x'")
        self.invoke(self.program('write([1,2])',prefix),fail="immutable storage 'x'")

    def test_generic_record_members_and_substitutions(self):
        prefix='Box::struct($T:typeid){value:T}\n'
        self.invoke(self.program('x:>Box([2]int){value=[1,2]}; x.value[0]=3',prefix),fail="immutable storage 'x'")
        self.invoke(self.program('data:=[1,2]; x:>Box([]int){value=data[:]}; x.value[0]=3; assert(data[0]==3)',prefix))

    def test_generic_guard_preserves_control_header_scopes_and_do_bodies(self):
        bodies = [
            'x:>value; for i:=0;i<1;x[0]=7 {i+=1}',
            'for x:>value; x[0]<7; x[0]=7 {}',
            'x:>value; if modify(&x[0]) {}',
            'x:>value; for i:=0;i<1;i+=1 do modify(&x[0])',
        ]
        for body in bodies:
            with self.subTest(body=body):
                prefix='modify::proc(p:^int)->bool{p^=7;return false}\nf::proc(value:$T){'+body+'}\n'
                self.invoke(self.program('data:=[1,2]; f(data[:]); assert(data[0]==7)',prefix))
                self.invoke(self.program('f([1,2])',prefix),fail="immutable storage 'x'")

    def test_tuple_result_shapes_and_discard_names(self):
        prefix='pair::proc(value:[]int)->(int,[]int){return 1,value}\n'
        self.invoke(self.program('data:=[1,2]; a,b:>pair(data[:]); b[0]=3; assert(a==1&&data[0]==3); _,spare:>1,2; _=spare',prefix))

    def test_contextual_array_through_overloaded_target(self):
        prefix='''Color::enum{Red,Blue}
a::proc(values:[2]Color, hint:int)->int{return int(values[1])}
b::proc(values:[2]Color, hint:string)->int{return 99}
choose::proc{a,b}
'''
        self.invoke(self.program('assert(choose([.Red,.Blue],int(1))==1)',prefix))
        self.invoke(self.program('assert(choose(hint="x",values=[.Red,.Blue])==99)',prefix))

    def test_contextual_array_through_generic_specialization(self):
        prefix='Color::enum{Red,Blue}\nf::proc(values:$A/[2]Color)->Color{return values[1]}\n'
        self.invoke(self.program('assert(f([.Red,.Blue])==.Blue)',prefix))

    def test_conditional_generic_context_follows_native_branch(self):
        prefix='''A::enum{Red,Blue}
B::enum{Red,Blue}
#if #config(BRANCH,false) {
    f::proc(value:$T/[2]A)->int{return 1}
} #else {
    f::proc(value:$T/[2]B)->int{return 2}
}
'''
        self.invoke(self.program('assert(f([.Red,.Blue])==2)',prefix))
        self.invoke(self.program('assert(f([.Red,.Blue])==1)',prefix),flags=['-define:BRANCH=true'])

    def test_distinct_overload_and_explicit_generic_context(self):
        prefix='''Color::enum{Red,Blue}
Id::distinct int
a::proc(values:[2]Color,hint:Id)->int{return 1}
b::proc(values:[2]Color,hint:int)->int{return 2}
f::proc{a,b}
g::proc($T:typeid, values:[2]T)->T{return values[1]}
'''
        self.invoke(self.program('assert(f([.Red,.Blue],Id(1))==1);assert(f([.Red,.Blue],int(1))==2);assert(g(Color,[.Red,.Blue])==.Blue)',prefix))

    def test_generated_output_is_not_rediscovered_as_input(self):
        root,_=self.invoke(self.program('x:>1;assert(x==1)'),command='transpile')
        generated=next((root/'output').rglob('main.odin'))
        result=subprocess.run([str(THOR),'check',str(generated)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('use the original source',result.stderr)

    def test_manifest_records_native_flags_and_default_collection(self):
        root,_=self.invoke(self.program('_=f.group_integer(7)','import f "thor:format"\n'),command='check',flags=['-define:EXAMPLE=7'])
        manifest=json.loads((root/'output'/'thor-manifest.json').read_text())
        self.assertIn('-define:EXAMPLE=7',manifest['odin_flags'])
        self.assertTrue(any(f.startswith('-collection:thor=') for f in manifest['odin_flags']))

    def test_generic_element_type_from_another_argument(self):
        prefix='Color::enum{Red,Blue}\nf::proc(values:[2]$T,hint:T)->T{return values[1]}\n'
        self.invoke(self.program('assert(f([.Red,.Blue],Color.Red)==.Blue)',prefix))

    def test_context_wrappers_resolve_private_types_in_defining_package(self):
        library='''package lib
@(private="file") Color::enum{Red,Blue}
@(private="file") a::proc(values:[2]Color,hint:int)->int{return int(values[1])}
@(private="file") b::proc(values:[2]Color,hint:string)->int{return 99}
choose::proc{a,b}
generic::proc(values:$A/[2]Color)->int{return int(values[1])}
'''
        self.invoke(self.program('assert(lib.choose([.Red,.Blue],int(1))==1);assert(lib.generic([.Red,.Blue])==1)','import "lib"\n'),
                    files={'lib/lib.thor':library})

    def test_contextual_array_through_procedure_alias(self):
        self.invoke(self.program('alias::f; assert(alias([.Red,.Blue])==.Blue)',
                    'Color::enum{Red,Blue}\nf::proc(values:[2]Color)->Color{return values[1]}\n'))

    def test_generic_length_and_type_parameter_inference(self):
        prefix='f::proc(values:[$N]$T)->int{return N}\n'
        self.invoke(self.program('assert(f([1,2,3])==3)',prefix))

    def test_selective_alias_survives_shadowed_package_namespace(self):
        self.invoke(self.program('math:=7; assert(sin(f64(0))==0 && math==7)', 'import "core:math" / sin\n'))

    def test_cross_target_source_selection_uses_target_not_host(self):
        files={'selected_linux_amd64.thor':'package main\nVALUE::7\n',
               'selected_darwin_arm64.thor':'package main\nVALUE::9\n',
               'broken_windows.thor':'not valid Thor at all\n'}
        self.invoke(self.program('_ = VALUE'), files=files, directory=True, command='check', flags=['-target:linux_amd64'])
        self.invoke(self.program('_ = VALUE'), files=files, directory=True, command='check', flags=['-target:windows_amd64'],fail='package declaration')

    def test_cross_target_build_tags_exclude_untaken_parse_failures(self):
        self.invoke(self.program('assert(VALUE==7)'), directory=True, command='check', flags=['-target:linux_amd64'],
                    files={'linux.thor':'#+build linux\npackage main\nVALUE::7\n',
                           'other.thor':'#+build darwin\npackage main\nthis is invalid\n'})

    def test_build_test_mode_includes_test_tagged_sources(self):
        root, _ = self.invoke('package main\nmain::proc(){}\n', command='build', directory=True, flags=['-build-mode:test'],
                    files={'extra.thor':'#+test\npackage main\nimport "core:testing"\n@(test) added::proc(t:^testing.T){testing.expect(t,true)}\n'})
        executable=root/(root.name+('.exe' if os.name=='nt' else ''))
        result=subprocess.run([str(executable)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('1 test', result.stderr)

    def test_relative_foreign_object_is_resolved_against_original_source(self):
        compiler=shutil.which('cc')
        if not compiler:
            self.skipTest('A C compiler is required for this fixture')
        temporary=tempfile.TemporaryDirectory(prefix='thor-foreign-')
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve()
        source=root/'input';source.mkdir()
        (source/'answer.c').write_text('int thor_fixture_answer(void){return 42;}\n')
        subprocess.run([compiler,'-c',str(source/'answer.c'),'-o',str(source/'answer.o')],check=True,capture_output=True)
        (source/'main.thor').write_text('package main\nforeign import lib "answer.o"\nforeign lib {thor_fixture_answer::proc "c"()->i32 ---}\nmain::proc(){assert(thor_fixture_answer()==42)}\n')
        result=subprocess.run([str(THOR),'run',str(source),'--output-dir',str(root/'output')],capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_collection_resources_use_original_directory(self):
        with tempfile.TemporaryDirectory(prefix='thor-assets-') as directory:
            assets=Path(directory).resolve()
            (assets/'message.txt').write_text('collection resource')
            self.invoke(self.program('assert(#exists("assets:message.txt")); text::string(#load("assets:message.txt"));assert(text=="collection resource")'),
                        flags=[f'-collection:assets={assets}'])


if __name__ == '__main__':
    unittest.main()

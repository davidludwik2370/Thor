"""Builtin call results and aliases through the real TLS transport."""
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

from tests import test_tls as base


COMPILER_BUILTINS = (
    'len', 'cap', 'size_of', 'align_of', 'offset_of', 'offset_of_by_string',
    'type_of', 'type_info_of', 'typeid_of', 'swizzle', 'complex', 'quaternion',
    'real', 'imag', 'jmag', 'kmag', 'conj', 'expand_values', 'compress_values',
    'min', 'max', 'abs', 'clamp', 'soa_zip', 'soa_unzip', 'unreachable', 'raw_data',
)


class BuiltinInferenceTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    position = staticmethod(base.TLSTests.position)
    hover = base.TLSTests.hover

    def complete(self, client, path, text, needle, trigger='.'):
        result = client.request('textDocument/completion', {
            'textDocument': {'uri': path.as_uri()},
            'position': self.position(text, needle, len(needle)),
            'context': {'triggerKind': 2, 'triggerCharacter': trigger},
        })
        return {item['label']: item for item in result['items']}

    def test_compiler_builtin_results_and_aliases(self):
        cases = [
            ('len', 'values', 'int'), ('cap', 'growing', 'int'),
            ('size_of', 'Person', 'int'), ('align_of', 'Person', 'int'),
            ('offset_of', 'Person, age', 'uintptr'),
            ('offset_of_by_string', 'Person, "age"', 'uintptr'),
            ('type_info_of', 'Person', 'Type_Info'), ('typeid_of', 'Person', 'typeid'),
            ('swizzle', 'values, 1, 0', '[2]Person'),
            ('complex', 'number, number', 'complex64'),
            ('quaternion', 'real=number, imag=number, jmag=number, kmag=number', 'quaternion128'),
            ('real', 'z', 'f32'), ('imag', 'z', 'f32'),
            ('jmag', 'q', 'f32'), ('kmag', 'q', 'f32'), ('conj', 'q', 'quaternion128'),
            ('compress_values', 'person, person', '[2]Person'),
            ('min', 'number, f32(2)', 'f32'), ('max', 'number, f32(2)', 'f32'),
            ('abs', 'z', 'f32'), ('clamp', 'number, f32(0), f32(2)', 'f32'),
            ('raw_data', 'values[:]', '[^]Person'),
        ]
        library = 'package aliases\n' + ''.join(f'get_{name}::{name}\n' for name in COMPILER_BUILTINS)
        for prefix in ('', 'get_', 'aliases.get_'):
            with self.subTest(prefix=prefix):
                aliases = ''.join(f'get_{name}::({name})\n' for name in COMPILER_BUILTINS)
                text = '''// 😀 builtin caller scope
package main
import aliases "aliases"
Person::struct{name:string,age:int}
ALIASES
main::proc(){
    person:=Person{"Tim",22}
    values:=[3]Person{person,person,person}
    growing:[dynamic]Person
    number:f32=1
    z:=complex(number,number)
    q:=quaternion(real=number,imag=number,jmag=number,kmag=number)
BODY
}
'''.replace('ALIASES', aliases)
                text = text.replace('BODY', '\n'.join(
                    f'    result_{name}:={prefix}{name}({args})\n    _=result_{name}'
                    for name, args, _ in cases))
                client, path = self.open(text, files={'aliases/aliases.odin': library}, compiler=False)
                for name, _, expected in cases:
                    with self.subTest(builtin=name):
                        result = self.hover(client, path, text, f'result_{name}:=')
                        self.assertIsNotNone(result, name)
                        self.assertIn(expected, result['contents']['value'])
                        call_name = prefix.rsplit('.', 1)[-1] + name
                        call = self.hover(client, path, text, f'{call_name}(')
                        self.assertIsNotNone(call, name)
                        self.assertIn('proc', call['contents']['value'])

    def test_type_of_aliases_produce_types_and_struct_completions(self):
        for alias in ('type_of', 'get_type', 'again', 'aliases.get_type'):
            with self.subTest(alias=alias):
                text = '''package main
import aliases "aliases"
get_type::type_of
again::(get_type)
main::proc(){
    Person::struct{name:string,age:int}
    person:Person
    Derived::CALLEE(person)
    copy:Derived
    _=copy.
}
'''.replace('CALLEE', alias)
                client, path = self.open(text, files={'aliases/aliases.odin': 'package aliases\nget_type::type_of\n'}, compiler=False)
                result = self.hover(client, path, text, 'copy:Derived')
                self.assertIsNotNone(result)
                self.assertEqual(set(self.complete(client, path, text, '_=copy.')), {'name', 'age'})

    def test_shape_changing_builtins_and_multi_result_types(self):
        text = '''package main
unzip::soa_unzip
expand::expand_values
combine::compress_values
shuffle::swizzle
Person::struct{name:string,age:int}
main::proc(){
    values:=[]Person{{"Tim",22},{"Sally",5}}
    counts:=[]int{1,2}
    rows:=soa_zip(person=values,count=counts)
    people,numbers:=unzip(rows)
    person,count:=expand(combine(values[0],counts[0]))
    packed:=combine(values[0],counts[0])
    selected:=shuffle([3]Person{},2,1)
    _=people[0].
    _=person.
    _=packed.
    _=selected[0].
    _=numbers;_=count
}
'''
        client, path = self.open(text, compiler=False)
        for needle in ('_=people[0].', '_=person.', '_=selected[0].'):
            with self.subTest(needle=needle):
                self.assertEqual(set(self.complete(client, path, text, needle)), {'name', 'age'})
        self.assertEqual(set(self.complete(client, path, text, '_=packed.')), {'v0', 'v1'})
        for needle, typ in [('people,', '[]Person'), ('numbers:=', '[]int'), ('person,count', 'Person'), ('count:=', 'int')]:
            with self.subTest(binding=needle):
                result = self.hover(client, path, text, needle)
                self.assertIsNotNone(result)
                self.assertIn(typ, result['contents']['value'])

    def test_builtin_catalog_and_alias_completion(self):
        aliases = '\n'.join(f'use_{name}::{name}' for name in COMPILER_BUILTINS)
        text = 'package main\n' + aliases + '\nmain::proc(){\n    unreachable()\n    use_\n}\n'
        client, path = self.open(text, compiler=False)
        result = client.request('textDocument/completion', {
            'textDocument': {'uri': path.as_uri()},
            'position': self.position(text, '\n    use_', len('\n    use_')),
            'context': {'triggerKind': 1},
        })
        items = {item['label']: item for item in result['items']}
        for name in COMPILER_BUILTINS:
            with self.subTest(builtin=name):
                alias = 'use_' + name
                self.assertIn(alias, items)
                self.assertIn('proc', json.dumps(items[alias]))
                response = self.hover(client, path, text, alias+'::')
                self.assertIsNotNone(response)
                self.assertIn('proc', response['contents']['value'])

    def test_runtime_builtin_generic_results_and_aliases(self):
        text = '''package main
allocate::new
allocate_copy::new_clone
create::make
push::append
take::pop
try_take::pop_safe
copy_data::copy
release::delete
Person::struct{name:string,age:int}
main::proc(){
    person:=Person{"Tim",22}
    pointer,allocation_error:=allocate(Person)
    duplicate:=allocate_copy(person)
    values,creation_error:=create([]Person,2)
    growing:=create([dynamic]Person)
    added,append_error:=push(&growing,person)
    item,found:=try_take(&growing)
    other:=take(&growing)
    copied:=copy_data(values,values)
    freed:=release(values)
    _=pointer.;_=duplicate.;_=values[0].;_=item.;_=other.
    _=allocation_error;_=creation_error;_=append_error;_=added;_=found;_=copied;_=freed
}
'''
        client, path = self.open(text, compiler=False)
        for needle in ('_=pointer.', '_=duplicate.', '_=values[0].', '_=item.', '_=other.'):
            with self.subTest(needle=needle):
                self.assertEqual(set(self.complete(client, path, text, needle)), {'name', 'age'})
        for needle, typ in [('added,', 'int'), ('found:=', 'bool'), ('copied:=', 'int'), ('freed:=', 'Allocator_Error')]:
            with self.subTest(binding=needle):
                result = self.hover(client, path, text, needle)
                self.assertIsNotNone(result)
                self.assertIn(typ, result['contents']['value'])

    def test_numeric_string_enum_and_nested_call_variants(self):
        cases = [
            ('small_text', 'low("b","a")', 'string'),
            ('large_text', 'high("b","a")', 'string'),
            ('clamped_text', 'limit("b","a","z")', 'string'),
            ('character', "high('a','b')", 'rune'),
            ('small_index', 'low([Direction]int)', 'Direction'),
            ('large_index', 'high([Direction]int)', 'Direction'),
            ('literal_real', 'component(1i)', 'f64'),
            ('literal_abs', 'magnitude(1i)', 'f64'),
            ('literal_conj', 'conjugate(1i)', 'complex128'),
            ('literal_quat', 'conjugate(1j)', 'quaternion256'),
            ('complex_array', 'pack(1i,2i)', '[2]complex128'),
            ('quaternion_array', 'pack(1j,2k)', '[2]quaternion256'),
            ('call_array', 'pack(identity(1),identity(2))', '[2]int'),
        ]
        text = '''package main
low::min;high::max;limit::clamp;component::real;magnitude::abs;conjugate::conj;pack::compress_values
identity::proc(value:$T)->T{return value}
Direction::enum{north,south}
main::proc(){
BODY
}
'''.replace('BODY', '\n'.join(f'{name}:={expression}\n_={name}' for name, expression, _ in cases))
        client, path = self.open(text, compiler=False)
        for name, _, expected in cases:
            with self.subTest(binding=name):
                result = self.hover(client, path, text, name+':=')
                self.assertIsNotNone(result)
                self.assertIn(expected, result['contents']['value'])

    def test_raw_data_all_native_container_shapes(self):
        text = '''package main
raw::raw_data
Person::struct{name:string}
Key::enum{first,second}
main::proc(){
    slice:[]Person
    growing:[dynamic]Person
    bounded:[dynamic;4]Person
    array:[4]Person
    keyed:[Key]Person
    vector:#simd[4]f32
    matrix_value:matrix[2,2]f32
    wide:string16
    multi_array:[^][4]Person
    multi_bounded:[^][dynamic;4]Person
    multi_vector:[^]#simd[4]f32
    multi_matrix:[^]matrix[2,2]f32
    slice_data:=raw(slice)
    growing_data:=raw(growing)
    bounded_data:=raw(&bounded)
    array_data:=raw(&array)
    keyed_data:=raw(&keyed)
    vector_data:=raw(&vector)
    matrix_data:=raw(&matrix_value)
    wide_data:=raw(wide)
    multi_array_data:=raw(multi_array)
    multi_bounded_data:=raw(multi_bounded)
    multi_vector_data:=raw(multi_vector)
    multi_matrix_data:=raw(multi_matrix)
    _=slice_data;_=growing_data;_=bounded_data;_=array_data;_=keyed_data;_=vector_data;_=matrix_data;_=wide_data
}
'''
        client, path = self.open(text, compiler=False)
        for name, expected in [('slice', 'Person'), ('growing', 'Person'), ('bounded', 'Person'),
                               ('array', 'Person'), ('keyed', 'Person'), ('vector', 'f32'),
                               ('matrix', 'f32'), ('wide', 'u16'), ('multi_array', 'Person'),
                               ('multi_bounded', 'Person'), ('multi_vector', 'f32'), ('multi_matrix', 'f32')]:
            with self.subTest(container=name):
                result = self.hover(client, path, text, name+'_data:=')
                self.assertIsNotNone(result)
                self.assertIn('[^]'+expected, result['contents']['value'])

    def test_type_info_and_swizzle_refresh_with_unsaved_edits(self):
        text = '''package main
metadata::type_info_of
pick::swizzle
Person::struct{name:string,age:int}
Color::struct{red,green,blue:f32}
main::proc(){
    values:[3]Person
    selected:=pick(values,1,0)
    info:=metadata(Person)
    _=selected[0].
    _=info.
}
'''
        client, path = self.open(text, compiler=False)
        for version, element, fields in [(1, 'Person', {'name', 'age'}), (2, 'Color', {'red', 'green', 'blue'})]:
            changed = text.replace('values:[3]Person', f'values:[3]{element}')
            if version > 1:
                client.send('textDocument/didChange', {
                    'textDocument': {'uri': path.as_uri(), 'version': version},
                    'contentChanges': [{'text': changed}],
                })
            self.assertEqual(set(self.complete(client, path, changed, '_=selected[0].')), fields)
            self.assertTrue({'size', 'align', 'id', 'variant'} <= self.complete(client, path, changed, '_=info.').keys())

    def test_builtin_declarations_cover_the_native_catalog(self):
        for folder in ('builtin', 'tests/builtin'):
            declared = (base.ROOT / 'tls' / folder / 'builtin.odin').read_text()
            names = set(re.findall(r'(?m)^(?:@builtin\s+)?(\w+)\s*::', declared))
            self.assertTrue(set(COMPILER_BUILTINS) <= names, set(COMPILER_BUILTINS)-names)
        odin_root = subprocess.check_output([os.environ.get('THOR_ODIN', 'odin'), 'root'], text=True).strip()
        header = Path(odin_root) / 'src/checker_builtin_procs.hpp'
        if header.is_file():
            table = header.read_text().split('gb_global BuiltinProc builtin_procs', 1)[1]
            native = set(re.findall(r'\{STR_LIT\("(\w+)"\).*?BuiltinProcPkg_builtin\b', table))
            self.assertEqual(set(COMPILER_BUILTINS), native)

    def test_user_procedures_shadow_builtin_names(self):
        text = '''package main
Row::struct{custom:bool}
main::proc(){
    type_of::proc(value:int)->Row{return {true}}
    swizzle::proc(value:int)->Row{return {true}}
    soa_unzip::proc(value:int)->Row{return {true}}
    kind::type_of
    choose::swizzle
    split::soa_unzip
    a:=kind(1);b:=choose(1);c:=split(1)
    _=a.;_=b.;_=c.
}
'''
        client, path = self.open(text, compiler=False)
        for needle in ('_=a.', '_=b.', '_=c.'):
            with self.subTest(needle=needle):
                self.assertEqual(set(self.complete(client, path, text, needle)), {'custom'})

    def test_incomplete_builtin_calls_recover_without_stale_types(self):
        text = '''package main
kind::type_of;pick::swizzle;split::soa_unzip
Person::struct{name:string,age:int}
main::proc(){
    values:[3]Person
    selected:=pick(values,1,0)
    Known::kind(Person{})
    proxy:Known
    column:=split(soa_zip(person=values[:]))
    _=selected[0].;_=proxy.;_=column[0].
}
'''
        client, path = self.open(text, compiler=False)
        version = 1
        for complete, incomplete, needle in [('pick(values,1,0)', 'pick()', '_=selected[0].'),
                                             ('split(soa_zip(person=values[:]))', 'split(42)', '_=column[0].'),
                                             ('kind(Person{})', 'kind()', '_=proxy.')]:
            with self.subTest(call=complete):
                version += 1
                client.send('textDocument/didChange', {
                    'textDocument': {'uri': path.as_uri(), 'version': version},
                    'contentChanges': [{'text': text.replace(complete, incomplete)}],
                })
                self.complete(client, path, text.replace(complete, incomplete), needle)
                version += 1
                client.send('textDocument/didChange', {
                    'textDocument': {'uri': path.as_uri(), 'version': version},
                    'contentChanges': [{'text': text}],
                })
                for selector in ('_=selected[0].', '_=proxy.', '_=column[0].'):
                    self.assertEqual(set(self.complete(client, path, text, selector)), {'name', 'age'})

    def test_native_checked_and_unchecked_program_matches_tls(self):
        text = '''package main
import runtime "base:runtime"
kind::type_of;pick::swizzle;split::soa_unzip;unpack::expand_values;pack::compress_values
info::type_info_of;offset::offset_of;raw::raw_data;low::min
Person::struct{name:string,age:int}
Derived::kind(Person{})
make_person::proc()->Person{return {"Tim",22}}
main::proc(){
    person:Derived=make_person()
    people:=[3]Person{person,person,person}
    selected:=pick(people,2,0)
    if len(selected)!=2||selected[0].name!="Tim" {panic("swizzle result")}
    values:=people[:]
    ages:=[]int{22,5,12}
    rows:=soa_zip(person=values,age=ages)
    people_column,age_column:=split(rows)
    if people_column[1].age!=22||age_column[1]!=5 {panic("unzip results")}
    name,age:=unpack(pack(person.name,person.age))
    if name!="Tim"||age!=22 {panic("nested expansion")}
    details:^runtime.Type_Info=info(Person)
    if details.size!=size_of(Person)||offset(Person,age)!=offset_of_by_string(Person,"age") {panic("metadata")}
    if raw(values)[0].name!="Tim"||low("b","a")!="a" {panic("raw data or string minimum")}
}
'''
        client, path = self.open(text)
        client.diagnostic(path.as_uri(), empty=True)
        self.assertIn('[2]Person', self.hover(client, path, text, 'selected:=')['contents']['value'])
        self.assertIn('[]int', self.hover(client, path, text, 'age_column:=')['contents']['value'])
        native = path.with_suffix('.odin')
        for compiler, source in [(os.environ.get('THOR_ODIN', 'odin'), native), (str(base.THOR), path)]:
            if source == native:
                native.write_text(text)
            try:
                for flags in ([], ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']):
                    result = subprocess.run([compiler, 'run', str(source), '-file', *flags],
                        cwd=path.parent, capture_output=True, text=True, timeout=40)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            finally:
                if source == native:
                    native.unlink()


if __name__ == '__main__':
    unittest.main()

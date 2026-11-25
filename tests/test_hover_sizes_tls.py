"""Compact hover sizes use the selected ABI without invoking a compiler."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import unittest

from tests import test_tls as base

MARKER = '\n\n<!-- thor-hover-size -->\n'


class HoverSizeTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    hover = base.TLSTests.hover
    position = staticmethod(base.TLSTests.position)

    def detail(self, client, main, text, needle, offset=1):
        result = client.request('textDocument/hover', {
            'textDocument': {'uri': main.as_uri()}, 'position': self.position(text, needle, offset)})
        value = result['contents']['value']
        self.assertIn(MARKER, value, value)
        return value.split(MARKER, 1)[1].split('\n', 1)[0], value

    def test_default_compact_line_for_builtins_values_and_headers(self):
        text = '''package main
Person::struct{age:i32}
main::proc(){
 count:i32
 native:int
 pointer:^Person
 many:[^]Person
 values:[]i32
 growing:[dynamic]i32
 fixed:[dynamic;3]u8
 mapping:map[int]int
 text:string
 c_text:cstring
 bytes:[10]i32
 callback:proc()
 _=count;_=native;_=pointer;_=many;_=values;_=growing;_=fixed;_=mapping;_=text;_=c_text;_=bytes;_=callback
}
'''
        client, main = self.open(text, compiler=False)
        for needle, expected in [
            ('count:i32', '4 bytes'), ('native:int', '8 bytes · target dependent'),
            ('pointer:^', '8 bytes · pointer · target dependent'),
            ('many:[^]', '8 bytes · pointer · target dependent'),
            ('values:[]', '16 bytes · slice header · target dependent'),
            ('growing:[dynamic]', '40 bytes · dynamic array header · target dependent'),
            ('fixed:[dynamic;', '16 bytes · target dependent'),
            ('mapping:map', '32 bytes · map header · target dependent'),
            ('text:string', '16 bytes · string header · target dependent'),
            ('c_text:cstring', '8 bytes · pointer · target dependent'),
            ('bytes:[10]', '40 bytes'), ('callback:proc', '8 bytes · pointer · target dependent')]:
            with self.subTest(needle=needle):
                detail, value = self.detail(client, main, text, needle)
                self.assertEqual(detail, expected)
                self.assertNotIn('size=', value)
                self.assertNotIn('align=', value)
                self.assertNotIn('padding=', value)
        for needle, expected in [('count:i32', '4 bytes'), ('native:int', '8 bytes · target dependent')]:
            position = text.index(needle) + len(needle.split(':')[0]) + 2
            preceding = text[:position]
            result = client.request('textDocument/hover', {'textDocument': {'uri': main.as_uri()},
                'position': {'line': preceding.count('\n'), 'character': len(preceding.rsplit('\n', 1)[-1])}})
            self.assertIn(MARKER + expected, result['contents']['value'])

    def test_inferred_mutable_and_immutable_storage_and_fstrings(self):
        text='''package main
main::proc(){
 inferred:=7
 readonly:>7
 flag:=true
 word:="hello"
 values:=[1,2,3]
 output:=f"{readonly}"
 defer delete(output)
 _=inferred;_=readonly;_=flag;_=word;_=values
}
'''
        client,main=self.open(text,compiler=False)
        for needle,expected in [('inferred:=','8 bytes · target dependent'),('readonly:>','8 bytes · target dependent'),
            ('flag:=','1 byte'),('word:=','16 bytes · string header · target dependent'),
            ('values:=','24 bytes · target dependent'),('output:=','16 bytes · string header · target dependent')]:
            with self.subTest(needle=needle):self.assertEqual(self.detail(client,main,text,needle)[0],expected)
        result=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'{readonly}',2)})
        self.assertIn(MARKER+'8 bytes · target dependent',result['contents']['value'])

    def test_structs_fields_aliases_imports_generics_and_using(self):
        text = '''package main
import "model"
Local::struct{a:u8,b:i64}
Alias::Local
Box::struct($T:typeid){value:T}
Pair::struct{a:Box(u8),b:Box(i64)}
main::proc(){
 record:Alias
 imported:model.Record
 pair:Pair
 using record
 _=record.b;_=imported.value;_=pair.a;_=a
}
'''
        client, main = self.open(text, compiler=False,
            files={'model/model.thor': 'package model\nRecord::struct{value:int}\n'})
        for needle, expected in [('record:Alias', '16 bytes · target dependent'),
            ('imported:model', '8 bytes · target dependent'), ('pair:Pair', '16 bytes · target dependent'),
            ('record.b', '8 bytes'), ('_=a', '1 byte')]:
            offset = len('record.') if needle == 'record.b' else len('_=') if needle == '_=a' else 1
            with self.subTest(needle=needle):
                detail, _ = self.detail(client, main, text, needle, offset)
                self.assertEqual(detail, expected)

    def test_alias_of_header_does_not_turn_into_pointee_size(self):
        text = '''package main
Slice::[]i32
Pointer::^Huge
Callback::proc(int)->int
Huge::struct{data:[1000000000]u8}
main::proc(){s:Slice;p:Pointer;_=s;_=p}
'''
        client, main = self.open(text, compiler=False)
        self.assertEqual(self.detail(client, main, text, 's:Slice')[0], '16 bytes · slice header · target dependent')
        self.assertEqual(self.detail(client, main, text, 'p:Pointer')[0], '8 bytes · pointer · target dependent')
        self.assertEqual(self.detail(client, main, text, 'Callback::')[0], '8 bytes · pointer · target dependent')

    def test_alignment_changes_only_qualify_sizes_when_padding_changes(self):
        definitions = '''Pair::struct{a,b:f64}
Single::struct{a:f64}
Nested::struct{a:Pair,b:Pair}
Box::struct($T:typeid){value:T}
Generic::Box(Pair)
Alias::distinct Pair
Array::[3]Pair
Scalars::[3]f64
WithArray::struct{a:Scalars,b:f64}
Aligned::struct #align(16){a:f64,b:u8}
Capped::struct #max_field_align(2){tag:u8,value:f64}
Minimum::struct #min_field_align(8){tag:u8,value:f64}
Packed::struct #packed{tag:u8,value:f64}
Raw::struct #raw_union{a:f64,b:i64}
Padded::struct{tag:u8,value:f64}
Tail::struct{value:f64,tag:u8}
Offset::struct{head:[3]u32,value:f64}
Soa::#soa[3]Pair
Vector::struct{value:#simd[2]f64}
Matrix::struct{a,b:matrix[2,2]f64}
BySize::[size_of(Pair)]u8
ByAlignment::[align_of(Pair)]u8
'''
        invariant = {'Pair': 16, 'Single': 8, 'Nested': 32, 'Generic': 16, 'Alias': 16,
            'Array': 48, 'Scalars': 24, 'WithArray': 32, 'Aligned': 16, 'Capped': 10,
            'Minimum': 16, 'Packed': 9, 'Raw': 8, 'Soa': 48, 'Vector': 16,
            'Matrix': 64, 'BySize': 16}
        names = list(invariant) + ['Padded', 'Tail', 'Offset', 'ByAlignment']
        text = 'package main\n' + definitions + '\nmain::proc(){\n' + '\n'.join(
            f'v{i}:{name};_=v{i}' for i, name in enumerate(names)) + '''
 left:>Pair{1,2};right:>Pair{3,4}
 result:=left+right;_=result
}\n'''
        for arch, os_name, target, alignment in [('arm64', 'linux', 'linux_arm64', 8),
            ('amd64', 'linux', 'linux_amd64', 8), ('i386', 'linux', 'linux_i386', 4),
            ('i386', 'windows', 'windows_i386', 8)]:
            with self.subTest(target=target):
                client, main = self.open(text, compiler=False, initialization_options={
                    'profile': 'target', 'profiles': [{'name': 'target', 'arch': arch, 'os': os_name}]})
                expected = {**invariant, 'Padded': 12 if alignment == 4 else 16,
                    'Tail': 12 if alignment == 4 else 16, 'Offset': 20 if alignment == 4 else 24,
                    'ByAlignment': alignment}
                for i, name in enumerate(names):
                    suffix = '' if name in invariant else ' · target dependent'
                    self.assertEqual(self.detail(client, main, text, f'v{i}:{name}')[0], f'{expected[name]} bytes{suffix}', name)
                self.assertEqual(self.detail(client, main, text, 'result:=')[0], '16 bytes')
                # Keep Windows profile coverage on hosts without its SDK;
                # native compiler oracles use locally available target support.
                if os_name == 'windows' and os.name != 'nt':
                    continue
                native = main.parent / 'abi' / 'sizes.odin'
                native.parent.mkdir()
                native.write_text('package main\n' + definitions + '\n' + '\n'.join(
                    f'#assert(size_of({name}) == {expected[name]})' for name in names) + '\nmain::proc(){}\n')
                checked = subprocess.run([os.environ.get('THOR_ODIN', 'odin'), 'check', str(native),
                    '-file', '-target:' + target], capture_output=True, text=True, timeout=60)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_profile_controls_native_widths_and_dependent_const_counts(self):
        text = '''package main
COUNT::size_of(int)
Shape::struct{tag:u8,value:i64}
main::proc(){native:int;p:^int;s:[]int;ary:[COUNT]u8;record:Shape;_=native;_=p;_=s;_=ary;_=record}
'''
        for arch, os_name, width, shape in [('amd64', 'linux', 8, 16), ('i386', 'linux', 4, 12), ('i386', 'windows', 4, 16)]:
            with self.subTest(arch=arch, os=os_name):
                client, main = self.open(text, compiler=False, initialization_options={
                    'profile': 'target', 'profiles': [{'name': 'target', 'arch': arch, 'os': os_name}]})
                for needle, expected in [('native:int', f'{width} bytes · target dependent'),
                    ('p:^int', f'{width} bytes · pointer · target dependent'),
                    ('s:[]int', f'{2*width} bytes · slice header · target dependent'),
                    ('ary:[COUNT]', f'{width} bytes · target dependent'),
                    ('record:Shape', f'{shape} bytes · target dependent')]:
                    self.assertEqual(self.detail(client, main, text, needle)[0], expected)

    def test_disable_option_and_existing_detailed_layout_are_independent(self):
        text = 'package main\nR::struct{a:u8,b:i64}\nmain::proc(){v:R;_=v}\n'
        client, main = self.open(text, compiler=False, initialization_options={'enable_hover_size': False})
        value = json.dumps(self.hover(client, main, text, 'v:R'))
        self.assertNotIn('bytes', value)
        self.assertNotIn(MARKER, value)
        detailed, main2 = self.open(text, compiler=False, initialization_options={
            'enable_hover_size': False, 'enable_hover_layout': True})
        value = json.dumps(self.hover(detailed, main2, text, 'v:R'))
        self.assertIn('size=16, align=8', value)

    def test_unresolved_types_and_non_value_symbols_do_not_show_guessed_sizes(self):
        text = '''package main
import "core:fmt"
identity::proc(v:$T)->T{return v}
UNKNOWN::123
main::proc(){value:Missing;_=value}
'''
        client, main = self.open(text, compiler=False)
        for needle in ['identity::', 'UNKNOWN::', 'package main']:
            value = json.dumps(self.hover(client, main, text, needle))
            self.assertNotIn('bytes', value)
        symbols = client.request('textDocument/documentSymbol', {'textDocument': {'uri': main.as_uri()}})
        self.assertTrue(symbols)
        self.assertIsNone(client.process.poll())

    def test_layout_matches_native_sizeof_for_padding_simd_and_matrix(self):
        definitions = '''Empty::struct{}
Packed::struct #packed{a:u8,b:i64}
Vec::#simd[4]f32
VectorArray::[2]Vec
Matrices::struct{a:matrix[3,3]f32,b:matrix[4,4]f64,c:matrix[2,3]f32}
Row::#row_major matrix[2,3]f32
Column::#column_major matrix[2,3]f32
RowRecord::struct{tag:u8,data:Row}
ColumnRecord::struct{tag:u8,data:Column}
Box::struct($T:typeid){tag:u8,data:T}
BoxedRow::Box(Row)
BoxedColumn::Box(Column)
'''
        names = ['Empty', 'Packed', 'Vec', 'VectorArray', 'Matrices', 'RowRecord', 'ColumnRecord', 'BoxedRow', 'BoxedColumn']
        text = 'package main\n' + definitions + '\nmain::proc(){\n' + '\n'.join(f'v{i}:{name};_=v{i}' for i, name in enumerate(names)) + '\n}\n'
        client, main = self.open(text, compiler=False)
        native = main.parent / 'abi' / 'native.odin'
        native.parent.mkdir()
        native.write_text('package main\nimport "core:fmt"\n' + definitions + '\nmain::proc(){' +
            ';'.join(f'fmt.println(size_of({name}))' for name in names) + '}\n')
        result = subprocess.run([os.environ.get('THOR_ODIN', 'odin'), 'run', str(native), '-file',
            '-out:' + str(main.parent / ('native-program.exe' if os.name=='nt' else 'native-program'))], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = [int(line) for line in result.stdout.splitlines()]
        for i, size in enumerate(expected):
            with self.subTest(name=names[i]):
                actual, _ = self.detail(client, main, text, f'v{i}:{names[i]}')
                self.assertEqual(int(actual.split()[0]), size)

    def test_big_numbers_report_the_fixed_representation_not_the_heap_payload(self):
        text='package main\nimport "thor:big"\nmain::proc(){a:Integer;b:Rational;_=a;_=b}\n'
        client,main=self.open(text,compiler=False)
        native=main.parent/'abi'/'sizes.odin'
        native.parent.mkdir()
        native.write_text('package main\nimport "core:fmt"\nimport "thor:big"\nmain::proc(){fmt.println(size_of(big.Integer));fmt.println(size_of(big.Rational))}\n')
        result=subprocess.run([os.environ.get('THOR_ODIN','odin'),'run',str(native),'-file',
            '-collection:thor='+str(base.ROOT/'runtime'),'-out:'+str(main.parent/('sizes-program.exe' if os.name=='nt' else 'sizes-program'))],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        for needle,size in zip(['a:Integer','b:Rational'],map(int,result.stdout.splitlines())):
            detail,_=self.detail(client,main,text,needle)
            self.assertEqual(detail,f'{size} bytes · variable storage · target dependent')

    def test_invalid_or_enormous_types_do_not_show_guesses_or_hang(self):
        for definition in ['Bad::struct{self:Bad}','Bad::[9223372036854775807]i64',
            'Bad::#soa[][1000000000]i32','Bad::#simd[3]f32']:
            with self.subTest(definition=definition):
                text='package main\n'+definition+'\nmain::proc(){value:Bad;_=value}\n'
                client,main=self.open(text,compiler=False)
                start=time.monotonic()
                result=self.hover(client,main,text,'value:Bad')
                self.assertNotIn(MARKER,json.dumps(result))
                self.assertLess(time.monotonic()-start,3)
                self.assertIsNone(client.process.poll())
        text='package main\nmain::proc(){value:int;_=value}\n'
        client,main=self.open(text,compiler=False,initialization_options={
            'profile':'unknown','profiles':[{'name':'unknown','arch':'not-an-architecture','os':'linux'}]})
        self.assertNotIn(MARKER,json.dumps(self.hover(client,main,text,'value:int')))

    def test_unsaved_edits_refresh_sizes_and_recursive_graphs_stay_bounded(self):
        types = 'T0::struct{value:i32}\n' + '\n'.join(f'T{i}::struct{{a:T{i-1},b:T{i-1}}}' for i in range(1, 13))
        text = 'package main\n' + types + '\nmain::proc(){value:T12;_=value}\n'
        client, main = self.open(text, compiler=False)
        start = time.monotonic()
        self.assertEqual(self.detail(client, main, text, 'value:T12')[0], '16384 bytes')
        self.assertLess(time.monotonic()-start, 3)
        edited = text.replace('value:i32', 'value:i64')
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2},
            'contentChanges': [{'text': edited}]})
        self.assertEqual(self.detail(client, main, edited, 'value:T12')[0], '32768 bytes')


if __name__ == '__main__':
    unittest.main()

"""Native-selected compile-time bindings retain reference storage permissions."""
import unittest
import json
from tests import test_rust_compiler as native

class RustConditionalBindingTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program
    def test_native_type_selected_mutability_and_fixed_array_copies(self):
        for scalar in ['int','string']:
            condition='T==int'
            for swapped in [False,True]:
                first='values :> [1,2]' if swapped else 'values := [1,2]'
                second='values := [1,2]' if swapped else 'values :> [1,2]'
                writable=(scalar=='int')!=swapped
                code=self.program(f'T::{scalar};when {condition} {{{first}}} else {{{second}}};values[0]=9;fmt.println(values)', 'import "core:fmt"\n')
                for flags in [[],native.UNCHECKED]:
                    with self.subTest(type=scalar,swapped=swapped,flags=flags):
                        self.compare(code,command='run' if writable else 'build',flags=flags,expected='[9, 2]' if writable else None,
                                     fail=None if writable else 'Cannot write immutable storage')

    def test_conditional_readonly_slice_aliases_addresses_and_writers(self):
        prefix='import "core:fmt"\nwrite::proc(#mut value:[]int){value[0]=9}\n'
        for scalar in ['int','string']:
            header=f'T::{scalar};when T==int {{values:=[]int{{1,2}}}} else {{values :> []int{{1,2}}}};'
            for operation in ['values[0]=9','alias:=values;alias[0]=9','pointer:=&values[0];pointer^=9','write(values)']:
                with self.subTest(type=scalar,operation=operation):
                    code=self.program(header+operation+';fmt.println(values[0])',prefix)
                    self.compare(code,command='run' if scalar=='int' else 'check',flags=['-json-errors'],
                        expected='9' if scalar=='int' else None,fail=None if scalar=='int' else 'read-only slice')

    def test_conditional_immutable_scalar_binding_assignment_and_address(self):
        for operation in ['value=2','pointer:=&value;pointer^=2']:
            for scalar in ['int','string']:
                with self.subTest(type=scalar,operation=operation):
                    source=self.program(f'T::{scalar};when T==int {{value:=1}} else {{value :> 1}};{operation};_=value')
                    self.compare(source,command='check',fail=None if scalar=='int' else 'immutable storage')

    def test_noncurly_nested_and_generic_native_branches(self):
        prefix='''import "core:fmt"
work::proc($T:typeid)->int {
    when T==int {when size_of(T)>0 {value:=1}else{value :> 1}} else {value :> 1}
    when T==int {value=3}
    return value
}
'''
        self.compare(self.program('fmt.println(work(int),work(string))',prefix),expected='3 1')
        self.compare(self.program('T::int;when T==int do value :> 1\nfmt.println(value)','import "core:fmt"\n'),expected='1')
        self.compare(self.program('T::int;when T==int do value :> 1\nvalue=2;_=value'),command='check',fail='immutable storage')

    def test_cross_file_package_and_qualified_readonly_bindings(self):
        files={'library/first.thor':'''package library
T::int
when T==int {values:> []int{1,2}} else {values:=[]int{1,2}}
'''}
        self.compare(self.program('fmt.println(library.values[0])','import "library"\nimport "core:fmt"\n'),files=files,expected='1')
        self.compare(self.program('library.values[0]=9','import "library"\n'),files=files,command='check',fail='read-only slice')

    def test_free_value_copy_and_clone_do_not_keep_conditional_readonly_data(self):
        self.compare(self.program('''T::string
when T==int {values:=[]int{1,2}}else{values:> []int{1,2}}
copy:=slice.clone(values);defer delete(copy);copy[0]=9
if values[0]!=1||copy[0]!=9 {panic("clone")}
''','import "core:slice"\n'),flags=native.UNCHECKED)

    def test_pure_and_compile_time_selected_mutability(self):
        prefix='''import "core:fmt"
calculate::fn()->int{T::int;when T==int{value:=1}else{value:>1};value=3;return value}
VALUE::#run calculate()
'''
        for flags in [[],native.UNCHECKED]:
            self.compare(self.program('fmt.println(VALUE,calculate())',prefix),flags=flags,expected='3 3')

    def test_conditional_slice_diagnostic_keeps_original_tls_location(self):
        source=self.program('''// café 🌩
T::string
when T==int {values:=[]int{1,2}}else{values:> []int{1,2}}
values[0]=9
''')
        result=self.compare(source,command='check',flags=['-json-errors','--editor-info'],fail='read-only slice')
        packet=json.loads(result.stderr)
        error=next(error for error in packet['errors'] if 'read-only slice' in str(error.get('msgs')))
        self.assertEqual(error['pos']['file'],str(self.source))
        self.assertEqual(error['pos']['offset'],source.encode().index(b'values[0]'))
        self.assertTrue(packet['slice_access'])


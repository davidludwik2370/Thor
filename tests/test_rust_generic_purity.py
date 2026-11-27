"""Native generic contracts validate concrete instantiations without an ABI change."""
import unittest
from tests import test_rust_compiler as native
from tests import test_rust_checked_calls as checked


class RustGenericPurityTests(unittest.TestCase):
    setUpClass=classmethod(checked.RustCheckedCallTests.setUpClass.__func__)
    setUp=checked.RustCheckedCallTests.setUp
    invoke=checked.RustCheckedCallTests.invoke
    compare=checked.RustCheckedCallTests.compare
    program=checked.RustCheckedCallTests.program
    tracked=checked.RustCheckedCallTests.tracked

    def test_scalar_generics_preserve_concrete_types_and_function_pointer_signatures(self):
        self.tracked(self.program('''n:=3;f:=f64(4)
if square(n)!=9||square(f)!=16||same(true)!=true{panic("generic scalars")}
z:complex128=complex(1,2);q:quaternion256=quaternion(w=1,x=2,y=3,z=4)
if square(z)!=complex(-3,4)||square(q)!=quaternion(w=-28,x=4,y=6,z=8){panic("generic complex values")}
callback:proc(int)->int=same
if callback(n)!=n||intrinsics.type_proc_parameter_count(type_of(callback))!=1{panic("generic signature")}
''','''import "base:intrinsics"
same::fn(value:$T)->T{return value}
square::fn(value:$T)->T{return value*value}
'''))

    def test_generic_slice_results_are_independent_of_readonly_inputs(self):
        self.tracked(self.program('''values :> []int{3}
copy:=same(values);defer delete(copy);copy[0]=9
if values[0]!=3||copy[0]!=9{panic("generic slice ownership")}
''','import "thor:pure"\nsame::fn(value:$T)->T{return pure.clone(value)}\n'))

    def test_generic_records_keep_private_local_type_identity_and_nested_ownership(self):
        self.tracked(self.program('''Row::struct{values:[]int,text:string}
values:=[]int{3};text:="hello";original :> Row{values,text}
copy:=same(original);defer pure.destroy(copy)
copy.values[0]=9
if original.values[0]!=3||copy.text!="hello"{panic("generic record ownership")}
''','''import "thor:pure"
same::fn(value:$T)->T{return pure.clone(value)}
'''))

    def test_generic_array_dimensions_keep_inline_copy_mutation(self):
        self.tracked(self.program('values:=[3,4];copy:=twice(values);if copy!=[6,8]||values!=[3,4]{panic("generic inline copy")}', '''
twice::fn(value:[$N]int)->[N]int{copy:=value;for &item in copy{item*=2};return copy}
'''))

    def test_generic_builders_recursively_clone_nested_borrowed_elements(self):
        self.tracked(self.program('''values:=[]int{3};copy:=wrap(values);defer pure.destroy(copy)
copy[0][0]=9
if values[0]!=3||copy[0][0]!=9{panic("generic builder ownership")}
''','''import "thor:pure"
wrap::fn(value:$T)->[]T{out:=make([]T,1);out[0]=pure.clone(value);return out}
'''))

    def test_generic_public_values_still_reject_unsupported_instantiations(self):
        for statement in ('n:=3;_=same(&n)',
                          'value:[dynamic]int;_=same(value)',
                          'value:map[int]int;_=same(value)',
                          'value:any=3;_=same(value)',
                          'value:=cstring("hello");_=same(value)'):
            with self.subTest(statement=statement):
                self.compare(self.program(statement,'same::fn(value:$T)->T{return value}\n'),
                             command='check',fail='Unsupported pure value type')

    def test_generic_copy_mutation_distinguishes_inline_arrays_from_slice_headers(self):
        self.compare(self.program('values:=[3,4];copy:=work(values);if copy[0]!=4||values[0]!=3{panic("array copy")}',
            'work::fn(value:$T)->T{copy:=value;copy[0]+=1;return copy}\n'))

    def test_indirect_generic_instantiation_cannot_bypass_borrow_checks(self):
        self.source.write_text(self.program('''values:=[3,4]
callback:proc([]int)->[]int=work
_=work(values);_=callback(values[:])
''','import "thor:pure"\nwork::fn(value:$T)->T{copy:=value;copy[0]+=1;return pure.clone(copy)}\n'))
        if native.reference_enabled():
            reference=self.invoke('python',command='check')
        if native.reference_enabled():
            self.assertEqual(reference.returncode,1,reference.stderr)
        if native.reference_enabled():
            self.assertIn('borrowed from an input',reference.stderr)
        result=self.invoke('rust',command='check',flags=['-json-errors'])
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn('borrowed from an input',result.stderr)

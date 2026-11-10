"""Reference symbolic permissions for direct generic fn storage operations."""
import unittest
from tests import test_rust_compiler as native


class RustLocalOwnershipTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def run_profiles(self,source):
        for flags in (['-debug'],native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_generic_array_copy_is_mutable_and_original_stays_unchanged(self):
        self.run_profiles(self.program('''values:=[1,2]
if work(values)!=9||values[0]!=1{panic("generic array copy")}
''','work::fn(input:$T)->int{local:=input;local[0]=9;return local[0]}\n'))

    def test_generic_array_copy_can_be_changed_inside_a_native_when(self):
        self.run_profiles(self.program('values:=[1,2];if work(values)!=9||values[0]!=1{panic("conditional copy")}',
            'work::fn(input:$T)->int{local:=input;when T==[2]int{local[0]=9};return local[0]}\n'))

    def test_inactive_generic_borrowed_write_does_not_reject_an_array(self):
        prefix='work::fn(input:$T)->int{when T==[]int{input[0]=9};return 0}\n'
        self.run_profiles(self.program('values:=[1,2];if work(values)!=0||values[0]!=1{panic("inactive write")}',prefix))
        self.compare(self.program('values:=[1,2];_=work(values[:])',prefix),command='check',fail='borrowed from an input')

    def test_generic_slice_copy_still_shares_borrowed_backing_storage(self):
        prefix='work::fn(input:$T)->int{local:=input;local[0]=9;return local[0]}\n'
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('values:=[1,2];_=work(values[:])',prefix),
                             command='build',flags=flags,fail='borrowed from an input')

    def test_generic_record_copies_own_inline_fields_and_retain_borrowed_slices(self):
        prefix='Row::struct{values:[2]int}\nwork::fn(input:$T)->int{local:=input;local.values[-1]=9;return local.values[-1]}\n'
        self.run_profiles(self.program('row:=Row{[1,2]};if work(row)!=9||row.values[1]!=2{panic("record copy")}',prefix))
        prefix=prefix.replace('values:[2]int','values:[]int')
        self.compare(self.program('values:=[1,2];_=work(Row{values[:]})',prefix),
                     command='check',fail='borrowed from an input')

    def test_nested_generic_arrays_copy_inline_elements_and_share_slice_elements(self):
        prefix='work::fn(input:$T)->int{local:=input;local[0][-1]=9;return local[0][-1]}\n'
        self.run_profiles(self.program('values:=[2][2]int{{1,2},{3,4}};if work(values)!=9||values[0][1]!=2{panic("nested copy")}',prefix))
        self.compare(self.program('values:=[2][]int{{1,2},{3,4}};_=work(values)',prefix),
                     command='check',fail='borrowed from an input')

    def test_control_headers_and_do_bodies_keep_permission_type_queries_in_scope(self):
        for body in ('local:=input;for i:=0;i<1;local[0]=9{i+=1};return local[0]',
                     'for local:=input;local[0]<9;local[0]=9{};return 9',
                     'local:=input;for i:=0;i<1;i+=1 do local[0]=9;return local[0]'):
            with self.subTest(body=body):
                prefix='work::fn(input:$T)->int{'+body+'}\n'
                self.compare(self.program('values:=[1,2];if work(values)!=9{panic("control scope")}',prefix))
                self.compare(self.program('values:=[1,2];_=work(values[:])',prefix),command='check',fail='borrowed from an input')

    def test_nested_generic_fn_is_guarded_in_checked_and_ordinary_outer_bodies(self):
        self.run_profiles(self.program('values:=[1,2];if outer(values)!=9||work(values)!=9||values[0]!=1{panic("nested fn")}', '''
outer::proc(values:[2]int)->int{
 inner::fn(input:$T)->int{local:=input;local[0]=9;return local[0]}
 return inner(values)
}
work::fn(values:[2]int)->int{return outer(values)}
'''))


if __name__=='__main__':unittest.main()

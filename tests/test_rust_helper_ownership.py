"""Reference helper permission profiles, stored aliases and native laziness."""
import json
import unittest
from tests import test_rust_compiler as native


class RustHelperOwnershipTests(unittest.TestCase):
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

    def test_transitive_scalar_setters_can_modify_only_local_storage(self):
        self.run_profiles(self.program('if work(3)!=9{panic("local setter")}', '''
set::proc(value:^int){value^=9}
forward::proc(value:^int){set(value)}
work::fn(n:int)->int{x:=n;forward(&x);return x}
'''))

    def test_empty_parameter_lists_receive_lazy_ownership_metadata(self):
        self.run_profiles(self.program('if work(3)!=10{panic("empty helper parameters")}', '''
value::proc()->int{return 7}
work::fn(n:int)->int{return value()+n}
'''))

    def test_generic_helper_can_mutate_an_inline_copy(self):
        self.run_profiles(self.program('values:=[1,2];if work(values)!=9||values[0]!=1{panic("helper copy")}', '''
copy::proc(input:$T)->int{local:=input;local[0]=9;return local[0]}
work::fn(input:$T)->int{return copy(input)}
'''))

    def test_generic_helper_cannot_mutate_a_shared_slice_copy(self):
        source=self.program('values:=[1,2];_=work(values[:])','''
copy::proc(input:$T)->int{local:=input;local[0]=9;return local[0]}
work::fn(input:$T)->int{return copy(input)}
''')
        self.compare(source,command='check',fail='borrowed from an input')

    def test_owned_buffer_mutation_keeps_the_callers_allocation_lifetime(self):
        self.run_profiles(self.program('if work(2)!=9{panic("owned helper")}', '''
set::proc(values:[]int){values[0]=9}
work::fn(n:int)->int{values:=make([]int,n);set(values);return values[0]}
'''))

    def test_out_parameter_stores_preserve_borrowed_backing_storage(self):
        self.compare(self.program('values:=[1,2];_=work(values[:])','''
Box::struct{values:[]int}
link::proc(out:^Box,values:[]int){out.values=values}
work::fn(input:[]int)->int{box:Box;link(&box,input);box.values[0]=9;return 0}
'''),command='check',fail='borrowed from an input')

    def test_out_parameter_stores_can_install_a_fresh_owned_buffer(self):
        self.run_profiles(self.program('if work(2)!=9{panic("out allocation")}', '''
Box::struct{values:[]int}
create::proc(out:^Box,n:int){out.values=make([]int,n)}
work::fn(n:int)->int{box:Box;create(&box,n);box.values[0]=9;return box.values[0]}
'''))

    def test_append_keeps_reference_elements_borrowed(self):
        self.compare(self.program('values:=[1,2];_=work(values[:])','''
push::proc(out:^[dynamic;2][]int,values:[]int){append(out,values)}
work::fn(values:[]int)->int{out:[dynamic;2][]int;push(&out,values);out[0][0]=9;return 0}
'''),command='check',fail='borrowed from an input')

    def test_original_helper_signatures_still_support_procedure_values(self):
        self.run_profiles(self.program('''
f:proc(^int)=set
value:=1;f(&value)
if value!=9||work(3)!=9{panic("original signature")}
''','''
set::proc(value:^int){value^=9}
work::fn(n:int)->int{x:=n;set(&x);return x}
'''))

    def test_inactive_helper_borrowed_write_is_checked_only_when_called(self):
        prefix='''
set::proc(values:[]int){values[0]=9}
work::fn(input:$T)->int{when T==[]int{set(input)};return 0}
'''
        self.run_profiles(self.program('values:=[1,2];if work(values)!=0{panic("inactive helper")}',prefix))
        self.compare(self.program('values:=[1,2];_=work(values[:])',prefix),command='check',fail='borrowed from an input')

    def test_reference_iteration_passes_local_elements_to_a_helper(self):
        self.run_profiles(self.program('if work(2)!=18{panic("local iteration")}', '''
set::proc(value:^int){value^=9}
work::fn(n:int)->int{values:=[1,2];for &value in values{set(&value)};return values[0]+values[1]}
'''))

    def test_value_iteration_does_not_own_reference_containing_elements(self):
        self.compare(self.program('values:=[2][]int{{1},{2}};_=work(values)','''
set::proc(values:[]int){values[0]=9}
work::fn(input:[2][]int)->int{for row in input{set(row)};return 0}
'''),command='check',fail='borrowed from an input')

    def test_type_switch_slice_alias_keeps_its_input_permissions(self):
        self.compare(self.program('values:=[1,2];_=work(Input(values[:]))','''
Input::union{[2]int,[]int}
set::proc(values:[]int){values[0]=9}
work::fn(input:Input)->int{switch value in input{case []int:set(value)};return 0}
'''),command='check',fail='borrowed from an input')

    def test_type_queries_leave_local_values_unchanged(self):
        self.run_profiles(self.program('if work(2)!=2{panic("type query")}', '''
set::proc(value:^int)->int{value^=9;return value^}
work::fn(n:int)->int{x:=n;_=type_of(set(&x));return x}
'''))

    def test_reviewed_slice_clone_owns_its_outer_buffer(self):
        for imports,callee in [('import "core:slice"','slice.clone'),
                               ('import "core:slice"\nDUPLICATE::slice.clone','DUPLICATE'),
                               ('import "core:slice" / clone','clone')]:
            with self.subTest(callee=callee):
                self.run_profiles(self.program('values:=[1,2];if work(values[:])!=9||values[0]!=1{panic("clone buffer")}',
                    imports+'\nwork::fn(input:[]int)->int{local:='+callee+'(input);local[0]=9;return local[0]}\n'))

    def test_reviewed_slice_clone_keeps_nested_reference_elements_borrowed(self):
        self.compare(self.program('values:=[2][]int{{1},{2}};_=work(values[:])','''
import "core:slice"
work::fn(input:[][]int)->int{local:=slice.clone(input);local[0][0]=9;return 0}
'''),command='check',fail='borrowed from an input')

    def test_reviewed_string_clone_can_be_deleted_locally(self):
        self.run_profiles(self.program('input:="text";if work(input)!=4{panic("string clone")}', '''
import "core:strings"
work::fn(input:string)->int{local:=strings.clone(input);n:=len(local);delete(local);return n}
'''))

    def test_constant_string_clone_records_a_reference_evaluator_false_positive(self):
        # Reference exception: automatic evaluation adds a generated argument
        # to the approved strings.clone call, whose native signature lacks it.
        self.source.write_text(self.program('if work("text")!=4{panic("constant clone")}', '''
import "core:strings"
work::fn(input:string)->int{local:=strings.clone(input);n:=len(local);delete(local);return n}
'''))
        reference=self.invoke('python',flags=native.UNCHECKED)
        self.assertEqual(reference.returncode,1,reference.stderr)
        self.assertIn('No parameter named',reference.stderr)
        result=self.invoke('rust',flags=native.UNCHECKED+['--track-allocations=basic'])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'')
        self.assertEqual(result.stderr,'')

    def test_a_local_clone_name_does_not_gain_a_reviewed_library_contract(self):
        self.compare(self.program('_=work("text")','''
state:int
clone::proc(input:string)->string{_=state;return input}
work::fn(input:string)->int{return len(clone(input))}
'''),command='check',fail='outside the function')

    def test_reviewed_clone_rejects_a_selected_allocator(self):
        self.compare(self.program('values:=[1,2];_=work(values[:])','''
import "core:slice"
work::fn(input:[]int)->int{_=slice.clone(input,allocator=context.allocator);return 0}
'''),command='check',fail='select an allocator')

    def test_generic_borrow_failure_reports_the_call_that_requires_purity(self):
        text=self.program('values:=[1,2];_=work(values[:])','''
copy::proc(input:$T)->int{local:=input;local[0]=9;return local[0]}
work::fn(input:$T)->int{return copy(input)}
''')
        self.source.write_text(text)
        result=self.invoke('rust',command='check',flags=['-json-errors'])
        self.assertEqual(result.returncode,1,result.stderr)
        errors=json.loads(result.stderr)['errors']
        primary=next(error for error in errors if 'borrowed from an input' in str(error))
        self.assertEqual(primary['pos']['file'],str(self.source))
        self.assertEqual(primary['pos']['offset'],text.index('copy(input)',text.index('work::')))
        self.assertIn("Pure function 'work'",primary['msgs'][0])
        self.assertTrue(primary.get('related'),primary)

    def test_imported_checked_bodies_receive_the_callers_permission_profile(self):
        source=self.program('values:=[1,2];if work(values)!=9||values[0]!=1{panic("imported copy")}',
                            'import "library"\nwork::fn(input:$T)->int{return library.copy(input)}\n')
        for extension in ('thor','odin'):
            directory='library_'+extension
            text=source.replace('import "library"','import library "'+directory+'"')
            files={directory+'/copy.'+extension:'package library\ncopy::proc(input:$T)->int{local:=input;local[0]=9;return local[0]}\n'}
            with self.subTest(extension=extension):
                self.compare(text,files=files,flags=native.UNCHECKED)
                self.compare(text.replace('work(values)!=9','work(values[:])!=9'),files=files,
                             command='check',fail='borrowed from an input')

    def test_sdk_reverse_checks_its_transitive_helper_permissions(self):
        self.run_profiles(self.program('n:=3;if work(n)!=4{panic("SDK reverse")}', '''
import "core:slice"
work::fn(n:int)->int{values:=[n,n+1];slice.reverse(values[:]);return values[0]}
'''))
        self.compare(self.program('values:=[1,2];_=work(values[:])','''
import "core:slice"
work::fn(input:[]int)->int{slice.reverse(input);return input[0]}
'''),command='check',fail='borrowed from an input')

    def test_shared_helper_diagnostic_selects_the_actual_failing_fn(self):
        for prefix in ('','text:="😀";_=text;'):
            with self.subTest(prefix=prefix):
                self.check_shared_helper_diagnostic(prefix)

    def check_shared_helper_diagnostic(self,prefix):
        text=self.program('values:=[1,2];_=good(values);_=bad(values[:])','''
helper::proc(input:$T)->int{local:=input;local[0]=9;return local[0]}
good::fn(input:[2]int)->int{return helper(input)}
bad::fn(input:[]int)->int{'''+prefix+'''return helper(input)}
''')
        self.source.write_text(text)
        result=self.invoke('rust',command='check',flags=['-debug','-json-errors'])
        self.assertEqual(result.returncode,1,result.stderr)
        errors=json.loads(result.stderr)['errors']
        failures=[error for error in errors if 'borrowed from an input' in str(error)]
        self.assertTrue(failures,errors)
        for error in failures:
            offset=text.index('helper(input)',text.index('bad::'))
            self.assertEqual(error['pos']['offset'],len(text[:offset].encode()))
            column=len(text[:offset].rsplit('\n',1)[-1].encode('utf-16-le'))//2+1
            self.assertEqual(error['pos']['column'],column)
            self.assertIn("Pure function 'bad'",error['msgs'][0])
            self.assertNotIn("fn 'good'",json.dumps(error))
            self.assertNotIn('good -> helper',json.dumps(error))


if __name__=='__main__':unittest.main()

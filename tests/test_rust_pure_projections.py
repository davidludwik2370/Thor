"""Pure using fields follow their owner's schema, effects and result lifetime."""
import unittest
from tests import test_rust_compiler as native


class RustPureProjectionTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare
    program = native.RustCompilerTests.program

    def checked_and_unchecked(self, code):
        for flags in (['-debug'], native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(code,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_using_parameters_read_borrowed_slices_in_lowered_expressions(self):
        self.checked_and_unchecked(self.program('''
source:=[3,4];row:>Row{source[:]}
if read(row)!=13||generic(row)!=4{panic("pure using reads")}
''','''Row::struct{values:[]int}
read::fn(using row:Row)->int{return values[-1]+values[0]**2}
generic::fn(using row:$T/Row)->int{return values[-1]}
'''))

    def test_using_local_copies_keep_scalar_mutations_local(self):
        self.checked_and_unchecked(self.program('''
original:=Row{3};n:=7
result:=changed(original,n)
if original.value!=3||result.value!=7{panic("pure using local copy")}
''','''Row::struct{value:int}
changed::fn(input:Row,n:int)->Row{using local:=input;value=n;return local}
'''))

    def test_local_allocations_can_be_mutated_through_promoted_views(self):
        self.checked_and_unchecked(self.program('''
n:=3;values:=build(n);defer delete(values)
if len(values)!=3||values[-1]!=9{panic("promoted owned result")}
''','''Row::struct{values:[]int}
fill::proc(using row:Row,n:int){values[-1]=n*3}
build::fn(n:int)->[]int{
 using row:=Row{make([]int,n)}
 fill(row,n)
 return values
}
'''))

    def test_promoted_borrowed_writes_are_rejected_at_the_pure_call(self):
        for declaration in ('change::proc(using row:Row){values[0]=9}',
                            'change::proc(row:Row){using row;values[0]=9}'):
            with self.subTest(declaration=declaration):
                self.compare(self.program('data:=[1];_=work(Row{data[:]})',
                    'Row::struct{values:[]int}\n'+declaration+'\n'
                    'work::fn(row:Row)->int{change(row);return row.values[0]}\n'),
                    command='check',fail='borrowed from an input')

    def test_pointer_helpers_can_replace_locally_owned_promoted_headers(self):
        self.checked_and_unchecked(self.program('''
n:=3;values:=build(n);defer delete(values)
if len(values)!=3||values[-1]!=9{panic("promoted header effect")}
''','''Row::struct{values:[]int}
initialize::proc(using row:^Row,n:int){values=make([]int,n);values[-1]=9}
build::fn(n:int)->[]int{row:Row;initialize(&row,n);return row.values}
'''))

    def test_assigning_a_borrowed_view_to_a_promoted_header_does_not_own_it(self):
        self.compare(self.program('data:=[1];_=work(data[:])','''
Row::struct{values:[]int}
work::fn(input:[]int)->int{using row:=Row{};values=input;values[0]=9;return values[0]}
'''),command='check',fail='borrowed from an input')

    def test_returned_promoted_views_require_an_explicit_clone(self):
        program = self.program('''
source:=[3,4];row:>Row{source[:]}
values:=copy(row);defer delete(values);values[0]=9
if source[0]!=3||values[0]!=9{panic("promoted result ownership")}
''','''import "thor:pure"
Row::struct{values:[]int}
view::proc(using row:Row)->[]int{return values}
copy::fn(row:Row)->[]int{return pure.clone(view(row))}
''')
        self.checked_and_unchecked(program)
        self.compare(program.replace('pure.clone(view(row))', 'view(row)'),
                     command='check', fail='cannot return borrowed storage')

    def test_promoted_global_read_is_still_external_runtime_storage(self):
        self.compare(self.program('n:=3;_=work(n)','''
Row::struct{value:int}
STATE:>Row{4}
work::fn(n:int)->int{using STATE;return value+n}
'''),command='check',fail='outside the function')

    def test_unconstrained_using_parameters_remain_rejected(self):
        self.compare(self.program('n:=3;_=work(Row{n})','''
Row::struct{value:int}
work::fn(using row:$T)->int{return value}
'''),command='check',fail='resolved record schema')


if __name__=='__main__':unittest.main()

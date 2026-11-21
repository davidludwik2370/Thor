"""Variadic parameters retain their slice element types throughout analysis."""
import unittest
from tests import test_rust_compiler as base


class NoAllocVariadicTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    compare=base.RustCompilerTests.compare
    invoke=base.RustCompilerTests.invoke

    def test_boolean_reductions_and_sandbox_operations_allocate_nothing(self):
        code='''package main
import "core:fmt"
import "core:mem"
import "thor:basic" / and, or
Foo::struct{a,b:f64}
@(no_alloc)
calculate::proc()->int{
 values:>[true,false,true]
 fmt.println(and(..values[:]),or(..values[:]),and(),or())
 a,b,c:>true,false,true
 fmt.println(and(a,b,c),or(a,b,c))
 names:>[]string{"Tim","Sally","Tom"}
 ages:>[]int{22,5,12,7}
 people:>soa_zip(name=names,age=ages)
 count:=0
 for person in people {using person;count+=age}
 x:=Foo{2,3}+Foo{4,5}
 x+={6,7}
 y:=x**{4,4}
 fmt.println(y)
 return count
}
main::proc(){
 backing:=context.allocator;temp:=context.temp_allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 context.allocator=mem.tracking_allocator(&tracker);context.temp_allocator=context.allocator
 value:=calculate();count:=tracker.total_allocation_count
 context.allocator=backing;context.temp_allocator=temp
 mem.tracking_allocator_destroy(&tracker)
 if value!=39 || count!=0 {panic("variadic allocation regression")}
}
'''
        for flags in [[],base.UNCHECKED,['-debug']]:
            with self.subTest(flags=flags):
                self.compare(code,flags=['-thread-count:1',*flags],expected='false true true false\nfalse true\nFoo{a = 20736, b = 50625}')

    def test_typed_variadic_iteration_and_forwarding(self):
        self.compare('''package main
import "core:fmt"
Number::int
Pair::struct{a,b:int}
sum::proc(values:..Number)->(result:Number){for value,i in values {result+=value+Number(i)};return}
forward::proc(values:..Number)->Number{return sum(..values)}
pairs::proc(values:..Pair)->(result:int){for value in values {result+=value.a+value.b};return}
@(no_alloc)
work::proc(){
 values:=[3]Number{2,3,4}
 fmt.println(sum(),sum(2,3,4),sum(..values[:]),forward(..values[:]),pairs(Pair{1,2},Pair{3,4}))
}
main::proc(){work()}
''',expected='0 12 12 12 10',flags=['-thread-count:1'])

    def test_generic_variadic_value_types_survive_nested_forwarding(self):
        self.compare('''package main
import "core:fmt"
sum::proc(values:..$T)->(result:T){for value in values {result+=value};return}
forward::proc(values:..$T)->T{return sum(..values)}
@(no_alloc)
work::proc(){
 a:=[2]i16{2,3};b:=[2]f64{1.5,2.5}
 fmt.println(sum(..a[:]),forward(..a[:]),forward(..b[:]))
}
main::proc(){work()}
''',expected='5 5 4',flags=['-thread-count:1'])

    def test_variadic_slice_indexing_and_mutation_remain_native(self):
        self.compare('''package main
import "core:fmt"
change::proc(values:..int){for &value in values {value+=2}}
first::proc(values:..int)->int{return values[0]}
@(no_alloc)
work::proc(){
 values:=[2,3,4];change(..values[:])
 fmt.println(first(..values[:]),values)
}
main::proc(){work()}
''',expected='4 [4, 5, 6]',flags=['-thread-count:1'])

    def test_aliases_named_packs_and_overloads_keep_element_types(self):
        self.compare('''package main
import "core:fmt"
import "thor:basic" / and
booleans::proc(values:..bool)->bool{return and(..values)}
integers::proc(values:..int)->bool{_=make([]int,1);return false}
reduce::proc{booleans,integers}
alias::booleans
@(no_alloc)
work::proc(){
 values:>[]bool{true,false}
 callback:proc(#ro values:..bool)->bool=booleans
 fmt.println(reduce(..values),reduce(true,false),booleans(values=values),alias(values=values),callback(..values))
}
main::proc(){work()}
''',expected='false false false false false',flags=['-thread-count:1'])

    def test_generic_slice_array_and_map_iteration_keep_specializations(self):
        self.compare('''package main
import "core:fmt"
slice_sum::proc(values:[]$T)->(total:T){for value in values {total+=value};return}
array_sum::proc(values:[$N]$T)->(total:T){for value in values {total+=value};return}
map_sum::proc(values:map[$K]$V)->(total:int){for key,value in values {total+=int(key)+int(value)};return}
@(no_alloc)
work::proc(table:map[int]i16){
 values:=[2]i16{2,3}
 fmt.println(slice_sum(values[:]),array_sum(values),map_sum(table))
}
main::proc(){table:=make(map[int]i16);defer delete(table);table[2]=3;work(table)}
''',expected='5 5 5',flags=['-thread-count:1'])

    def test_owned_elements_and_real_allocations_are_still_rejected(self):
        for decl,body,reason in [
            ('reduce::proc(values:..bool)->bool{for value in values {_=make([]int,1);if value{return true}};return false}',
             '_=reduce(true)',"builtin 'make'"),
            ('import * "thor:basic"\ninspect::proc(values:..Integer){for value in values {_=value}}',
             'inspect()', 'Integer/Rational'),
            ('import * "thor:basic"\ninspect::proc(values:..$T){for value in values {_=value}}\nouter::proc(values:[]Integer){inspect(..values)}',
             'outer(nil)','Integer/Rational'),
            ('safe::proc(values:..bool){}\nunsafe::proc(values:..int){_=make([]int,1)}\nreduce::proc{safe,unsafe}',
             'values:=[1,2];reduce(..values[:])',"builtin 'make'"),
        ]:
            with self.subTest(decl=decl):
                self.compare('package main\n'+decl+'\n@(no_alloc)\nwork::proc(){'+body+'}\nmain::proc(){work()}',command='check',flags=['-thread-count:1'],fail=reason)


if __name__=='__main__':unittest.main()

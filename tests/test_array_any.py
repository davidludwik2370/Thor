"""An any context boxes the whole inferred array, not each literal element."""
import unittest
from tests import test_thor as base


class ArrayAnyTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_calls_callbacks_generics_nested_arrays_and_single_evaluation(self):
        source=self.program('''
accept([next(),next(),next()])
if calls!=3 {panic("array evaluated more than once")}
accept_nested([[1,2],[3,4]])
accept_named(box=[1,2,3])
accept_generic([1,2,3])
callback:proc(value:any)=accept
callback([1,2,3])
boxed:any=[1,2,3]
if boxed.id!=[3]int {panic("whole array boxing")}
''','''calls:int
next::proc()->int{calls+=1;return calls}
accept::proc(value:any){
 if value.id!=[3]int {panic("array element type")}
 values:=value.([3]int)
 if values[0]!=1||values[2]!=3 {panic("array contents")}
}
accept_named::proc(box:any){accept(box)}
accept_generic::proc(value:$T){accept(value)}
accept_nested::proc(value:any){
 if value.id!=[2][2]int {panic("nested element types")}
 values:=value.([2][2]int)
 if values[1][0]!=3 {panic("nested contents")}
}
''')
        for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'],['-debug']]:
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_explicit_array_of_any_keeps_heterogeneous_element_context(self):
        self.invoke(self.program('''
values:[2]any=[1,"two"]
if values[0].(int)!=1||values[1].(string)!="two" {panic("explicit any elements")}
accept([1,"two"])
''','''accept::proc(values:[2]any){
 if values[0].(int)!=1||values[1].(string)!="two" {panic("parameter any elements")}
}
'''))

    def test_any_cannot_supply_missing_or_heterogeneous_element_type(self):
        for literal in ['[]','[1,"two"]']:
            with self.subTest(literal=literal):
                self.invoke(self.program('accept('+literal+')','accept::proc(value:any){}\n'),command='check',fail='Error:')


if __name__=='__main__':unittest.main()

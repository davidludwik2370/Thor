"""Odin-superset regressions reduced from the user's real project corpus."""
import json
from pathlib import Path
import subprocess
import unittest

from tests import test_thor as base


AUDIT = base.ROOT / 'docs' / 'compatibility-audit-2026-10-29'


class OdinCompatibilityTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_corpus_reproductions_build_from_identical_odin_and_thor_sources(self):
        for case in json.loads((AUDIT / 'repros.json').read_text()):
            with self.subTest(case=case['name']):
                text = (AUDIT / 'repros' / case['file']).read_text()
                root, _ = self.invoke(text, command='build')
                native = root / 'native.odin'
                native.write_text(text)
                result = subprocess.run(['odin', 'build', str(native), '-file', '-out:'+str(root/'native')],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                if case['name'] == 'imported_enum_index':
                    self.invoke(text.replace('[.TEXT]','[(.TEXT)]'),command='check')

    def test_ranges_enum_type_operands_aliases_and_generic_type_parameters(self):
        self.invoke(self.program('''
total := 0
for start in 1..<4 { for n in (start..=4) { total += n } }
assert(total == 26)
count := 0
for color in (Alias) { count += int(color)+1 }
assert(count == 3 && count_types(Color) == 2)
verify_length([2]int{})
#assert(len(Color) == 2 && len([Color]int) == 2)
TEXT::#switch 2{case 1:"one";case:"two"}
#assert(len(TEXT)==3)
assert(len(TEXT)==3)
''', '''Color::enum{Red,Blue}
Alias::Color
count_types::proc($T:typeid)->int{count:=0;for value in T{count+=1};return count}
verify_length::proc(values:$T){#assert(len(values)==2)}
'''))

    def test_native_sdk_enum_type_operands_and_sparse_enum_lengths(self):
        self.invoke(self.program('''
count:=0
for value in runtime.Allocator_Mode{count+=1}
assert(count==len(runtime.Allocator_Mode))
Alias::runtime.Allocator_Mode
alias_count:=0
for value in Alias{alias_count+=1}
#assert(len(Alias)==len(runtime.Allocator_Mode))
assert(alias_count==count)
#assert(len(Sparse)==2 && len(#sparse[Sparse]int)==5)
''', 'import "base:runtime"\nSparse::enum{First=0,Last=4}\n'))

    def test_named_results_are_writable_and_preserve_inline_storage(self):
        self.invoke(self.program('''
board:=make_board(-1)
assert(board[0]==3 && board[1]==7)
result:=make_fixed(-1)
assert(len(result)==2 && result[0]==10 && result[1]==21)
''', '''
make_board::proc(i:int)->(board:[2]int){
 board[i]=7;pointer:=&board[0];pointer^=3;return
}
make_fixed::proc(i:int)->(values:[dynamic;4]int){
 append(&values,10,20);values[i]+=1;return
}
'''))

    def test_case_helpers_preserve_lazy_evaluation_and_side_effect_order(self):
        self.invoke(self.program('''
values:=[3,7];i:=-1;calls:=0;result:=0
switch {
case false && values[next(&calls)]==0: assert(false)
case values[i]==7: result=1
case: assert(false)
}
switch 7 {
case values[next(&calls)]: result+=2
case: assert(false)
}
assert(result==3 && calls==1)
''', 'next::proc(calls:^int)->int{calls^+=1;return -1}\n'))

    def test_deferred_loop_helpers_read_at_exit_and_keep_nested_scope(self):
        self.invoke(self.program('''
result:=0
{
 values:=[1,2]
 defer for i in 0..<2 { result += values[i] }
 values[0]=10
}
assert(result==12)
{
 values:=[3,4]
 defer { for i in 0..<2 { switch {case values[i]>0:result+=values[i]} } }
}
assert(result==19)
'''))

    def test_using_readonly_records_and_existing_pointer_members(self):
        self.invoke('#+feature using-stmt\n'+self.program('''
record:=Record{values={1,2}}
assert(read(record,-1)==2 && generic_read(record,-1)==2)
write(&record,-1)
assert(record.values[1]==9)
''', '''
Record::struct{values:[2]int}
read::proc(using record:Record,i:int)->int{return values[i]}
generic_read::proc(using record:$T/Record,i:int)->int{return values[i]}
write::proc(using record:^Record,i:int){values[i]=9}
'''))
        self.invoke('#+feature using-stmt\n'+self.program('write({},0)', '''
Record::struct{values:[2]int}
write::proc(using record:Record,i:int){values[i]=9}
'''),command='check',fail='temporary array')
        self.invoke(self.program('values:[dynamic;4]int;write(values,0)',
            'write::proc(values:[dynamic;4]int,i:int){values[i]=9}\n'),
            command='check',fail='temporary array')

    def test_value_parameter_shadowing_uses_outer_binding_in_initializer(self):
        self.invoke(self.program('assert(change(Board{values={1,2}},-1)==12)', '''
Board::struct{values:[2]int}
change::proc(board:Board,i:int)->int{
 before:=board.values[i]
 board:=board
 board.values[i]+=10
 return before+board.values[i]-2
}
'''))

    def test_value_and_reference_type_switches_keep_native_storage_semantics(self):
        self.invoke(self.program('''
board:Board
board[1][1]=[3]bool{true,false,true}
assert(read(&board,-1,-1,-1))
write(&board,-1,-1,-1)
assert(!read(&board,-1,-1,-1))
''', '''
Flags::[3]bool
Cell::union{int,Flags}
Board::[2][2]Cell
read::proc(board:^Board,y,x,n:int)->bool{
 switch v in board^[y][x]{case Flags:return v[n];case int:return false}
 return false
}
write::proc(board:^Board,y,x,n:int){
 switch &v in board^[y][x]{case Flags:v[n]=false;case int:return}
}
'''))

    def test_fixed_dynamic_views_addresses_and_generic_access_alias_original(self):
        self.invoke(self.program('''
values:[dynamic;4]int
append(&values,1,2,3)
index:=-1
view:=values[index:]
pointer:=&values[index]
pointer^=9
assert(view[0]==9 && values[2]==9)
generic_write(&values,index)
assert(view[0]==10 && generic_read(values,index)==10)
''', '''
generic_write::proc(values:^$T,i:int){values^[i]+=1}
generic_read::proc(values:$T,i:int)->int{return values[i]}
'''))

    def test_native_iterators_and_generic_sets_keep_their_iteration_protocol(self):
        self.invoke(self.program('''
state:=0;total:=0
for value in (next(&state,int)){total+=value}
assert(total==3 && state==3)
set_value:=set[int]{2,3};defer delete(set_value)
assert(sum_items(set_value)==5 && sum_items([2]int{2,3})==5)
''', '''
next::proc(state:^int,$T:typeid)->(value:T,ok:bool){state^+=1;return T(state^),state^<3}
sum_items::proc(items:$T)->int{total:=0;for item in items{total+=item};return total}
'''))

    def test_zipped_soa_iteration_mutation_labels_and_generic_callers(self):
        self.invoke(self.program('''
x:=[1,2];y:=[3,4]
zipped:=soa_zip(x=x[:],y=y[:])
total:=0
outer:for &value,i in zipped{
 total+=value.x+value.y
 value.x+=10
 if i==0{continue outer}
 break outer
}
assert(total==10 && x==[2]int{11,12})
assert(sum_zip(zipped)==30)
''', 'sum_zip::proc(values:$T)->int{total:=0;for v in values{total+=v.x+v.y};return total}\n'))


if __name__ == '__main__':
    unittest.main()

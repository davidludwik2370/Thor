"""Inline generic callback defaults, exercised through the real Thor/Odin pipeline."""
import unittest

from tests import test_thor as base


class DefaultCallbackTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_sorted_defaults_multiple_types_and_custom_comparators(self):
        self.invoke(self.program('''
data := []int{3, 1, 2}
ascending := sorted(data)
defer delete(ascending)
assert(ascending[0] == 1 && ascending[1] == 2 && ascending[2] == 3)
assert(data[0] == 3 && data[1] == 1 && data[2] == 2)
descending := sorted(data, proc(i, j: int) -> bool { return i > j })
defer delete(descending)
assert(descending[0] == 3 && descending[2] == 1)
floats := sorted([]f64{1.5, -2, 4})
defer delete(floats)
assert(floats[0] == -2 && floats[2] == 4)
typed: proc([]int, proc(int,int)->bool)->[]int = sorted
again := typed(data, proc(i,j:int)->bool{return i<j})
defer delete(again)
assert(again[0] == 1)
''', '''
import "core:slice"
sorted :: proc(data: $T/[]$E, less := proc(i, j: E) -> bool { return i < j }) -> []E {
    ret := make([]E, len(data))
    copy(ret, data)
    slice.sort_by(ret, less)
    return ret
}
'''))

    def test_typed_generic_defaults_and_separately_generic_literals(self):
        for callback in ['proc(i,j:E)->bool{return i<j}', 'proc(i,j:$U)->bool{return i<j}']:
            with self.subTest(callback=callback):
                self.invoke(self.program('''
assert(work([]int{1,2}))
assert(!work([]int{1,2}, compare=proc(i,j:int)->bool{return i>j}))
''', 'work::proc(data:[]$E,compare:proc(E,E)->bool='+callback+')->bool{return compare(data[0],data[1])}\n'))

    def test_override_does_not_instantiate_an_inapplicable_default(self):
        self.invoke(self.program('''
data:=[]Record{{3},{7}}
assert(work(data,proc(a,b:Record)->bool{return a.key<b.key}))
''', '''
Record::struct{key:int}
work::proc(data:[]$E,less:=proc(a,b:E)->bool{return a<b})->bool{return less(data[0],data[1])}
'''))

    def test_generic_array_lengths_and_multiple_type_parameters(self):
        self.invoke(self.program('''
assert(work(2, []int{7,8}) == 7)
assert(pair(3, "hello"))
''', '''
work::proc($N:int,data:[]$E,first:=proc(value:[N]E)->E{return value[0]})->E{
    value:[N]E
    copy(value[:],data)
    return first(value)
}
pair::proc(a:$A,b:$B,compare:=proc(x:A,y:B)->bool{return size_of(A)>0 && len(y)>0})->bool{return compare(a,b)}
'''))

    def test_multiple_defaults_comments_parentheses_and_constraints(self):
        self.invoke(self.program('''
assert(work([]int{2,4}) == 6)
assert(work([]int{2,4}, combine=proc(a,b:int)->int{return a*b}) == 8)
''', '''
work::proc(data:[]$E,
    compare /* compare comment */ := (proc(a:E,b:E)->bool{return a<b}),
    combine:=proc(a,b:E)->E{return a+b},
)->E where E in [int,f64] {
    assert(compare(data[0],data[1]))
    return combine(data[0],data[1])
}
'''))

    def test_helper_retains_enclosing_scope_and_selected_imports(self):
        self.invoke(self.program('assert(outer(3))', '''
import "core:math" / abs
outer::proc($Offset:int)->bool{
    local_constant::2
    local::proc(data:[]$E,compare:=proc(a,b:E)->bool{return abs(a)+Offset+local_constant<abs(b)})->bool{
        return compare(data[0],data[1])
    }
    return local([]int{-1,10})
}
'''))

    def test_conditional_and_nested_declarations_keep_native_checking(self):
        for directive in ['when', 'when false {} else when']:
            with self.subTest(directive=directive):
                self.invoke(self.program('assert(compare([]int{1,2}))', directive+''' true {
compare::proc(data:[]$E,less:=proc(a,b:E)->bool{return a<b})->bool{return less(data[0],data[1])}
} else {
compare::proc(data:[]$E,less:=proc(a,b:E)->bool{return missing})->bool{return less(data[0],data[1])}
}
'''))
        self.invoke(self.program('', '''
unused::proc($T:typeid){
    inner::proc(data:[]$E,less:=proc(a,b:E)->bool{return missing})->bool{return less(data[0],data[1])}
}
'''))

    def test_null_override_is_not_replaced_by_the_default(self):
        self.invoke(self.program('assert(present([]int{1}));assert(!present([]int{1},nil))', '''
present::proc(data:[]$E,compare:=proc(a,b:E)->bool{return a<b})->bool{return compare!=nil}
'''))

    def test_other_thor_syntax_in_default_callback(self):
        self.invoke(self.program('assert(work([]int{3,4}) == "value 16")', '''
work::proc(data:[]$E,format:=proc(value:E)->string{return f"value {switch value{case 3: value ** 2;case: value ** 2}}"})->string{
    result:=format(data[-1])
    defer delete(result)
    return result=="value 16" ? "value 16" : "wrong"
}
'''))

    def test_pure_callback_in_an_ordinary_procedure(self):
        self.invoke(self.program('assert(choose([7,3])==3 && choose([2,5])==2)', '''
choose::proc(data:[2]$E,less:=fn(a,b:E)->bool{return a<b})->E{
    return less(data[0],data[1]) ? data[0] : data[1]
}
'''))

    def test_purity_still_forbids_indirect_callback_inputs(self):
        self.invoke(self.program('assert(choose([7,3])==3)', '''
choose::fn(data:[2]$E,less:=fn(a,b:E)->bool{return a<b})->E{
    return less(data[0],data[1]) ? data[0] : data[1]
}
'''), fail='indirect, foreign and unknown calls are forbidden')

    def test_imported_thor_callbacks_keep_their_own_imports(self):
        self.invoke(self.program('assert(library.compare([]int{2,5}))', 'import "library"\n'), files={
            'library/main.thor': '''package library
import "core:math" / abs
compare::proc(data:[]$E,less:=proc(a,b:E)->bool{return abs(a)<abs(b)})->bool{return less(data[0],data[1])}
'''})

    def test_imported_defaults_selected_aliases_native_callers_and_multiple_callbacks(self):
        library = '''package library
work::proc(data:[]$E,less:=proc(a,b:E)->bool{return a<b},add:=proc(a,b:E)->E{return a+b})->E{
    return less(data[0],data[1]) ? add(data[0],data[1]) : data[0]
}
'''
        self.invoke(self.program('''
assert(work(data=[]int{2,3})==5)
assert(library.work([]int{2,3}, /* comma in comment , */)==5)
assert(library.work([]int{2,3},add=proc(a,b:int)->int{return a*b})==6)
assert(library.work([]int{2,3},proc(a,b:int)->bool{return a>b})==2)
assert(alias([]int{2,3})==5)
assert(native()==5 && other()==5)
''', 'import "library" / work\n'), directory=True, files={
            'library/main.thor': library,
            'alias.thor': 'package main\nimport "library"\nalias::library.work\n',
            'other.thor': 'package main\nother::proc()->int{return alias([]int{2,3})}\n',
            'native.odin': 'package main\nimport "library"\nnative::proc()->int{return library.work([]int{2,3})}\n',
        })

    def test_imported_defaults_preserve_side_effects_and_conditional_symbols(self):
        self.invoke(self.program('''
assert(library.work(data())==1)
assert(calls==1)
''', '''import "library"
calls:int
values:[2]int={1,2}
data::proc()->[]int{calls+=1;return values[:]}
'''), files={'library/main.thor': '''package library
when true{
work::proc(data:[]$E,less:=proc(a,b:E)->bool{return a<b})->E{return less(data[0],data[1]) ? data[0] : data[1]}
}else{
work::proc(data:[]$E,less:=proc(a,b:E)->bool{return missing})->E{return data[0]}
}
when false{
inactive::proc(data:[]$E,less:=proc(a,b:E)->bool{return missing})->E{return data[0]}
}
'''})

    def test_else_branch_helpers_are_selected_by_odin_even_across_files(self):
        for split in [False, True]:
            with self.subTest(split=split):
                first = '''when false {
work::proc(data:[]$E,less:=proc(a,b:E)->bool{return a<b})->E{return less(data[0],data[1]) ? data[0] : data[1]}
}'''
                second = '''when true {
work::proc(data:[]$E,less:=proc(a,b:E)->bool{return a>b})->E{return less(data[0],data[1]) ? data[0] : data[1]}
}'''
                files = {'library/main.thor': 'package library\n'+first+'\n'}
                if split:
                    files['library/other.thor'] = 'package library\n'+second+'\n'
                else:
                    files['library/main.thor'] += second+'\n'
                self.invoke(self.program('assert(library.work([]int{2,3})==3)', 'import "library"\n'), files=files)


if __name__ == '__main__':
    unittest.main()

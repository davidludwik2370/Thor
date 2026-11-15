"""Constant ++ values, typing, native compatibility and source diagnostics."""
import json
import unittest

from tests import test_thor as base


class ConcatenationTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_parser_precedence_and_native_odin_mode(self):
        from thorlang.compiler import Source,ThorError
        from thorlang.driver import frontend_binary
        frontend,_=frontend_binary('odin')
        text='package main\nmain::proc(){_=A+B ++ C*D;_=A ++ B ++ C}\n'
        parsed=Source(base.ROOT/'concat-precedence.thor',frontend,raw=text.encode(),cache=False)
        assignments=[node for node in parsed.nodes if node.kind=='Assignment']
        first,second=[node.group('rhs')[0] for node in assignments]
        self.assertEqual(first.data['op'],'++')
        self.assertEqual(first.role('left').data['op'],'+')
        self.assertEqual(first.role('right').data['op'],'*')
        self.assertEqual(second.data['op'],'++')
        self.assertEqual(second.role('left').data['op'],'++')
        with self.assertRaises(ThorError):
            Source(base.ROOT/'concat-native.odin',frontend,thor=False,raw=text.encode(),cache=False)

    def test_arrays_chains_grouping_and_additive_precedence(self):
        self.invoke(self.program('''
if ALL != [1,2,3,4,5] || type_of(ALL) != [5]int {panic("array chain")}
if ([1] ++ [2]+[10,20]) != [11,22] {panic("left associativity")}
if ([1] ++ [2*3]) != [1,6] {panic("constant arithmetic")}
if ([1] ++ ([2] ++ [3])) != [1,2,3] {panic("grouped chain")}
if ALL[-1]!=5 || 4 not_in ALL {panic("sequence operators")}
''', 'A::[1,2]\nB::[3]\nALL::A ++ B ++ [4,5]\n'))

    def test_slices_empty_arrays_and_zero_filled_tails(self):
        self.invoke(self.program('''
if len(S)!=3 || S[0]!=1 || S[-1]!=3 || type_of(S)!=[]int {panic("slice")}
if len(E)!=0 || type_of(E)!=[0]int {panic("empty")}
if PADDED!=[0,0,0,2,0] {panic("zero-filled tail")}
if ([] ++ [1] ++ []) != [1] {panic("inferred empty")}
if len(SE)!=0 || type_of(SE)!=[]int {panic("empty slices")}
''','''
S::[]int{1} ++ []int{2,3}
E::[0]int{} ++ [0]int{}
SE::[]int{} ++ []int{}
PADDED::[3]int{} ++ [2]int{2,0}
'''))

    def test_literals_inherit_context_without_converting_established_arrays(self):
        self.invoke(self.program('''
A::[1]i8{1}
if (A ++ [2] != [2]i8{1,2}) {panic("trailing literal")}
if ([0] ++ A != [2]i8{0,1}) {panic("leading literal")}
if take([.Red] ++ [.Blue])!=2 {panic("argument context")}
if (CHOICE!=[2]Color{.Red,.Blue} || len(NONE)!=0) {panic("declaration context")}
if result()!=CHOICE {panic("return context")}
''','''
Color::enum{Red,Blue}
CHOICE:[2]Color: [.Red] ++ [.Blue]
NONE:[0]Color: [] ++ []
take::proc(values:[2]Color)->int{return len(values)}
result::proc()->[2]Color{return [.Red] ++ [.Blue]}
'''))

    def test_nested_arrays_records_unicode_and_shallow_slice_elements(self):
        self.invoke(self.program('''
if ROWS!=[[1,2],[3,4],[5,6]] || type_of(ROWS)!=[3][2]int {panic("rows")}
if NODES[0].value!=1 || NODES[1].value!=2 {panic("records")}
if WORDS[0]!="é😀" || WORDS[1]!="Thor" {panic("strings")}
if VIEWS[0][0]!=INPUT[0][0] || VIEWS[1][0]!=6 {panic("slice elements")}
''','''
Node::struct{value:int}
ROWS::[[1,2]] ++ [[3,4],[5,6]]
NODES::[Node{1}] ++ [{2}]
WORDS::["é😀"] ++ ["Thor"]
INPUT::[1][]int{{4,5}}
VIEWS::INPUT ++ [1][]int{{6}}
'''))

    def test_distinct_array_result_is_plain_and_distinct_slices_keep_identity(self):
        self.invoke(self.program('''
if A!=[1,2] || type_of(A)!=[2]int {panic("plain array result")}
if len(S)!=2 || type_of(S)!=Numbers {panic("slice identity")}
''','''
Pair::distinct [1]int
Numbers::distinct []int
A::Pair{1} ++ Pair{2}
S::Numbers{1} ++ Numbers{2}
'''))

    def test_multiline_comments_and_native_expression_compatibility(self):
        self.invoke(self.program('''
if VALUES!=[1,2,3] {panic("line continuation")}
v:=[1,2];v+=[3,4]
if v!=[4,6] || "a"+"b"!="ab" || "a"*2!="aa" {panic("Odin arithmetic")}
''','''
VALUES::[1] ++ // append second
    [2] ++ /* append third */
    [3]
'''))

    def test_imported_constants_native_files_and_no_basic_dependency(self):
        self.invoke(self.program('''
if ALL!=[1,2,3] || !native() {panic("imported concatenation")}
''','import "values" / A\nALL::A ++ [3]\n'), directory=True, files={
            'values/main.thor':'package values\nA::[1,2]\n',
            'native.odin':'package main\nnative::proc()->bool{return ALL==[3]int{1,2,3}}\n'})

    def test_generic_constant_parameters_and_signature_expressions(self):
        self.invoke(self.program('''
if combine([1,2],[3])!=[1,2,3] {panic("generic body")}
if sized([1,2])!=3 {panic("generic signature")}
''','''
import "base:intrinsics"
combine::proc($a:[$N]$T,$b:[$M]T)->[N+M]T{return a ++ b}
sized::proc($a:[2]int)->int where len(a ++ [3])==3 {return len(a ++ [3])}
'''))

    def test_pure_functions_run_results_type_queries_and_defaults(self):
        self.invoke(self.program('''
if AUTO!=[1,2,3] || EXPLICIT!=AUTO || COMBINED!=[1,2,3,4] {panic("evaluation")}
if len(storage)!=4 || default_value()!=[1,2,3] {panic("type/default")}
text:=f"{COMBINED}"
if text!="[1, 2, 3, 4]" {panic("interpolation")}
delete(text)
''','''
make_values::fn()->[3]int{return [1] ++ [2,3]}
AUTO::make_values()
EXPLICIT::#run make_values()
COMBINED::EXPLICIT ++ [4]
storage:[len(COMBINED)]int
default_value::proc(value:=[1] ++ [2,3])->[3]int{return value}
'''))

    def test_inactive_when_and_compile_switch_arms_remain_unchecked(self):
        self.invoke(self.program('''
when false {BAD::[1] ++ "bad";_=BAD}
when false {value:=[1];_=value ++ [2]}
GOOD::[1] when true else [1] ++ "bad"
CHOSEN::#switch{case true:[2] ++ [3];case:[1] ++ "bad"}
if GOOD!=[1] || CHOSEN!=[2,3] {panic("inactive branch")}
when len([1] ++ [2])==2 {_=0} else {missing_selected()}
'''))

    def test_runtime_variables_immutables_parameters_and_counts_are_rejected(self):
        for body in ['values:=[1];_=values ++ [2]', 'values:>[1];_=[2] ++ values',
                     'values:=[]int{1};_=values ++ []int{2}',
                     '_=bad([1])']:
            with self.subTest(body=body):
                prefix='bad::proc(values:[1]int)->[2]int{return values ++ [2]}\n' if 'bad(' in body else ''
                self.invoke(self.program(body,prefix),command='check',fail='requires compile-time constant operands')

    def test_mixed_containers_non_sequences_and_mismatched_element_types(self):
        for expression,message in [
            ('[]int{1} ++ [2]','cannot mix fixed arrays and slices'),
            ('[1] ++ []int{2}','cannot mix fixed arrays and slices'),
            ('"a" ++ "b"','use + for constant strings'),
            ('[1] ++ 2','requires fixed arrays or constant slices'),
            ('set[int]{1} ++ set[int]{2}','requires'),
            ('[1]int{1} ++ [1]f64{2}','matching element types'),
            ('[]int{1} ++ []f64{2}','matching slice types'),
        ]:
            with self.subTest(expression=expression):
                self.invoke(self.program('_='+expression),command='check',fail=message)

    def test_empty_without_context_keyed_entries_and_increment_remain_errors(self):
        for body,message in [('_=[] ++ []','empty array literal needs an element type'),
                             ('_=[2]int{1=2} ++ [1]int{3}','positional entries'),
                             ('n:=1;n++','expected'), ('n:=1;++n','unary'),
                             ('a:=[1];a++=[2]','expected')]:
            with self.subTest(body=body):
                self.invoke(self.program(body),command='check',fail=message)

    def test_errors_highlight_original_operand_without_lowering_names(self):
        text=self.program('// 😀\nvalues:>[1]\n_=[2] ++ values')
        _,result=self.invoke(text,command='check',fail='requires compile-time constant operands',flags=['--diagnostic-format=json'])
        errors=json.loads(result.stdout or result.stderr)['errors']
        self.assertEqual(len(errors),1,errors)
        start=text.encode().rindex(b'values')
        self.assertEqual(errors[0]['pos']['offset'],start)
        self.assertEqual(errors[0]['pos']['end_offset'],start+6)
        self.assertNotIn('__thor_',result.stdout)
        self.assertNotIn('Thor expression',result.stdout)

    def test_invalid_literal_element_keeps_the_root_type_error(self):
        text=self.program('_=[1] ++ [f64(2)]')
        _,result=self.invoke(text,command='check',fail='to \'int\' in an array literal',flags=['--diagnostic-format=json'])
        errors=json.loads(result.stdout or result.stderr)['errors']
        self.assertEqual(len(errors),1,errors)
        self.assertIn('f64',errors[0]['msgs'][0])
        self.assertEqual(errors[0]['pos']['offset'],text.encode().index(b'f64(2)'))

    def test_chain_lowers_to_one_intrinsic_without_evaluation_cache_or_runtime_helper(self):
        root,result=self.invoke(self.program('if ALL!=[1,2,3,4]{panic("value")}','ALL::[1] ++ ([2] ++ [3]) ++ [4]\n'),flags=['--cache-report'])
        code=next((root/'output').rglob('main.odin')).read_text()
        self.assertEqual(code.count('.concatenate('),1,code)
        self.assertEqual(code.count('proc()'),1,code)
        self.assertEqual(json.loads(result.stdout)['thor_cache']['writes'],0)

    def test_checked_debug_and_unchecked_builds_do_not_allocate(self):
        text=self.program('''
local:=ALL
if local!=[1,2,3] || len(S)!=2 {panic("release result")}
''','ALL::[1] ++ [2,3]\nS::[]int{4} ++ []int{5}\n')
        for flags in [[],['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(text,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

    def test_native_array_arithmetic_is_not_silently_treated_as_a_constant(self):
        self.invoke(self.program('_=([1]+[2]) ++ [3]'),command='check',fail='requires compile-time constant arrays or slices')

    def test_nested_context_preserves_distinct_array_element_types(self):
        self.invoke(self.program('''
if type_of(ALL)!=[2]Pair || ALL[0][1]!=2 || ALL[1][1]!=4 {panic("distinct element")}
''','Pair::distinct [2]int\nALL::[Pair{1,2}] ++ [[3,4]]\n'))

    def test_basic_and_big_numeric_imports_keep_nested_literals_constant(self):
        self.invoke(self.program('''
if ROWS!=[[1,2],[3,4]] || type_of(ROWS)!=[2][2]int {panic("basic nested arrays")}
if NUMBERS[0]!=1 || NUMBERS[1]!=2 {panic("constant big values")}
''','''
import "thor:basic"
ROWS::[[1,2]] ++ [[3,4]]
NUMBERS::[1] ++ [2]
'''))

    def test_basic_concat_alias_has_been_removed(self):
        self.invoke(self.program('_=basic.CONCAT','import "thor:basic"\n'),command='check',fail='CONCAT')

    def test_big_number_aggregate_constants_retain_the_existing_native_boundary(self):
        self.invoke(self.program('_=ALL','''
import "thor:big"
ALL::[Integer(1)] ++ [Integer(2)]
'''),command='check',fail='requires compile-time constant arrays or slices')

    def test_pure_slice_results_promote_the_constant_backing_to_independent_owners(self):
        self.invoke(self.program('''
a:=#run make_values()
b:=#run make_values()
if len(a)!=2 || a[1]!=2 || raw_data(a)==raw_data(b) {panic("owned result")}
a[0]=9
if b[0]!=1 {panic("independent storage")}
delete(a);delete(b)
''','make_values::fn()->[]int{return []int{1} ++ []int{2}}\n'),flags=['-debug'])

    def test_large_first_literal_is_not_duplicated_in_every_type_query(self):
        first='['+','.join(str(i) for i in range(1000))+']'
        text=self.program('if len(ALL)!=1003 || ALL[1002]!=3 {panic("large chain")}','ALL::'+first+' ++ [1] ++ [2] ++ [3]\n')
        root,_=self.invoke(text)
        code=next((root/'output').rglob('main.odin')).read_text()
        self.assertLess(len(code),12000)
        self.assertEqual(code.count('.concatenate('),1)


if __name__=='__main__':unittest.main()

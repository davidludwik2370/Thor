"""Fixed-value struct operators, constants, ownership restrictions and mapping."""
from tests.test_rust_compiler import compiler_engines
import json
import re
import shutil
import unittest
from tests import test_rust_compiler as native


class StructArithmeticTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare
    program = native.RustCompilerTests.program

    def run_both(self, body, prefix=''):
        for flags in ([], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program(body, prefix), flags=flags)

    def test_basic_operators_literals_chains_and_named_results(self):
        self.run_both('''
x:=Foo{8,12};y:=Foo{2,3}
if (x+y!=Foo{10,15} || x-y!=Foo{6,9} || x*y!=Foo{16,36} || x/y!=Foo{4,4}) {panic("operators")}
if (x+{1,2}!=Foo{9,14} || {1,2}+x!=Foo{9,14}) {panic("literal context")}
if ((x+{1,2})*{2,3}!=Foo{18,42}) {panic("chain")}
x+={3,4};x-={1,2};x*={2,3};x/={2,3}
if (x!=Foo{10,14}) {panic("compound")}
result:Foo={1,2}+{3,4}
if (result!=Foo{4,6} || type_of(result)!=Foo) {panic("expected type")}
''', 'Foo::struct{a,b:int}\n')

    def test_nested_fixed_arrays_using_packed_and_distinct_fields(self):
        self.run_both('''
a:=Outer{Inner{Small(2),4},[2]int{2,4},[2]Inner{{3,6},{4,8}}}
b:=Outer{Inner{Small(1),2},[2]int{1,2},[2]Inner{{1,2},{2,4}}}
c:=a/b
if (c.inner!=Inner{Small(2),2} || c.vector!=[2]int{2,2} || c.rows!=[2]Inner{{3,3},{2,2}}) {panic("nested")}
if (a+b-b!=a) {panic("nested chain")}
p:=Packed{1,2};p+={3,4}
if (p!=Packed{4,6}) {panic("packed")}
''', '''Small::distinct int
Inner::struct{a:Small,b:int}
Outer::struct{using inner:Inner,vector:[2]int,rows:[2]Inner}
Packed::struct #packed {a:i8,b:i64}
''')

    def test_operands_and_assignment_targets_are_evaluated_once(self):
        self.run_both('''
calls:=0
sum:=next(&calls)+next(&calls)
if (calls!=2 || sum!=Foo{3,5}) {panic("operand calls")}
x:=Foo{1,2};x+={x.b,x.a}
if (x!=Foo{3,3}) {panic("rhs snapshot")}
items:=[2]Foo{{1,2},{3,4}};index_calls:=0
items[index(&index_calls)]+={2,3}
if (index_calls!=1 || items[0]!=Foo{3,5}) {panic("indexed target")}
m:=make(map[int]Foo);defer delete(m);m[0]={1,2};index_calls=0
m[index(&index_calls)]+={3,4}
if (index_calls!=1 || m[0]!=Foo{4,6}) {panic("map target")}
for i:=(Foo{});i.a<3;i+=({1,2}) {if i.b!=i.a*2 {panic("for post")}}
''', '''Foo::struct{a,b:int}
next::proc(calls:^int)->Foo{calls^+=1;return {calls^,calls^+1}}
index::proc(calls:^int)->int{calls^+=1;return 0}
''')

    def test_true_constants_defaults_type_queries_and_pure_execution(self):
        self.run_both('''
if (VALUE!=Foo{10,14} || SIZE!=10 || len(storage)!=10) {panic("constant")}
if (AUTO!=Foo{3,5} || RUN!=AUTO || local()!=Foo{4,6} || defaults()!=VALUE) {panic("pure")}
''', '''Foo::struct{a,b:int}
VALUE::(Foo{1,2}+{4,5})*{2,2}
SIZE::VALUE.a
storage:[SIZE]int
calculate::fn(x:Foo)->Foo{return x+{1,2}}
AUTO::calculate(Foo{2,3})
RUN::#run calculate(Foo{2,3})
local::fn()->Foo{x:=Foo{1,2};x+={3,4};return x}
defaults::proc(value:=VALUE)->Foo{return value}
''')

    def test_imported_native_types_and_field_names_survive(self):
        self.compare(self.program('''
a:=model.Foo{1,2};b:=a+{b=4,a=3}
if (b!=model.Foo{4,6}) {panic("native imported type")}
''','import "model"\n'), files={'model/model.odin':'package model\nFoo::struct{a,b:int}\n'})

    def test_aliases_and_distinct_structs_keep_the_exact_result_type(self):
        self.run_both('''
a:=Alias{1,2};b:=Foo{3,4};c:=a+b
if (type_of(c)!=Foo || c!=Foo{4,6}) {panic("alias")}
x:=Wrapped{1,2};y:={3,4}+x;x+={3,4}
if (type_of(y)!=Wrapped || y!=Wrapped{4,6} || x!=y || CONSTANT!=y) {panic("distinct result")}
''','Foo::struct{a,b:int}\nAlias::Foo\nWrapped::distinct Foo\nCONSTANT::Wrapped{1,2}+{3,4}\n')

    def test_same_shape_different_types_and_invalid_literals_are_rejected(self):
        for body in ['a:=A{};b:=B{};_=a+b','a:=A{};a+=B{}','a:=A{};_=a+{1,2,3}',
                     'a:=A{};_=a+{bad=1}','a:=A{};_=a+{1,"wrong"}',
                     'BAD::A{}+B{};_=BAD']:
            with self.subTest(body=body):
                self.compare(self.program(body,'A::struct{x,y:int}\nB::struct{x,y:int}\n'), command='check',fail='Error:')
        for body in ['a:=Base{1};b:=Derived{{2}};_=a+b','a:=Base{1};b:=Derived{{2}};a+=b']:
            self.compare(self.program(body,'Base::struct{x:int}\nDerived::struct{using base:Base}\n'),
                         command='check',fail='same type')

    def test_generic_callbacks_and_records_preserve_native_specializations(self):
        self.run_both('''
a:=Foo{1,2};b:=Foo{3,4}
if (add(a,b)!=Foo{4,6} || add(1,2)!=3 || add([1,2],[3,4])!=[4,6]) {panic("generic")}
if (BOX!=Box(Foo){Foo{4,6}} || generic_box(Box(Foo){a},Box(Foo){b})!=BOX) {panic("generic record")}
if (GENERATED!=Foo{4,6}) {panic("generic pure")}
if (twice(Vectors(2){[2]int{1,2}}).values!=[2]int{2,4}) {panic("generic length")}
if (update(a,b)!=Foo{7,10} || a!=Foo{1,2}) {panic("generic compound")}
''','''Foo::struct{a,b:int}
Box::struct($T:typeid){value:T}
Vectors::struct($N:int){values:[N]int}
twice::proc(value:Vectors($N))->Vectors(N){return value+value}
update::proc(a,b:$T)->T{value:=a;for i:=0;i<2;value+=b {i+=1};return value}
add::proc(a,b:$T)->T{return a+b}
generic_box::proc(a,b:Box($T))->Box(T){return a+b}
calculate::fn(a,b:$T)->T{return a+b}
GENERATED::#run calculate(Foo{1,2},Foo{3,4})
BOX::Box(Foo){Foo{1,2}}+Box(Foo){Foo{3,4}}
''')

    def test_reference_containing_and_non_arithmetic_fields_are_rejected(self):
        for typ in ['^int','[]int','string','[dynamic]int','map[int]int','set[int]',
                    'Integer','Rational','bool','any','rawptr','proc()','union{int,f64}',
                    '[2][]int','struct{nested:[]int}']:
            with self.subTest(typ=typ):
                self.compare(self.program('a:=S{};b:=S{};_=a+b','S::struct{field:'+typ+'}\n'),command='check',fail='Cannot apply')

    def test_complex_array_matrix_and_shadowed_builtin_fields(self):
        self.run_both('''
x:=Data{1+2i,quaternion128(1),[2]f64{2,4},matrix[2,2]f64{1,0,0,1}}
y:=x+x
if (y.z!=2+4i || y.q!=quaternion128(2) || y.v!=[2]f64{4,8} || y.m!=matrix[2,2]f64{2,0,0,2}) {panic("native leaf types")}
''','Data::struct{z:complex128,q:quaternion128,v:[2]f64,m:matrix[2,2]f64}\n')
        self.run_both('x:=Foo{1};x+={2};y:={3}+x;if y.value!=6 {panic("shadow")}',
                      'Foo::struct{value:int}\ntype_of::proc()->int{return 9}\nlen::proc()->int{return 7}\n')

    def test_native_bit_sets_use_their_existing_operator_rules(self):
        self.run_both('''
a:=Holder{Bits{.a}};b:=Holder{Bits{.b}};c:=a+b;c-=b
if (c.flags!=Bits{.a} || VALUE.flags!=Bits{.a,.b}) {panic("bit sets")}
''',
            'E::enum{a,b}\nBits::bit_set[E]\nHolder::struct{flags:Bits}\nVALUE::Holder{Bits{.a}}+Holder{Bits{.b}}\n')
        self.compare(self.program('x:=Holder{};_=x*x','E::enum{a,b}\nHolder::struct{flags:bit_set[E]}\n'),command='check',fail='not allowed')

    def test_unknown_constant_shapes_keep_untyped_literal_coercion(self):
        self.run_both('take(PAD+"ok");small:i8=NUM+1;if small!=5 {panic("literal")}',
            'PAD::#switch{case false:missing;case:" "*2}\nNUM::#switch{case false:missing;case:4}\ntake::proc(value:cstring){if value==nil {panic("cstring")}}\n')

    def test_generic_native_addition_does_not_add_logical_number_or_set_imports(self):
        self.source.write_text(self.program('if add(1,2)!=3 {panic("native")}',
            'add::proc(a,b:$T)->T{return a+b}\n'))
        for engine in compiler_engines():
            result=self.invoke(engine,command='transpile')
            self.assertEqual(result.returncode,0,result.stderr)
            files=list((self.root/engine).rglob('main.odin'))
            self.assertTrue(files,result.stdout)
            for path in files:
                self.assertNotRegex(path.read_text(),r'import[^\n]*[/:](?:big|sets)"')

    def test_constant_nested_arrays_empty_records_and_contextual_defaults(self):
        self.run_both('''
if (RESULT.rows!=[2]Foo{{4,6},{8,10}} || CHOSEN!=Foo{4,6}) {panic("constant nesting")}
empty:=Empty{}+Empty{};empty+=Empty{}
if type_of(empty)!=Empty {panic("empty")}
''','''Foo::struct{a,b:int}
Rows::struct{rows:[2]Foo}
Empty::struct{}
RESULT::Rows{[2]Foo{{1,2},{3,4}}}+Rows{[2]Foo{{3,4},{5,6}}}
CHOSEN:Foo: {1,2}+{3,4}
''')

    def test_comprehensions_fstrings_fmap_and_pmap_share_the_result_type(self):
        self.run_both('''
jobs:=[Foo{1,2},Foo{3,4}]
mapped:=basic.fmap(jobs[:],shift)
parallel:=basic.pmap(jobs[:],shift,workers=2)
collected:=[job+{1,2} for job in jobs]
defer delete(mapped);defer delete(parallel);defer delete(collected)
for entry,i in mapped {if entry!=parallel[i] || entry!=collected[i] || entry!=COMPILED[i] {panic("mapping")}}
text:=f"{(jobs[0]+{1,2}).a}";defer delete(text)
if text!="2" {panic("interpolation")}
''','''import "thor:basic"
Foo::struct{a,b:int}
shift::fn(value:Foo)->Foo{return value+{1,2}}
COMPILED::[job+{1,2} for job in [Foo{1,2},Foo{3,4}]]
''')

    def test_generic_fields_cannot_hide_references_or_logical_numbers(self):
        for typ in ['[]int','Integer','struct{data:[]int}']:
            with self.subTest(typ=typ):
                self.compare(self.program('x:=Box('+typ+'){};_=add(x,x)',
                    'Box::struct($T:typeid){value:T}\nadd::proc(a,b:$T)->T{return a+b}\n'),
                    command='check',fail='Cannot apply')

    def test_immutable_destination_and_inactive_branches_keep_native_rules(self):
        self.compare(self.program('a:>S{1};a+={2}','S::struct{x:int}\n'), command='check',fail='immutable')
        self.run_both('when false {a:=Bad{};_=a+a}\nvalue:=Good{1}+{2};if value.x!=3 {panic("when")}',
                      'Good::struct{x:int}\nBad::struct{x:[]int}\n')

    def test_immutable_inputs_produce_independent_mutable_value_copies(self):
        self.run_both('''
frozen:>Row{[2]int{1,2}}
copy:=frozen+{[2]int{3,4}}
copy.values[0]=99
edit(copy.values[:])
if (frozen.values!=[2]int{1,2} || copy.values!=[2]int{99,88}) {panic("fresh value storage")}
for row in ([1]Row{frozen}) {next:=row+{[2]int{1,1}};next.values[0]=5}
''','Row::struct{values:[2]int}\nedit::proc(#mut values:[]int){values[1]=88}\n')

    def test_restriction_diagnostics_keep_original_expression_and_field_path(self):
        source=self.program('// 😀\na:=S{}\n_=a+a','S::struct{inner:struct{values:[]int}}\n')
        self.source.write_text(source)
        for engine in compiler_engines():
            result=self.invoke(engine,command='check',flags=['-json-errors'])
            self.assertEqual(result.returncode,1,result.stderr)
            errors=json.loads(result.stderr)['errors']
            self.assertEqual(len(errors),1,errors)
            self.assertIn('inner.values',errors[0]['msgs'][0])
            pos=errors[0]['pos'];start=source.encode().index(b'a+a')
            self.assertEqual((pos['offset'],pos['end_offset']),(start,start+3),errors)
            self.assertNotIn('__thor_',json.dumps(errors))

    def test_generic_restrictions_point_to_the_operator(self):
        source=self.program('x:=Bad{};_=add(x,x)',
            '// 😀 header\nBad::struct{data:[]int}\nadd::proc(a,b:$T)->T{return a+b}\n')
        self.source.write_text(source)
        for engine in compiler_engines():
            result=self.invoke(engine,command='check',flags=['-json-errors'])
            self.assertEqual(result.returncode,1,result.stderr)
            errors=json.loads(result.stderr)['errors']
            self.assertEqual(len(errors),1,errors)
            start=source.encode().index(b'a+b');pos=errors[0]['pos']
            self.assertEqual((pos['offset'],pos['end_offset']),(start,start+3),errors)
            self.assertNotIn('__thor_',json.dumps(errors))

    def test_raw_union_storage_cannot_be_hidden_by_generic_callbacks(self):
        for fields in ('value:int','value:int,zero:[0]int'):
            for generic in (False,True):
                expression='add(x,x)' if generic else 'x+x'
                self.compare(self.program('x:=Raw{};_='+expression,
                    'Raw::struct #raw_union #align(16){'+fields+'}\nadd::proc(a,b:$T)->T{return a+b}\n'),
                    command='check',fail='Cannot apply')
        self.run_both('x:=Ordinary{1};if add(x,x).value!=2 {panic("ordinary")}',
            'Raw::struct #raw_union{value:int}\nOrdinary::struct{value:int}\nadd::proc(a,b:$T)->T{return a+b}\n')

    def test_optimized_numeric_arithmetic_matches_handwritten_field_operations(self):
        self.source.write_text('''package main
Pair::struct{a,b:f64}
@(export)
struct_add::proc(a,b:Pair)->Pair{return a+b}
@(export)
manual_add::proc(a,b:Pair)->Pair{return {a.a+b.a,a.b+b.b}}
main::proc(){}
''')
        for engine in compiler_engines():
            directory=self.root/(engine+'-ir');directory.mkdir()
            result=self.invoke(engine,command='build',flags=[*native.UNCHECKED,'-build-mode:llvm-ir',
                '-use-single-module','-out:'+str(directory)])
            self.assertEqual(result.returncode,0,result.stderr)
            ir='\n'.join(path.read_text() for path in directory.rglob('*.ll'))
            bodies=[]
            for name in ('struct_add','manual_add'):
                match=re.search(r'^define[^\n]*@'+name+r'\([^\n]*\n(.*?)^}',ir,re.MULTILINE|re.DOTALL)
                self.assertIsNotNone(match,name)
                names={}
                def rename(symbol):
                    return names.setdefault(symbol[0],'%v'+str(len(names)))
                bodies.append(re.sub(r'%[\w.$-]+',rename,match[1]))
            self.assertEqual(bodies[0],bodies[1])
            self.assertNotIn('call ',bodies[0])

    @unittest.skipUnless(shutil.which('node'),'debug map consumer requires Node')
    def test_debugger_maps_operator_lines_without_stopping_in_generated_helpers(self):
        from tests.test_rust_debug_maps import RustDebugMapTests
        source='''package main
Foo::struct{a,b:int}
main::proc(){
    x:=Foo{1,2}
    x+={3,4}
    y:=x+{1,2}
    if y.a!=5 || y.b!=8 {panic("debug")}
}
'''
        manifest,_,binary=RustDebugMapTests.build(self,source)
        for line in (5,6):
            info=RustDebugMapTests.inspect(self,manifest,binary,line)
            self.assertTrue(info['candidates'],info)
            self.assertTrue(all(not candidate['helper'] for candidate in info['candidates']),info)
        self.assertTrue(any(region['kind']=='expression-helper' for region in info['regions']))


if __name__=='__main__':unittest.main()

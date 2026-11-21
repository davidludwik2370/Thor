"""Struct bitwise, shift, complement, and power operators use scalar semantics."""
from tests.test_rust_compiler import compiler_engines
import unittest
from tests import test_struct_arithmetic as arithmetic
from tests import test_rust_compiler as native


class StructBitwisePowerTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program
    run_both=arithmetic.StructArithmeticTests.run_both

    def test_bitwise_shifts_complement_and_literals(self):
        self.run_both('''
a:=Bits{12,10};b:=Bits{10,6}
if (a&b!=Bits{8,2} || a|b!=Bits{14,14} || a~b!=Bits{6,12} || a&~b!=Bits{4,8}) {panic("bitwise")}
if (a<<{1,2}!=Bits{24,40} || {32,64}>>Bits{2,3}!=Bits{8,8}) {panic("shifts")}
if ((~a).a!=~a.a || ~~a!=a) {panic("complement")}
a&={10,6};a|={1,4};a~={2,3};a&~={1,2};a<<={1,2};a>>={1,2}
if (a!=Bits{10,5}) {panic("compound")}
expected:Bits={3,4}|{4,8}
if (expected!=Bits{7,12}) {panic("context")}
''','Bits::struct{a,b:uint}\n')

    def test_native_boolean_width_fields_aliases_generics_and_comptime(self):
        for width in ['b8','b16','b32','b64']:
            code=self.program('''a:=Flags{left=true,right=false};b:=Flags{left=false,right=true}
expected:=Flags{left=true,right=true};merged:=a|b
if merged!=expected||RESULT!=expected{panic("wide boolean fields")}
a|=b;if a!=expected{panic("boolean compound")}
if (~a).left!=~a.left || (a&b).right!=(a.right&b.right){panic("boolean native operations")}
''',f'''B::distinct {width}
Flags::struct{{left,right:B,_:[3]byte}}
merge::fn($T:typeid,a,b:T)->T{{return a|b}}
RESULT::#run merge(Flags,Flags{{left=true,right=false}},Flags{{left=false,right=true}})
''')
            for flags in ([],native.UNCHECKED):
                with self.subTest(width=width,flags=flags):self.compare(code,flags=flags,expected='')

    def test_integer_float_power_and_compound(self):
        self.run_both('''
a:=Ints{2,3};b:=a**{5,4}
coerced:Ints={2,3}**{5,4}
if coerced!=b {panic("power context")}
coerced={2,3}**{5,4}
if (take({2,3}**{5,4})!=32 || coerced!=b || returned()!=Ints{8,9} || IN_CALL!=32 || NAMED!=32) {panic("surrounding context")}
if (b!=Ints{32,81} || {3,4}**a!=Ints{9,64}) {panic("integer power")}
a**={3,2}
if (a!=Ints{8,9}) {panic("power compound")}
f:=Floats{4,2};g:=f**{0.5,-2}
if (g!=Floats{2,0.25}) {panic("floating power")}
f**={0.5,3}
if (f!=Floats{2,8}) {panic("floating compound")}
x:=3;x**=4
if x!=81 {panic("scalar power compound")}
''','Ints::struct{a,b:int}\nFloats::struct{a,b:f64}\ntake::fn(x:Ints)->int{return x.a}\nreturned::fn()->Ints{return {2,3}**{3,2}}\nIN_CALL::take({2,3}**{5,4})\nNAMED::take(x={2,3}**{5,4})\n')

    def test_constants_and_pure_execution(self):
        self.run_both('''
if (BITS!=Bits{16,64} || INV.a!=~uint(16)) {panic("constant bits")}
if (POWER!=Ints{32,81} || FLOAT!=Floats{2,0.25}) {panic("constant power")}
if (AUTO!=Ints{8,9} || RUN!=AUTO || defaults()!=POWER || len(storage)!=32) {panic("pure")}
''','''Bits::struct{a,b:uint}
Ints::struct{a,b:int}
Floats::struct{a,b:f64}
BITS::((Bits{8,32}|{1,2})&~{1,2})<<{1,1}
INV::~BITS
POWER:Ints: {2,3}**{5,4}
FLOAT::Floats{4,2}**{0.5,-2}
storage:[POWER.a]int
defaults::proc(x:=Ints{2,3}**{5,4})->Ints{return x}
calculate::fn(input:Ints)->Ints{x:=input;x**={3,2};return x}
AUTO::calculate(Ints{2,3})
RUN::#run calculate(Ints{2,3})
''')

    def test_nested_fixed_arrays_generic_records_and_padding(self):
        self.run_both('''
a:=Box(uint,2){values={2,3}};b:=Box(uint,2){values={3,2}}
if (power(a,b).values!=[2]uint{8,9} || shift(a,b).values!=[2]uint{16,12}) {panic("generic arrays")}
if (invert(invert(a)).values!=a.values || combine(a,b).values!=[2]uint{3,3}) {panic("generic bitwise")}
if (COMPILED.values!=[2]uint{8,9} || SHIFTED.values!=[2]uint{16,12} || COMPLEMENT.values[1]!=~uint(3)) {panic("generic constants")}
rows:=Rows{rows={{values={2,3}},{values={3,2}}}}
powered:=rows**{rows={{values={3,2}},{values={2,3}}}}
if (powered.rows[0].values!=[2]uint{8,9} || powered.rows[1].values!=[2]uint{9,8}) {panic("nested")}
rows**={rows={{values={3,2}},{values={2,3}}}}
if rows.rows[0].values!=powered.rows[0].values {panic("nested assignment")}
p:=Pad{value=12};p&~={value=10};p<<={value=1};p**={value=2}
if p.value!=64 || (~p).value!=~uint(64) {panic("padding")}
''','''Box::struct($T:typeid,$N:int){_:[]int,values:[N]T,_:^int}
Rows::struct{rows:[2]Box(uint,2)}
Pad::struct{_:map[int]int,value:uint,_:string}
power::fn(a,b:$T)->T{return a**b}
shift::proc(a,b:$T)->T{return a<<b}
invert::fn(a:$T)->T{return ~a}
combine::proc(a,b:$T)->T{x:=a;x&=b;x|=b;x~=b;x&~=b;return a|b}
COMPILED::#run power(Box(uint,2){values={2,3}},Box(uint,2){values={3,2}})
SHIFTED::#run shift(Box(uint,2){values={2,3}},Box(uint,2){values={3,2}})
COMPLEMENT::#run invert(Box(uint,2){values={2,3}})
''')

    def test_enum_indexed_arrays_preserve_native_index_types(self):
        self.run_both('''
a:=S{{.first=2,.last=3}};b:=S{{.first=3,.last=2}}
p:=a**b;q:=a<<b
if p.lanes[.first]!=8 || p.lanes[.last]!=9 || q.lanes[.first]!=16 || q.lanes[.last]!=12 {panic("enum array")}
if POWER.lanes[.first]!=8 || SHIFT.lanes[.last]!=12 {panic("enum compile time")}
if generic(a,b).lanes[.first]!=8 {panic("enum generic")}
''','''E::enum{first,last}
S::struct{lanes:[E]uint}
generic::fn(a,b:$T)->T{return a**b}
shift::fn(a,b:$T)->T{return a<<b}
POWER::#run generic(S{{.first=2,.last=3}},S{{.first=3,.last=2}})
SHIFT::#run shift(S{{.first=2,.last=3}},S{{.first=3,.last=2}})
''')

    def test_native_bool_bit_sets_distinct_fields_and_empty_structs(self):
        self.run_both('''
a:=Flags{true,Bits{.a}};b:=Flags{false,Bits{.b}}
if (a|b!=Flags{true,Bits{.a,.b}} || a&b!=Flags{false,Bits{}} || a~b!=Flags{true,Bits{.a,.b}}) {panic("native fields")}
if (~~a!=a || invert(invert(a))!=a) {panic("typed bool complement")}
inverted:=~a
if (bool_and(inverted,a)!=(inverted&a) || bool_or(inverted,a)!=(inverted|a) || bool_xor(inverted,a)!=(inverted~a)) {panic("generic bool bit patterns")}
x:=Wrapped{Count(2)};x**={Count(3)};x<<={Count(1)}
if (type_of(x)!=Wrapped || x.n!=16) {panic("distinct")}
z:=Empty{};z**=Empty{};z<<=Empty{};_=~z
if type_of(z)!=Empty {panic("empty")}
if scalar(2,3)!=8 {panic("scalar big enabled fallback")}
''','''E::enum{a,b,c}
Bits::bit_set[E]
Flags::struct{flag:bool,bits:Bits}
bool_and::proc(a,b:$T)->T{return a&b}
bool_or::proc(a,b:$T)->T{return a|b}
bool_xor::proc(a,b:$T)->T{return a~b}
Count::distinct uint
Wrapped::struct{n:Count}
Empty::struct{_:[]int}
scalar::proc(a,b:$T)->T{return a**b}
invert::proc(a:$T)->T{return ~a}
''')

    def test_native_simd_bitwise_fields(self):
        self.run_both('''
a:=S{{12,10,3,1}};b:=S{{10,6,1,2}}
x:=andnot(a,b);direct:=a&~b;a&~=b
v:=transmute([4]u32)x.lanes
w:=transmute([4]u32)direct.lanes
z:=transmute([4]u32)a.lanes
if (v!=[4]u32{4,8,2,1} || w!=v || z!=v) {panic("SIMD bitwise")}
''','S::struct{lanes:#simd[4]u32}\nandnot::proc(a,b:$T)->T{return a&~b}\n')

    def test_single_evaluation_compound_targets_for_posts_and_scalar_fallback(self):
        self.run_both('''
order:=0
value:=mark(&order,2)**mark(&order,3)
if order!=23 || value.n!=8 {panic("operand order")}
order=0;_=~mark(&order,4)
if order!=4 {panic("complement once")}
items:=[1]S{{2}};calls:=0
items[index(&calls)]**={3}
if calls!=1 || items[0].n!=8 {panic("indexed target")}
items[0]<<={items[0].n>>2}
if items[0].n!=32 {panic("snapshot")}
m:=make(map[int]S);defer delete(m);m[0]={2};calls=0
m[index(&calls)]**={4}
if calls!=1 || m[0].n!=16 {panic("map target")}
p:=&items[0];p^>>={2}
if p.n!=8 {panic("pointer target")}
iterations:=0
for x:=(S{2});x.n<=16;x**=({2}) {iterations+=1}
if iterations!=3 {panic("post")}
if scalar_power(3,4)!=81 || scalar_invert(uint(0))!=~uint(0) {panic("native fallback")}
''','''S::struct{n:uint}
mark::proc(order:^int,n:uint)->S{order^=order^*10+int(n);return {n}}
index::proc(count:^int)->int{count^+=1;return 0}
scalar_power::proc(a,b:$T)->T{x:=a;x**=b;return x}
scalar_invert::proc(x:$T)->T{return ~x}
''')

    def test_power_assignment_keeps_big_numbers_separate(self):
        self.run_both('''
x:=Integer(2);defer delete(x);x**=10
r:=Rational(1,2);defer delete(r);r**=3
if x!=1024 || r!=Rational(1,8) {panic("logical power")}
''','import "thor:big"\n')

    def test_constant_power_with_basic_and_local_types(self):
        self.run_both('''
Local::struct{a,b:uint}
VALUE::Local{2,3}**{3,2}
ANNOTATED:Local: {2,3}**{3,2}
if (VALUE!=Local{8,9} || ANNOTATED!=VALUE || GLOBAL!=Global{8,9}) {panic("constant numeric dispatch")}
''','''import * "thor:basic"
Global::struct{a,b:uint}
GLOBAL::Global{2,3}**{3,2}
''')

    def test_negative_runtime_exponents_keep_scalar_failure(self):
        for flags in ([],native.UNCHECKED):
            self.compare(self.program('x:=S{2};n:=-1;_=x**S{n}','S::struct{n:int}\n'),flags=flags,fail='Integer power requires')

    def test_restrictions_same_type_and_immutable_destinations(self):
        for body,prefix,message in [
            ('x:=Bad{};_=~x','Bad::struct{data:[]int}','self-contained fixed-size'),
            ('x:=Bad{};_=x**x','Bad::struct{data:^int}','self-contained fixed-size'),
            ('x:=Bad{};_=shift(x,x)','Bad::struct{data:[]int}\nshift::proc(a,b:$T)->T{return a<<b}','Cannot apply'),
            ('x:=Bad{};_=invert(x)','Bad::struct{data:string}\ninvert::proc(a:$T)->T{return ~a}','Cannot apply'),
            ('_=A{}**B{}','A::struct{x:int}\nB::struct{x:int}','same type'),
            ('x:=A{};x|=B{}','A::struct{x:uint}\nB::struct{x:uint}','same type'),
            ('x:>A{2};x**={3}','A::struct{x:int}','immutable'),
            ('x:=A{1};_=x<<{1}','A::struct{x:int}','unsigned'),
            ('x:=A{1};_=x&x','A::struct{x:f64}','does not support'),
            ('x:=Raw{};_=invert(x)','Raw::struct #raw_union{x:uint}\ninvert::proc(a:$T)->T{return ~a}','Cannot apply'),
        ]:
            with self.subTest(body=body):
                self.compare(self.program(body,prefix+'\n'),command='check',fail=message)

    def test_compilation_interactions_and_native_expansion(self):
        self.run_both('''
jobs:=[S{2},S{3}]
values:=[x**{3} for x in jobs];defer delete(values)
mapped:=basic.fmap(jobs[:],cube);defer delete(mapped)
parallel:=basic.pmap(jobs[:],cube,workers=2);defer delete(parallel)
for x,i in values {if x!=mapped[i] || x!=parallel[i] || x!=CONSTANTS[i] {panic("collections")}}
text:=f"{(values[0]**{2}).n}";defer delete(text)
if text!="64" {panic("fstring")}
a:=S{7};n:=expand_values(a);if n!=7 {panic("expand_values")}
take(**a)
when false {bad:=Bad{};_=bad**bad;_=~bad}
''','''import "thor:basic"
S::struct{n:int}
Bad::struct{n:[]int}
cube::fn(x:S)->S{return x**{3}}
take::proc(n:int){if n!=7 {panic("spread")}}
CONSTANTS::[x**{3} for x in [S{2},S{3}]]
''')


    def test_optimized_operators_match_native_field_code(self):
        import re
        from collections import Counter
        self.source.write_text('''package main
import "thor:numeric"
S::struct{a,b:uint}
combine::proc(a,b:$T)->T{return ~(a|b)}
power::proc(a,b:$T)->T{return a**b}
@(export) struct_bits::proc(a,b:S)->S{return ~(a|b)}
@(export) generic_bits::proc(a,b:S)->S{return combine(a,b)}
@(export) manual_bits::proc(a,b:S)->S{return {~(a.a|b.a),~(a.b|b.b)}}
@(export) struct_shift::proc(a,b:S)->S{return a<<b}
@(export) manual_shift::proc(a,b:S)->S{return {a.a<<b.a,a.b<<b.b}}
@(export) struct_power::proc(a,b:S)->S{return a**b}
@(export) generic_power::proc(a,b:S)->S{return power(a,b)}
@(export) manual_power::proc(a,b:S)->S{return {numeric.power(a.a,b.a),numeric.power(a.b,b.b)}}
main::proc(){}
''')
        for engine in compiler_engines():
            directory=self.root/(engine+'-ir');directory.mkdir()
            result=self.invoke(engine,command='build',flags=[*native.UNCHECKED,'-build-mode:llvm-ir',
                '-use-single-module','-out:'+str(directory)])
            self.assertEqual(result.returncode,0,result.stderr)
            ir='\n'.join(path.read_text() for path in directory.rglob('*.ll'))
            def instructions(name):
                match=re.search(r'^define[^\n]*@'+name+r'\([^\n]*\n(.*?)^}',ir,re.MULTILINE|re.DOTALL)
                self.assertIsNotNone(match,name)
                lines=[]
                for line in match[1].splitlines():
                    if 'llvm.experimental.noalias.scope.decl' in line:continue
                    line=re.sub(r', !(?:alias.scope|noalias) !\d+','',line.strip())
                    line=line.split(';',1)[0].rstrip()
                    if line.endswith(':'):line='block:'
                    line=re.sub(r'%"[^"]+"|%[\w.$-]+','%value',line)
                    lines.append(line)
                return Counter(lines)
            for operation in ('bits','shift','power'):
                manual=instructions('manual_'+operation)
                actual=instructions('struct_'+operation)
                self.assertEqual(list((actual-manual).elements()),list((manual-actual).elements()),operation)
                if operation!='shift':
                    actual=instructions('generic_'+operation)
                    self.assertEqual(list((actual-manual).elements()),list((manual-actual).elements()),operation)
                self.assertFalse(any('struct_math' in line or 'struct_generic' in line for line in manual))

    def test_debugger_maps_extended_operator_lines(self):
        from tests.test_rust_debug_maps import RustDebugMapTests
        source='''package main
S::struct{_:bool,n:uint}
main::proc(){
    x:=S{n=2}
    x**={n=3}
    x<<={n=1}
    y:=~x
    if x.n!=16 || y.n!=~uint(16) {panic("debug")}
}
'''
        manifest,_,binary=RustDebugMapTests.build(self,source)
        for line in (5,6,7):
            info=RustDebugMapTests.inspect(self,manifest,binary,line)
            self.assertTrue(info['candidates'],info)
            self.assertTrue(all(not candidate['helper'] for candidate in info['candidates']),info)


if __name__=='__main__':unittest.main()

"""Operator trees stay compact without changing native evaluation or types."""
import re
import unittest
from tests import test_rust_compiler as native


class StructOperatorScalingTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_long_generic_and_concrete_trees_emit_bounded_source(self):
        expression='x'
        for _ in range(10):
            for op in ['+','-','*','/']:
                expression=f'({expression}{op}x)'
        prefix='S::struct{value:int}\nfold::proc(x:$T)->T{return '+expression+'}\nconcrete::proc(x:S)->S{return '+expression+'}\n'
        source=self.program('''
if (fold(i16(2))!=2 || fold(S{2})!=S{2} || concrete(S{2})!=S{2}) {panic("operator tree")}
if (fold([2]int{2,2})!=[2]int{2,2}) {panic("native array")}
''',prefix)
        self.source.write_text(source)
        result=self.invoke('rust','transpile')
        self.assertEqual(result.returncode,0,result.stderr)
        emitted=next((self.root/'rust').rglob('main.odin')).read_bytes()
        self.assertLess(len(emitted),250_000)
        for flags in [[],native.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(source,flags=flags)

    def test_side_effects_literals_and_compile_time_evaluation(self):
        source=self.program('''
order:int
if fold(&order,2)!=4 || order!=1234 {panic("native operand order")}
order=0
if (fold(&order,S{2})!=S{4} || order!=1234) {panic("record operand order")}
_=size_of(fold(&order,S{2}))
if (order!=1234 || VALUE!=1234 || literal(S{2})!=S{10}) {panic("query or literal")}
''','''S::struct{value:int}
mark::proc(order:^int,digit:int,x:$T)->T{order^=order^*10+digit;return x}
fold::proc(order:^int,x:$T)->T{return mark(order,1,x)+mark(order,2,x)*mark(order,3,x)-mark(order,4,x)}
literal::proc(x:$T)->T{return ({2}+x)*{3}-x}
prove::fn()->int{order:int;_=fold(&order,2);return order}
VALUE::#run prove()
''')
        for flags in [[],native.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(source,flags=flags)

    def test_native_matrix_products_can_change_shape_within_a_tree(self):
        source=self.program('''
a:matrix[2,3]f64;a[0,0]=1;a[1,1]=1
b:matrix[3,4]f64;b[0,0]=1;b[1,1]=1;b[2,2]=1
c:matrix[4,2]f64;c[0,0]=2;c[1,1]=3
r:=product(a,b,c)
if (r!=matrix[2,2]f64{2,0,0,3}) {panic("matrix result shape")}
''','product::proc(a:$A,b:$B,c:$C)->matrix[2,2]f64{return a*b*c}\n')
        for flags in [[],native.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(source,flags=flags)

    def test_generic_boolean_trees_preserve_native_storage_bits(self):
        source=self.program('''
x:bool=transmute(bool)u8(0x80)
s:=S{x}
if bits(x)!=control.bits(x) || bits(s)!=control.bits(x) {panic("Boolean storage")}
''','''import "control"
S::struct{value:bool}
bits::proc(#by_ptr x:$T)->u8{return transmute(u8)(~(~(x|x)))}
''')
        files={'control/control.odin':'package control\nbits::proc(#by_ptr x:bool)->u8{return transmute(u8)(~(~(x|x)))}\n'}
        for flags in [[],native.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(source,files=files,flags=flags)

    def test_optimized_generic_tree_matches_native_arithmetic_instructions(self):
        expression='x*y+x/y-x*y+x/y-x*y+x/y-x*y+x/y'
        self.source.write_text(self.program('',f'''
blend::#force_inline proc(x,y:$T)->T{{return {expression}}}
@(export) generic_math::proc(x,y:f64)->f64{{return blend(x,y)}}
@(export) manual_math::proc(x,y:f64)->f64{{return {expression}}}
'''))
        directory=self.root/'ir';directory.mkdir()
        result=self.invoke('rust','build',flags=[*native.UNCHECKED,'-build-mode:llvm-ir','-use-single-module','-out:'+str(directory)])
        self.assertEqual(result.returncode,0,result.stderr)
        ir='\n'.join(path.read_text() for path in directory.rglob('*.ll'))
        bodies=[]
        for name in ['generic_math','manual_math']:
            match=re.search(r'^define[^\n]*@'+name+r'\([^\n]*\n(.*?)^}',ir,re.MULTILINE|re.DOTALL)
            self.assertIsNotNone(match,name)
            instructions=[]
            for line in match[1].splitlines():
                if 'llvm.experimental.noalias.scope.decl' in line:continue
                line=re.sub(r', !(?:alias.scope|noalias) !\d+','',line.strip())
                instructions.append(re.sub(r'%[\w.$-]+','%value',line))
            bodies.append(instructions)
        self.assertEqual(bodies[0],bodies[1])
        self.assertFalse(any('call ' in line for line in bodies[0]))


if __name__=='__main__':unittest.main()

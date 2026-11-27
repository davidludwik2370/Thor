"""Unnamed padding is not an arithmetic value, purity input, or owned field."""
from tests.test_rust_compiler import compiler_engines
import re
import shutil
import unittest
from tests import test_rust_compiler as native
from tests import test_struct_arithmetic as arithmetic


class StructPaddingTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program
    run_both=arithmetic.StructArithmeticTests.run_both

    def test_padding_is_ignored_by_all_operators_literals_and_compounds(self):
        self.run_both('''
x:=Value{a=8,b=12};y:=Value{a=2,b=3}
if (x+y).a!=10 || (x-y).b!=9 || (x*y).a!=16 || (x/y).b!=4 {panic("operators")}
x+={a=3,b=4};x-={a=1,b=2};x*={a=2,b=3};x/={a=2,b=3}
if x.a!=10 || x.b!=14 {panic("compound")}
left:={a=2,b=3}+x;right:=x+{a=2,b=3}
if left.a!=12 || right.b!=17 {panic("literals")}
''','Value::struct{_:bool,a:int,_:^int,b:int,_:[]string}\n')

    def test_generic_nested_arrays_local_types_and_layout_tags(self):
        self.run_both('''
x:=Leaf{value=3};y:=add(x,x)
if y.value!=6 || update(x,x).value!=9 {panic("generic leaf")}
rows:=Rows{items=[2]Leaf{{value=1},{value=2}}}
if add(rows,rows).items[1].value!=4 {panic("array recursion")}
p:=Packed{value=4};if add(p,p).value!=8 {panic("packed tags")}
aligned:=Aligned{value=4};if add(aligned,aligned).value!=8 {panic("aligned")}
strict:AllOrNone;strict.value=5
if add(strict,strict).value!=10 || (strict+strict).value!=10 {panic("all-or-none")}
u:=Promoted{leaf=x};if add(u,u).value!=6 {panic("using")}
sub:=Subtype{leaf=x};if add(sub,sub).leaf.value!=6 {panic("subtype")}
Local::struct{_:proc(),value:int,_:any}
local:=Local{value=7};if add(local,local).value!=14 {panic("local")}
g:=Generic([]int,2){items=[2]Leaf{{value=2},{value=3}}}
if add(g,g).items[0].value!=4 {panic("generic padding type")}
''','''Leaf::struct{_:map[int]int,value:int,_:bool}
Rows::struct{_:Integer,items:[2]Leaf,_:Rational}
Packed::struct #packed{_:[]int `unused`,value:int `amount`,_:bool}
Aligned::struct #align(16){_:bool,value:int,_:[]int}
AllOrNone::struct #all_or_none{_:bool,value:int,_:int}
Promoted::struct{_:rawptr,using leaf:Leaf,_:[]int}
Subtype::struct{#subtype leaf:Leaf,_:bool}
Generic::struct($P:typeid,$N:int){_:P,items:[N]Leaf,_:^P}
add::proc(a,b:$T)->T{return a+b}
update::proc(a,b:$T)->T{result:=a;result+=b;result+=b;return result}
''')

    def test_empty_named_layouts_and_uniform_field_types(self):
        self.run_both('''
single:=add(Single{},Single{});single/=Single{}
opaque:=add(Opaque{},Opaque{});identity:=add(Identity{},Identity{})
same:=add(Same{},Same{});same*=Same{}
mixed:=add(Mixed{},Mixed{});mixed-=Mixed{}
if type_of(single)!=Single || type_of(same)!=Same || type_of(mixed)!=Mixed || type_of(opaque)!=Opaque || type_of(identity)!=Identity {panic("padding-only")}
x:=Uniform{a=4,b=6};y:=add(x,x)
if y.a!=8 || y.b!=12 {panic("uniform types")}
grouped:=Grouped{value=3};if add(grouped,grouped).value!=6 {panic("grouped fields")}
empty:=add(Empty{},Empty{});_=empty
''','''Single::struct{_:[]int}
Same::struct{_,_:int}
Opaque::struct{_:any}
Identity::struct{_:typeid}
Mixed::struct{_:^int,_:[]string,_:proc()}
Uniform::struct{_:int,a:int,_:int,b:int}
Grouped::struct{_,value,_:int}
Empty::struct{}
add::proc(a,b:$T)->T{return a+b}
''')

    def test_constants_pure_functions_and_compile_time_materialization(self):
        self.run_both('''
if VALUE.a!=4 || VALUE.b!=4 || len(storage)!=4 {panic("constants")}
if AUTO.a!=6 || RUN.b!=10 || local().a!=8 {panic("pure")}
if len(COMPREHENSION)!=2 || COMPREHENSION[1].a!=4 {panic("comprehension")}
if type_of(EMPTY)!=OnlyPadding {panic("empty record constant")}
''','''Value::struct{_:rawptr,a:int,_:map[int]int,b:int,_:bool}
OnlyPadding::struct{_:[]int,_:proc()}
VALUE::Value{a=8,b=12}/Value{a=2,b=3}
storage:[VALUE.a]int
add::fn(a,b:$T)->T{return a+b}
AUTO::add(Value{a=3,b=5},Value{a=3,b=5})
RUN::#run add(Value{a=3,b=5},Value{a=3,b=5})
EMPTY::#run add(OnlyPadding{},OnlyPadding{})
local::fn()->Value{x:=Value{a=4,b=6};x+=x;return x}
COMPREHENSION::[item+item for item in [Value{a=1,b=3},Value{a=2,b=4}]]
''')

    def test_generic_purity_distinguishes_procedure_padding_from_callable_fields(self):
        prefix = '''Inner::struct{_:proc(), n:int}
Outer::struct{items:[2]Inner}
sum::fn(x:$T)->int{x.items[0].n+x.items[1].n}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('v:=Outer{items={{n=2},{n=3}}};if sum(v)!=5{panic("sum")}',prefix),
                             flags=flags)
        # Naming the very same field makes the procedure part of the value.
        self.compare(self.program('v:Outer;_=sum(v)', prefix.replace('_:proc()', 'callback:proc()')),
                     command='check', fail='ordinary procedure values')

    def test_imported_native_private_layouts(self):
        for flags in ([],native.UNCHECKED):
            self.compare(self.program('x:=model.make_value(4);y:=add(x,x);if y.value!=8 {panic("native private")}',
                'import "model"\nadd::proc(a,b:$T)->T{return a+b}\n'),flags=flags,
                files={'model/model.odin':'''package model
@(private="file")
Hidden::struct #packed{_:^int,value:int `value`,_:[]string}
make_value::proc(value:int)->Hidden{return {value=value}}
'''})

    def test_inactive_invalid_padding_layout_does_not_affect_generic_arithmetic(self):
        self.run_both('x:=Padded{value=3};if add(x,x).value!=6 {panic("inactive layout")}',
            '''when false {Invalid::struct{_:int,value:int,value:int}}
Padded::struct{_:int,value:int,_:bool}
add::proc(a,b:$T)->T{return a+b}
''')

    def test_equivalent_tag_spellings_do_not_count_padding_twice(self):
        self.run_both('''
a:=Backtick{value=3};b:=Quoted{value=4};c:=NoTag{value=5};d:=EmptyTag{value=6}
if add(a,a).value!=6 || add(b,b).value!=8 || add(c,c).value!=10 || add(d,d).value!=12 {panic("tag equivalence")}
''','''Backtick::struct{_:int,value:int `amount`}
Quoted::struct{_:int,value:int "amount"}
NoTag::struct{_:int,value:int}
EmptyTag::struct{_:int,value:int ""}
add::proc(a,b:$T)->T{return a+b}
''')

    def test_padding_does_not_hide_invalid_named_fields_or_different_types(self):
        for typ in ('[]int','^int','Integer','Rational','map[int]int','bool'):
            for expr in ('x+x','add(x,x)'):
                with self.subTest(typ=typ,expr=expr):
                    self.compare(self.program('x:=Bad{};_='+expr,
                        'Bad::struct{_:int,value:'+typ+',_:int}\nadd::proc(a,b:$T)->T{return a+b}\n'),
                        command='check',fail='Cannot apply')
        for expr in ('x+y','add(x,y)','x+=y'):
            self.compare(self.program('x:=A{};y:=B{};'+('_='+expr if expr!='x+=y' else expr),
                'A::struct{_:int,value:int}\nB::struct{_:int,value:int}\nadd::proc(a:$T,b:$U)->T{return a+b}\n'),
                command='check',fail='same type')
        for fields in ('_:int,value:int','_:int,zero:[0]int','_:int'):
            self.compare(self.program('x:=Raw{};_=add(x,x)',
                'Raw::struct #raw_union{'+fields+'}\nOrdinary::struct{'+fields+'}\nadd::proc(a,b:$T)->T{return a+b}\n'),
                command='check',fail='Cannot apply')

    def test_pure_owned_results_ignore_padding_without_leaks(self):
        source=self.program('x:=produce();defer delete(x.values);if len(x.values)!=2 || x.values[0]!=7 {panic("owned result")}',
            '''Box::struct{_:map[int]int,values:[]int,_:proc()}
produce::fn()->Box{values:=make([]int,2);values[0]=7;return {values=values}}
''')
        for flags in ([],native.UNCHECKED,['-debug','--track-allocations=basic']):
            result=self.compare(source,flags=flags)
            self.assertNotIn('still allocated',result.stderr)

    def test_explicit_padding_inspection_keeps_purity_restrictions(self):
        for expression in ('expand_values(value)','unpack(value)','**value'):
            self.compare(self.program('x:=Pad{};_=inspect(x)',
                'Pad::struct{_:^int,number:int}\nunpack::expand_values\n'
                'inspect::fn(value:$T)->int{pointer,_:='+expression+';return pointer^}\n'),
                command='check',fail='Pure code')
        for expression in ('value==value','transmute(u128)value'):
            result='bool' if '==' in expression else 'u128'
            self.compare(self.program('', 'Pad::struct{_:^int,number:int}\ninspect::fn(value:Pad)->'+result+'{return '+expression+'}\n'),
                command='check',fail='Pure')
        for typ in ('[]int','[dynamic]int','struct{values:[]int}'):
            self.compare(self.program('','Pad::struct{_:'+typ+',number:int}\n'
                'inspect::fn(value:Pad)->int{buffer,_:=expand_values(value);_=buffer;return 0}\n'),
                command='check',fail='Pure code')
        self.run_both('''
if safe(Pad{number=4})!=4 || local()!=6 || RUN!=4 {panic("safe expansion")}
''','''Pad::struct{_:int,number:int}
Plain::struct{pointer:^int,number:int}
safe::fn(value:$T)->int{_,number:=expand_values(value);return number}
RUN::#run safe(Pad{number=4})
local::fn()->int{x:=2;obj:=Plain{&x,4};p,n:=expand_values(obj);p^+=n;return x}
''')

    def test_padding_has_no_optimized_runtime_reflection_or_arithmetic(self):
        self.source.write_text('''package main
Pair::struct{_:[5]byte,a,b:f64,_:bool}
add::proc(a,b:$T)->T{return a+b}
@(export)
struct_add::proc(a,b:Pair)->Pair{return a+b}
@(export)
generic_add::proc(a,b:Pair)->Pair{return add(a,b)}
@(export)
manual_add::proc(a,b:Pair)->Pair{return {a=a.a+b.a,b=a.b+b.b}}
main::proc(){}
''')
        for engine in compiler_engines():
            directory=self.root/(engine+'-ir');directory.mkdir()
            result=self.invoke(engine,command='build',flags=[*native.UNCHECKED,'-build-mode:llvm-ir',
                '-use-single-module','-out:'+str(directory)])
            self.assertEqual(result.returncode,0,result.stderr)
            ir='\n'.join(path.read_text() for path in directory.rglob('*.ll'))
            bodies=[]
            for name in ('struct_add','generic_add','manual_add'):
                match=re.search(r'^define[^\n]*@'+name+r'\([^\n]*\n(.*?)^}',ir,re.MULTILINE|re.DOTALL)
                self.assertIsNotNone(match,name)
                instructions=[]
                for line in match[1].splitlines():
                    if 'llvm.experimental.noalias.scope.decl' in line:continue
                    line=re.sub(r', !(?:alias.scope|noalias) !\d+','',line.strip())
                    instructions.append(re.sub(r'%[\w.$-]+','%value',line))
                # Inlining can reorder independent stores/GEPs and add alias
                # metadata. Compare native instructions, constants and types.
                bodies.append(sorted(instructions))
            self.assertEqual(bodies[0],bodies[2])
            self.assertEqual(bodies[1],bodies[2])
            self.assertFalse(any('call ' in line for line in bodies[0]))

    @unittest.skipUnless(shutil.which('node'),'debug map consumer requires Node')
    def test_padding_generic_debugger_maps_user_operations(self):
        from tests.test_rust_debug_maps import RustDebugMapTests
        source='''package main
Padded::struct{_:bool,value:int,_:[3]byte}
add::proc(a,b:$T)->T{return a+b}
main::proc(){
    x:=Padded{value=3}
    y:=add(x,x)
    x+={value=2}
    if x.value!=5 || y.value!=6 {panic("debug padding")}
}
'''
        manifest,_,binary=RustDebugMapTests.build(self,source)
        for line in (3,6,7):
            info=RustDebugMapTests.inspect(self,manifest,binary,line)
            self.assertTrue(info['candidates'],info)
            self.assertTrue(all(not candidate['helper'] for candidate in info['candidates']),info)


if __name__=='__main__':unittest.main()

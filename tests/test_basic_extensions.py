"""Basic library additions, including ownership and cross-feature boundaries."""
import unittest

from tests import test_thor as base


class BasicExtensionTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_function_first_mapping_and_filtering_callbacks(self):
        source = '''package main
import "thor:basic"
double :: fn(x: int) -> int {return x*2}
even :: fn(x: int) -> bool {return x%2==0}
identity :: proc(x: $T) -> T {return x}
positive :: proc(x: $T) -> bool {return x>0}
calls: int
record :: proc(x: int) -> int {calls+=1; return x+calls}
main :: proc() {
    data :> []int{3,1,2}
    mapped := basic.fmap(double, data)
    defer delete(mapped)
    kept := basic.filter(even, data)
    defer delete(kept)
    if len(mapped)!=3 || mapped[0]!=6 || mapped[2]!=4 {panic("mapped values")}
    if len(kept)!=1 || kept[0]!=2 {panic("filtered values")}
    mapped[0]=99
    kept[0]=88
    if data[0]!=3 || data[2]!=2 {panic("outputs must have independent storage")}

    // Runtime procedure values and helper aliases keep their normal behavior.
    callback: proc(int)->int = record
    map_alias :: basic.fmap
    filter_alias :: basic.filter
    recorded := map_alias(callback, data, allocator=context.allocator)
    defer delete(recorded)
    if calls!=3 || recorded[0]!=4 || recorded[1]!=3 || recorded[2]!=5 {panic("callback order/count")}
    predicate: proc(int)->bool = even
    selected := filter_alias(predicate, data, allocator=context.allocator)
    defer delete(selected)
    if len(selected)!=1 || selected[0]!=2 {panic("runtime predicate")}

    // Explicitly specialize polymorphic callbacks before passing them.
    copier: proc(int)->int = identity
    test_positive: proc(int)->bool = positive
    copied := basic.fmap(copier, data)
    defer delete(copied)
    positives := basic.filter(test_positive, data)
    defer delete(positives)
    if copied[1]!=1 || len(positives)!=3 {panic("specialized callbacks")}

    Item :: struct {name: string, score: int}
    items :> []Item{{"oak",1},{"elm",2}}
    changed := basic.fmap(proc(x: Item)->Item {return {x.name,x.score+10}}, items)
    defer delete(changed)
    if changed[0].score!=11 || changed[1].name!="elm" || items[0].score!=1 {panic("struct mapping")}
}
'''
        for flags in ([], ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']):
            with self.subTest(flags=flags):
                self.invoke(source, flags=flags)

    def test_function_first_collection_imports_and_named_arguments(self):
        imports = [
            ('import "thor:basic"', 'basic.fmap', 'basic.filter'),
            ('import b "thor:basic"', 'b.fmap', 'b.filter'),
            ('import * "thor:basic"', 'fmap', 'filter'),
            ('import "thor:basic" / fmap, filter', 'fmap', 'filter'),
            ('import _ "thor:basic" / fmap, filter', 'fmap', 'filter'),
        ]
        body = '''
data :> []i16{1,2,3}
mapped, err := MAP(func=proc(x: i16)->i16 {return x+1}, vals=data, allocator=context.allocator)
defer delete(mapped)
kept := FILTER(f=proc(x: i16)->bool {return x>1}, vals=data, allocator=context.allocator)
defer delete(kept)
if err!=nil || len(mapped)!=3 || mapped[2]!=4 || len(kept)!=2 || kept[0]!=2 {panic("imports/named arguments")}
'''
        for declaration, mapper, predicate in imports:
            with self.subTest(declaration=declaration):
                self.invoke(self.program(body.replace('MAP',mapper).replace('FILTER',predicate),declaration+'\n'))

    def test_function_first_empty_inputs_and_mapping_allocation_failure(self):
        source = '''package main
import "thor:basic"
import "core:mem"
calls: int
record :: proc(x: int) -> int {calls+=1; return x}
accept :: proc(x: int) -> bool {calls+=1; return true}
fail :: proc(data: rawptr, mode: mem.Allocator_Mode, size, alignment: int,
    old: rawptr, old_size: int, loc:=#caller_location) -> ([]byte, mem.Allocator_Error) {
    return nil, .Out_Of_Memory
}
main :: proc() {
    nil_values: []int
    literal :> []int{1}
    for values in ([]([]int){nil_values, literal[:0]}) {
        mapped, err := basic.fmap(record, values)
        defer delete(mapped)
        kept := basic.filter(accept, values)
        defer delete(kept)
        if err!=nil || len(mapped)!=0 || len(kept)!=0 || calls!=0 {panic("empty callbacks/results")}
    }
    result, err := basic.fmap(record, literal, allocator=mem.Allocator{fail,nil})
    if err!=.Out_Of_Memory || len(result)!=0 || calls!=0 {panic("allocation failure")}
}
'''
        for flags in ([], ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']):
            with self.subTest(flags=flags):
                self.invoke(source,flags=flags)

    def test_function_first_collection_helpers_from_native_odin(self):
        self.invoke(self.program('native()', ''), directory=True, files={'native.odin':'''package main
import "thor:basic"
native :: proc() {
    values := []int{1,2,3}
    mapped := basic.fmap(proc(x: int)->int {return x*3}, values)
    defer delete(mapped)
    kept := basic.filter(proc(x: int)->bool {return x>1}, values)
    defer delete(kept)
    if mapped[2]!=9 || len(kept)!=2 || kept[0]!=2 {panic("native callbacks")}
}
'''})

    def test_function_first_collection_helpers_reject_wrong_order_and_types(self):
        for expression in [
            'basic.fmap([]int{1},proc(x:int)->int{return x})',
            'basic.filter([]int{1},proc(x:int)->bool{return true})',
            'basic.fmap(proc(x:int)->bool{return true},[]int{1})',
            'basic.filter(proc(x:int)->int{return x},[]int{1})',
            'basic.fmap(proc(x:string)->string{return x},[]int{1})',
            'basic.filter(proc(x:string)->bool{return true},[]int{1})',
        ]:
            with self.subTest(expression=expression):
                self.invoke(self.program('_='+expression,'import "thor:basic"\n'),command='check',fail='Error:')

    def test_function_first_callbacks_cannot_write_through_readonly_nested_slices(self):
        for name,callback in [
            ('fmap','proc(x:[]int)->[]int{x[0]=9;return x}'),
            ('filter','proc(x:[]int)->bool{x[0]=9;return true}'),
        ]:
            with self.subTest(name=name):
                self.invoke(self.program('values :> [][]int{{1,2}}\n_=basic.'+name+'('+callback+',values)',
                                         'import "thor:basic"\n'),command='check',fail='read-only slice')

    def test_filter_order_empty_inputs_and_shallow_elements(self):
        self.invoke(self.program('''
data := []int{5, 2, 4, 1, 6}
even := proc(x: int)->bool { return x % 2 == 0 }
result := basic.filter(even, data)
defer delete(result)
assert(len(result)==3 && result[0]==2 && result[1]==4 && result[2]==6)
assert(data[0]==5 && data[1]==2)
none := basic.filter(proc(x:int)->bool{return false}, data)
empty := basic.filter(even, []int{})
defer delete(none); defer delete(empty)
assert(len(none)==0 && len(empty)==0)
words := []string{"oak", "elm"}
kept := basic.filter(proc(x:string)->bool{return true}, words)
defer delete(kept)
assert(raw_data(kept[0])==raw_data(words[0]))
''', 'import "thor:basic"\n'))

    def test_filter_and_range_use_the_supplied_allocator_without_bad_frees(self):
        self.invoke('''package basic_test
import "thor:basic"
import "core:testing"
import "core:mem"
// Forward allocations, but refuse shrinking so filter must copy to exact size.
no_shrink :: proc(data:rawptr, mode:mem.Allocator_Mode, size, alignment:int,
    old:rawptr, old_size:int, loc:=#caller_location)->([]byte,mem.Allocator_Error) {
    backing := (^mem.Allocator)(data)^
    if (mode == .Resize || mode == .Resize_Non_Zeroed) && size < old_size { return nil, .Out_Of_Memory }
    return backing.procedure(backing.data,mode,size,alignment,old,old_size,loc)
}
@(test) allocations :: proc(t:^testing.T) {
    backing := context.allocator
    allocator := mem.Allocator{no_shrink, &backing}
    data := []int{1,2,3,4,5}
    for mode in 0..<3 {
        kept := basic.filter(proc(x:int)->bool{return x % 2 == 0}, data, allocator)
        defer delete(kept,allocator)
        testing.expect(t,len(kept)==2 && kept[0]==2 && kept[1]==4)
        none := basic.filter(proc(x:int)->bool{return false},data,allocator)
        defer delete(none,allocator)
        all := basic.filter(proc(x:int)->bool{return true},data,allocator)
        defer delete(all,allocator)
    }
    values := basic.range(7,-3,-3,allocator)
    defer delete(values,allocator)
    testing.expect(t,len(values)==4 && values[3]==-2)
}
''',command='test',flags=('-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true',))

    def test_sum_numeric_types_and_empty_identity(self):
        self.invoke(self.program('''
Count :: distinct i64
assert(basic.sum([]int{1,-2,3})==2 && basic.sum([]int{})==0)
assert(basic.sum([]u128{1<<100,1<<100})==1<<101)
assert(basic.sum([]Count{2,3})==5)
assert(basic.sum([]f64{1.5,-0.25})==1.25)
assert(basic.sum([]complex128{1+2i,3-1i})==4+1i)
assert(basic.sum([]quaternion256{quaternion(real=1,imag=2,jmag=3,kmag=4),quaternion(real=2,imag=3,jmag=4,kmag=5)})==quaternion(real=3,imag=5,jmag=7,kmag=9))
''','import "thor:basic"\n'))

    def test_helper_type_constraints_reject_unsupported_arguments(self):
        for expression in ['basic.sum([]string{"x"})','basic.filter(7,[]int{1})',
                           'basic.range(1.5)','basic.chr(1.5)','basic.table([]int{1})',
                           'basic.random(string)','basic.ord(1.5)']:
            with self.subTest(expression=expression):
                self.invoke(self.program('_='+expression,'import "thor:basic"\n'),command='check',fail='Error:')

    def test_range_half_open_direction_and_integer_extremes(self):
        self.invoke(self.program('''
one := basic.range(4); defer delete(one)
two := basic.range(-2,3); defer delete(two)
up := basic.range(1,8,3); defer delete(up)
down := basic.range(8,1,-3); defer delete(down)
assert(len(one)==4 && one[0]==0 && one[-1]==3)
assert(len(two)==5 && two[0]==-2 && two[-1]==2)
assert(len(up)==3 && up[-1]==7 && len(down)==3 && down[-1]==2)
for value in ([]int{0,-1}) { empty:=basic.range(value);assert(len(empty)==0);delete(empty) }
for step in ([]int{-1,0,1}) { empty:=basic.range(2,2,step);assert(len(empty)==0);delete(empty) }
wrong := basic.range(1,5,-1); defer delete(wrong);assert(len(wrong)==0)
zero := basic.range(1,5,0); defer delete(zero);assert(len(zero)==0)
wide := basic.range(min(int),max(int),max(int)); defer delete(wide)
assert(len(wide)==3 && wide[0]==min(int) && wide[1]==-1 && wide[2]==max(int)-1)
back := basic.range(max(int),min(int),min(int)); defer delete(back)
assert(len(back)==2 && back[1]==-1)
n:u8=3
small := basic.range(n);defer delete(small);assert(len(small)==3)
''','import "thor:basic"\n'))

    def test_unicode_chr_ord_and_byte_semantics(self):
        self.invoke(self.program('''
assert(basic.chr(0)=='\\x00' && basic.chr(u128(0x10ffff))=='\\U0010ffff')
assert(basic.ord("é")==233 && basic.ord('😀')==0x1f600)
assert(basic.ord("\\ufffd")==0xfffd && basic.chr(basic.ord("界"))=='界')
assert(basic.ord([]u8{255})==255 && basic.ord([]u8{0})==0)
''','import "thor:basic"\n'))

    def test_unicode_rejects_invalid_scalars_and_non_single_inputs(self):
        for expression, message in [
            ('basic.chr(-1)','chr: code point'),
            ('basic.chr(u128(1)<<100)','chr: code point'),
            ('basic.chr(0xd800)','valid Unicode scalar'),
            ('basic.ord(rune(0xdfff))','valid Unicode scalar'),
            ('basic.ord("")','exactly one'),
            ('basic.ord("ab")','exactly one'),
            ('basic.ord("e\\u0301")','exactly one'),
            ('basic.ord("\\xff")','valid UTF-8'),
            ('basic.ord([]u8{1,2})','exactly one byte'),
            ('basic.ord([]u8{})','exactly one byte'),
        ]:
            with self.subTest(expression=expression):
                self.invoke(self.program('_='+expression,'import "thor:basic"\n'),fail=message)

    def test_random_typed_bounds_defaults_and_seeded_generator(self):
        self.invoke(self.program('''
state := rand.create(42)
gen := rand.default_random_generator(&state)
for _ in 0..<100 {
    x := basic.random(int,-3,3,gen); assert(x>=-3 && x<=3)
    y := basic.random(u128,0,max(u128),gen); _=y
    z := basic.random(i128,min(i128),max(i128),gen); _=z
    f := basic.random(f64,-1.0,1.0,gen);assert(f>=-1 && f<=1)
    full := basic.random(f64,gen=gen);assert(full>=min(f64) && full<=max(f64))
    half := basic.random(f16,0,1,gen);assert(half>=0 && half<=1)
}
assert(basic.random(int,0,0,gen)==0 && basic.random(f64,0,0,gen)==0)
assert(basic.random(int,7,7,gen)==7)
for _ in 0..<100 {
    nonnegative := basic.random(int,lo=0,gen=gen);assert(nonnegative>=0)
    nonpositive := basic.random(int,hi=0,gen=gen);assert(nonpositive<=0)
}
''','import "thor:basic"\nimport "core:math/rand"\n'))

    def test_table_tags_wrapping_numeric_and_unicode_alignment(self):
        self.invoke(self.program('''
Row :: struct { name:string `Item`, count:int `Qty`, price:f64 `Price` }
text:=basic.table([]Row{{"pear",2,2.5},{"apple",-12,-1.25}})
defer delete(text)
assert(text==`+-------+-----+-------+
| Item  | Qty | Price |
+-------+-----+-------+
| pear  |   2 |  2.50 |
| apple | -12 | -1.25 |
+-------+-----+-------+
`)
Word :: struct{word:string}
wrapped:=basic.table([]Word{{"é界e\\u0301😀"}},max_col_width=4)
defer delete(wrapped)
assert(wrapped==`+------+
| word |
+------+
| é界é |
|   😀 |
+------+
`)
empty:=basic.table([]Word{});defer delete(empty)
assert(empty==`+------+
| word |
+------+
`)
zero:=basic.table([]struct{}{{}});delete(zero);assert(zero=="")
''','import "thor:basic"\n'))

    def test_table_allocations_controls_narrow_columns_and_nested_structs(self):
        self.invoke('''package basic_test
import "thor:basic"
import "core:testing"
import "core:strings"
@(test) tables :: proc(t:^testing.T) {
    Amount :: distinct f64
    Row :: struct { name:string `LongHeader`, nested:struct{n:int}, number:Amount }
    rows:=[]Row{{"a\\tb\\r\\nc\\x1b",{7},1.25},{"👩‍💻",{8},-2.5}}
    text:=basic.table(rows,float_format="%.1f");defer delete(text)
    testing.expect(t,!strings.contains(text,"\\r") && !strings.contains(text,"\\t"))
    testing.expect(t,strings.contains(text,"\\\\x1b") && strings.contains(text,"👩‍💻"))
    for width in 1..<4 {
        narrow:=basic.table([]struct{v:string}{{"abcdef\\n"}},max_col_width=width)
        delete(narrow)
    }
}
''',command='test',flags=('-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true',))

    def test_deterministic_basic_helpers_with_fn_run_and_runtime_allocations(self):
        self.invoke('''package main
import * "thor:basic"
total :: fn(values:[]int)->int { return sum(values) }
TOTAL :: total([]int{1,2,3})
LETTER :: #run chr(0x754c)
CODE :: #run ord('😀')
main::proc(){
    assert(TOTAL==6 && LETTER=='界' && CODE==0x1f600)
    values:=range(4);defer delete(values)
    assert(len(values)==4 && values[-1]==3)
    runtime:=2
    other:=range(runtime);defer delete(other)
    assert(len(other)==2 && other[-1]==1)
}
''')

    def test_random_endpoints_and_explicit_zero_are_not_omitted_bounds(self):
        self.invoke('''package main
import "thor:basic"
import "base:runtime"
fixed :: proc(data:rawptr,mode:runtime.Random_Generator_Mode,p:[]byte) {
    assert(mode==.Read)
    value:=(^u64)(data)^
    for &b,i in p { b=u8(value >> uint(8*(i%8))) }
}
main::proc(){
    bits:u64=(1<<53)+1
    gen:=runtime.Random_Generator{fixed,&bits}
    assert(basic.random(f64,-5,7,gen)==-5)
    bits=1<<53
    assert(basic.random(f64,-5,7,gen)==7)
    bits=(1<<24)+1
    assert(basic.random(f32,-5,7,gen)==-5)
    bits=1<<24
    assert(basic.random(f32,-5,7,gen)==7)
    bits=(1<<11)+1
    assert(basic.random(f16,-5,7,gen)==-5)
    bits=1<<11
    assert(basic.random(f16,-5,7,gen)==7)
    bits=0
    assert(basic.random(i8,0,1,gen)==0)
    bits=1
    assert(basic.random(i8,0,1,gen)==1)
    assert(basic.random(int,0,0,gen)==0)
}
''')

    def test_random_invalid_bounds_and_table_widths_are_rejected(self):
        for expression, message in [
            ('basic.random(int,3,2)','lo must be less'),
            ('basic.random(f64,math.nan_f64(),1)','lo must be less'),
            ('basic.random(f64,0,math.inf_f64(1))','must be finite'),
            ('basic.table([]struct{v:string}{{"界"}},max_col_width=1)','narrower than'),
            ('basic.table([]struct{v:string}{{"a"}},max_col_width=-1)','must be nonnegative'),
        ]:
            with self.subTest(expression=expression):
                self.invoke(self.program('_='+expression,'import "thor:basic"\nimport "core:math"\n'),fail=message)

    def test_pure_boundaries_for_random_callbacks_reflection_and_utf8_tables(self):
        # Eligibility follows implementation effects, not a library allowlist.
        # These useful runtime helpers need capabilities excluded by fn/#run.
        for expression, message in [
            ('basic.random(int)','Pure'),
            ('basic.filter(proc(x:int)->bool{return true},[]int{1})','Pure'),
            ('basic.table([]struct{v:int}{{1}})','Pure'),
            ('basic.ord("😀")','runtime storage'),
        ]:
            with self.subTest(expression=expression):
                self.invoke('package main\nimport "thor:basic"\nVALUE::#run '+expression+'\nmain::proc(){_=VALUE}\n',
                            command='check',fail=message)


if __name__ == '__main__':
    unittest.main()

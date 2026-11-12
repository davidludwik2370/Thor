"""Read-only literal backing, with native and reference compiler parity."""
import json
import unittest
from tests import test_rust_compiler as native


class ReadonlyLiteralDataTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def run_both(self, source, *, static=None, global_count=None, flags=None, files=None):
        self.source.write_text(source)
        for name, content in (files or {}).items():
            path = self.app/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        for engine in ['rust', 'python']:
            for options in flags or [['-debug'], native.UNCHECKED]:
                with self.subTest(engine=engine, flags=options):
                    result = self.invoke(engine, flags=options)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stderr, '')
                    generated = (self.root/engine/'main.odin').read_text()
                    if static is not None:
                        self.assertEqual(generated.count('@(static, rodata)'), static, generated)
                    if global_count is not None:
                        self.assertEqual(generated.count('@(rodata)'), global_count, generated)

    def test_global_local_grouped_and_contextually_typed_literals(self):
        self.run_both(self.program('''
local :> []int{BASE + 1, -2, int(u8(3))}
left, right :> []int{4}, []int{5}
typed: []int > {6, 7}
alias :> Numbers{8, 9}
if global[0]!=1 || local[0]!=2 || local[1]!=-2 || local[2]!=3 || left[0]+right[0]!=9 ||
    typed[1]!=7 || alias[0]!=8 {panic("literal data")}
''', '''BASE :: 1
Numbers :: []int
global :> []int{BASE, 2, 3}
'''), static=4, global_count=1)

    def test_scalar_kinds_constants_and_native_constant_expressions(self):
        self.run_both(self.program('''
ints :> []int{len("abc"), max(2, 3), abs(-4), 1<<3}
floats :> []f64{PI, 1.5, f64(2)}
strings :> []string{"hello", "a" + "b", "x" * 3}
booleans :> []bool{true, !false, 2<3}
enums :> []Kind{.first, Kind.second}
if ints[0]!=3 || ints[1]!=3 || ints[2]!=4 || ints[3]!=8 || floats[0]!=PI ||
    strings[1]!="ab" || strings[2]!="xxx" || !booleans[2] || enums[1]!=.second {panic("constant kinds")}
''', '''PI :: 3.14159
Kind :: enum {first, second}
'''), static=5)

    def test_nested_literal_slices_inline_arrays_and_record_zero_initialization(self):
        self.run_both(self.program('''
rows :> []Row{{number=1, pair={2, 3}, items=[]int{4, 5}}}
arrays :> [][2]int{{6, 7}, PAIR}
nested :> [][]int{{8, 9}, {10}}
empty :> []int{}
row := rows[0]
row.number = 99
if rows[0].number!=1 || row.number!=99 || rows[0].pair[1]!=3 || rows[0].items[1]!=5 ||
    arrays[1][0]!=11 || nested[1][0]!=10 || len(empty)!=0 {panic("nested literals")}
''', '''PAIR :: [2]int{11, 12}
Row :: struct {number: int, pair: [2]int, items: []int, enabled: bool}
'''), static=4)

    def test_imported_constants_and_record_layouts(self):
        self.run_both(self.program('''
values :> []int{library.VALUE}
rows :> []library.Row{{value=library.VALUE}}
if values[0]!=7 || rows[0].value!=7 {panic("imported constants")}
''', 'import "library"\n'), static=2, files={'library/library.thor': '''package library
VALUE :: 7
Row :: struct {value: int}
'''})

    def test_runtime_values_and_views_keep_original_storage_and_evaluation(self):
        self.run_both(self.program('''
count := 0
x := 1
runtime :> []int{x, next(&count)}
frozen_value :> x
not_constant :> []int{frozen_value}
original := []int{1, 2}
view :> original
part :> original[1:]
returned :> borrow(original)
original[0] = 9
original[1] = 8
if runtime[0]!=1 || runtime[1]!=1 || count!=1 || not_constant[0]!=1 ||
    view[0]!=9 || part[0]!=8 || returned[1]!=8 {panic("runtime/view semantics")}
''', '''next :: proc(count: ^int) -> int {count^ += 1; return count^}
borrow :: proc(values: []int) -> []int {return values}
'''), static=0, global_count=0)

    def test_named_slice_constants_and_pointer_targets_are_not_frozen(self):
        self.run_both(self.program('''
mutable := SHARED
rows :> [][]int{SHARED}
mutable[0] = 9
node := Node{1}
pointers :> []^Node{&node}
pointers[0]^.value = 7
if mutable[0]!=9 || rows[0][0]!=1 || node.value!=7 {panic("aliases")}
''', '''SHARED :: []int{1, 2}
Node :: struct {value: int}
'''), static=0)

    def test_slice_to_string_cast_remains_a_runtime_view_conversion(self):
        self.run_both(self.program('''
bytes := []u8{65, 66}
strings :> []string{string(bytes)}
if strings[0]!="AB" {panic("slice conversion")}
'''), static=0)

    def test_raw_union_literals_keep_native_runtime_initialization(self):
        self.run_both(self.program('''
values :> []Value{{number=7}}
if values[0].number!=7 {panic("raw union")}
''', 'Value :: struct #raw_union {number: int, other: f64}\n'), static=0)

    def test_shadowed_builtins_are_not_executed_or_frozen(self):
        self.run_both(self.program('''
count:=0
values :> []int{abs(&count)}
if count!=1 || values[0]!=7 {panic("shadowed builtin")}
''', 'abs :: proc(count: ^int)->int {count^+=1; return 7}\n'), static=0)

    def test_native_runtime_operations_on_constant_contents_are_not_static_initializers(self):
        self.run_both(self.program('''
arrays :> [][2]int{[2]int{1,2} + [2]int{3,4}}
equal :> []bool{Row{1} == Row{1}}
swizzles :> [][2]int{[2]int{1,2}.yx}
lengths :> []int{len([]int{1,2})}
if arrays[0][0]!=4 || !equal[0] || swizzles[0][0]!=2 || lengths[0]!=2 {panic("runtime operations")}
''', 'Row :: struct {value: int}\n'), static=0)

    def test_expanding_generic_layout_proof_is_bounded_and_resets_per_declaration(self):
        typ = 'int'
        for _ in range(18):
            typ = f'Pair({typ})'
        self.source.write_text(self.program('values :> []Deep{}; small :> []int{1}; _=values; _=small',
            f'Pair :: struct($T: typeid) {{a, b: T}}\nDeep :: {typ}\n'))
        for engine in ['rust', 'python']:
            result = self.invoke(engine, command='transpile')
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            generated = (self.root/engine/'main.odin').read_text()
            self.assertEqual(generated.count('@(static, rodata)'), 1, generated)

    def test_mutable_literals_fixed_arrays_and_owned_values_keep_existing_lowering(self):
        self.run_both(self.program('''
mutable := []int{1, 2}
mutable[0] = 9
fixed :> [1, 2, 3]
copy := fixed
copy[0] = 9
big :> []Integer{Integer(123456789012345678901234567890)}
defer delete(big[0])
if fixed[0]!=1 || copy[0]!=9 || mutable[0]!=9 || big[0]!=Integer(123456789012345678901234567890) {panic("unchanged shapes")}
'''), static=0)

    def test_constant_data_inside_pure_functions_and_compile_time_execution(self):
        self.run_both(self.program('''
values :> []int{#run calculate()}
if calculate()!=9 || RESULT!=9 || values[0]!=9 {panic("pure literal")}
''', '''calculate :: fn() -> int {values :> []int{4, 5}; return values[0] + values[1]}
RESULT :: #run calculate()
'''), static=2)

    def test_compile_time_string_results_keep_owned_runtime_materialization(self):
        self.run_both(self.program('''
strings :> []string{#run text(), text()}
defer delete(strings[0])
defer delete(strings[1])
if strings[0]!="hello" || strings[1]!="hello" {panic("owned strings")}
''', 'text :: fn()->string {return "hello"}\n'), static=0)

    def test_branches_loops_and_generic_runtime_inputs(self):
        self.run_both(self.program('''
for i in 0..<3 {
    values :> []int{1, 2}
    runtime :> []int{i}
    if values[0]!=1 || runtime[0]!=i {panic("loop")}
}
when false {bad :> []int{unknown_name}}
if get(3)!=3 || get(f64(4))!=4 {panic("generic")}
''', '''get :: proc(value: $T) -> T {values :> []T{value}; return values[0]}
'''), static=1)

    def test_annotated_declarations_keep_explicit_storage_policy(self):
        self.run_both(self.program('''
@(static) values :> []int{1, 2}
if values[0]!=1 {panic("explicit storage")}
'''), static=0, global_count=0)

    def test_type_errors_retain_original_source_positions(self):
        text = self.program('// 😀 source mapping\nvalues :> []int{1, "wrong"}\n_=values')
        self.source.write_text(text)
        for engine in ['rust', 'python']:
            result = self.invoke(engine, command='check', flags=['-json-errors'])
            self.assertNotEqual(result.returncode, 0)
            errors = json.loads(result.stderr)['errors']
            relevant = [error for error in errors if 'wrong' in json.dumps(error)]
            self.assertTrue(relevant, errors)
            self.assertTrue(all(error['pos']['file'] == str(self.source) for error in relevant), errors)
            self.assertTrue(all(error['pos']['line'] == 5 for error in relevant), errors)
            self.assertNotIn('must have constant initialization', result.stderr)
            self.assertNotIn('static variable declaration', result.stderr)


if __name__ == '__main__':
    unittest.main()

"""Migrated build policies survive vet's fn/immutable source corrections."""
from tests.test_rust_compiler import compiler_paths
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest

from tests import test_migrate as support
from tests.test_native_vet import VIOLATIONS


class NativePolicyProjectionTests(unittest.TestCase):
    setUpClass = classmethod(support.MigrationTests.setUpClass.__func__)
    setUp = support.MigrationTests.setUp
    write = support.MigrationTests.write

    def call(self, *arguments, status=0, env=None):
        result = subprocess.run([str(support.COMPILER), *map(str, arguments)],
            cwd=self.root, capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        return result

    def test_migrated_build_and_test_commands_survive_apply_checked_and_unchecked(self):
        self.write('''package main
import "core:fmt"
double :: proc(value: int) -> int {
\treturn value*2
}
main :: proc() {
\tresult := double(7)
\tfmt.println(result)
}
''', {'main_test.odin': '''package main
import "core:testing"
@(test) smoke :: proc(t: ^testing.T) {
\ttesting.expect(t, double(7) == 14)
}
'''})
        flags = ['-vet', '-vet-tabs', '-strict-style', '-thread-count:1']
        commands = [shlex.join(['odin', action, str(self.source), *flags]) for action in ['build', 'test']]
        migrated = self.call('migrate', self.source, '--output-dir', self.output.parent,
            *[argument for command in commands for argument in ['--command', command]])
        corrected = self.call('vet', self.output, '--apply', '--odin-compatible', *flags)
        text = (self.output/'main.thor').read_text()
        self.assertIn('double :: fn', text)
        self.assertIn('result :>', text)
        self.assertIn('Applied', corrected.stdout + corrected.stderr)
        for command in migrated.stdout.splitlines():
            arguments = shlex.split(command)[1:]
            for configuration in [[], support.UNCHECKED]:
                self.call(*arguments, *configuration)
                if arguments[0] == 'build':
                    run = subprocess.run([str(self.root/'sample')], capture_output=True, text=True, timeout=10)
                    self.assertEqual((run.returncode, run.stdout), (0, '14\n'), run.stderr)

    def test_native_policy_errors_still_match_the_original_sources(self):
        for policy, (source, fragment) in VIOLATIONS.items():
            with self.subTest(policy=policy):
                self.write(source)
                target = self.source/'main.thor'
                (self.source/'main.odin').rename(target)
                scope=['-vet-packages:main'] if policy=='vet-unused-procedures' else []
                result = self.call('check', self.source, '--odin-compatible', '-'+policy,
                    *scope, '-thread-count:1', '-json-errors', status=1)
                errors = json.loads(result.stderr)['errors']
                self.assertTrue(any(fragment in ' '.join(error['msgs']).lower() for error in errors), errors)
                self.assertTrue(any(error.get('pos', {}).get('file') == str(target) for error in errors), errors)
                target.unlink()

    def test_projection_maps_columns_after_fn_edits_and_unicode(self):
        self.write('package main\nmain::proc(){f::fn()->int{return 7}; x:=f(); _=cast(int)x} // 🌩\n')
        target = self.source/'main.thor'
        (self.source/'main.odin').rename(target)
        result = self.call('check', self.source, '--odin-compatible', '-vet-cast', '-thread-count:1', '-json-errors', status=1)
        errors = json.loads(result.stderr)['errors']
        cast = next(error for error in errors if 'cast' in ' '.join(error['msgs']).lower())
        text = target.read_text()
        expected = text.index('cast(int)') + len('cast(int)')
        self.assertEqual(cast['pos']['file'], str(target))
        self.assertEqual(cast['pos']['offset'], expected)
        self.assertEqual(cast['pos']['line'], 2)

    def test_readonly_and_purity_checks_remain_enforced(self):
        sources = [
            ('package main\nmain::proc(){values:>[]int{1,2};values[0]=7}\n', 'read-only'),
            ('package main\nx:=7\nf::fn()->int{return x}\nmain::proc(){_=f()}\n', 'outside the function'),
        ]
        for source, fragment in sources:
            with self.subTest(fragment=fragment):
                self.write(source)
                target = self.source/'main.thor'
                (self.source/'main.odin').rename(target)
                result = self.call('check', self.source, '--odin-compatible', '-vet', '-thread-count:1', status=1)
                self.assertIn(fragment, result.stderr.lower())
                target.unlink()

    def test_package_filters_apply_to_the_source_policy_pass(self):
        self.write('package main\nf::fn(x:int)->int{return x+1}\nmain::proc(){\n    _=f(1)\n}\n')
        target = self.source/'main.thor'
        (self.source/'main.odin').rename(target)
        self.call('check', self.source, '--odin-compatible', '-vet-tabs', '-vet-packages:other', '-thread-count:1')

    def test_vet_can_correct_test_declarations_without_executing_tests(self):
        self.write('package sample\n', {'sample_test.odin': '''package sample
import "core:testing"
triple :: proc(x: int) -> int { return x * 3 }
@(test) fails_when_run :: proc(t: ^testing.T) {
    value := triple(7)
    testing.expect(t, value == 0)
}
'''})
        for path in self.source.glob('*.odin'):
            path.rename(path.with_suffix('.thor'))
        result = self.call('vet', self.source, '-build-mode:test', '--apply', '-thread-count:1')
        self.assertIn('Applied', result.stdout)
        corrected = (self.source/'sample_test.thor').read_text()
        self.assertIn('triple :: fn', corrected)
        self.assertIn('value :>', corrected)
        self.call('test', self.source, '-thread-count:1', status=1)

    def test_vet_test_validation_retains_imported_package_discovery(self):
        self.write('''package sample
import "checks"
triple :: proc(x:int)->int {return x*3}
''', {'checks/main.odin':'''package checks
import "core:testing"
@(test) imported :: proc(t:^testing.T) {testing.expect(t,true)}
'''})
        for path in list(self.source.rglob('*.odin')):
            path.rename(path.with_suffix('.thor'))
        for compiler in compiler_paths(support.COMPILER):
            with self.subTest(compiler=compiler):
                source=self.source/'main.thor'
                source.write_text(source.read_text().replace('triple :: fn','triple :: proc'))
                result=subprocess.run([str(compiler),'vet',str(source),'-file','-build-mode:test',
                    '-all-packages','--apply','-thread-count:1'],cwd=self.root,
                    capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('triple :: fn',source.read_text())
        self.call('test',self.source/'main.thor','-file','-all-packages','-thread-count:1')

    def test_native_collection_contexts_and_pointer_index_types(self):
        source = '''package main
import "core:fmt"
import "core:reflect"
Choice :: union {int, string}
List :: [dynamic]Choice
Flags :: bit_set[enum {A, B}]
Pair :: struct {first, second: int}
Kind :: enum {Integer, Rational}
Labels :: struct {Integer, Rational: int}
finish :: proc() { fmt.println("deferred") }
@(deferred_none=finish) trace :: proc(name: string) {}
main :: proc() {
    kind := Kind.Integer
    labels := Labels{Integer=7, Rational=9}
    if kind != .Integer || labels.Rational != 9 { panic("native member names") }
    trace(#procedure + " trace")
    values: List
    append(&values, Choice(7))
    defer delete(values)
    pointer := &values
    value, ok := pointer[0].(int)
    if !ok || value != 7 { panic("pointer indexing") }
    table := make(map[string]int)
    table["key"] = 9
    defer delete(table)
    if "key" not_in &table { panic("pointer membership") }
    fields := reflect.struct_fields_zipped(Pair)
    index := 1
    fmt.println(fields[index].name)
    flags: Flags = {.A}
    if flags & {.A} != {} { flags = (flags - {.A}) + {.B} }
    if .B not_in flags { panic("bit set context") }
    fmt.println(value, table["key"])
}
'''
        self.write(source)
        binary = self.root/'oracle'
        for flags in [[], support.UNCHECKED]:
            built = subprocess.run(['odin', 'build', str(self.source), '-thread-count:1',
                *flags, '-out:'+str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            expected = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            target = self.source/'main.thor'
            (self.source/'main.odin').rename(target)
            try:
                self.call('build', self.source, '-thread-count:1', *flags, '-out:'+str(binary))
                actual = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual((actual.returncode, actual.stdout), (expected.returncode, expected.stdout), actual.stderr)
                self.assertEqual(actual.stdout, 'second\n7 9\ndeferred\n')
            finally:
                target.rename(target.with_suffix('.odin'))

    def test_lowered_helpers_preserve_unchecked_procedures_and_statements(self):
        self.write('''package main
fill :: proc(values: []int, index: int) #no_bounds_check { values[index] = 7 }
fill_generic :: proc(values: ^$T, index: int) { #no_bounds_check values[index] = 13 }
main :: proc() {
    storage := make([]int, 4)
    defer delete(storage)
    empty := storage[:0]
    fill(empty, 2)
    index := 3
    #no_bounds_check { empty[index] = 9 }
    fill_generic(&empty, 1)
    if storage[1] != 13 || storage[2] != 7 || storage[3] != 9 { panic("unchecked storage") }
}
''')
        binary = self.root/'program'
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[], support.UNCHECKED]:
            self.call('build', self.source, '-thread-count:1', *flags, '-out:'+str(binary))
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_soa_column_multipointers_keep_native_indexing(self):
        self.write('''package main
Row :: struct {number: int}
main :: proc() {
    rows: #soa[2]Row
    rows[0].number = 7
    rows[1].number = 9
    view := rows[:]
    columns := view.number
    index := 1
    columns[index] = 11
    if rows[1].number != 11 { panic("column identity") }
}
''')
        binary = self.root/'program'
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[], support.UNCHECKED]:
            self.call('build', self.source, '-thread-count:1', *flags, '-out:'+str(binary))
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_unrolled_bindings_soa_row_addresses_and_pointer_assertions(self):
        self.write('''package main
Choice :: union {int, string}
Row :: struct {number: int}
Shard :: struct {metadata: []#soa[]Row}
update :: proc(s: ^Shard, i, j: int) {
    columns := s.metadata[i].number[:]
    columns[j] = 11
    s.metadata[i][j].number = 13
}
main :: proc() {
    values: [4]int
    #unroll for index in 0..<4 { values[index] = index+1 }
    if values[3] != 4 { panic("unroll") }
    rows: #soa[2]Row
    slots := [1]#soa[]Row{rows[:]}
    shard := Shard{slots[:]}
    update(&shard, 0, 1)
    if rows[1].number != 13 { panic("soa row identity") }
    choices := [1]Choice{int(7)}
    index := 0
    if value, ok := &choices[index].(int); ok { value^ = 9 }
    if value, ok := choices[index].(int); !ok || value != 9 { panic("pointer assertion") }
}
''')
        binary = self.root/'program'
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[], support.UNCHECKED]:
            self.call('build', self.source, '-thread-count:1', *flags, '-out:'+str(binary))
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_enum_indices_generic_swizzles_and_untyped_constants(self):
        self.write('''package main
import "thor:sets"
Index :: enum {first, second}
mask :: 3
update :: proc(vector: ^$V) { vector.xy += [2]f32{2, 3} }
main :: proc() {
    vector := [3]f32{1, 2, 3}
    update(&vector)
    if vector.x != 3 || vector.y != 5 { panic("swizzle") }
    values := [2]int{7, 9}
    index := Index.second
    if values[index] != 9 { panic("enum index") }
    bits := u64(7)
    if bits & mask != 3 { panic("constant context") }
    owned: sets.Set(int)
    sets.insert(&owned, 7)
    defer sets.destroy(owned)
    if sets.count(owned) != 1 { panic("set activation") }
}
''')
        binary = self.root/'program'
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[], support.UNCHECKED]:
            self.call('build', self.source, '-thread-count:1', *flags, '-out:'+str(binary))
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_separate_frontend_builder_preserves_backend_sdk_selection(self):
        import shutil
        native = Path(shutil.which('odin')).resolve()
        sdk = Path(subprocess.check_output([str(native), 'root'], text=True).strip())
        selected = self.root/'selected-sdk'
        selected.mkdir()
        for name in ['core', 'base', 'vendor']:
            (selected/name).symlink_to(sdk/name, target_is_directory=True)
        wrapper = self.root/'backend'
        wrapper.write_text('#!/bin/sh\n'
            'if [ "$1" = build ] && [ "$2" = '+shlex.quote(str(support.ROOT/'frontend'))+' ]; then exit 91; fi\n'
            'export ODIN_ROOT='+shlex.quote(str(selected))+'\n'
            'exec '+shlex.quote(str(native))+' "$@"\n')
        wrapper.chmod(0o755)
        self.write('package main\nimport "core:fmt"\nmain::proc(){fmt.println(7)}\n')
        (self.source/'main.odin').rename(self.source/'main.thor')
        environment = {**os.environ, 'THOR_FRONTEND_ODIN': str(native)}
        self.call('check', self.source, '--odin', wrapper, '-thread-count:1', env=environment)
        packet = json.loads(self.call('ast', self.source, '--odin', wrapper, env=environment).stdout)
        self.assertEqual(Path(packet['sources'][0]['frontend']['odin_root']).resolve(), selected.resolve())

    def test_native_map_keys_soa_columns_and_assignment_control_flow(self):
        self.write('''package main
import "base:intrinsics"
Key :: struct {a, b: int}
Row :: struct {value: f32}
read :: proc() -> (int, bool) { return 7, true }
update :: proc(values: $T, key: int) -> bool {
    values := values
    #no_bounds_check values[key] = read() or_return
    return true
}
check_rows :: proc(rows: #soa[]Row, first: int) {
    column := rows.value[first:]
    pointer := cast(^f32)column
    if pointer^ != 9 { panic("SOA column shape") }
}
main :: proc() {
    values := make([]int, 4)
    defer delete(values)
    if !update(values, 2) || values[2] != 7 { panic("or_return assignment") }
    table := make(map[Key][]int)
    defer delete(table)
    data := [1]int{11}
    table[{1, 2}] = data[:]
    if table[{1, 2}][0] != 11 { panic("implicit map key") }
    rows: #soa[2]Row
    rows[1].value = 9
    check_rows(rows[:], 1)
}
''')
        binary = self.root/'program'
        native = subprocess.run(['odin', 'run', str(self.source), '-thread-count:1', '-out:'+str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(native.returncode, 0, native.stderr)
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[], support.UNCHECKED]:
            self.call('build', self.source, '-thread-count:1', *flags, '-out:'+str(binary))
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_generic_assignments_preserve_storage_order_and_early_returns(self):
        self.write('''package main
import "core:fmt"
Box :: struct($T:typeid) {values:T}
read :: proc(success:bool) -> (int,bool) {fmt.print("right ");return 9,success}
key :: proc() -> int {fmt.print("left ");return -1}
set_value :: proc(box:^Box($T), success:bool) -> bool {
    box.values[key()] = read(success) or_return
    return true
}
set_map :: proc(box:^Box($T)) -> bool {
    index:=3
    box.values[index] = read(true) or_return
    return true
}
main :: proc() {
    data := [3]int{1,2,3}
    box := Box([]int){data[:]}
    if set_value(&box,false) {panic("early return")}
    if !set_value(&box,true) || data[2]!=9 {panic("slice storage")}
    table := Box(map[int]int){}
    defer delete(table.values)
    if !set_map(&table) || table.values[3]!=9 {panic("map header")}
    fixed := Box([3]int){data}
    if !set_value(&fixed,true) || fixed.values[2]!=9 {panic("array storage")}
    fmt.println("done")
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        binary = self.root/'program'
        for flags in [[], support.UNCHECKED]:
            self.call('build', self.source, '-thread-count:1', *flags, '-out:'+str(binary))
            run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual((run.returncode,run.stdout),(0,'left right left right right left right done\n'),run.stderr)

    def test_platform_excluded_packages_keep_their_native_directory(self):
        self.write('''package main
@(require) import "platform"
main :: proc() {}
''', {'platform/platform.odin':'#+build windows\npackage platform\nvalue :: proc() -> int {return 7}\n'})
        self.call('migrate',self.source,'--output-dir',self.output.parent,
            '--command',shlex.join(['odin','build',str(self.source),'-thread-count:1']))
        self.call('build',self.output,'-thread-count:1','-out:'+str(self.root/'program'))

    def test_native_policy_checking_does_not_inject_platform_memory_tracking(self):
        self.write('package main\nbump::proc(x:int)->int{return x+1}\nmain::proc(){_=bump(7)}\n')
        (self.source/'main.odin').rename(self.source/'main.thor')
        output=self.root/'lowered'
        self.call('check',self.source,'--odin-compatible','-target:js_wasm32','-vet',
            '-thread-count:1','--output-dir',output)
        generated=''.join(path.read_text() for path in output.rglob('*.odin'))
        self.assertNotIn('memory_start',generated)
        self.assertNotIn('memory_map',generated)

    def test_migrated_native_file_policy_exceptions_survive_source_checks(self):
        self.write('''#+vet !shadowing
package main
main :: proc() {
    value := 1
    { value := value + 1; _ = value }
    _ = value
}
''')
        self.call('migrate',self.source,'--output-dir',self.output.parent,
            '--command',shlex.join(['odin','build',str(self.source),'-vet','-thread-count:1']))
        text=(self.output/'main.thor').read_text()
        self.assertIn('// thor-migrate #+vet !shadowing',text)
        self.call('vet',self.output,'--apply','-thread-count:1')
        self.call('check',self.output,'--odin-compatible','-vet','-thread-count:1')
        path=self.output/'main.thor'
        path.write_text(path.read_text().replace('// thor-migrate #+vet','// Odin vet'))
        self.call('check',self.output,'--odin-compatible','-vet','-thread-count:1')

    def test_generic_do_assignments_and_implicit_map_keys(self):
        self.write('''package main
Box :: struct($T:typeid) {values:T}
Key :: struct {a,b:int}
read :: proc() -> (int,bool) {return 7,true}
fill :: proc(box:^Box($T)) -> bool {
    for i in 0..<len(box.values) do box.values[i]=read() or_return
    if len(box.values)>0 do box.values[0]=9
    return true
}
insert :: proc(box:^Box($T)) -> bool {
    box.values[{1,2}]=read() or_return
    return true
}
main :: proc() {
    a:=Box([]int){make([]int,3)}
    defer delete(a.values)
    if !fill(&a)||a.values[0]!=9||a.values[2]!=7 {panic("do assignment")}
    m:=Box(map[Key]int){}
    defer delete(m.values)
    if !insert(&m)||m.values[{1,2}]!=7 {panic("implicit map key")}
}
''')
        binary=self.root/'program'
        native=subprocess.run(['odin','run',str(self.source),'-thread-count:1','-out:'+str(binary)],capture_output=True,text=True,timeout=60)
        self.assertEqual(native.returncode,0,native.stderr)
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[], support.UNCHECKED]:
            self.call('build',self.source,'-thread-count:1',*flags,'-out:'+str(binary))
            run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)

    def test_generic_multiple_assignment_captures_all_targets_before_rhs(self):
        self.write('''package main
Box :: struct($T:typeid) {values:T}
Cell :: struct {value:int}
Packed :: bit_field u8 {value:u8|4}
Nested :: struct {values:[2]int}
first, second:int
current:^int
cells:[2]Cell
selected:^Cell
phase:int
point :: proc()->^int {return current}
index :: proc()->int {current=&second;return 0}
cell :: proc()->^Cell {return selected}
change_cell :: proc()->int {selected=&cells[1];return 0}
pair :: proc()->(int,int,bool) {return 7,9,true}
assign :: proc(box:^Box($T))->bool {
    point()^,box.values[index()]=pair() or_return
    return true
}
assign_field :: proc(box:^Box($T))->bool {
    cell().value,box.values[change_cell()]=pair() or_return
    return true
}
assign_packed :: proc(box:^Box($T),packed:^Packed,vector:^[2]int) {
    packed.value,box.values[index()]=3,4
    vector.x,box.values[index()]=5,6
    vector.xy,box.values[index()]={7,8},9
}
collection :: proc(box:^Box($T))->T {phase=1;return box.values}
keys :: proc()->[1]int {
    if phase!=1 {panic("collection must precede index expression")}
    phase=2
    return {0}
}
assign_nested_key :: proc(box:^Box($T)) {
    collection(box)[keys()[0]]=8
}
assign_nested_storage :: proc(rows:$T,i,j:int) {rows[i].values[j]=12}
main :: proc() {
    box:=Box([]int){make([]int,1)}
    defer delete(box.values)
    current=&first
    if !assign(&box)||first!=7||second!=0||box.values[0]!=9 {panic("assignment target order")}
    selected=&cells[0]
    if !assign_field(&box)||cells[0].value!=7||cells[1].value!=0 {panic("field target order")}
    packed:Packed
    vector:[2]int
    assign_packed(&box,&packed,&vector)
    if packed.value!=3||vector.x!=7||vector.y!=8||box.values[0]!=9 {panic("packed field targets")}
    assign_nested_key(&box)
    if phase!=2||box.values[0]!=8 {panic("nested key evaluation")}
    rows:=[1]Nested{{[2]int{1,2}}}
    assign_nested_storage(rows[:],0,1)
    if rows[0].values[1]!=12 {panic("nested backing storage")}
}
''')
        binary=self.root/'program'
        native=subprocess.run(['odin','run',str(self.source),'-thread-count:1','-out:'+str(binary)],capture_output=True,text=True,timeout=60)
        self.assertEqual(native.returncode,0,native.stderr)
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[], support.UNCHECKED]:
            self.call('build',self.source,'-thread-count:1',*flags,'-out:'+str(binary))
            run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)

    def test_vet_keeps_corrections_valid_for_inactive_configurations_and_platforms(self):
        self.write('''package main
helper :: proc(values:[]int) {
    when #config(WRITE,false) {values[0]=9} else {_=values[0]}
}
wrapper :: proc(values:[]int) {helper(values)}
main :: proc() {
    values:=[]int{1}
    wrapper(values)
    size:=len(values)
    if size!=1 {panic("size")}
}
''')
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        self.call('vet',self.source,'--apply','-define:WRITE=false','-thread-count:1')
        text=path.read_text()
        self.assertIn('values:=[]int',text)
        self.assertIn('wrapper :: proc',text)
        self.assertIn('helper :: proc',text)
        self.assertIn('size:>',text.replace(' ',''))
        for value in ['false','true']:
            for flags in [[],support.UNCHECKED]:
                self.call('run',self.source,'-define:WRITE='+value,'-thread-count:1',*flags)
        path.write_text('package main\nwrapper::proc()->int{return platform_value()}\nmain::proc(){_=wrapper()}\n')
        (self.source/'platform_posix.thor').write_text('#+build !windows\npackage main\nplatform_value::proc()->int{return 7}\n')
        (self.source/'platform_windows.thor').write_text('package main\nplatform_value::proc()->int{return context.user_index}\n')
        self.call('vet',self.source,'--apply','-thread-count:1')
        self.assertIn('wrapper::proc',path.read_text())
        result=subprocess.run([str(support.COMPILER),'check',str(self.source),'-thread-count:1','-target:windows_amd64'],
            cwd=self.root,capture_output=True,text=True,timeout=120)
        if result.returncode and '-windows-sdk-root:' in result.stderr:
            self.skipTest('selected Odin requires a Windows SDK for cross-target checking')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_vet_keeps_generic_reference_locals_valid_for_other_entry_points(self):
        self.write('''package shared
mutate :: proc(values:[]$T) {values[0]=9}
measure :: proc(values:[]$T)->int {
    alias:=values[:]
    size:=len(alias)
    when T==f32 {mutate(alias)}
    return size
}
exercise :: proc()->int {values:=[1]f64{4};return measure(values[:])}
''',{'client/main.odin':'''package main
import shared ".."
main :: proc() {
    values:=[1]f32{2}
    length:=shared.measure(values[:])
    if length!=1 || values[0]!=9 {panic("generic write")}
}
'''})
        for path in list(self.source.rglob('*.odin')):
            path.rename(path.with_suffix('.thor'))
        self.call('vet',self.source,'--apply','-no-entry-point','-thread-count:1')
        text=(self.source/'main.thor').read_text().replace(' ','')
        self.assertIn('alias:=values[:]',text)
        self.assertIn('size:>len(alias)',text)
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source/'client','-thread-count:1',*flags)

    def test_vet_rejects_readonly_corrections_in_one_batch_with_shifted_unicode_offsets(self):
        rows='\n'.join(f'values_{i}:=[]int{{1}};change(values_{i});if values_{i}[0]!=9{{panic("mutation")}}' for i in range(16))
        self.write('''package main
// 🌩 positions use byte offsets
change :: proc(values:[]int) {values[0]=9}
twice :: proc(value:int)->int {return value*2}
main :: proc() {
    keep:=twice(3)
    if keep!=6 {panic("scalar")}
'''+rows+'\n}\n')
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        profile=self.root/'profile.json'
        self.call('vet',self.source,'--apply','-thread-count:1',
            env={**os.environ,'THOR_PROFILE':str(profile)})
        text=path.read_text()
        self.assertIn('twice :: fn',text)
        self.assertIn('keep:>',text.replace(' ',''))
        self.assertEqual(text.count(':=[]int'),16,text)
        counters=json.loads(profile.read_text())['counters']
        self.assertGreaterEqual(counters.get('vet.readonly_corrections_rejected',0),16,counters)
        self.assertLessEqual(counters.get('vet.validation_checks',0),8,counters)
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)

    def test_reference_free_unions_remain_copies_through_opaque_calls_and_sorting(self):
        self.write('''package main
import "core:slice"
Value :: union {int,string}
Prop :: struct {name:string,value:Value}
inspect :: proc(value:any) {_ = value}
main :: proc() {
    initial:Value=7
    frozen :> initial
    inspect(frozen)
    props:=[2]Prop{{"b",frozen},{"a",Value("text")}}
    slice.stable_sort_by(props[:],proc(a,b:Prop)->bool{return a.name<b.name})
    if props[0].name!="a" {panic("sort")}
}
''')
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)
        valid=path.read_text()
        path.write_text(valid+'\nbad :: fn(value:Value)->int {switch item in value {case string:delete(item)};return 0}\n')
        failed=self.call('check',self.source,'-thread-count:1',status=1)
        self.assertIn('borrow',failed.stderr.lower())
        path.write_text(valid.replace('union {int,string}','union {int,string,[]int}'))
        failed=self.call('check',self.source,'-thread-count:1',status=1)
        self.assertIn('read-only slice',failed.stderr)

    def test_mutable_native_callback_results_survive_unrelated_immutable_values(self):
        self.write('''package main
Slot :: struct {callback:proc()->[]int}
create :: proc()->[]int {return make([]int,1)}
pair :: proc()->(int,bool) {return 7,true}
label :: proc()->[]u8 {value,_ :> pair();_ = value;return make([]u8,1)}
main :: proc() {
    probe:=label()
    defer delete(probe)

    sentinel :> 7
    local :> []int{1}
    if len(local)!=1 {panic("local view")}
    slot:=Slot{create}
    data:=slot.callback()
    defer delete(data)
    data[0]=sentinel
    if data[0]!=7 {panic("native callback permissions")}
}
''')
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)
        for declaration, expression in [
            ('values :> []int{1}', 'values'),
            ('values :> [1]int{1}', 'values[:]'),
            ('values :> struct{items:[1]int}{[1]int{1}}', 'values.items[:]'),
        ]:
            for local in [False, True]:
                with self.subTest(declaration=declaration, local=local):
                    global_declaration = '' if local else declaration
                    local_declaration = declaration+';' if local else ''
                    path.write_text(f'''package main
{global_declaration}
reader :: proc()->[]int {{ {local_declaration}return {expression} }}
main :: proc() {{
    callback:=reader
    result:=callback()
    result[0]=9
}}
''')
                    failed=self.call('check',self.source,'-thread-count:1',status=1)
                    self.assertIn('read-only slice',failed.stderr)

    def test_vet_preserves_writable_returned_storage_for_other_clients(self):
        self.write('''package shared
make_values :: proc()->[]int {
    values:=make([]int,1)
    values[0]=7
    return values
}
''', {'client/main.odin':'''package main
import shared ".."
main :: proc() {
    values:=shared.make_values()
    defer delete(values)
    values[0]=9
    if values[0]!=9 {panic("writable library result")}
}
'''})
        for path in list(self.source.rglob('*.odin')):
            path.rename(path.with_suffix('.thor'))
        self.call('vet',self.source,'--apply','-no-entry-point','-thread-count:1')
        text=(self.source/'main.thor').read_text()
        self.assertIn('values:=make([]int,1)',text)
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source/'client','-thread-count:1',*flags)

    def test_complex_and_quaternion_results_are_reference_free_values(self):
        self.write('''package main
import "core:math/cmplx"
import "core:math/linalg"
consume :: proc(value:any) {_ = value}
main :: proc() {
    value64 :> cmplx.pow(complex64(1),complex64(2))
    value128 :> cmplx.pow(complex128(1),complex128(2))
    rotation :> linalg.quaternion_angle_axis(f64(3),linalg.Vector3f64{1,0,0})
    consume(value64)
    consume(value128)
    consume(rotation)
    copy64,copy128,copy_rotation := value64,value128,rotation
    copy64=3
    copy128=4
    copy_rotation=0
    if value64!=1||value128!=1||rotation==0||copy64!=3||copy128!=4||copy_rotation!=0 {
        panic("numeric value copies")
    }
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)

    def test_vet_rejects_impure_wrappers_as_a_batch_and_keeps_local_mutation(self):
        source='''package main
// 🌩 Earlier fn edits shift the later diagnostic offsets.
one :: proc(n:int)->int {return n+1}
two :: proc(n:int)->int {return n+2}
write :: proc(values:[]int) {values[0]=9}
peek :: proc(value:^int)->int {return value^}
read_slice :: proc(values:[]int)->int {return peek(&values[0])}
first :: proc(values:[]int) {write(values)}
second :: proc(values:[]int) {write(values)}
third :: proc(values:[]int) {write(values)}
local :: proc()->int {values:=[1]int{1};write(values[:]);return values[0]}
nested :: proc()->int {
    inner :: proc(values:[]int) {values[0]=8}
    values:=[1]int{1}
    inner(values[:])
    return values[0]
}
main :: proc() {
    values:=[1]int{1}
    first(values[:]);second(values[:]);third(values[:])
    if one(1)!=2||two(1)!=3||local()!=9||nested()!=8||read_slice(values[:])!=9||values[0]!=9 {panic("vet purity selection")}
}
'''
        self.write(source)
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        for compiler in compiler_paths(support.COMPILER):
            with self.subTest(compiler=compiler):
                path.write_text(source)
                profile=self.root/'profile.json'
                result=subprocess.run([str(compiler),'vet',str(self.source),'--vet=pure','--apply','-thread-count:1'],
                    cwd=self.root,capture_output=True,text=True,timeout=90,
                    env={**os.environ,'THOR_PROFILE':str(profile)})
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                text=path.read_text()
                for name in ('one','two','local','read_slice','nested'):self.assertIn(name+' :: fn',text)
                for name in ('write','first','second','third','inner'):self.assertIn(name+' :: proc',text)
                if compiler==support.COMPILER:
                    counters=json.loads(profile.read_text())['counters']
                    # Odin can stop at the first failing compile-time panic.
                    # Each rejection then needs one check, plus the final batch.
                    self.assertLessEqual(counters.get('vet.validation_checks',0),4,counters)
                    self.assertEqual(counters.get('vet.pure_corrections_rejected',0),3,counters)
                for flags in [[],support.UNCHECKED]:
                    self.call('run',self.source,'-thread-count:1',*flags)

    def test_runtime_indices_resolve_native_namespace_arrays(self):
        self.write('''package main
import "core:crypto/ecdsa"
import "core:encoding/base64"
main :: proc() {
    curves:=[]struct{curve:ecdsa.Curve}{{.SECP256R1}}
    for value in curves {
        size:=ecdsa.PRIVATE_KEY_SIZES[value.curve]
        if size!=32 {panic("native enum array")}
    }
    index:=0
    expected:=base64.ENC_TABLE[0]
    if base64.ENC_TABLE[index]!=expected {panic("native numeric array")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)

    def test_nested_map_assignment_updates_the_stored_header(self):
        self.write('''package main
State :: struct {env:[]map[string]int}
main :: proc() {
    state:=State{make([]map[string]int,1)}
    defer delete(state.env)
    defer delete(state.env[0])
    key:="key"
    state.env[len(state.env)-1][key]=9
    if state.env[0][key]!=9||len(state.env[0])!=1 {panic("stored map header")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)

    def test_contextual_literals_do_not_name_another_files_private_type(self):
        self.write('''package main
Context :: struct($T:typeid) {value:T}
Payload :: struct($T:typeid) {value:T,number:int}
number :: fn()->int {return 7}
consume :: proc(ctx:^Context($T),payload:Payload(T))->int {return payload.number}
produce :: proc(ctx:^Context($T))->int {return consume(ctx,{value=ctx.value,number=number()})}
''', {'client.odin':'''package main
@(private="file") Private :: distinct int
main :: proc() {
    ctx:=Context(Private){Private(3)}
    if produce(&ctx)!=7 {panic("private generic type")}
}
'''})
        for path in list(self.source.glob('*.odin')):
            path.rename(path.with_suffix('.thor'))
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)

    def test_pure_native_overloads_keep_typed_arithmetic_operands(self):
        self.write('''package main
import "core:math"
curve :: fn(value:f32)->f32 {return math.pow((value+0.055)/1.055,2.4)}
half :: fn(value:$T)->T {return value/2}
first :: fn(values:$S/[]$T)->T {return values[0]}
main :: proc() {
    x:f32=0.5
    expected:=math.pow((x+0.055)/1.055,2.4)
    values:=[2]f32{7,9}
    if curve(x)!=expected||half(f32(7))!=3.5||first(values[:])!=7 {panic("typed float operands")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)

    def test_soa_array_rows_preserve_runtime_indexing_and_addresses(self):
        self.write('''package main
main :: proc() {
    rows:#soa[2][4]int
    rows[0]=[4]int{1,2,3,4}
    rows[1]=[4]int{5,6,7,8}
    for &row in rows {
        index:=2
        if row[index]<=0 {panic("SOA row read")}
        pointer:=&row[index]
        pointer^=9
        index=-1
        row[index]=10
        if row[2]!=9||row[3]!=10 {panic("SOA row write")}
        text:=f"{row[index]}"
        defer delete(text)
        if text!="10" {panic("SOA row capture")}
    }
    if rows[0][2]!=9||rows[1][3]!=10 {panic("SOA row storage")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)

    def test_nested_slice_addresses_preserve_inference_and_storage(self):
        self.write('''package main
Image :: [3][]f32
write :: proc(image:Image,k,index:int) {
    pointer:=(^f32)(&image[k][index])
    pointer^=9
}
main :: proc() {
    rows:=[3][2]f32{{1,2},{3,4},{5,6}}
    image:Image
    for row,k in rows {image[k]=rows[k][:];_=row}
    write(image,1,1)
    write(image,-1,-1)
    if rows[1][1]!=9||rows[2][1]!=9 {panic("nested pointer storage")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)

    def test_nonnegative_range_indices_preserve_native_accesses(self):
        self.write('''package main
main :: proc() {
    values:=[3]int{2,3,4}
    total:int
    for i in 0..<len(values) {total+=values[i]}
    if total!=9 {panic("native range access")}
}
''')
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        output=self.root/'lowered'
        self.call('check',self.source,'-thread-count:1','--output-dir',output)
        generated=''.join(p.read_text() for p in output.rglob('main.odin'))
        if support.COMPILER.name == 'thor-rust':
            self.assertIn('values[i]',generated)
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)
        path.write_text(path.read_text().replace('0..<len(values)', '-3..<0'))
        self.call('run',self.source,'-thread-count:1')
        path.write_text(path.read_text().replace('-3..<0','0..<i128(len(values))'))
        self.call('run',self.source,'-thread-count:1')

    def test_generic_union_arguments_preserve_reference_free_and_slice_variants(self):
        self.write('''package main
Option :: union($T:typeid) {T}
Holder :: struct {item:Option(int)}
Node :: struct {left,right:^Node,values:[]int}
inspect :: proc(value:any) {_ = value}
visit :: proc(value:Holder,node:^Node,action:proc()->int)->int {
    if node==nil do return action()
    return visit(value,node.left,action)+visit(value,node.right,action)
}
main :: proc() {
    item :> Holder{Option(int)(7)}
    inspect(item)
    if visit(item,nil,proc()->int{return 7})!=7 {panic("generic union")}
}
''')
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        for flags in [[],support.UNCHECKED]:
            profile=self.root/'profile.json'
            self.call('run',self.source,'-thread-count:1',*flags,
                env={**os.environ,'THOR_PROFILE':str(profile)})
            counters=json.loads(profile.read_text())['counters']
            self.assertLess(counters.get('permissions.summaries_built',0),10,counters)
        path.write_text('''package main
Option :: union($T:typeid) {T}
write :: proc(value:Option([]int)) {
    switch item in value {case []int:item[0]=9}
}
main :: proc() {
    value :> Option([]int)([]int{1})
    write(value)
}
''')
        failed=self.call('check',self.source,'-thread-count:1',status=1)
        self.assertIn('read-only slice',failed.stderr)

    def test_unqualified_factory_graphs_do_not_require_result_instantiation(self):
        chain='\n'.join(f'factory_{i} :: proc()->^Node {{return factory_{i+1}()}}' for i in range(40))
        self.write('''package main
Node :: struct {next:^Node,values:[]int}
factory_40 :: proc()->^Node {return new(Node)}
'''+chain+'''
main :: proc() {
    expected :> 7
    node:=factory_0()
    defer free(node)
    if expected!=7||node==nil {panic("factory result")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            profile=self.root/'profile.json'
            self.call('run',self.source,'-thread-count:1',*flags,
                env={**os.environ,'THOR_PROFILE':str(profile)})
            counters=json.loads(profile.read_text())['counters']
            self.assertLessEqual(counters.get('permissions.summaries_built',0),45,counters)

    def test_shared_native_imports_do_not_enable_unneeded_effect_discovery(self):
        self.write('''package main
import "core:math"
main :: proc() {
    values:=[3]f64{4,9,16}
    total:f64
    for value in values {total+=math.sqrt(value)}
    if total!=9 {panic("native values")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            profile=self.root/'profile.json'
            self.call('run',self.source,'-thread-count:1',*flags,
                env={**os.environ,'THOR_PROFILE':str(profile)})
            counters=json.loads(profile.read_text())['counters']
            self.assertEqual(counters.get('discovery.type_queries',0),0,counters)

    def test_native_type_helpers_preserve_callback_shapes_in_immutable_records(self):
        self.write('''package main
import "base:runtime"
Callback :: #type proc(value:int)->int
Node :: struct {next:^Node,values:[]int}
identity :: proc(value:int)->int {return value}
inspect :: proc(value:any) {_ = value}
visit :: proc(allocator:runtime.Allocator,node:^Node,callback:Callback)->int {
    if node==nil do return callback(7)
    return visit(allocator,node.next,callback)
}
main :: proc() {
    allocator :> context.allocator
    callback :> Callback(identity)
    inspect(allocator)
    if visit(allocator,nil,callback)!=7 {panic("callback shape")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            profile=self.root/'profile.json'
            self.call('run',self.source,'-thread-count:1',*flags,
                env={**os.environ,'THOR_PROFILE':str(profile)})
            counters=json.loads(profile.read_text())['counters']
            self.assertLess(counters.get('permissions.summaries_built',0),10,counters)

    def test_native_packed_fields_and_bit_sets_are_reference_free(self):
        self.write('''package main
Id :: bit_field u64 {index:uint|32,generation:uint|32}
Flags :: bit_set[enum {a,b};u8]
inspect :: proc(value:any) {_ = value}
Node :: struct {left,right:^Node,values:[]int}
square :: proc(value:int)->int {
    Local :: bit_field u8 {value:u8|8}
    _ = Local{}
    return value**2
}
visit :: proc(id:Id,flags:Flags,node:^Node)->uint {
    if node==nil do return id.index+uint(.a in flags)
    return visit(id,flags,node.left)+visit(id,flags,node.right)
}
main :: proc() {
    id :> Id{index=7,generation=2}
    flags :> Flags{.a}
    inspect(id)
    inspect(flags)
    if visit(id,flags,nil)!=8 || square(7)!=49 {panic("packed value")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            profile=self.root/'profile.json'
            self.call('run',self.source,'-thread-count:1',*flags,
                env={**os.environ,'THOR_PROFILE':str(profile)})
            counters=json.loads(profile.read_text())['counters']
            self.assertLess(counters.get('permissions.summaries_built',0),10,counters)

    def test_recursive_reference_summaries_do_not_expand_binary_trees(self):
        self.write('''package main
Node :: struct {left, right: ^Node, values: []int}
next :: proc(node: ^Node) -> ^Node { return node.left }
read :: proc(node: ^Node) -> []int { return node.values }
main :: proc() {
    storage := [1]int{7}
    view :> storage[:]
    node := Node{values=storage[:]}
    node.left, node.right = &node, &node
    values := read(next(&node))
    if values[0] != view[0] { panic("recursive alias") }
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        profile = self.root/'profile.json'
        self.call('check', self.source, '-thread-count:1', env={**os.environ, 'THOR_PROFILE':str(profile)})
        counters = json.loads(profile.read_text())['counters']
        self.assertLess(counters.get('permissions.values', 0), 20_000, counters)

    def test_comptime_helpers_accept_defines_consumed_by_the_complete_project(self):
        self.write('''package main
import "core:testing"
CHOICE :: #config(MODE, 0)
add :: fn(value:int)->int {return value+1}
main :: proc() {
    if add(2)!=3||CHOICE!=7 {panic("constant result")}
}
@(test) smoke :: proc(t:^testing.T) {testing.expect(t,add(4)==5)}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-define:MODE=7','-thread-count:1',*flags)
            report=self.root/'tests.json'
            self.call('test',self.source,'-define:MODE=7',
                '-define:ODIN_TEST_JSON_REPORT='+str(report),'-define:ODIN_TEST_THREADS=1',
                '-thread-count:1',*flags)
            self.assertEqual(json.loads(report.read_text())['success'],1)
        failed=self.call('check',self.source,'-define:MODE=7','-define:UNUSED=1','-warnings-as-errors',
            '-thread-count:1',status=1)
        self.assertIn('UNUSED',failed.stderr)
        self.assertIn('unused',failed.stderr.lower())

    def test_reference_summaries_bound_wide_acyclic_type_graphs(self):
        definitions='\n'.join(f'Node{i} :: struct {{left,right:^Node{i+1},values:[]int}}' for i in range(16))
        self.write('package main\n'+definitions+'''\nNode16 :: struct {values:[]int}
retain :: proc(value:^$T)->^T {return value}
read :: proc(value:^Node0)->int {return len(value.values)}
main :: proc() {
    values:=[1]int{7}
    node:=Node0{values=values[:]}
    if read(retain(&node))!=1 {panic("reference graph")}
}
''')
        (self.source/'main.odin').rename(self.source/'main.thor')
        profile=self.root/'profile.json'
        self.call('check',self.source,'-thread-count:1',env={**os.environ,'THOR_PROFILE':str(profile)})
        counters=json.loads(profile.read_text())['counters']
        self.assertLess(counters.get('permissions.values',0),20_000,counters)

    def test_reference_checks_keep_sibling_fields_independent(self):
        self.write('''package main
Pair :: struct {readonly,mutable:[]int}
consume :: proc(value:any) {_ = value}
pass_field :: proc(value:$T) {consume(value.mutable)}
main :: proc() {
    a:=[1]int{1}
    b:=[1]int{2}
    frozen :> a[:]
    pair:=Pair{frozen,b[:]}
    pass_field(Pair{b[:],b[:]})
    pass_field(pair)
}
''')
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        for flags in [[],support.UNCHECKED]:
            self.call('run',self.source,'-thread-count:1',*flags)
        path.write_text(path.read_text().replace('consume(value.mutable)','consume(value.readonly)'))
        failed=self.call('check',self.source,'-thread-count:1',status=1)
        self.assertIn('read-only slice',failed.stderr)

    def test_opaque_cyclic_metadata_keeps_compact_slice_provenance(self):
        self.write('''package main
import "base:runtime"
Node :: struct {database:^Database,values:[]int}
Database :: struct {left,right:^Node,parent:^Database,info:^runtime.Type_Info}
erase :: proc(value:^$T)->rawptr {return transmute(rawptr)value}
forward :: proc(value:^Node)->rawptr {return erase(value)}
main :: proc() {
    database:Database
    values:=[1]int{7}
    node:=Node{&database,values[:]}
    database.left,database.right,database.parent=&node,&node,&database
    if forward(&node)==nil {panic("pointer identity")}
}
''')
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        profile=self.root/'profile.json'
        self.call('check',self.source,'-thread-count:1',env={**os.environ,'THOR_PROFILE':str(profile)})
        counters=json.loads(profile.read_text())['counters']
        self.assertLess(counters.get('permissions.values',0),20_000,counters)
        path.write_text(path.read_text().replace('node:=Node{&database,values[:]}',
            'frozen :> values[:]\n    node:=Node{&database,frozen}'))
        failed=self.call('check',self.source,'-thread-count:1',status=1)
        self.assertIn('read-only slice',failed.stderr)

    def test_slice_permissions_survive_many_returned_aliases(self):
        forwards='\n'.join(f'hop{i} :: proc(value:[]int)->[]int {{return hop{i-1}(value)}}' for i in range(1,25))
        self.write('''package main
hop0 :: proc(value:[]int)->[]int {return value}
'''+forwards+'''
change :: proc(value:[]int) {value[0]=9}
main :: proc() {
    marker :> 0
    _ = marker
    values:=[1]int{7}
    result:=hop24(values[:])
    change(result)
    if values[0]!=9 {panic("returned slice alias")}
}
''')
        path=self.source/'main.thor'
        (self.source/'main.odin').rename(path)
        profile=self.root/'profile.json'
        self.call('run',self.source,'-thread-count:1',env={**os.environ,'THOR_PROFILE':str(profile)})
        counters=json.loads(profile.read_text())['counters']
        self.assertLess(counters.get('permissions.values',0),30_000,counters)
        path.write_text(path.read_text().replace('result:=hop24(values[:])',
            'frozen :> values[:]\n    result :> hop24(frozen)'))
        failed=self.call('check',self.source,'-thread-count:1',status=1)
        self.assertIn('read-only slice',failed.stderr)


if __name__ == '__main__':
    unittest.main()

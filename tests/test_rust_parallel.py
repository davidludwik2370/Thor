"""Worker count preserves generated source, diagnostics, permissions and runtime results."""
import json
import os
import unittest
from tests import test_rust_compiler as native


class RustParallelTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def profiled(self, workers, command='transpile', flags=(), directory=True):
        profile = self.root/'profile.json'
        result = self.invoke('rust', command=command, flags=flags, directory=directory,
                             env={**os.environ, 'THOR_JOBS': str(workers), 'THOR_PROFILE': str(profile)})
        return result, json.loads(profile.read_text())

    def test_parallel_sources_and_maps_match_serial_output_in_both_build_configurations(self):
        for index in range(8):
            (self.app/f'part_{index}.thor').write_text(
                f'package main\npart_{index} :: fn()->int {{return {index+1}}}\n')
        total = '+'.join(f'part_{index}()' for index in range(8))
        self.source.write_text(self.program('fmt.println(f"sum {VALUE}")',
            'import "core:fmt"\n'+f'total::fn()->int{{return {total}}}\nVALUE :: #run total()\n'))
        for flags in (['-debug'], native.UNCHECKED):
            with self.subTest(flags=flags):
                baseline = None
                for workers in (1, 4, 4):
                    result, profile = self.profiled(workers, flags=flags)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    generated = {str(path.relative_to(self.root/'rust')): path.read_bytes()
                                 for path in (self.root/'rust').rglob('*') if path.is_file()}
                    if baseline is None:
                        baseline = generated
                    else:
                        self.assertEqual(generated, baseline)
                    if workers > 1:
                        self.assertGreater(profile['counters']['parallel.tasks'], 8)
                        self.assertIn('parser.source', profile['worker_stages'])
                        self.assertIn('emit.serialize', profile['worker_stages'])
                run, _ = self.profiled(4, command='run', flags=flags)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(run.stdout, 'sum 36\n')
                self.assertEqual(run.stderr, '')

    def test_multiple_parse_errors_keep_source_order(self):
        self.source.write_text(self.program(''))
        for name in ('a.thor', 'z.thor'):
            (self.app/name).write_text('package main\nbad :: proc( {\n')
        outputs = []
        for workers in (1, 4, 4):
            result, _ = self.profiled(workers, flags=['-json-errors'])
            self.assertEqual(result.returncode, 1, result.stderr)
            packet = json.loads(result.stderr)
            self.assertTrue(all(item['pos']['file'].endswith('/a.thor') for item in packet['errors']))
            outputs.append((result.stdout, result.stderr))
        self.assertEqual(outputs[1:], outputs[:1]*2)

    def test_permission_reuse_preserves_owned_copies_and_readonly_errors(self):
        code = self.program('''
values := make([]int, 1); defer delete(values)
frozen :> values
copy := slice.clone(frozen); defer delete(copy)
change(copy)
if values[0]!=0 || copy[0]!=4 {panic("permissions")}
''', 'import "core:slice"\nchange::proc(values:[]int){values[0]=4}\n')
        for flags in (['-debug'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.source.write_text(code)
                result, profile = self.profiled(4, command='run', flags=flags)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertGreater(profile['counters'].get('permissions.reuses', 0), 0)
                self.source.write_text(code.replace('change(copy)', 'change(frozen)'))
                invalid, invalid_profile = self.profiled(4, command='run', flags=flags+['-json-errors'])
                self.assertEqual(invalid.returncode, 1, invalid.stderr)
                self.assertIn('read-only slice', invalid.stderr.lower())
                self.assertGreater(invalid_profile['counters'].get('permissions.reuses', 0), 0)

    def test_cross_package_slice_checks_follow_all_callees(self):
        vendor = self.root/'vendor'
        for package, code in {
            'first': 'import "../second"\nforward::proc(v:[]int){second.forward(v)}',
            'second': 'import "../third"\nforward::proc(v:[]int){third.forward(v)}',
            'third': 'forward::proc(v:[]int){v[0]=9}',
        }.items():
            path = vendor/package/(package+'.odin')
            path.parent.mkdir(parents=True)
            path.write_text('package '+package+'\n'+code+'\n')
        code = self.program('''
values := make([]int, 1); defer delete(values)
frozen :> values
copy := make([]int, 1); defer delete(copy)
copy[0] = frozen[0]
first.forward(copy)
if copy[0]!=9 || frozen[0]!=0 {panic("dependency effects")}
''', 'import "vendor:first"\n')
        flags = ['-collection:vendor='+str(vendor), '-json-errors']
        for workers in (1, 4):
            with self.subTest(workers=workers):
                self.source.write_text(code)
                valid, profile = self.profiled(workers, command='run', flags=flags)
                self.assertEqual(valid.returncode, 0, valid.stderr)
                self.assertGreater(profile['counters'].get('permissions.analyses', 0), 0)
                self.source.write_text(code.replace('first.forward(copy)', 'first.forward(frozen)'))
                invalid, _ = self.profiled(workers, command='run', flags=flags)
                self.assertEqual(invalid.returncode, 1, invalid.stderr)
                self.assertIn('read-only slice', invalid.stderr.lower())

    def test_metadata_reuse_keeps_generic_instantiations_and_permissions_separate(self):
        code = self.program('''
integer := read(Holder(int){7})
floating := read(Holder(f64){2.5})
array := read(Holder([2]int){[2]int{3,4}})
values := make([]int, 1); defer delete(values)
result := read(Holder([]int){values}); result[0] = 5
view :> values
holder :> Holder([]int){view}
readonly_result :> read(holder)
copy := slice.clone(readonly_result); defer delete(copy)
copy[0] = 8
if integer!=7 || floating!=2.5 || array[1]!=4 || values[0]!=5 || copy[0]!=8 {
    panic("generic metadata")
}
''', '''import "core:slice"
Holder :: struct($T:typeid) {value:T}
read :: proc(box:Holder($T))->T {return box.value}
''')
        for flags in (['-debug'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.source.write_text(code)
                valid, _ = self.profiled(4, command='run', flags=flags)
                self.assertEqual(valid.returncode, 0, valid.stderr)
                self.assertEqual(valid.stderr, '')
                self.source.write_text(code.replace('copy[0] = 8', 'readonly_result[0] = 8'))
                invalid, _ = self.profiled(4, command='run', flags=flags+['-json-errors'])
                self.assertEqual(invalid.returncode, 1, invalid.stderr)
                self.assertIn('read-only slice', invalid.stderr.lower())


if __name__ == '__main__':
    unittest.main()

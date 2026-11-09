"""Differential acceptance checks for the independent native compiler.

An unsupported-feature status is a migration gap, never a successful comparison.
Use real Odin runs and output checks that survive -disable-assert.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / 'rust/target/release/thor-rust'
UNCHECKED = ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']


class RustCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('cargo') is None:
            raise unittest.SkipTest('Rust compiler tests require Cargo')
        result = subprocess.run(['cargo', 'build', '--release', '--locked', '--offline',
                                 '--manifest-path', str(ROOT/'rust/Cargo.toml')],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-rust-differential-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = self.root/'input'
        self.app.mkdir()
        self.source = self.app/'main.thor'

    def invoke(self, engine, command='run', flags=(), directory=False, args=(), env=None):
        compiler = ROOT/'thor' if engine == 'python' else RUST
        argv = [str(compiler), command, str(self.app if directory else self.source),
                '--output-dir', str(self.root/engine), '--cache-dir', str(self.root/'cache'),
                *flags]
        if args:
            argv += ['--', *args]
        return subprocess.run(argv, capture_output=True, text=True, cwd=self.root,
                              env=env, timeout=60)

    def compare(self, source, *, files=None, command='run', flags=(), expected=None,
                fail=None, directory=False, args=()):
        self.source.write_text(source)
        for name, content in (files or {}).items():
            path = self.app/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        before = {p:p.read_bytes() for p in self.app.rglob('*') if p.is_file()}
        python = self.invoke('python', command, flags, directory, args)
        rust = self.invoke('rust', command, flags, directory, args)
        details = f'Python:\n{python.stdout}{python.stderr}\nRust:\n{rust.stdout}{rust.stderr}'
        self.assertNotEqual(rust.returncode, 3, details)
        self.assertEqual(rust.returncode, python.returncode, details)
        if fail:
            self.assertNotEqual(rust.returncode, 0, details)
            self.assertIn(fail, python.stdout+python.stderr)
            self.assertIn(fail, rust.stdout+rust.stderr)
        else:
            self.assertEqual(rust.returncode, 0, details)
            self.assertEqual(rust.stdout, python.stdout, details)
            if expected is not None:
                self.assertEqual(rust.stdout.rstrip('\n'), expected)
        self.assertEqual(before, {p:p.read_bytes() for p in before})
        return rust

    def program(self, body, prefix=''):
        return 'package main\n'+prefix+'\nmain :: proc() {\n'+body+'\n}\n'

    def test_ordinary_odin_and_unicode_both_build_configurations(self):
        code = self.program('values := [?]int{3,4}; for value in values {fmt.println(value)}',
                            'import "core:fmt"\n// 😀 nested /* comment */\n')
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags):
                self.compare(code, flags=flags, expected='3\n4')

    def test_selected_import_and_lexical_shadowing(self):
        self.compare(self.program('println(7); {println := 42; fmt.println(println)}',
                                  'import "core:fmt" / println\n'), expected='7\n42')

    def test_blank_selected_import(self):
        self.compare(self.program('println("selected")', 'import _ "core:fmt" / println\n'),
                     expected='selected')

    def test_unused_selected_import_still_resolves(self):
        self.compare(self.program('', 'import "core:fmt" / does_not_exist\n'), command='check',
                     fail='does_not_exist')

    def test_selected_import_comments_and_multi_file_scope(self):
        self.compare(self.program('println(other())', 'import "core:fmt" / println /* kept */\n'),
                     files={'other.thor':'package main\nother::proc()->int{return 9}\n'},
                     directory=True, expected='9')

    def test_arrays_membership_and_once_only_evaluation(self):
        self.compare(self.program('''
counter := 0
values := [next(&counter), next(&counter), next(&counter)]
if counter != 3 || 2 not_in values || 7 in values {panic("array semantics")}
fmt.println(values)
''', '''import "core:fmt"
next::proc(n:^int)->int{n^+=1;return n^}
'''), expected='[1, 2, 3]')

    def test_shadowed_type_query_is_a_normal_once_only_call(self):
        self.compare(self.program('''
count:=0
if type_of(&count) not_in [1,2,3] || count!=1 {panic("shadowed builtin")}
fmt.println(count)
''','import "core:fmt"\ntype_of::proc(count:^int)->int{count^+=1;return 2}\n'),expected='1')

    def test_contextual_array_and_membership_numeric_types(self):
        self.compare(self.program('''
x:i8=4
if x not_in [4,8] {panic("context")}
values:[3]i16=[1,2,3]
if 2 not_in values {panic("needle")}
fmt.println(values)
''', 'import "core:fmt"\n'), expected='[1, 2, 3]')

    def test_contextual_parameter_return_and_nested_arrays(self):
        self.compare(self.program('fmt.println(sum([3,4]), build())', '''import "core:fmt"
sum::proc(values:[2]i8)->int{return int(values[0]+values[1])}
build::proc()->[2][2]int{return [[1,2],[3,4]]}
'''), expected='7 [[1, 2], [3, 4]]')

    def test_constant_membership_and_ternary_stay_constants(self):
        self.compare(self.program('fmt.println(values)', '''import "core:fmt"
SIZE :: (2 in [1,2,3]) ? 2 : 1
values:[SIZE]int
'''), expected='[0, 0]')

    def test_constant_concatenation_preserves_element_type(self):
        self.compare(self.program('fmt.println(DATA)', '''import "core:fmt"
LEFT :: [2]i8{1,2}
DATA :: LEFT ++ [3,4] ++ []
'''), expected='[1, 2, 3, 4]')

    def test_concatenation_runtime_rejected(self):
        self.compare(self.program('values := [1,2]; other := values ++ [3]; _=other'),
                     command='check', fail='requires compile-time constant operands')

    def test_string_repetition_and_untyped_cstring(self):
        self.compare(self.program('consume(PAD + "x"); fmt.println(LABEL)', '''import "core:fmt"
PAD :: " " * 3
LABEL :: 4 * "a" + "b"
consume::proc(value:cstring){fmt.println(value)}
'''), expected='   x\naaaab')

    def test_repetition_zero_and_local_constant_counts(self):
        self.compare(self.program('N::5; text::"x"*N; empty::"a"*0; fmt.println(text, len(empty))',
                                  'import "core:fmt"\n'), expected='xxxxx 0')

    def test_runtime_string_repetition_rejected(self):
        self.compare(self.program('text:="x"; result:=text*3; _=result'), command='check',
                     fail='String repetition requires compile-time constant operands')

    def test_numeric_power_runtime_and_evaluation_order(self):
        self.compare(self.program('''
counter:=0
a:=next(&counter) ** next(&counter)
x:f64=2; y:f64=-3
if counter!=2 || a!=1 || x**y!=0.125 {panic("power")}
fmt.println(a,x**y)
''', 'import "core:fmt"\nnext::proc(n:^int)->int{n^+=1;return n^}\n'), expected='1 0.125')

    def test_numeric_power_defaults_literal_operand_types(self):
        self.compare(self.program('''
integer:=2**3
fraction:=2.0 ** -3
if integer!=8 || fraction!=0.125 {panic("default operand types")}
fmt.println(integer, fraction)
''','import "core:fmt"\n'),expected='8 0.125')

    def test_negative_fixed_array_indices_and_maps(self):
        self.compare(self.program('''
values:=[10,20,30]
values[-1]=40
table:=make(map[int]int); defer delete(table); table[-1]=7
fmt.println(values[-2], values[-1], table[-1])
''', 'import "core:fmt"\n'), expected='20 40 7')

    def test_immutable_inline_storage_rejected(self):
        self.compare(self.program('values :> [1,2]; values[0]=3'), command='check',
                     fail="Cannot write immutable storage 'values'")
        self.compare(self.program('values :> [1,2]; pointer:=raw_data(values);_=pointer'),command='check',
                     fail="Cannot obtain a mutable data pointer to immutable storage 'values'")
        self.compare(self.program('values :> [1,2]; for &value in values {value=3}'),command='check',
                     fail="Cannot iterate by mutable reference over immutable storage 'values'")

    def test_immutable_copy_and_shadowing(self):
        self.compare(self.program('''
values :> [1,2]
copy:=values; copy[0]=7
{values := [3,4]; values[1]=5;fmt.println(values)}
fmt.println(values,copy)
''', 'import "core:fmt"\n'), expected='[3, 5]\n[1, 2] [7, 2]')

    def test_immutable_shadow_initializer_reads_outer_binding(self):
        self.compare(self.program('''
value:=7
{value :> value; fmt.println(value)}
fmt.println(value)
''','import "core:fmt"\n'),expected='7\n7')

    def test_automatic_using_feature_preserves_source(self):
        self.compare(self.program('record:=Record{4}; fmt.println(read(record))', '''import "core:fmt"
Record::struct{value:int}
read::proc(using r:Record)->int{return value}
'''), expected='4')

    def test_directory_build_excludes_test_sources(self):
        self.compare(self.program('fmt.println(7)', 'import "core:fmt"\n'), directory=True,
                     files={'broken_test.thor':'package main\nthis is invalid\n'}, expected='7')

    def test_mixed_relative_import_packages(self):
        self.compare(self.program('fmt.println(helper.value())',
                                  'import "core:fmt"\nimport "helper"\n'),
                     directory=True, files={'helper/value.odin':'package helper\nvalue::proc()->int{return 8}\n'},
                     expected='8')

    def test_program_arguments_stay_separate(self):
        self.compare(self.program('fmt.println(os.args[1],os.args[2])',
                                  'import "core:fmt"\nimport "core:os"\n'),
                     args=['one two', 'three'], expected='one two three')

    def test_resources_keep_original_file_relative_paths(self):
        code = self.program('fmt.println(DATA, PRESENT, ABSENT)', '''import "core:fmt"
DATA :: #load("assets/value.txt", string)
PRESENT :: #exists("assets/value.txt")
ABSENT :: #exists("assets/missing.txt")
''')
        for extension in ['thor', 'odin']:
            with self.subTest(extension=extension):
                self.source = self.app/('main.'+extension)
                self.compare(code, files={'assets/value.txt':'payload'}, expected='payload true false')

    def test_default_executable_name_matches_current_directory(self):
        self.source.write_text(self.program(''))
        for engine in ['python', 'rust']:
            with self.subTest(engine=engine):
                result = self.invoke(engine, command='build')
                self.assertEqual(result.returncode, 0, result.stderr)
                executable = self.root/(self.root.name+('.exe' if os.name=='nt' else ''))
                self.assertTrue(executable.is_file())
                executed = subprocess.run([str(executable)], capture_output=True, text=True)
                self.assertEqual(executed.returncode, 0, executed.stderr)

    def test_interpolation_formatting_named_and_manual_values(self):
        self.compare(self.program('''
width:=10; n:=1234
text:=f"value {n:0{width},d} {(1.0/3):.2%}"
defer delete(text)
fmt.println(text)
consume(f!"manual {n}")
''', 'import "core:fmt"\nconsume::proc(text:string){fmt.println(text);delete(text)}\n'),
                     expected='value 000001,234 33.33%\nmanual 1234')

    def test_interpolation_anonymous_arguments_do_not_leak_in_release(self):
        code = self.program('''
tracker:mem.Tracking_Allocator
mem.tracking_allocator_init(&tracker, context.allocator)
defer mem.tracking_allocator_destroy(&tracker)
{
 context.allocator=mem.tracking_allocator(&tracker)
 for i in 0..<30 {
  if len(identity(f"hello {i}"))<7 {panic("temporary lifetime")}
  if false && len(identity(f"not used {i}"))>0 {panic("lazy argument")}
  consume(f!"manual {i}")
  text:=f"named {i}";delete(text)
 }
}
if tracker.current_memory_allocated!=0 || len(tracker.bad_free_array)!=0 {panic("string lifetime")}
fmt.println("ok")
''', '''import "core:fmt"
import "core:mem"
identity::proc(text:string)->string{return text}
consume::proc(text:string){delete(text)}
''')
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags):
                self.compare(code, flags=flags, expected='ok')

    def test_interpolation_format_parser_matches_reference(self):
        code = self.program('''
fmt.println(f"{12:+08d}|{0.125:.2%}|{'a':U}|{\"hi\":'界'^6}")
''', 'import "core:fmt"\n')
        self.compare(code)
        for spec, message in [('>>6', 'no longer uses < or >'), ('width', 'variable width needs nested braces'),
                              ('--4', 'Duplicate interpolation flag'), ('^.2', 'requires an explicit verb')]:
            with self.subTest(spec=spec):
                self.compare(self.program('text:=f"{2:'+spec+'}";_=text'), command='check', fail=message)

    def test_interpolation_width_and_field_evaluation_order(self):
        self.compare(self.program('''
order:=0
text:=f"{step(&order,1):{step(&order,2)}} {step(&order,3)}"
defer delete(text)
if order!=123 {panic("evaluation order")}
fmt.println(text)
''', 'import "core:fmt"\nstep::proc(order:^int,n:int)->int{order^=order^*10+n;return n}\n'),
                     expected=' 1 3')

    def test_scope_cleanup_call_is_not_moved_into_format_helper(self):
        self.compare(self.program('text:=f"{acquire()}";delete(text)', '''
@(deferred_out=release)
acquire::proc()->int{return 7}
release::proc(value:int){}
'''),command='check',fail='automatic scope cleanup must be called directly')
        self.compare(self.program('text:=f"{library.acquire()}";delete(text)', 'import "library"\n'),
                     files={'library/main.odin':'''package library
@(deferred_out=release)
acquire::proc()->int{return 7}
release::proc(value:int){}
'''},command='check',fail='automatic scope cleanup must be called directly')
        self.compare(self.program('borrow(f"hello {2}")','''
@(deferred_in=release)
borrow::proc(text:string){}
release::proc(text:string){}
'''),command='check',fail='automatic scope cleanup must be called directly')

    def test_original_diagnostic_offsets_utf16_and_schema(self):
        self.source.write_text(self.program('text:="😀"; value:int="bad"; _=text; _=value'))
        rust = self.invoke('rust', command='check', flags=['-json-errors'])
        python = self.invoke('python', command='check', flags=['-json-errors'])
        self.assertEqual(rust.returncode, 1, rust.stderr)
        rust_errors = json.loads(rust.stderr)['errors']
        python_errors = json.loads(python.stderr)['errors']
        self.assertEqual(rust_errors[0]['pos']['file'], str(self.source))
        self.assertEqual(rust_errors[0]['pos']['line'], python_errors[0]['pos']['line'])
        self.assertEqual(rust_errors[0]['pos']['column'], python_errors[0]['pos']['column'])
        self.assertEqual(rust_errors[0]['msgs'], python_errors[0]['msgs'])

    def test_source_map_partition_and_lookup_matches_python_consumer(self):
        self.source.write_text(self.program('values:=[1,2]; _=values'))
        result = self.invoke('rust', command='transpile')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = Path(result.stdout.strip())
        mapping = json.loads(Path(str(output)+'.map.json').read_text())
        from thorlang.sourcemap import SourceMap
        consumer = SourceMap(mapping['segments'], mapping['expansions'], mapping['regions'])
        cursor = 0
        for first, last, source, start, end, kind, exact in mapping['segments']:
            self.assertEqual(first, cursor)
            self.assertGreater(last, first)
            self.assertEqual(source, str(self.source))
            cursor = last
        self.assertEqual(cursor, len(output.read_bytes()))
        offset = output.read_bytes().index(b'values')
        origin = consumer.lookup(offset, len('values'))
        self.assertEqual(self.source.read_bytes()[origin.start:origin.end], b'values')
        self.assertTrue(mapping['expansions'])

    def test_parser_cache_corruption_recomputed(self):
        self.source.write_text(self.program(''))
        first = self.invoke('rust', command='check')
        self.assertEqual(first.returncode, 0, first.stderr)
        cache_files = list((self.root/'cache/rust-parse-v1').glob('*.json'))
        self.assertTrue(cache_files)
        for file in cache_files:
            file.write_text('{broken')
        second = self.invoke('rust', command='check')
        self.assertEqual(second.returncode, 0, second.stderr)
        for file in cache_files:
            self.assertIsInstance(json.loads(file.read_text())['payload']['nodes'], list)
            packet=json.loads(file.read_text())
            self.assertEqual(packet['schema'],'odin-frontend-raw-v1')
            self.assertTrue(all('immutable' not in node for node in packet['payload']['nodes']))

    def test_unported_features_fail_closed_without_python(self):
        for code in ['values :> []int{1,2}; _=values', 'values :> [1,2]; alias:=values[:];_=alias',
                     'values :> any([]int{1,2});_=values', 'value:=f"hello {2}"=="hello 2";_=value',
                     'when true {value:=1} else {value :> 1};value=2;_=value',
                     'value :> struct{x:int}{1};using value;x=2',
                     'value:=#run calculate();_=value', 'value:Integer=2;_=value',
                     'value:=[x for x in 0..<3];_=value']:
            with self.subTest(code=code):
                self.source.write_text(self.program(code, 'calculate::proc()->int{return 2}\n'))
                result = self.invoke('rust', command='check', flags=['-json-errors'])
                self.assertEqual(result.returncode, 3, result.stderr)
                packet = json.loads(result.stderr)
                self.assertEqual(packet['errors'][0]['stage'], 'rust-migration')

    def test_capabilities_admit_incomplete_port(self):
        result = subprocess.run([str(RUST), '--capabilities'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertFalse(packet['complete'])
        self.assertFalse(packet['python_fallback'])
        self.assertFalse(packet['parallel_lowering'])

    def test_frontend_ast_protocol_matches_reference_on_cross_feature_sources(self):
        from thorlang.compiler import Source
        from thorlang.driver import frontend_binary
        frontend, _ = frontend_binary(os.environ.get('THOR_ODIN', 'odin'))
        paths = [ROOT/name for name in ['demo/main.thor', 'examples/comprehensions/demo.thor',
                                       'examples/slice_access/demo.thor'] if (ROOT/name).is_file()]
        self.source.write_text(self.program('values:=[1,2];_=values'))
        paths.append(self.source)
        for path in paths:
            with self.subTest(path=path):
                result = subprocess.run([str(RUST), 'ast', str(path), '--no-cache'],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = next(item['frontend'] for item in json.loads(result.stdout)['sources']
                              if Path(item['path']) == path.resolve())
                expected = Source(path, frontend, package=False, project_name=path.stem, cache=False).info
                self.assertEqual(actual, expected)

    def test_large_ast_pipe_is_drained_without_deadlock(self):
        self.source.write_text('package main\n'+''.join(f'VALUE_{i}::{i}\n' for i in range(2000))+'main::proc(){}\n')
        result = self.invoke('rust', command='ast')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(len(result.stdout), 1_000_000)
        self.assertGreater(len(json.loads(result.stdout)['sources'][0]['frontend']['nodes']), 6000)

    def test_single_value_or_panic_evaluates_once(self):
        self.compare(self.program('''
count:=0
value:=get(&count) or_panic
if count!=1 || value!=7 {panic("optional evaluation")}
fmt.println(value)
''','import "core:fmt"\nget::proc(n:^int)->(int,bool){n^+=1;return 7,true}\n'), expected='7')

    def test_or_panic_failure_keeps_unicode_source_location_and_exit_status(self):
        code=self.program('/* 😀 */ value:=get() or_panic;_=value',
                          'get::proc()->(int,bool){return 0,false}\n')
        self.source.write_text(code)
        line=next(i for i,text in enumerate(code.splitlines(),1) if 'or_panic' in text)
        source_line=code.splitlines()[line-1]
        column=source_line.index('get()')+1
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags):
                python=self.invoke('python',flags=flags)
                rust=self.invoke('rust',flags=flags)
                self.assertNotEqual(python.returncode,0,python.stderr)
                self.assertEqual(rust.returncode,python.returncode,rust.stderr)
                for result in [python,rust]:
                    self.assertIn(f'{self.source}({line}:{column})',result.stderr)
                    self.assertIn('unexpected failure',result.stderr)

    def test_no_python_subprocess_fallback(self):
        self.source.write_text(self.program(''))
        trap = self.root/'trap'
        trap.mkdir()
        for name in ['python', 'python3']:
            program = trap/name
            program.write_text('#!/bin/sh\nexit 97\n')
            program.chmod(0o755)
        environment = {**os.environ, 'PATH':str(trap)+os.pathsep+os.environ.get('PATH', '')}
        result = self.invoke('rust', command='check', env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

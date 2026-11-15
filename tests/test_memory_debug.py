"""Debug defaults, standalone mapped ASan reports and tracking lifetimes."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from thorlang.compiler import ThorError
from thorlang.memory_debug import MemoryDebug
from thorlang.memorymap import Locations, decode, encode, read_binary
from thorlang.symbolizer import Symbolizer

ROOT = Path(__file__).resolve().parents[1]
THOR = Path(os.environ.get("THOR_TEST_COMPILER", ROOT / "thor"))


class MemoryPolicyTests(unittest.TestCase):
    def test_automatic_memory_checks_are_native_macos_debug_only(self):
        for platform in ['linux', 'win32', 'darwin']:
            for flags in [[], ['-debug'], ['-debug', '-target:linux_amd64'], ['-debug', '-target:windows_amd64']]:
                with self.subTest(platform=platform, flags=flags):
                    options = MemoryDebug.select('build', flags, platform=platform)
                    expected = platform == 'darwin' and flags == ['-debug']
                    self.assertEqual(options.enabled, expected)
                    self.assertEqual(options.tracking, 'basic' if expected else 'off')
                    self.assertEqual('-sanitize:address' in options.extra_flags, expected)

    def test_allocator_and_sanitizer_overrides_are_independent(self):
        options = MemoryDebug.select('run', ['-debug'], 'off', platform='darwin')
        self.assertTrue(options.address)
        self.assertEqual(options.tracking, 'off')
        options = MemoryDebug.select('run', ['-debug'], no_sanitize=True, platform='darwin')
        self.assertFalse(options.address)
        self.assertEqual(options.tracking, 'basic')
        options = MemoryDebug.select('run', [], 'basic', platform='darwin')
        self.assertFalse(options.address)
        self.assertEqual(options.tracking, 'basic')
        for flags in [['-debug', '-sanitize:thread'], ['-debug', '-use-separate-modules']]:
            options = MemoryDebug.select('run', flags, platform='darwin')
            self.assertNotIn('-use-single-module', options.extra_flags)
            if '-sanitize:thread' in flags:
                self.assertFalse(options.address)

    def test_other_build_modes_and_tests_keep_their_allocator_behavior(self):
        for command, flags in [('check', ['-debug']), ('transpile', ['-debug']),
                               ('build', ['-debug', '-build-mode:static']),
                               ('build', ['-debug', '-no-entry-point'])]:
            self.assertFalse(MemoryDebug.select(command, flags, platform='darwin').enabled)
        options = MemoryDebug.select('test', ['-debug'], platform='darwin')
        self.assertEqual(options.tracking, 'off')
        self.assertTrue(options.address)
        with self.assertRaisesRegex(ThorError, 'requires -debug'):
            MemoryDebug.select('run', [], 'trace', platform='darwin')

    def test_map_integrity_and_symbolizer_protocol_preserve_native_frames(self):
        data = {'version': 1, 'files': ['/original/é😀.thor'], 'aliases': {}, 'units': [
            {'path': '/generated/main.odin', 'rows': [[50, 4, 20, 0, 7, 8, True, 'work']]}]}
        self.assertEqual(decode(encode(data)), data)
        damaged = bytearray(encode(data)); damaged[-1] ^= 1
        with self.assertRaisesRegex(ValueError, 'checksum'):
            decode(damaged)
        worker = Symbolizer()
        worker.locations = [Locations(data)]
        self.assertEqual(worker.transform('main.__thor_helper\n/generated/main.odin:50:6\n\n'),
                         'work\n/original/é😀.thor:7:10\n\n')
        native = 'runtime::free\n/odin/base/runtime.odin:12:3\n\n'
        self.assertEqual(worker.transform(native), native)
        self.assertEqual(worker.transform('global\n0 4\n/generated/main.odin:50\n\n', 'DATA'),
                         'global\n0 4\n/original/é😀.thor:7\n\n')


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('odin'), 'native macOS Odin required')
class MemoryDebugTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor memory ünicode ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root/'main.thor'
        self.output = self.root/'generated'
        self.exe = self.root/'program'

    def build(self, text, flags=('-debug',), files=None):
        self.source.write_text(text)
        for name, content in (files or {}).items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        result = subprocess.run([str(THOR), 'build', str(self.source), *flags,
            '--output-dir', str(self.output), '-out:'+str(self.exe)], cwd=self.root,
            capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.build_result = result
        self.assertEqual(self.source.read_text(), text)
        return json.loads((self.output/'thor-manifest.json').read_text())

    def run_program(self, *args, executable=None, env=None):
        environment = {k:v for k,v in os.environ.items() if not k.startswith(('ASAN_', 'LSAN_', 'THOR_LLVM_SYMBOLIZER'))}
        environment.update(env or {})
        return subprocess.run([str(executable or self.exe), *args], cwd='/tmp', env=environment,
                              capture_output=True, text=True, timeout=30)

    def test_clean_defers_finalizers_and_custom_allocators(self):
        manifest = self.build('''package main
import "base:runtime"
import "core:fmt"
values: [dynamic]int
@(fini) finish :: proc "contextless" () {
    context = runtime.default_context()
    delete(values)
}
main :: proc() {
    append(&values, 7)
    text := f"clean {values[0]}"
    defer delete(text)
    pointer := new(int)
    defer free(rawptr(pointer))
    {
        context.allocator = runtime.default_context().allocator
        explicit := make([]int, 2)
        defer delete(explicit)
    }
    fmt.println(text)
}
''')
        self.assertEqual(manifest['memory_debug']['tracking'], 'basic')
        self.assertIn('-sanitize:address', manifest['odin_flags'])
        self.assertIn('-use-single-module', manifest['odin_flags'])
        result = self.run_program()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'clean 7')
        self.assertEqual(result.stderr, '')

    def test_each_conditional_main_installs_tracking(self):
        text = '''package main
FIRST :: #config(FIRST, true)
when FIRST {
    main :: proc() { pointer:=new(int); _=pointer }
} else {
    main :: proc() { values:=make([]int,3); _=values }
}
'''
        for first, size, line in [(True, 8, 4), (False, 24, 6)]:
            with self.subTest(first=first):
                self.build(text, flags=('-debug', '-define:FIRST='+str(first).lower()))
                result = self.run_program()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'1 allocation(s), {size} byte(s)', result.stderr)
                self.assertIn(str(self.source)+f':{line}:', result.stderr)

    def test_leaks_basic_without_debug_and_tracking_off(self):
        source = 'package main\nmain :: proc() {\n    pointer := new(int)\n    _ = pointer\n}\n'
        self.build(source, ['--track-allocations=basic'])
        result = self.run_program()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1 allocation(s), 8 byte(s)', result.stderr)
        self.assertIn(str(self.source)+':3:', result.stderr)
        self.assertNotIn('.odin:', result.stderr)
        self.build(source, ['-debug', '--track-allocations=off'])
        result = self.run_program()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')

    def test_bad_free_is_immediate_and_mapped(self):
        self.build('''package main
main :: proc() {
    pointer := new(int)
    free(rawptr(pointer))
    free(rawptr(pointer))
}
''')
        result = self.run_program()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid or double free', result.stderr)
        self.assertRegex(result.stderr, re.escape(str(self.source))+r'(?:\(5:|:5:)')
        self.assertNotIn(str(self.output/'main.odin'), result.stderr)

    def test_standalone_asan_survives_moving_and_removing_generated_sources(self):
        self.build('''package main
import "core:fmt"
main :: proc() {
    label := f"probe {7}"
    defer delete(label)
    pointer := new(int)
    free(rawptr(pointer))
    fmt.println(crash(pointer))
}
crash :: proc(pointer: ^int) -> int {
    marker := "界😀"; _ = marker; return pointer^
}
''')
        data = read_binary(self.exe)
        self.assertEqual(data['version'], 1)
        self.assertNotIn('sources', data)
        moved = self.root/'moved with spaces'; moved.mkdir()
        shutil.copy2(self.exe, moved/'program')
        shutil.copytree(Path(str(self.exe)+'.dSYM'), moved/'program.dSYM')
        shutil.rmtree(self.output)
        result = self.run_program(executable=moved/'program')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('heap-use-after-free', result.stderr)
        self.assertIn(str(self.source)+':11:', result.stderr)
        self.assertNotIn('/generated/', result.stderr)

    def test_generated_fstring_leak_and_asan_environment_overrides(self):
        self.build('package main\nmain :: proc() {\n    text := f"value {7}"\n    _ = text\n}\n')
        result = self.run_program()
        self.assertIn(str(self.source)+':3:', result.stderr)
        self.assertNotIn('/generated/', result.stderr)
        self.build('''package main
import "core:fmt"
main :: proc() {
    pointer := new(int)
    free(rawptr(pointer))
    fmt.println(pointer^)
}
''')
        result = self.run_program(env={'ASAN_OPTIONS': 'symbolize=0'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('heap-use-after-free', result.stderr)
        self.assertNotIn('.thor:', result.stderr)
        result = self.run_program(env={'ASAN_OPTIONS': 'external_symbolizer_path=/tmp/llvm-symbolizer-thor-missing'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('heap-use-after-free', result.stderr)

    def test_user_sanitizer_hooks_and_function_opt_out_are_respected(self):
        self.build('''package main
import "core:fmt"
@(export, no_sanitize_address, link_name="__asan_default_options")
custom_options :: proc "c" () -> cstring { return "symbolize=0" }
@(no_sanitize_address)
work :: proc(value:int)->int { return value+1 }
main :: proc() {
    assert(work(6)==7)
    pointer:=new(int)
    free(rawptr(pointer))
    fmt.println(pointer^)
}
''')
        result = self.run_program()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('heap-use-after-free', result.stderr)
        self.assertNotIn('.thor:', result.stderr)
        generated = next(self.output.rglob('main.odin')).read_text()
        work = generated[generated.index('work :: proc'):generated.index('main :: proc')]
        self.assertNotIn('_debug_entry', work)
        # A comment mentioning the hook must not disable automatic mapping.
        self.build('''package main
// __asan_default_options is configured by Thor.
main :: proc() { pointer:=new(int); free(rawptr(pointer)); assert(pointer^==0) }
''')
        result = self.run_program()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.source)+':3:', result.stderr)

    def test_excluded_platform_hook_does_not_disable_automatic_mapping(self):
        self.build('''package main
import "hooks"
main::proc(){assert(hooks.VALUE==0);p:=new(int);free(rawptr(p));assert(p^==1)}
''', files={
            'hooks/hooks.thor':'package hooks\nVALUE::0\n',
            'hooks/hooks_linux.thor':'''package hooks
@(export,no_sanitize_address,link_name="__asan_default_options")
options::proc "c" ()->cstring{return "symbolize=0"}
''',
        })
        result=self.run_program()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('heap-use-after-free',result.stderr)
        self.assertIn(str(self.source)+':3:',result.stderr)

    def test_explicit_symbolizer_with_a_conditional_user_hook(self):
        self.build('''package main
when false {
    @(export,no_sanitize_address,link_name="__asan_default_options")
    options::proc "c" ()->cstring{return "symbolize=0"}
}
main::proc(){p:=new(int);free(rawptr(p));assert(p^==1)}
''')
        result=self.run_program(env={'ASAN_SYMBOLIZER_PATH':str(ROOT/'llvm-symbolizer-thor')})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('heap-use-after-free',result.stderr)
        self.assertIn(str(self.source)+':6:',result.stderr)

    def test_debug_compiler_errors_and_compile_time_cache_are_unchanged(self):
        self.source.write_text('package main\nmain :: proc() {\n    value: int = "wrong"\n    _ = value\n}\n')
        result = subprocess.run([str(THOR), 'build', str(self.source), '-debug',
            '--output-dir', str(self.output), '-out:'+str(self.exe)], cwd=self.root,
            capture_output=True, text=True, timeout=90)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.source)+'(3:', result.stderr)
        self.assertNotIn(str(self.output/'main.odin'), result.stderr)
        code = 'package main\nwork :: fn(n:int)->int{return n*n}\nVALUE :: #run work(7)\nmain :: proc(){assert(VALUE==49)}\n'
        self.build(code, ['-debug', '--cache-report'])
        self.build(code, ['-debug', '--cache-report', '--track-allocations=off', '--no-sanitize'])
        stats = next(json.loads(line)['thor_cache'] for line in self.build_result.stdout.splitlines()
                     if line.startswith('{"thor_cache"'))
        self.assertGreater(stats['hits'], 0, stats)
        self.assertEqual(stats['misses'], 0, stats)

    def test_expression_helpers_stack_and_global_reports(self):
        self.build('''package main
import "core:fmt"
import "core:os"
data: [4]int
main :: proc() {
    label := f"start {1}"
    defer delete(label)
    switch os.args[1] {
    case "expression":
        pointer := new(int)
        free(rawptr(pointer))
        text := f"value {switch {case true: pointer^; case: 0}}"
        fmt.println(text)
    case "stack":
        local: [4]int
        pointer := cast([^]int)&local
        fmt.println(pointer[len(os.args)+2])
    case "global":
        pointer := cast([^]int)&data
        fmt.println(pointer[len(os.args)+2])
    }
}
''')
        for kind, error, line in [('expression', 'heap-use-after-free', 12),
                                  ('stack', 'stack-buffer-overflow', 17),
                                  ('global', 'global-buffer-overflow', 20)]:
            with self.subTest(kind=kind):
                result = self.run_program(kind)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn(error, result.stderr)
                self.assertIn(str(self.source)+f':{line}:', result.stderr)
                self.assertNotIn('/generated/', result.stderr)
                self.assertNotIn('__thor_', result.stderr)
        result = self.run_program('global')
        self.assertIn("defined in '"+str(self.source)+":4'", result.stderr)

    def test_big_number_copy_leak_trace_identifies_original_thor_owner(self):
        self.build('''package main
main::proc(){
    value:=Integer(123456789012345678901234567890)
    copy:=value
    delete(value)
    _=copy // Intentionally leak the independent copy to test its report.
}
''', ['-debug','--track-allocations=trace','--no-sanitize'])
        result=self.run_program()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('with call stacks',result.stderr)
        self.assertIn(str(self.source)+':4:',result.stderr)
        self.assertNotIn('/generated/',result.stderr)
        self.assertNotIn('__thor_',result.stderr)

    def test_trace_and_imported_thor_allocation_locations(self):
        self.build('''package main
import "helper"
main :: proc() { _ = helper.allocate() }
''', ['-debug', '--track-allocations=trace', '--no-sanitize'],
            {'helper/main.thor': 'package helper\nallocate :: proc() -> ^int {\n    return new(int)\n}\n'})
        result = self.run_program()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('with call stacks', result.stderr)
        self.assertIn(str(self.root/'helper/main.thor')+':3:', result.stderr)
        self.assertIn(str(self.source)+':3:', result.stderr)
        self.assertNotIn('/generated/', result.stderr)

    def test_normal_build_has_no_memory_support_and_debug_opt_out_removes_it(self):
        source = 'package main\nmain :: proc() {}\n'
        for flags in [[], ['-debug', '--track-allocations=off', '--no-sanitize']]:
            manifest = self.build(source, flags)
            self.assertNotIn('memory_debug', manifest)
            self.assertNotIn('-sanitize:address', manifest['odin_flags'])
            generated = next(self.output.rglob('main.odin')).read_text()
            self.assertNotIn('memory_', generated)
            self.assertNotIn('__thor_map', generated)
        self.assertEqual(self.run_program().returncode, 0)

    def test_native_test_tracker_is_not_replaced(self):
        self.source.write_text('''package main
import "core:testing"
@(test) check :: proc(t: ^testing.T) {
    text := f"test {1}"
    defer delete(text)
    testing.expect(t, text == "test 1")
}
''')
        result = subprocess.run([str(THOR), 'test', str(self.source), '-debug',
            '-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true', '--output-dir', str(self.output)],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        manifest = json.loads((self.output/'thor-manifest.json').read_text())
        self.assertEqual(manifest['memory_debug']['tracking'], 'off')


if __name__ == '__main__':
    unittest.main()

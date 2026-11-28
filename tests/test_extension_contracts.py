"""Executable language contracts. The programs come directly from the specification."""
import json
import re
import shutil
import subprocess
import unittest

from tests import test_migrate as migration
from tests import test_rust_compiler as native


SPEC = native.ROOT / 'docs/extension-contracts.md'
EXAMPLE = re.compile(r'^<!-- contract: ([\w-]+) -->\n```(thor|odin)\n(.*?)^```$', re.M | re.S)


def examples():
    return EXAMPLE.findall(SPEC.read_text(encoding='utf-8'))


def program(name):
    return next(source for identifier, _, source in examples() if identifier == name)


class ExtensionContracts(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare

    def test_every_marked_example_has_a_verdict(self):
        cases = examples()
        self.assertCountEqual([(name, language) for name, language, _ in cases], [
            ('migration', 'odin'), ('reflected-type', 'odin'), ('owned', 'thor'), ('borrowed', 'thor'),
            ('cache', 'thor'), ('vet', 'thor'),
        ])
        markdown = SPEC.read_text(encoding='utf-8')
        self.assertEqual(len(cases), markdown.count('<!-- contract:'))
        self.assertEqual(len(cases), len(re.findall(r'^```(?:thor|odin)$', markdown, re.M)))

    def test_fn_result_ownership(self):
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(program('owned'), flags=['--comptime=required', *flags], expected='5 11')
                for tail in ('alias[1:]', 'alias[:0]'):
                    with self.subTest(tail=tail):
                        borrowed = program('borrowed').replace('alias[1:]', tail)
                        self.compare(borrowed, command='build', flags=flags, fail='cannot return borrowed storage')

    def cached_run(self, mode, offset, expected, *, disabled=False):
        flags = ['--comptime='+mode, '--cache-report']
        if offset is not None:
            flags.append('-define:OFFSET='+str(offset))
        if disabled:
            flags.append('--no-cache')
        result = self.invoke('rust', flags=flags, directory=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        lines = result.stdout.splitlines()
        reports = [line for line in lines if line.startswith('{"thor_cache"')]
        self.assertEqual([line for line in lines if line not in reports], [expected])
        self.assertEqual(len(reports), 1, result.stdout)
        return json.loads(reports[0])['thor_cache']

    def test_required_execution_replays_independent_owned_values(self):
        self.source.write_text(program('cache'))
        for mode in ('required', 'normal', 'aggressive', 'override'):
            with self.subTest(mode=mode):
                cache = self.root / 'cache'
                if cache.exists():
                    shutil.rmtree(cache)
                cold = self.cached_run(mode, 3, '99 7 10')
                warm = self.cached_run(mode, 3, '99 7 10')
                self.assertGreater(cold['writes'], 0)
                self.assertGreater(warm['hits'], 0)
                self.cached_run(mode, 11, '99 7 18')
                self.cached_run(mode, 11, '99 7 18', disabled=True)

    def test_warm_cache_revalidates_transitive_ownership_and_values(self):
        source = program('cache')
        start = source.index('make_values ::')
        end = source.index('saved ::')
        helper = self.app / 'values.thor'
        helper_source = 'package main\n' + source[start:end]
        self.source.write_text(source[:start] + source[end:])
        helper.write_text(helper_source)
        self.cached_run('required', 3, '99 7 10')
        helper.write_text(helper_source.replace('n + OFFSET', 'n - OFFSET'))
        self.cached_run('required', 3, '99 7 4')
        warm = self.cached_run('required', 3, '99 7 4')
        self.assertGreater(warm['hits'], 0)
        helper.write_text('package main\nmake_values :: fn(n: int) -> []int { []int{n, n} }\n')
        for flags in ([], ['--no-cache']):
            bad = self.invoke('rust', 'check', ['--comptime=required', *flags], directory=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn('cannot return borrowed storage', bad.stderr)

    def test_cached_run_cannot_override_native_bounds(self):
        self.source.write_text('''package main
import "core:fmt"
read :: fn(xs: []int, i: int) -> int { xs[i] }
main :: proc() { fmt.println(#run read([]int{3, 7}, -1)) }
''')
        self.cached_run('required', None, '7')
        warm = self.cached_run('required', None, '7')
        self.assertGreater(warm['hits'], 0)
        for flags in ([], ['--no-cache']):
            result = self.invoke('rust', flags=['--odin-compatible', '--comptime=required', *flags])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('execution failed', result.stderr)
        self.cached_run('required', None, '7')

    def test_warm_cache_observes_callees_file_bounds(self):
        self.source.write_text('''package main /*odin-bounds*/
import "core:fmt"
main :: proc() { fmt.println(#run last(-1)) }
''')
        helper = self.app / 'helper.thor'
        body = '\nlast :: fn(i: int) -> int { xs := [3, 7]; return xs[i] }\n'
        helper.write_text('package main' + body)
        self.cached_run('required', None, '7')
        warm = self.cached_run('required', None, '7')
        self.assertGreater(warm['hits'], 0)
        helper.write_text('package main /*odin-bounds*/' + body)
        for flags in ([], ['--no-cache']):
            bad = self.invoke('rust', flags=['--comptime=required', *flags], directory=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn('execution failed', bad.stderr)
        helper.write_text('package main' + body)
        self.cached_run('required', None, '7')

    def test_immutable_apply_preserves_both_configurations(self):
        initial = program('vet')
        for active in ('false', 'true'):
            with self.subTest(apply_under=active):
                self.source.write_text(initial)
                flags = ['--apply', '--vet=immutable,unused', '-define:CHANGE='+active]
                for stage in ('before', 'after'):
                    if stage == 'after':
                        edited = self.invoke('rust', 'vet', flags)
                        self.assertEqual(edited.returncode, 0, edited.stderr)
                        self.assertEqual(self.source.read_text(), initial.replace('base :=', 'base :>'))
                    for value, expected in (('false', '4 5'), ('true', '4 7')):
                        for build in ([], native.UNCHECKED):
                            run = self.invoke('rust', flags=['-define:CHANGE='+value, *build])
                            self.assertEqual((run.returncode, run.stdout.strip()), (0, expected), run.stderr)
                once = self.source.read_bytes()
                twice = self.invoke('rust', 'vet', flags)
                self.assertEqual(twice.returncode, 0, twice.stderr)
                self.assertEqual(self.source.read_bytes(), once)


class OdinCompatibilityContract(unittest.TestCase):
    setUpClass = classmethod(migration.MigrationTests.setUpClass.__func__)
    setUp = migration.MigrationTests.setUp
    write = migration.MigrationTests.write
    snapshot = migration.MigrationTests.snapshot
    migrate = migration.MigrationTests.migrate
    compare_builds = migration.MigrationTests.compare_builds

    def test_reflected_type_name_after_migration(self):
        self.write(program('reflected-type'))
        self.migrate()
        baseline = subprocess.run(['odin', 'run', '.', '-out:'+str(self.root / ('baseline'+migration.EXE_SUFFIX))],
                                  cwd=self.source, capture_output=True, text=True, timeout=60)
        self.assertEqual((baseline.returncode, baseline.stdout),
                         (0, 'Integer\nBox($Rational=i16, $set=2){values = [0, 0]}\n'), baseline.stderr)
        actual = subprocess.run([migration.COMPILER, 'run', '.'],
                                cwd=self.output, capture_output=True, text=True, timeout=60)
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(actual.stdout, baseline.stdout)

    def test_reflection_of_migrated_generic_type_and_value_parameters(self):
        self.write('''package main
import "core:fmt"
import "lib"
main :: proc() {
    a := lib.fn(or_panic=i16, set=2){}
    b := lib.Box(Integer=i8, Rational=1){}
    fmt.println(typeid_of(type_of(a)), a.items, b)
    fmt.println(typeid_of(lib.Alias), size_of(lib.Alias), typeid_of(lib.Integer_odin))
    fmt.println(typeid_of(lib.Local), typeid_of(lib.or_panic), typeid_of(lib.set))
}
''', {'lib/records.odin': '''package lib
fn :: struct($or_panic: typeid, $set: int) { items: [set]or_panic }
Box :: struct(Integer: typeid, Rational: int) { data: [Rational]Integer }
Local :: fn(i16, 2)
or_panic :: distinct u16
set :: struct { value: u8 }
Integer_odin :: distinct u8
Alias :: Integer
''', 'lib/conditional.odin': '''package lib
when #config(WIDE, false) { Integer :: distinct i64 } else { Integer :: distinct i16 }
'''})
        self.migrate()
        for flags, size in [([], 2), (['-define:WIDE=true', '-disable-assert'], 8), (native.UNCHECKED, 2)]:
            self.compare_builds(flags, expected=(
                'fn($or_panic=i16, $set=2) [0, 0] Box($Integer=i8, $Rational=1){data = [0]}\n'
                f'Integer {size} Integer_odin\nfn($or_panic=i16, $set=2) or_panic set\n'))

    def test_plain_run_mixes_converted_and_new_files(self):
        self.write('''package main
import "core:fmt"
import "core:os"
read :: proc(xs: []int, i: int) -> int { return xs[i] }
main :: proc() {
    xs := [2]int{3, 7}
    fmt.println(read(xs[:], 1))
}
''')
        self.migrate()
        entry = self.output / 'main.thor'
        entry.write_text(entry.read_text().replace('fmt.println(read(xs[:], 1))', '''
    if len(os.args) > 1 {
        if os.args[1] == "native" { fmt.println(native_read(xs[:], -1)); return }
        fmt.println(from_thor(xs[:], -1))
        return
    }
    fmt.println(last(xs[:], -1))
'''))
        (self.output / 'new.thor').write_text('''package main
// package main /*odin-bounds*/
NOTE :: `package main /*odin-bounds*/`
last :: proc(xs: []int, i: int) -> int { return xs[i:][0] }
from_thor :: proc(xs: []int, i: int) -> int { return read(xs, i) }
''')
        (self.output / 'native.odin').write_text(
            'package main\nnative_read :: proc(xs: []int, i: int) -> int { return xs[i] }\n')
        for args, success in [([], True), (['--', 'negative'], False), (['--', 'native'], False),
                              (['--odin-compatible'], False)]:
            run = subprocess.run([migration.COMPILER, 'run', '.', *args], cwd=self.output,
                                 capture_output=True, text=True, timeout=60)
            if success:
                self.assertEqual((run.returncode, run.stdout.strip()), (0, '7'), run.stderr)
            else:
                self.assertNotEqual(run.returncode, 0)
                self.assertRegex(run.stderr + run.stdout, r'(?i)(bounds|indices)')

    def test_migrated_native_sequence_bounds(self):
        self.write('''package main
import "core:fmt"
import "core:os"
take :: proc(xs: $T, i: int) -> int { return xs[i] }
main :: proc() {
    values := [2]int{3, 7}
    index := 1
    if len(os.args) > 2 { index = -1 }
    switch os.args[1] {
    case "read": fmt.println(take(values, index))
    case "write": values[index] += 2; fmt.println(values[1])
    case "address": slot := &values[index]; slot^ += 1; fmt.println(values[1])
    case "slice": fmt.println(values[index:])
    case "end": fmt.println(values[:index])
    }
}
''')
        self.migrate()
        original = self.root / ('baseline'+migration.EXE_SUFFIX)
        converted = self.root / ('candidate'+migration.EXE_SUFFIX)
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            for compiler, directory, out, policy in (
                ('odin', self.source, original, []),
                (migration.COMPILER, self.output, converted, []),
            ):
                built = subprocess.run([compiler, 'build', '.', '-out:'+str(out), *flags, *policy],
                                       cwd=directory, capture_output=True, text=True, timeout=90)
                self.assertEqual(built.returncode, 0, built.stderr)
            for operation, output in [('read', '7'), ('write', '9'), ('address', '8'),
                                      ('slice', '[7]'), ('end', '[3]')]:
                for binary in (original, converted):
                    good = subprocess.run([binary, operation], capture_output=True, text=True, timeout=10)
                    self.assertEqual((good.returncode, good.stdout.strip()), (0, output), good.stderr)
                    if '-no-bounds-check' in flags:
                        continue
                    bad = subprocess.run([binary, operation, 'negative'], capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(bad.returncode, 0, (binary, operation, bad.stdout))
                    self.assertRegex(bad.stdout + bad.stderr, r'(?i)(bounds|indices)')

    def test_original_and_migrated_colliding_names(self):
        self.write(program('migration'))
        self.migrate()
        for flags in ([], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare_builds(flags=flags, expected='2 4 6\n')

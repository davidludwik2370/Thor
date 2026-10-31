"""Stage-one big-number runtime: native tests, independent oracles, and imports."""
from fractions import Fraction
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest

from tests import test_thor as base


ROOT = Path(__file__).resolve().parents[1]


class BigRuntimeTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_native_runtime_with_allocation_tracking(self):
        odin = os.environ.get('THOR_ODIN', 'odin')
        if not shutil.which(odin):
            self.skipTest('Odin is required')
        with tempfile.TemporaryDirectory(prefix='thor-big-native-') as temporary:
            result = subprocess.run([odin, 'test', str(ROOT / 'runtime' / 'big'),
                '-out:' + str(Path(temporary) / 'big-tests'),
                '-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'],
                text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_commented_runtime_demo(self):
        result = subprocess.run([str(base.THOR), 'run', str(ROOT / 'examples' / 'big_runtime'),
            '--track-allocations=basic'], text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Repeating: 1/6', result.stdout)
        self.assertIn('Independent copy: -3/4', result.stdout)
        self.assertIn('All big-number runtime examples passed.', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_integer_reference_values_and_thousands_of_digits(self):
        rng = random.Random(0x54484F52)
        cases = []
        for bits in [0, 1, 7, 8, 63, 64, 127, 128, 129, 255, 512, 2048, 8192]:
            for sign in [1, -1]:
                value = sign * rng.getrandbits(bits)
                for radix, prefix, digits in [
                    (2, '0b', format(abs(value), 'b')),
                    (8, '0o', format(abs(value), 'o')),
                    (10, '', str(abs(value))),
                    (16, '0x', format(abs(value), 'x')),
                ]:
                    # Group from the left, independently of byte/limb boundaries.
                    grouped = '_'.join(digits[i:i+5] for i in range(0, len(digits), 5))
                    cases.append((('-' if value < 0 else '+') + prefix + grouped, str(value)))
        # Do not pass this through Python's default 4,300-digit int parser either.
        huge = '1234567890' * 600
        cases.extend([(huge, huge), ('-' + huge, '-' + huge)])
        pairs = ',\n'.join('{' + json.dumps(source) + ',' + json.dumps(expected) + '}'
                             for source, expected in cases)
        self.invoke(self.program('''
cases := [][2]string{''' + pairs + '''}
for item in cases {
    value, ok := big.parse_integer(item[0])
    assert(ok)
    text := big.to_string(value)
    if text != item[1] { fmt.println("input:", item[0], "actual:", text, "expected:", item[1]) }
    assert(text == item[1])
    delete(text)
    big.destroy(&value)
}
''', 'import "thor:big"\nimport "core:fmt"\n'), flags=('--track-allocations=basic',))

    def test_rational_reference_values(self):
        rng = random.Random(0x524154)
        cases = [('0.(9)', Fraction(1)), ('0.(000001)', Fraction(1, 999999)),
                 ('123.45(89)', Fraction(305561, 2475)),
                 ('1e4096', Fraction(10)**4096), ('1e-4096', Fraction(10)**-4096)]
        for _ in range(120):
            whole = str(rng.randrange(10**rng.randrange(1, 75)))
            digits = ''.join(str(rng.randrange(10)) for _ in range(rng.randrange(0, 45)))
            cycle = ''.join(str(rng.randrange(10)) for _ in range(rng.randrange(0, 30)))
            exponent = rng.randrange(-30, 31)
            negative = rng.choice([False, True])
            if cycle:
                # Difference-of-concatenations oracle, rather than the runtime's
                # multiply-by-all-nines construction.
                numerator = int(whole + digits + cycle) - int(whole + digits)
                denominator = 10**len(digits) * (10**len(cycle) - 1)
                expected = Fraction(numerator, denominator)
                source = whole + '.' + digits + '(' + cycle + ')'
            else:
                source = whole + '.' + (digits or '0')
                expected = Fraction(source)
            expected *= Fraction(10)**exponent
            source += 'e' + str(exponent)
            if negative:
                source = '-' + source
                expected = -expected
            cases.append((source, expected))
        for _ in range(30):
            numerator = rng.randrange(-10**150, 10**150)
            denominator = rng.choice([-1, 1]) * rng.randrange(1, 10**120)
            cases.append((f'{numerator}/{denominator}', Fraction(numerator, denominator)))
        pairs = ',\n'.join('{' + json.dumps(source) + ',' +
                             json.dumps(f'{expected.numerator}/{expected.denominator}') + '}'
                             for source, expected in cases)
        self.invoke(self.program('''
cases := [][2]string{''' + pairs + '''}
for item in cases {
    value, ok := big.parse_rational(item[0])
    assert(ok)
    text := big.to_string(value)
    if text != item[1] { fmt.println("input:", item[0], "actual:", text, "expected:", item[1]) }
    assert(text == item[1])
    delete(text)
    big.destroy(&value)
}
''', 'import "thor:big"\nimport "core:fmt"\n'), flags=('--track-allocations=basic',))

    def test_fresh_project_imports_and_test_file_exclusion(self):
        root, _ = self.invoke(self.program('''
value, ok := big.parse_rational("0.1(6)")
assert(ok)
copy := big.clone(value)
big.set_fraction(&value, numerator=2, denominator=-8)
text := big.to_string(copy)
assert(text == "1/6")
delete(text)
big.destroy(&value)
big.destroy(&copy)
''', 'import "thor:big"\n'), flags=('--track-allocations=basic',))
        manifest = json.loads((root / 'output' / 'thor-manifest.json').read_text())
        self.assertNotIn('big_test.odin', json.dumps(manifest))
        # Selection/wildcard imports use the existing language mechanisms.
        for declaration in ['import * "thor:big"',
                            'import _ "thor:big" / parse_integer, to_string, destroy']:
            with self.subTest(declaration=declaration):
                self.invoke(self.program('''
value, ok := parse_integer("999999999999999999999999999999999999999999")
assert(ok)
text := to_string(value)
assert(text == "999999999999999999999999999999999999999999")
delete(text)
destroy(&value)
''', declaration + '\n'))

    def test_public_errors_panic_instead_of_silently_returning_zero(self):
        root, _ = self.invoke(self.program('''
switch os.args[1] {
case "fraction": _ = big.fraction(1, 0)
case "zero_fraction": _ = big.fraction(0, 0)
case "parse": _, _ = big.parse_rational("1/0")
case "parse_pair": _, _ = big.parse_fraction("1", "-0")
case "allocation": _ = big.from_integer(1, allocator=mem.nil_allocator())
case "parse_allocation": _, _ = big.parse_integer("123", allocator=mem.nil_allocator())
case "allocator_integer": _, _ = big.parse_integer("123", allocator=mem.Allocator{bad_allocator, nil})
case "allocator_rational": _, _ = big.parse_rational("0.1(6)", allocator=mem.Allocator{bad_allocator, nil})
case "allocator_fraction": _, _ = big.parse_fraction("22", "13", allocator=mem.Allocator{bad_allocator, nil})
case "size": _, _ = big.parse_rational("1e99999999999999999999999999999999999999")
case "invalid":
    value, ok := big.parse_rational("1/not_a_number")
    assert(!ok && big.is_zero(value))
    big.destroy(&value)
}
''', '''import "thor:big"
import "core:mem"
import "core:os"
bad_allocator :: proc(data: rawptr, mode: mem.Allocator_Mode, size, alignment: int,
    old: rawptr, old_size: int, loc := #caller_location) -> ([]byte, mem.Allocator_Error) {
    return nil, .Invalid_Argument
}
'''), command='build')
        executable = root / (root.name + ('.exe' if os.name == 'nt' else ''))
        for argument in ['fraction', 'zero_fraction', 'parse', 'parse_pair',
                         'allocation', 'parse_allocation', 'allocator_integer',
                         'allocator_rational', 'allocator_fraction', 'size', 'invalid']:
            with self.subTest(argument=argument):
                result = subprocess.run([str(executable), argument], text=True, capture_output=True, timeout=10)
                if argument == 'invalid':
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    expected = ('allocator failed' if argument.startswith('allocator_') else
                                'size is too large' if argument == 'size' else
                                'allocation failed' if 'allocation' in argument else
                                'denominator must not be zero')
                    self.assertIn(expected, result.stdout + result.stderr)

    def test_native_constructor_inputs_reject_implicit_float_conversion(self):
        for expression in ['big.from_integer(f64(1.5))', 'big.fraction(1.5, 2)',
                           'big.fraction(1, f32(2))', 'big.rational_from_integer(true)']:
            with self.subTest(expression=expression):
                self.invoke(self.program('_ = ' + expression, 'import "thor:big"\n'),
                            command='check', fail='Error:')


if __name__ == '__main__':
    unittest.main()

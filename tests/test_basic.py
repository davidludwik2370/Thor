"""The bundled basic package, imported from projects outside the Thor checkout."""
import json
import subprocess
import unittest

from tests import test_thor as base


class BasicTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_default_import_sorting_and_explicit_comparators(self):
        self.invoke(self.program('''
data := []int{3, 1, 2}
ascending := basic.sorted(data)
defer delete(ascending)
assert(ascending[0] == 1 && ascending[2] == 3 && data[0] == 3)
descending := basic.sorted(data, proc(a, b: int) -> bool { return a > b })
defer delete(descending)
assert(descending[0] == 3 && descending[2] == 1)
floats := basic.sorted([]f64{1.5, -2, 4})
defer delete(floats)
assert(floats[0] == -2 && floats[2] == 4)
Record :: struct { key: int }
records := basic.sorted([]Record{{3}, {1}}, less=proc(a, b: Record) -> bool { return a.key < b.key })
defer delete(records)
assert(records[0].key == 1)
empty := basic.sorted([]int{})
defer delete(empty)
assert(len(empty) == 0)
assert(basic.type(data) == []int && basic.size(data) == size_of([]int))
PARTS :: basic.CONCAT([1]string{"Thor"}, [1]string{"basic"})
assert(len(PARTS) == 2 && PARTS[0] == "Thor" && PARTS[1] == "basic")
''', 'import "thor:basic"\n'))

    def test_selected_imports_aliases_and_native_consumers(self):
        self.invoke(self.program('''
result := sorted([]int{2, 1})
defer delete(result)
assert(result[0] == 1 && b.asc(1, 2, 3) && native())
print("basic imported")
''', 'import b "thor:basic" / sorted, print\n'), directory=True, files={
            'native.odin': '''package main
import "thor:basic"
native :: proc() -> bool {
    values := basic.sorted([]int{4, 2})
    defer delete(values)
    return values[0] == 2
}
'''}, expected='basic imported')

    def test_order_checks_including_empty_and_variadic_forwarding(self):
        self.invoke(self.program('''
assert(basic.asc(1) && basic.asc(1, 1, 2) && !basic.asc(2, 1))
assert(basic.desc(1) && basic.desc(3, 3, 1) && !basic.desc(1, 2))
assert(basic.ordered(..[]int{1, 2, 3}) && basic.ordered(..[]int{3, 2, 1}))
assert(basic.ordered(..[]int{4, 4}) && !basic.ordered(..[]int{1, 3, 2}))
assert(basic.ordered(..[]int{}))
assert(basic.ordered(..[]f64{3, 2, 1}))
''', 'import "thor:basic"\n'))

    def test_print_separator_and_terminator(self):
        self.invoke(self.program('basic.print("red", "blue", sep="|", end=".", flush=false)',
                                 'import "thor:basic"\n'), expected='red|blue.')

    def test_numeric_parsing_defaults_bases_and_bounds(self):
        self.invoke(self.program('''
v_int, ok_int := basic._int("0xff"); assert(ok_int && v_int == 255)
v_i64, ok_i64 := basic._i64("-42"); assert(ok_i64 && v_i64 == -42)
v_u64, ok_u64 := basic._u64("0xff"); assert(ok_u64 && v_u64 == 255)
v_i128, ok_i128 := basic._i128("-123456789012345678901"); assert(ok_i128 && v_i128 == -123456789012345678901)
assert(basic._i64("ff", 16) == 255 && basic._u64("ff", 16) == 255)
assert(basic._i128("ff", 16) == 255 && basic._uint("42") == 42)
assert(basic._i32("-2147483648") == min(i32) && basic._i16("32767") == max(i16))
assert(basic._i8("-128") == min(i8) && basic._u8("255") == max(u8))
assert(basic._u16("65535") == max(u16) && basic._u32("4294967295") == max(u32))
_, bad_i8 := basic._i8("128"); _, bad_u8 := basic._u8("-1")
_, bad_u16 := basic._u16("65536"); _, malformed := basic._int("no")
assert(!bad_i8 && !bad_u8 && !bad_u16 && !malformed)
assert(basic._f32("1.5") == 1.5 && basic._f64("2.25") == 2.25)
assert(basic._bool("true") && !basic._bool("false"))
''', 'import "thor:basic"\n'))

    def test_file_helpers_use_supplied_path_and_replace_contents(self):
        root, _ = self.invoke(self.program(''), command='transpile')
        target = root / 'content.txt'
        missing = root / 'missing.txt'
        source = self.program('''
path := ''' + json.dumps(str(target)) + '''
n, err := basic.write(path, "long first content"); assert(err == nil && n == 18)
n, err = basic.write(path, "é"); assert(err == nil && n == 2)
text, ok := basic.read(path); assert(ok && text == "é"); delete(text)
n, err = basic.write(path, ""); assert(err == nil && n == 0)
empty, empty_ok := basic.read(path); assert(empty_ok && empty == ""); delete(empty)
not_found, missing_ok := basic.read(''' + json.dumps(str(missing)) + ''')
assert(!missing_ok && not_found == "")
''', 'import "thor:basic"\n')
        self.invoke(source)
        self.assertEqual(target.read_bytes(), b'')

    def test_allocating_helpers_with_memory_tracking(self):
        self.invoke('''package basic_test
import "thor:basic"
import "core:testing"
@(test)
helpers :: proc(t: ^testing.T) {
    data := []int{3, 1, 2}
    result := basic.sorted(data, allocator=context.allocator)
    defer delete(result)
    mapped := basic.fmap(data, proc(x: int) -> int { return x * 2 })
    defer delete(mapped)
    testing.expect(t, mapped[0] == 6 && mapped[2] == 4)
    parts := basic.split("red,blue", ",")
    defer delete(parts)
    testing.expect(t, len(parts) == 2 && parts[1] == "blue")
    joined := basic.join(parts, "|")
    defer delete(joined)
    testing.expect_value(t, joined, "red|blue")
    // clone has ordinary value-copy semantics, including shallow references.
    copied := basic.clone(struct{value: int}{7})
    testing.expect_value(t, copied.value, 7)
    alias := basic.clone(data)
    testing.expect(t, raw_data(alias) == raw_data(data))
}
''', command='test', flags=('-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true',))

    def test_input_handles_multiple_lines_empty_lines_and_eof(self):
        root, _ = self.invoke(self.program('''
expected_lines := []string{"first", "", "é😀", "last"}
for expected in expected_lines {
    line, err := basic.input()
    assert(line == expected)
    if expected == "last" { assert(err == .EOF) } else { assert(err == nil) }
    delete(line)
}
empty, err := basic.input()
assert(empty == "" && err == .EOF)
delete(empty)
''', 'import "thor:basic"\n'), command='build')
        result = subprocess.run([str(root / 'output' / 'program')], input='first\n\né😀\r\nlast',
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_private_implementation_is_not_exported(self):
        self.invoke(self.program('_, _ = basic._parse_int("7", int)', 'import "thor:basic"\n'),
                    command='check', fail='private')

    def test_commented_basic_example(self):
        result = subprocess.run([str(base.THOR), 'run', str(base.ROOT / 'examples' / 'basic')],
                                text=True, capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Basic package examples passed.', result.stdout)


if __name__ == '__main__':
    unittest.main()

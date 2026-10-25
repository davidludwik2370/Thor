"""Behavioral acceptance tests: generated code is checked/run by real Odin."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
THOR = ROOT / "thor"


class ThorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run([str(THOR), "--version"], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def invoke(self, source, *, files=None, command="run", fail=None, flags=(), args=(), expected=None, directory=False):
        temporary = tempfile.TemporaryDirectory(prefix="thor-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        entry = root / "input" / "main.thor"
        entry.parent.mkdir()
        entry.write_text(source, encoding="utf-8")
        for name, content in (files or {}).items():
            path = entry.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        argv = [str(THOR), command, str(entry.parent if directory else entry), "--output-dir", str(root / "output"), *flags]
        if args:
            argv += ["--", *args]
        result = subprocess.run(argv, text=True, capture_output=True, timeout=40)
        combined = result.stdout + result.stderr
        if fail is not None:
            self.assertNotEqual(result.returncode, 0, combined)
            self.assertIn(fail, combined)
        else:
            self.assertEqual(result.returncode, 0, combined)
            if expected is not None:
                self.assertEqual(result.stdout.strip(), expected)
        self.assertEqual(entry.read_text(), source, "compiler changed the source")
        return root, result

    def program(self, body, prefix=""):
        return "package main\n" + prefix + "\nmain :: proc() {\n" + body + "\n}\n"

    # T01: command behavior and ordinary Odin compatibility.
    def test_help_and_version(self):
        for flag in ("--help", "--version"):
            result = subprocess.run([str(THOR), flag], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)

    def test_cli_transpile(self):
        root, result = self.invoke(self.program("x := [1, 2]; assert(x[1] == 2)"), command="transpile")
        output = Path(result.stdout.strip())
        self.assertTrue(output.is_file())
        self.assertTrue((root / "output" / "thor-manifest.json").is_file())

    def test_cli_check(self):
        self.invoke(self.program("assert(2 + 2 == 4)"), command="check")

    def test_cli_build(self):
        root, _ = self.invoke(self.program('fmt.println("built")', 'import "core:fmt"\n'), command="build")
        executable = root / "output" / ("program.exe" if os.name == "nt" else "program")
        run = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout.strip(), "built")

    def test_cli_run_arguments(self):
        self.invoke(self.program('assert(os.args[1] == "one two"); assert(os.args[2] == "three")', 'import "core:os"\n'), args=("one two", "three"))

    def test_cli_odin_test(self):
        self.invoke('package demo\nimport "core:testing"\n@(test)\nworks :: proc(t:^testing.T) { testing.expect(t, 2 in [1,2,3]) }\n', command="test")

    def test_cli_missing_source(self):
        result = subprocess.run([str(THOR), "check", "/does/not/exist.thor"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    def test_generated_filename_collision(self):
        self.invoke(self.program(""), directory=True, files={"main.odin": self.program("")}, fail="collision")

    def test_rebuild_removes_only_owned_stale_sources(self):
        root, _ = self.invoke(self.program(""), command="transpile", directory=True,
                              files={"old.thor": "package main\nold :: 1\n"})
        (root / "input" / "old.thor").unlink()
        preserved = root / "output" / "keep.txt"
        preserved.write_text("keep")
        result = subprocess.run([str(THOR), "transpile", str(root / "input"), "--output-dir", str(root / "output")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((root / "output" / "old.odin").exists())
        self.assertEqual(preserved.read_text(), "keep")
        manifest = json.loads((root / "output" / "thor-manifest.json").read_text())
        self.assertEqual(manifest["generated"], ["main.odin", "main.odin.map.json"])

    def test_output_directory_protection(self):
        root, _ = self.invoke(self.program(""), command="transpile")
        unrelated = root / "unrelated"
        unrelated.mkdir()
        marker = unrelated / "keep.txt"
        marker.write_text("keep")
        for destination, diagnostic in ((unrelated, "not managed by Thor"), (root / "input", "source files")):
            with self.subTest(destination=destination):
                result = subprocess.run([str(THOR), "transpile", str(root / "input"), "--output-dir", str(destination)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(diagnostic, result.stderr)
        self.assertEqual(marker.read_text(), "keep")

    def test_collection_package(self):
        root, _ = self.invoke(self.program(""), command="transpile", files={
            "collection/lib/library.thor": "package library\nvalue :: proc()->int {return 2 in [1,2] ? 7 : 0}\n",
        })
        entry = root / "input" / "main.thor"
        entry.write_text(self.program('assert(value() == 7)', 'import "custom:lib" / value\n'))
        result = subprocess.run([str(THOR), "run", str(entry), "--output-dir", str(root / "collection-output"),
                                 f"-collection:custom={root / 'input' / 'collection'}"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mixed_package(self):
        self.invoke(self.program("assert(from_odin() == 7); assert(from_thor() == 2)"), directory=True,
                    files={"other.odin": "package main\nfrom_odin :: proc()->int {return 7}\n",
                           "additional.thor": "package main\nfrom_thor :: proc()->int {a := [1,2]; return a[1]}\n"})

    def test_resource_paths(self):
        self.invoke(self.program('assert(string(#load("assets/message.txt")) == "hello")'), files={"assets/message.txt": "hello"})

    def test_resource_paths_in_ordinary_odin_dependency(self):
        self.invoke(self.program('assert(lib.message() == "hello")', 'import "lib"\n'), files={
            "lib/library.odin": 'package lib\nmessage :: proc()->string {return #load("text.txt",string)}\n',
            "lib/text.txt": "hello",
        })

    def test_native_file_selection_ignores_inactive_sources(self):
        other_os = "windows" if os.name != "nt" else "linux"
        self.invoke(self.program("assert(true)"), directory=True, files={
            f"excluded_{other_os}.thor": 'package main\nimport "does-not-exist"\nTHIS IS INVALID\n',
            "ignored.thor": '#+ignore\npackage main\nimport "also-does-not-exist"\nINVALID\n',
            "test_only.thor": '#+test\npackage main\nimport "not-needed-for-run"\nINVALID\n',
        })

    def test_passthrough_odin_features(self):
        self.invoke('''package main
// Symbols resembling future Thor syntax are ordinary identifiers.
fn :: proc(x:int)->int {return x+1}
set :: proc(x:int)->int {return x+2}
Color :: enum {Red, Blue}
Item :: struct {value:int}
generic :: proc(x:$T)->T {return x}
main :: proc() {
    a := [?]int{1,2,3}
    s := a[1:]
    p := &a[0]
    p^ += 1
    item := Item{value=generic(4)}
    color: Color = .Blue
    switch color {case .Red: assert(false); case .Blue: assert(true)}
    count := 0
    for value in s {count += value}
    assert(count == 5 && item.value == 4)
    assert(fn(1) == 2 && set(1) == 3)
}
''')

    def test_strings_comments_and_utf8_are_not_rewritten(self):
        source = self.program('''s := "λ #if x in [1,2] ? : / sin"
raw := `#else [3,4] // not code`
// #if missing [1, 2] ? :
/* nested /* [1,2] */ comment */
assert(len(s) > 0 && len(raw) > 0)
a := ["café", "日本語"]
assert(a[1] == "日本語")''')
        self.invoke(source)

    # T02: import selection is a file-local alias, with normal namespaces.
    def test_selective_import_single_line(self):
        self.invoke(self.program('println("ok"); assert(math.sin(f64(0)) == sin(f64(0)))', 'import "core:fmt" / println\nimport "core:math" / sin\n'), expected="ok")

    def test_selective_import_multiline_comments(self):
        self.invoke(self.program('println("multi"); assert(sin(f64(0)) == 0 && cos(f64(0)) == 1)', '''import "core:fmt" / println
import m "core:math" /
    sin, // sine
    /* block */ cos
'''), expected="multi")

    def test_selective_type_constant_generic_and_group(self):
        library = '''package lib
Thing :: struct {n:int}
VALUE :: 9
identity :: proc(x:$T)->T {return x}
f_int :: proc(x:int)->int {return x}
f_float :: proc(x:f64)->f64 {return x}
overloaded :: proc{f_int, f_float}
'''
        self.invoke(self.program('t := Thing{n=VALUE}; assert(identity(t).n == 9); assert(overloaded(f64(2)) == 2)', 'import "lib" / Thing, VALUE, identity, overloaded\n'), files={"lib/lib.odin": library})

    def test_selective_import_local_shadowing_and_fields(self):
        self.invoke(self.program('''println("ok")
{ println := 5; assert(println == 5) }
s := Record{println=2}; assert(s.println == 2)
assert(local(3) == 3)''', '''import "core:fmt" / println
Record :: struct {println:int}
local :: proc(println:int)->int {return println}
'''), expected="ok")

    def test_selective_import_loop_and_generic_shadowing(self):
        self.invoke(self.program('''for println in [1,2] {assert(println > 0)}
assert(f(int(3)) == 3)
println("ok")''', '''import "core:fmt" / println
import "library" / T
f :: proc(x:$T)->T {return x}
'''), files={"library/library.odin": "package library\nT :: f64\n"}, expected="ok")

    def test_selective_import_conditional_conflicts(self):
        self.invoke(self.program('println("ok")', '''import "core:fmt" / println
#if false {println :: 3}
'''), expected="ok")
        self.invoke(self.program("", '''import "core:fmt" / println
#if true {println :: 3}
'''), fail="conflicts with selective import")

    def test_selective_import_is_file_private(self):
        self.invoke(self.program("", 'import "core:math" / sin\n'), directory=True,
                    files={"other.thor": "package main\nother :: proc(){_ = sin(f64(0))}\n@(init)\nforce :: proc(){other()}\n"}, fail="Undeclared name: sin")

    def test_selective_import_is_not_exported(self):
        self.invoke(self.program("_ = lib.sin(f64(0))", 'import "lib"\n'),
                    files={"lib/lib.thor": "package lib\nimport \"core:math\" / sin\n"}, fail="sin")

    def test_selective_import_rejects_unused_missing_private_mutable(self):
        library = 'package lib\nvariable:int\n@(private)\nsecret :: 5\n'
        for name in ("missing", "secret", "variable"):
            with self.subTest(name=name):
                self.invoke(self.program("", f'import "lib" / {name}\n'), files={"lib/lib.odin": library}, fail=name)

    def test_selective_import_rejects_conflicts(self):
        for prefix in ('import "core:math" / sin, sin\n', 'import "core:math" / sin\nsin :: 5\n'):
            with self.subTest(prefix=prefix):
                self.invoke(self.program("", prefix), fail="selective import")

    def test_selective_import_rejects_trailing_comma(self):
        self.invoke(self.program("", 'import "core:math" / sin,\n'), fail="expected")

    # T03: unfinished ternaries continue; independent statements do not merge.
    def test_multiline_ternary(self):
        self.invoke(self.program('''x := true ? 27
    : 31
assert(x == 27)'''))

    def test_multiline_ternary_nested_and_comments(self):
        self.invoke(self.program('''x := false ? 0 // first
    : true ? 2 /* nested */
        // next arm
        : 3
y := (true ?
    4
    : 5)
assert(x == 2 && y == 4)'''))

    def test_multiline_ternary_context_and_lazy_evaluation(self):
        self.invoke(self.program('''x: Color = false ? .Red
    : .Blue
assert(x == .Blue)
assert((true ? 3
    : bad()) == 3)
next := 4
assert(next == 4)''', 'Color :: enum {Red, Blue}\nbad :: proc()->int {assert(false); return 0}\n'))

    def test_multiline_ternary_missing_colon(self):
        self.invoke(self.program("x := true ? 1\n2\n_ = x"), fail="expected ':'")

    # T04: additive conditional compilation, scopes, nesting, and skipped code.
    def test_if_else_if_else(self):
        for value in (1, 2, 3):
            with self.subTest(value=value):
                self.invoke(self.program(f"assert(V == {value})", f'''MODE :: {value}
#if MODE == 1 {{V :: 1}} #else if MODE == 2 {{V :: 2}} #else {{V :: 3}}
'''))

    def test_if_nested_and_original_when(self):
        self.invoke(self.program('''#if true {
    when true {chosen := 3; assert(chosen == 3)}
} #else {this_is_undefined()}
when true {
    #if false {also_undefined()} #else {assert(V == 4)}
}
x := 2 when V == 4 else 0
assert(x == 2)''', '''when true {
    #if true { V :: 4 }
} else when false { V :: 5 } else { V :: 6 }
'''))

    def test_if_define_forwarding(self):
        self.invoke(self.program("assert(V == 8)", '''MODE :: #config(MODE, 1)
#if MODE == 8 {V :: 8} #else {V :: 1}
'''), flags=("-define:MODE=8",))

    def test_if_nonconstant_rejected(self):
        self.invoke(self.program("x := 1\n#if x == 1 {assert(true)}"), fail="Error:")

    def test_if_skips_unsupported_array_in_untaken_branch(self):
        self.invoke(self.program("#if false {x := []; _ = x} #else {assert(true)}"))

    # T05: type inference, context, values, nesting, and precise array lengths.
    def test_array_literal_scalar_families(self):
        self.invoke(self.program('''a := [1,2,3]
b := [1.5,2.5]
c := [true,false]
d := ['a','λ']
e := ["one","two"]
assert(a[2] == 3 && b[0] == 1.5 && c[0] && d[1] == 'λ' && e[1] == "two")'''))

    def test_array_literal_first_expression(self):
        self.invoke(self.program('''a := [1+2, 7]
b := [i32(1),2]
assert(a[0] == 3 && type_of(b) == [2]i32)'''))

    def test_array_literal_explicit_context_empty_and_enums(self):
        self.invoke(self.program('''a: [2]f64 = [1,2]
b: [2]Color = [.Red,.Blue]
c: [0]int = []
assert(type_of(a) == [2]f64 && b[1] == .Blue && len(c) == 0)''', 'Color :: enum {Red,Blue}\n'))

    def test_array_literal_constant_and_type_length(self):
        self.invoke(self.program("assert(A[1] == 2 && len(b) == 2)", "A :: [1,2]\nN :: 2\nb: [N]int\n"))

    def test_array_literal_nested(self):
        self.invoke(self.program('''a := [[1,2], [3,4]]
b: [2][2]f64 = [[1,2],[3,4]]
c := [[f64(1),2], [3,4]]
assert(a[1][0] == 3 && type_of(b) == [2][2]f64)
assert(type_of(c) == [2][2]f64 && c[1][1] == 4)'''))

    def test_array_literal_inferred_struct_element_context(self):
        self.invoke(self.program('''a := [Record{values=[.Red,.Blue]}, {values=[.Blue,.Red]}]
assert(a[1].values[0] == .Blue)''', 'Color :: enum {Red,Blue}\nRecord :: struct {values:[2]Color}\n'))

    def test_array_literal_imported_record_private_context(self):
        library = '''package lib
@(private)
Color :: enum {Red,Blue}
Record :: struct {values:[2]Color}
accept :: proc(r:Record)->bool {return r.values[1] == .Blue}
'''
        self.invoke(self.program('''r := lib.Record{values=[.Red,.Blue]}
assert(lib.accept(r))
assert(lib.accept({values=[.Red,.Blue]}))''', 'import "lib"\n'), files={"lib/library.odin": library})

    def test_array_literal_argument_return_assignment_and_fields(self):
        self.invoke(self.program('''a: [2]f64
a = [1,2]
assert(sum([1,2]) == 3)
assert(sum(values=[3,4]) == 7)
assert(produce()[1] == 2)
r := Record{values=[3,4]}
r.values = [5,6]
assert(r.values[1] == 6)
assert(sum(a) == 3)''', '''Record :: struct {values:[2]f64}
sum :: proc(values:[2]f64)->f64 {return values[0]+values[1]}
produce :: proc()->[2]f64 {return [1,2]}
'''))

    def test_array_literal_reassignment_inferred(self):
        self.invoke(self.program('''a := [f64(1),2]
a = [3,4]
assert(type_of(a) == [2]f64 && a[1] == 4)'''))

    def test_array_literal_indexed_and_dereferenced_assignment(self):
        self.invoke(self.program('''a: [1][2]Color
a[0] = [.Red,.Blue]
p := &a[0]
p^ = [.Blue,.Red]
assert(a[0][0] == .Blue)''', 'Color :: enum {Red,Blue}\n'))

    def test_array_literal_parameter_default(self):
        self.invoke(self.program('assert(f()[0] == .Red)', '''Color :: enum {Red,Blue}
f :: proc(values: [2]Color = [.Red,.Blue])->[2]Color {return values}
'''))

    def test_array_literal_procedure_variable_context(self):
        self.invoke(self.program('''callback: proc([2]Color)->bool = accept
assert(callback([.Red,.Blue]))''', '''Color :: enum {Red,Blue}
accept :: proc(values:[2]Color)->bool {return values[1] == .Blue}
'''))

    def test_array_literal_generic_argument(self):
        self.invoke(self.program("assert(first([1,2]) == 1)", 'first :: proc(xs:[$N]$T)->T {return xs[0]}\n'))

    def test_array_literal_named_and_distinct_array_context(self):
        self.invoke(self.program('''a: Pair = [1,2]
b: DistinctPair = [.Red,.Blue]
c: Empty = []
assert(type_of(a) == Pair && a[1] == 2)
assert(type_of(b) == DistinctPair && b[1] == .Blue)
assert(len(c) == 0)''', '''Color :: enum {Red,Blue}
Pair :: [2]i32
DistinctPair :: distinct [2]Color
Empty :: [0]int
'''))

    def test_array_literal_imported_procedure_private_context(self):
        library = '''package lib
@(private)
Color :: enum {Red,Blue}
accept :: proc(values:[2]Color)->bool {return values[1] == .Blue}
'''
        self.invoke(self.program('assert(lib.accept([.Red,.Blue])); assert(accept(values=[.Red,.Blue]))', 'import "lib" / accept\n'), files={"lib/library.odin": library})

    def test_array_literal_typed_generic_local(self):
        self.invoke(self.program('assert(f(f64) == 3)', '''f :: proc($T:typeid)->T {
    values: [2]T = [1,2]
    return values[0] + values[1]
}
'''))

    def test_array_literal_no_extra_first_evaluation(self):
        self.invoke(self.program('''a := [next(),next(),next()]
assert(a == [3]int{1,2,3} && calls == 3)''', 'calls:int\nnext :: proc()->int {calls += 1; return calls}\n'))

    def test_array_literal_rejects_bad_types_and_lengths(self):
        for body in ('a := [1,"two"]; _=a', 'a:[3]int=[1,2]; _=a', 'a := []; _=a', 'a := [.Red,.Blue]; _=a'):
            with self.subTest(body=body):
                self.invoke(self.program(body, 'Color :: enum {Red,Blue}\n'), fail="Error:")

    def test_array_literal_multiline_trailing_comma(self):
        self.invoke(self.program('''a := [
  1, // one
  2,
]
b := [3,]
assert(a[1] == 2 && b[0] == 3)'''))

    # T06: homogeneous membership, existing native containers, compile-time use.
    def test_membership_array_slice_dynamic_and_fixed(self):
        self.invoke(self.program('''a := [1,2,3]
assert(2 in a && 4 not_in a)
assert(2 in a[:] && 4 not_in a[:])
d: [dynamic]int
defer delete(d)
append(&d,1,2,3)
assert(2 in d && 4 not_in d)
f: [dynamic;8]int
append(&f,1,2,3)
assert(2 in f && 4 not_in f)
'''))

    def test_membership_maps_and_bitsets(self):
        self.invoke(self.program('''m:map[string]int
defer delete(m)
m["present"]=0
assert("present" in m && "missing" not_in m)
bits:bit_set[Color]={.Red}
assert(.Red in bits && .Blue not_in bits)
numbers:bit_set[0..<8]={1,3}
assert(1 in numbers && 2 not_in numbers)''', 'Color :: enum {Red,Blue}\n'))

    def test_membership_contextual_needle(self):
        self.invoke(self.program('''a: [2]f64 = [1,2]
assert(1 in a)
c: []Color = {.Red,.Blue}
assert(.Red in c)
assert(Color.Blue in [.Red,.Blue])''', 'Color :: enum {Red,Blue}\n'))

    def test_membership_literal_eager_and_once_in_source_order(self):
        self.invoke(self.program('''assert(mark(1) in [mark(1),mark(2),mark(3)])
assert(log == 1123)
log = 0
assert(mark(1) in array())
assert(log == 14)''', '''log:int
mark :: proc(x:int)->int {log=log*10+x;return x}
array :: proc()->[2]int {log=log*10+4; return {1,2}}
'''))

    def test_membership_lazy_enclosing_context(self):
        self.invoke(self.program('''assert(!(false && (next() in [1,2])))
assert(true || (next() in [1,2]))
assert(calls == 0)
for i:=0; i<3; i+=1 { assert(next() in [1,2,3]) }
assert(calls == 3)''', 'calls:int\nnext :: proc()->int {calls+=1;return calls}\n'))

    def test_membership_empty_runtime_list_evaluates_needle(self):
        self.invoke(self.program('''assert(next() not_in [])
assert(calls == 1)''', 'calls:int\nnext :: proc()->int {calls+=1;return calls}\n'))

    def test_membership_strings_as_elements_and_structs(self):
        self.invoke(self.program('''assert("two" in ["one","two"])
assert(Item{n=2} in [Item{n=1},Item{n=2}])''', 'Item :: struct {n:int}\n'))

    def test_membership_rejects_substring_and_wrong_needle(self):
        for body in ('assert("a" in "abc")', 'assert("one" in [1,2])'):
            with self.subTest(body=body):
                self.invoke(self.program(body), fail="Error:")

    def test_membership_constant_contexts(self):
        self.invoke(self.program('''assert(OK && !NO)
#if 2 in [1,2,3] {assert(true)} #else {assert(false)}
when 4 not_in [1,2,3] {assert(true)}
#assert(2 in [1,2])''', 'OK :: 2 in [1,2,3]\nNO :: 4 in [1,2,3]\n'))

    def test_membership_named_constant_arrays(self):
        self.invoke(self.program('''assert(PRESENT && ABSENT && ZERO)
assert(allowed(4) == 4)''', '''CHOICES :: [2,4,8]
PADDED :: [3]int{1,2,0}
PRESENT :: 4 in CHOICES
ABSENT :: 3 not_in CHOICES
ZERO :: 0 in PADDED
allowed :: proc($N:int)->int where N in CHOICES {return N}
'''))

    def test_membership_type_list_in_runtime_expression(self):
        self.invoke(self.program('''assert(f(1))
assert(!f(true))
assert(int in [int,f64])
assert(type_of(typed) in [int,f64])''', '''typed: f64
f :: proc(x:$T)->bool {
    return T in [int,f64]
}
'''))

    def test_membership_constant_lists_enforce_array_typing(self):
        for prefix in ('OK :: 1 in [1,2.5]\n', 'OK :: 1.5 in [1,2]\n'):
            with self.subTest(prefix=prefix):
                self.invoke(self.program("_ = OK", prefix), fail="Error:")
        self.invoke(self.program("assert(OK)", 'Color :: enum {Red,Blue}\nOK :: Color.Blue in [.Red,.Blue]\n'))

    def test_membership_preserves_native_constant_bitsets(self):
        self.invoke(self.program("assert(PRESENT && ABSENT)", '''BITS :: bit_set[0..<8]{1,3}
PRESENT :: 1 in BITS
ABSENT :: 2 not_in BITS
'''))

    def test_membership_contextless_procedure(self):
        self.invoke(self.program("assert(f(2))", 'f :: proc "contextless" (v:int)->bool {return v in [1,2,3]}\n'))

    def test_membership_generic_types_and_aliases(self):
        self.invoke(self.program('''assert(identity(int(1)) == 1)
assert(identity(f64(2)) == 2)
assert(identity(Alias(3)) == 3)
assert(excluded(f32(4)) == 4)''', '''Alias :: int
identity :: proc(x:$T)->T where T in [int,f64] {return x}
excluded :: proc(x:$T)->T where T not_in [int,f64] {return x}
'''))

    def test_membership_generic_rejects_bool_and_distinct(self):
        prefix = 'Distinct :: distinct int\nidentity :: proc(x:$T)->T where T in [int,f64] {return x}\n'
        for argument in ('true', 'Distinct(1)'):
            with self.subTest(argument=argument):
                self.invoke(self.program(f"_ = identity({argument})", prefix), fail="where")

    def test_membership_generic_composed_duplicates_empty(self):
        self.invoke(self.program('''assert(f(int(3)) == 3)
assert(g(4) == 4)''', '''f :: proc(x:$T)->T where (T in [int,int,f64]) && (T not_in [bool]) {return x}
g :: proc(x:$T)->T where T not_in [] {return x}
'''))

    def test_membership_parenthesized_literal_and_type_list(self):
        self.invoke(self.program('''assert(f(1) == 1)
assert(2 in ([1,2,3]))
assert(Color.Blue in ([.Red,.Blue]))''', '''Color :: enum {Red,Blue}
f :: proc(x:$T)->T where T in ([int,f64]) {return x}
'''))

    def test_membership_empty_constraint_checks_operand(self):
        self.invoke(self.program("_ = f(1)", 'f :: proc(x:$T)->T where Missing not_in [] {return x}\n'), fail="Missing")

    def test_membership_generic_struct_constraint(self):
        self.invoke(self.program('''p := Pair(int){value=3}
assert(p.value == 3)''', 'Pair :: struct($T:typeid) where T in [int,f64] {value:T}\n'))

    def test_membership_generic_value_constraint(self):
        prefix = 'size :: proc($N:int)->int where N in [2,4,8] {return N}\n'
        self.invoke(self.program("assert(size(4) == 4)", prefix))
        self.invoke(self.program("_ = size(3)", prefix), fail="where")

    def test_membership_generic_mixed_type_list_rejected(self):
        self.invoke(self.program("_ = f(1)", 'f :: proc(x:$T)->T where T in [int,3] {return x}\n'), fail="Error:")

    def test_membership_preserves_for_in(self):
        self.invoke(self.program('''count := 0
for x in [1,2,3] {if x in [2,3] {count += 1}}
assert(count == 2)'''))

    def test_main_demo(self):
        result = subprocess.run([str(THOR), "run", str(ROOT / "demo")], text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("All T01-T23 demos passed.", result.stdout)
        self.assertNotIn("Warning:",result.stderr)

    def test_checked_in_rejection_examples(self):
        for directory in sorted((ROOT / "tests" / "fixtures").iterdir()):
            with self.subTest(fixture=directory.name):
                source = (directory / "main.thor").read_text()
                expected = (directory / "expected.txt").read_text().strip()
                self.invoke(source, command="check", fail=expected)


if __name__ == "__main__":
    unittest.main()

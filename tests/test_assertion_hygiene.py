"""Required toolchain work must survive Python -O and Odin -disable-assert."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from thorlang.compiler import Source
from thorlang.driver import frontend_binary

ROOT = Path(__file__).resolve().parents[1]

# All calls currently needed by toolchain assertion predicates have been
# reviewed as inspections. New calls require review, even if they return bool.
# This intentionally conservative check is not a whole-program purity proof.
READ_ONLY_NATIVE = {
    'len', 'cap', 'size_of', 'align_of', 'type_of', 'typeid_of', 'max', 'min',
    'int', 'u64', 'u128', 'i128',
    'os.is_absolute_path', 'filepath.volume_name', 'utf8.valid_rune',
    'strings.to_string',
    'intrinsics.type_is_integer', 'intrinsics.type_is_float',
    'intrinsics.type_is_enum', 'intrinsics.type_is_array',
    'intrinsics.type_is_enumerated_array', 'intrinsics.type_is_slice',
    'intrinsics.type_is_dynamic_array',
    'intrinsics.type_is_fixed_capacity_dynamic_array',
    'intrinsics.type_is_pointer', 'intrinsics.type_is_multi_pointer',
}


def native_issues(source):
    issues = []
    for assertion in source.nodes:
        if assertion.kind != 'Call':
            continue
        callee = assertion.role('callee')
        if not callee or callee.text not in {'assert', 'assert_contextless', '#assert'}:
            continue
        arguments = assertion.group('args')
        for node in source.nodes:
            if not any(arg.start <= node.start and node.end <= arg.end for arg in arguments):
                continue
            if node.kind == 'Call':
                target = node.role('callee')
                if not target or target.text not in READ_ONLY_NATIVE:
                    issues.append((assertion.start, target.text if target else '<indirect call>'))
            elif node.kind == 'Interpolation':
                issues.append((assertion.start, '<allocated interpolation>'))
    return issues


def python_issues(tree):
    issues = []
    for assertion in ast.walk(tree):
        if not isinstance(assertion, ast.Assert):
            continue
        for expression in [assertion.test, assertion.msg]:
            if expression is None:
                continue
            for node in ast.walk(expression):
                if isinstance(node, ast.Call):
                    if not isinstance(node.func, ast.Name) or node.func.id != 'len':
                        issues.append((assertion.lineno, ast.unparse(node.func)))
                elif isinstance(node, (ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom)):
                    issues.append((assertion.lineno, type(node).__name__))
    return issues


def editor_issues(text):
    # Deliberately narrow guard for required editor/test setup. Operations in
    # assert.rejects/throws callbacks are intentional failure tests.
    return re.findall(r'assert\.(?!rejects\b|throws\b)\w+\([^;]*?'
                      r'\b(applyEdit|runProject|writeFileSync|mkdirSync|unlinkSync|renameSync|executeCommand)\s*\(', text)


class AssertionHygieneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend, cls.odin = frontend_binary('odin')

    def parse(self, text):
        return Source(ROOT/'assertion-audit.odin', self.frontend, raw=text.encode(), cache=False)

    def test_native_toolchain_assertions_only_call_reviewed_inspections(self):
        issues = []
        for directory in ['runtime', 'frontend', 'tls/src']:
            for path in sorted((ROOT/directory).rglob('*')):
                if path.suffix not in {'.odin', '.thor'} or path.name.endswith('_test.odin'):
                    continue
                raw = path.read_bytes()
                if b'assert' not in raw:
                    continue
                # Audit other platforms too; preserve byte offsets for reports.
                ungated = re.sub(rb'(?m)^[ \t]*#\+build[^\r\n]*',
                                 lambda match: b' '*len(match[0]), raw)
                source = Source(path, self.frontend, thor=path.suffix == '.thor', raw=ungated, test=True)
                for offset, target in native_issues(source):
                    line = raw[:offset].count(b'\n')+1
                    issues.append(f'{path.relative_to(ROOT)}:{line}: review {target}; move required work before assert')
        self.assertEqual(issues, [])

    def test_python_toolchain_assertions_have_no_mutating_expressions(self):
        for directory in ['thorlang', 'tools']:
            for path in sorted((ROOT/directory).glob('*.py')):
                with self.subTest(path=path.relative_to(ROOT)):
                    self.assertEqual(python_issues(ast.parse(path.read_text())), [])

    def test_editor_assertions_do_not_perform_required_work(self):
        for path in sorted((ROOT/'editors/vscode/test').glob('*.js')):
            with self.subTest(path=path.name):
                self.assertEqual(editor_issues(path.read_text()), [])
        self.assertEqual(editor_issues('assert.ok(await vscode.workspace.applyEdit(edit));'), ['applyEdit'])
        self.assertEqual(editor_issues('assert.equal(await runProject(vscode),terminal);'), ['runProject'])
        self.assertEqual(editor_issues('assert.rejects(()=>runProject(vscode));'), [])

    def test_guard_detects_nested_operations_messages_and_contextless_asserts(self):
        for statement, target in [
            ('assert(resize(&data, 3) == nil)', 'resize'),
            ('assert(len(change()) > 0)', 'change'),
            ('assert_contextless(write())', 'write'),
            ('assert(true, describe_and_mutate())', 'describe_and_mutate'),
            ('assert(\n  mutate(\n    2, 3,\n  ),\n)', 'mutate'),
            ('assert(f"value {3}" != "")', '<allocated interpolation>'),
        ]:
            with self.subTest(statement=statement):
                source = self.parse('package main\nmain::proc(){\n'+statement+'\n}\n')
                self.assertIn(target, [target for _, target in native_issues(source)])
        for statement in ['assert modify()', 'assert len(values := read()) > 0', 'assert ok, save()']:
            with self.subTest(statement=statement):
                self.assertTrue(python_issues(ast.parse(statement)))
        self.assertEqual(native_issues(self.parse('package main\nmain::proc(){assert(len(data)>0)}')), [])
        self.assertEqual(native_issues(self.parse('package main\n// assert(write())\nmain::proc(){text:="assert(write())";assert(len(text)>0)}')), [])
        self.assertEqual(python_issues(ast.parse('assert len(values)>0')), [])

    def test_editor_setup_fixtures_do_not_hide_file_writes_in_assertions(self):
        for name in ['test_vscode.py', 'test_vscode_debug.py']:
            tree = ast.parse((ROOT/'tools'/name).read_text())
            fixtures = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                        and isinstance(node.value, str) and node.value.startswith('package main\n')]
            self.assertTrue(fixtures, name)
            for fixture in fixtures:
                self.assertEqual(native_issues(self.parse(fixture)), [], name)
                with tempfile.TemporaryDirectory(prefix='thor-editor-setup-noassert-') as directory:
                    root = Path(directory)
                    if name == 'test_vscode_debug.py':
                        fixture += '\nmain::proc(){confirm_run()}\n'
                    source = root/'main.thor'
                    source.write_text(fixture)
                    result = subprocess.run([str(ROOT/'thor'), 'run', str(source), '-o:speed', '-disable-assert',
                                             '--output-dir', str(root/'generated')], cwd=root,
                                            capture_output=True, text=True, timeout=40)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    marker = 'normal-run.txt' if name == 'test_vscode_debug.py' else 'standalone-run.txt'
                    self.assertEqual((root/marker).read_text(), 'ok')

    def test_frontend_ast_and_errors_match_with_native_assertions_disabled(self):
        with tempfile.TemporaryDirectory(prefix='thor-frontend-noassert-') as directory:
            binary = Path(directory)/'frontend'
            result = subprocess.run([self.odin, 'build', str(ROOT/'frontend'), '-o:speed',
                                     '-disable-assert', '-no-bounds-check', '-no-type-assert', f'-out:{binary}'],
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            invalid = Path(directory)/'invalid.thor'
            invalid.write_text('package main\nmain::proc( {\n')
            paths = [ROOT/name for name in ['demo/main.thor', 'examples/demo/demo.thor', 'runtime/basic/basic.thor',
                                           'tests/fixtures/array_type_mismatch/main.thor']]
            tagged=Path(directory)/'tagged.thor'
            tagged.write_text('''package main
Point::struct{x,y:f32}
Mode::enum u8{Read=1,Alias=Read,Write}
Packed::struct #packed{_:int,using point:Point `coordinates`,_:bool}
Aligned::struct #all_or_none #align(16){_:bool,value:int `amount`}
Subtype::struct{#subtype point:Point,_:int}
main::proc(){
 values:#soa[]Point;lanes:#simd[4]f32;_=values;_=lanes
 outer: for i in 0..<3 {if i==1 {continue};break outer}
 block_label: {break block_label}
}
''')
            paths.append(tagged)
            for path in [*paths, invalid]:
                for mode in [[], ['--source-tools']]:
                    args = [str(path), '--test', *mode]
                    ordinary = subprocess.run([str(self.frontend), *args], capture_output=True, timeout=30)
                    unchecked = subprocess.run([str(binary), *args], capture_output=True, timeout=30)
                    self.assertEqual(unchecked.returncode, ordinary.returncode, str(path))
                    self.assertEqual(json.loads(unchecked.stdout), json.loads(ordinary.stdout), str(path))

    def test_python_optimized_compiler_preserves_build_and_program_work(self):
        with tempfile.TemporaryDirectory(prefix='thor-python-noassert-') as directory:
            root = Path(directory)
            source = root/'main.thor'
            source.write_text('''package main
import "core:fmt"
square::fn(n:int)->int{return n*n}
ANSWER::#run square(7)
main::proc(){
 width:=6;values:=[10,20,30]
 text:=f"value {ANSWER:0{width}d} {values[-1]}"
 fmt.println(text);delete(text)
}
''')
            for options in [[], ['-O']]:
                with self.subTest(python_options=options):
                    result = subprocess.run([sys.executable, *options, str(ROOT/'thor-python'), 'run', str(source),
                                             '--output-dir', str(root/('optimized' if options else 'normal')),
                                             '-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert'],
                                            capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    self.assertEqual(result.stdout.strip(), 'value 000049 30')


if __name__ == '__main__':
    unittest.main()

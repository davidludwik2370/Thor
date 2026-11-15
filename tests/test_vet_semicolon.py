"""Thor vet distinguishes optional terminators from required semicolon syntax."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from tests import test_rust_compiler as native
from thorlang.sourcemap import position


class SemicolonVetTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp

    def invoke(self, engine, *flags, command='vet', status=0, input=None):
        compiler = native.PYTHON if engine == 'python' else native.RUST
        prefix = [sys.executable] if engine == 'python' else []
        result = subprocess.run([*prefix, str(compiler), command, str(self.app),
                                 '--output-dir', str(self.root/(engine+'-generated')), *flags],
                                cwd=self.root, input=input, capture_output=True,
                                text=True, timeout=90)
        self.assertEqual(result.returncode, status, result.stdout+result.stderr)
        return result

    def findings(self, engine, *flags, **options):
        result = self.invoke(engine, *flags, '-json-errors', **options)
        return json.loads(result.stderr)['errors']

    def test_trailing_semicolons_warn_by_default_and_have_exact_ranges_and_fixes(self):
        text = ('// 😀\r\npackage main\r\nimport "core:fmt"\r\nmain :: proc() {\r\n'
                '    /* 😀 */ x := 10;\r\n    y := 11; // comment ;\r\n'
                '    fmt.println(x+y)\r\n}\r\n')
        self.source.write_bytes(text.encode())
        for engine in ('python', 'rust'):
            errors = self.findings(engine)
            semicolons = [error for error in errors if error['code']=='thor/unnecessary-semicolon']
            self.assertEqual(len(semicolons), 2, errors)
            for error, marker in zip(semicolons, ('10;', '11;')):
                offset = text.encode().index(marker.encode())+len(marker)-1
                line, column = position(text.encode(), offset)
                self.assertEqual(error['type'], 'warning')
                self.assertEqual(error['msgs'], ['Unnecessary semicolon.'])
                self.assertEqual((error['pos']['offset'], error['pos']['end_offset']), (offset,offset+1))
                self.assertEqual((error['pos']['line'],error['pos']['column']), (line,column))
                self.assertEqual(error['fix']['text'], '')
                self.assertEqual(error['fix']['pos'], error['pos'])
            selected = self.findings(engine, '--vet=semicolon')
            self.assertEqual(selected, semicolons)
            self.assertEqual(self.source.read_bytes(),text.encode())
            self.assertFalse(any(error['code']=='thor/unnecessary-semicolon'
                                 for error in self.findings(engine, '--vet=immutable,pure,unused')))

    def test_required_statement_and_multiline_header_type_separators_are_preserved(self):
        text = '''package main
main :: proc() {
    x := 10; y := 11
    _ = x+y
    values: [dynamic;
        4]int
    for i:=0;
        i<2;
        i+=1 {
        append(&values, i)
    }
    if count:=len(values);
        count==2 { _=values[0] }
    switch count:=len(values);
        count {
    case 2: _=values[1]
    }
}
'''
        self.source.write_text(text)
        for engine in ('python', 'rust'):
            self.assertEqual(self.findings(engine, '--vet=semicolon'), [])
            self.invoke(engine, '--vet=semicolon', '--apply')
            self.assertEqual(self.source.read_text(),text)
            self.invoke(engine, command='run')
            self.invoke(engine, *native.UNCHECKED, command='run')

    def test_comments_strings_and_interpolation_code_have_distinct_semicolons(self):
        text = '''package main
import "core:fmt"
main :: proc() {
    // x := 1;
    /* y := 2; */
    fmt.println("literal;")
    fmt.println(`raw
literal;`)
    fmt.println(f"literal ; {1}")
    fmt.println(f"{(proc() -> int { value:=2; return value; })()}")
    value:=3; /* comment
    spanning lines ; */
    fmt.println(value)
}
'''
        self.source.write_text(text)
        for engine in ('python', 'rust'):
            errors = self.findings(engine, '--vet=semicolon')
            expected = [text.encode().index(b'return value;')+len(b'return value'),
                        text.encode().index(b'value:=3;')+len(b'value:=3')]
            self.assertEqual([error['pos']['offset'] for error in errors], expected, errors)

    def test_apply_and_ask_compose_with_immutable_fixes_and_preserve_bytes_and_permissions(self):
        text = ('// 😀\r\npackage main\r\nget::fn(x:int)->int{return x+1}\r\n'
                'main::proc(){\r\n    x /* note */ := get(1);\r\n    y := get(2);\r\n'
                '    if x+y!=5 { panic("result") }\r\n}\r\n')
        expected = text.replace(':=',':>').replace('get(1);','get(1)').replace('get(2);','get(2)')
        for engine in ('python','rust'):
            for answer, applied in [('n\n',False),('y\n',True)]:
                self.source.write_bytes(text.encode())
                self.source.chmod(0o640)
                result = self.invoke(engine, '--vet=immutable,semicolon', '--ask', input=answer)
                self.assertEqual(result.stdout.count('[y/N]'),1)
                self.assertEqual(self.source.read_bytes(),(expected if applied else text).encode())
            self.source.write_bytes(text.encode())
            result = self.invoke(engine, '--vet=immutable,semicolon', '--apply')
            self.assertIn('Applied 4 corrections.',result.stdout)
            self.assertEqual(self.source.read_bytes(),expected.encode())
            if os.name!='nt': self.assertEqual(self.source.stat().st_mode&0o777,0o640)
            self.assertEqual(self.findings(engine, '--vet=semicolon'),[])
            self.invoke(engine, command='run')
            self.invoke(engine, *native.UNCHECKED, command='run')

    def test_warning_policies_build_integration_and_native_policy_remain_consistent(self):
        self.source.write_text('package main\nmain::proc(){value:=1;_ = value;};')
        for engine in ('python','rust'):
            errors = self.findings(engine, '--vet=semicolon')
            self.assertEqual(len(errors),2,errors)
            self.assertEqual(self.findings(engine, '--vet=semicolon','-ignore-warnings'),[])
            strict = self.findings(engine, '--vet=semicolon','-warnings-as-errors',status=1)
            self.assertEqual([error['type'] for error in strict],['error','error'])
            build_errors = self.findings(engine,'--vet=semicolon',command='build',status=1)
            self.assertEqual(build_errors,[{**error,'type':'error'} for error in errors])
            self.assertEqual(self.findings(engine,'--vet=semicolon',*native.UNCHECKED,command='run'),errors)
            native_policy = self.invoke(engine,'-vet-semicolon',command='check',status=1)
            self.assertIn('unneeded semicolon',native_policy.stderr.lower())

    def test_project_scope_ignores_native_dependencies_and_inactive_files(self):
        self.source.write_text('package main\nmain::proc(){value:=1;_ = value}\n')
        (self.app/'native.odin').write_text('package main\nhelper::proc(){value:=1;_ = value;}\n')
        other_os = 'linux' if os.name=='nt' else 'windows'
        (self.app/f'excluded_{other_os}.thor').write_text('invalid source ;\n')
        for engine in ('python','rust'):
            self.assertEqual(self.findings(engine,'--vet=semicolon'),[])

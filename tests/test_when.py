"""Native Odin conditional compilation replaces the removed directive aliases."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests import test_thor as base
from thorlang.compiler import Source
from thorlang.driver import frontend_binary


class NativeWhenTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_removed_conditional_spellings_are_rejected_at_original_tokens(self):
        for body, word in [
            ('#if true {VALUE::1}', '#if'),
            ('#if false {VALUE::1}', '#if'),
            ('# if true {VALUE::1}', 'if'),
            ('when false { #if true {VALUE::1} }', '#if'),
            ('when true {} #else {}', '#else'),
            ('when true {} # else {}', 'else'),
            ('when false {} #else if true {}', '#else'),
            ('when false {} else if true {}', 'if'),
            ('when false {} else when false {} #else {}', '#else'),
        ]:
            for local in [False, True]:
                with self.subTest(body=body, local=local), tempfile.TemporaryDirectory(prefix='thor-removed-conditional-') as directory:
                    root = Path(directory)
                    source = root/'main.thor'
                    text = '// 😀 original header\npackage main\n'
                    text += ('main::proc(){\n    '+body+'\n}\n') if local else body+'\nmain::proc(){}\n'
                    source.write_text(text)
                    result = subprocess.run([str(base.THOR), 'check', str(source), '--diagnostic-format=json',
                                             '--output-dir', str(root/'output')], capture_output=True, text=True, timeout=40)
                    self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
                    errors = json.loads(result.stderr)['errors']
                    expected = text.encode().index(word.encode())+(1 if word.startswith('#') else 0)
                    self.assertTrue(any(error.get('pos', {}).get('offset') == expected for error in errors), errors)
                    self.assertTrue(all(error['pos']['file'] == str(source.resolve()) for error in errors))

    def test_native_branches_need_no_alias_rewrite(self):
        text = '''package main
MODE::2
when MODE==1 {VALUE::1} else when MODE==2 {VALUE::2} else {VALUE::3}
main::proc(){when VALUE==2 do assert(true)}
'''
        frontend, _ = frontend_binary('odin')
        parsed = Source(base.ROOT/'native-when.thor', frontend, raw=text.encode(), cache=False)
        self.assertEqual(parsed.info.get('edits'), [])
        self.assertEqual(sum(n.kind == 'When' for n in parsed.nodes), 3)
        self.invoke(text)

    def test_native_else_when_and_runtime_else_if_keep_separate_semantics(self):
        self.invoke(self.program('''
when false { missing() } else when true { selected:=7;assert(selected==7) } else {missing()}
runtime:=false
if runtime {panic("wrong branch")} else if !runtime {runtime=true} else {panic("wrong branch")}
if !runtime {panic("runtime branch did not execute")}
answer::4 when true else 8
assert(answer==4)
'''))

    def test_removed_spelling_remains_plain_string_and_comment_data(self):
        self.invoke(self.program('''
// #if and #else are ordinary comment text.
fmt.println("#if #else", `#else if`)
''', 'import "core:fmt"\n'), expected='#if #else #else if')


if __name__ == '__main__':
    unittest.main()

"""Unused imports are removable; unused type declarations stay review-only."""
from tests.test_rust_compiler import compiler_engines
import json
import os
import subprocess
import unittest

from tests import test_rust_compiler as native
from tests import test_unused_vet as usage


class VetDeclarationTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = usage.UnusedVetTests.setUp
    write = usage.UnusedVetTests.write
    invoke = usage.UnusedVetTests.invoke

    def reports(self, text, *flags, **kwargs):
        reports = []
        for engine in compiler_engines():
            self.write(text)
            with self.subTest(engine=engine):
                result, errors = self.invoke(engine, '--vet=unused', *flags, **kwargs)
                reports.append((errors, self.source.read_bytes()))
        self.assertEqual(len(reports),len(compiler_engines()))
        for report in reports[1:]:
            self.assertEqual(report,reports[0])
        return reports[0]

    def test_unused_imports_and_types_have_distinct_codes_and_only_import_fixes(self):
        text = '''package main
import "core:thread"
import unused "core:math"
Unused::struct{x:int}
Choice::enum{a,b}
main::proc(){Local::distinct int}
'''
        errors, unchanged = self.reports(text)
        self.assertEqual(unchanged, text.encode())
        self.assertEqual(sorted(error['code'] for error in errors),
                         ['thor/unused-import']*2+['thor/unused-type']*3)
        self.assertEqual({error['msgs'][0] for error in errors if error['code']=='thor/unused-type'},
                         {"Type 'Unused' declared but not used", "Type 'Choice' declared but not used", "Type 'Local' declared but not used"})
        for error in errors:
            self.assertEqual(error['type'], 'warning')
            self.assertEqual('fix' in error, error['code']=='thor/unused-import')
        remaining, updated = self.reports(text, '--apply')
        self.assertNotIn(b'import ', updated)
        self.assertIn(b'Unused::struct', updated)
        self.assertIn(b'Local::distinct int', updated)
        self.assertEqual([error['code'] for error in remaining], ['thor/unused-type']*3)

    def test_selective_and_wildcard_imports_are_removed_only_when_wholly_unused(self):
        text = '''package main
import * "core:math"
import _ "core:fmt" / print, println
main::proc(){}
'''
        errors, updated = self.reports(text, '--apply')
        self.assertEqual(errors, [])
        self.assertNotIn(b'import', updated)
        used = '''package main
import "core:fmt" / print, println
import * "core:math"
main::proc(){print(sqrt(f64(4)))}
'''
        errors, updated = self.reports(used, '--apply')
        self.assertEqual(errors, [])
        self.assertEqual(updated, used.encode())

    def test_aliases_using_type_annotations_and_fstrings_count_as_uses(self):
        text = '''package main
import "thor:basic" / print
import "core:math"
import time "core:time"
Thing::struct{duration:time.Duration}
Alias::Thing
root::math.sqrt
main::proc(){
    using math
    value:Alias
    print(f"{root(f64(4))} {sin(f64(0))} {value.duration}")
}
'''
        errors, updated = self.reports(text, '--apply')
        self.assertEqual(errors, [])
        self.assertEqual(updated, text.encode())

    def test_shadowed_names_and_field_labels_do_not_keep_imports_alive(self):
        text = '''package main
import "core:thread"
import _ "core:fmt" / print
Record::struct{thread,print:int}
main::proc(){
    thread,print:=1,2
    item:=Record{thread=thread,print=print}
    _=item.thread
    _=item.print
}
'''
        errors, updated = self.reports(text, '--apply')
        self.assertEqual(errors, [])
        self.assertNotIn(b'import ', updated)

    def test_imports_in_other_configurations_and_blank_imports_are_preserved(self):
        text = '''package main
import "core:fmt"
import _ "core:math"
main::proc(){when ODIN_DEBUG{fmt.println("debug")}}
'''
        for flags in ([], ['-debug']):
            errors, updated = self.reports(text, '--apply', *flags)
            self.assertEqual(errors, [])
            self.assertEqual(updated, text.encode())

    def test_sibling_packages_and_native_files_keep_referenced_types_alive(self):
        (self.app/'library').mkdir()
        (self.app/'library/main.thor').write_text('package library\nExported::struct{x:int}\n')
        (self.app/'native.odin').write_text('package main\nconsume::proc(value:NativeUsed){_=value}\n')
        text = '''package main
import "library"
NativeUsed::struct{x:int}
SiblingUsed::struct{x:int}
main::proc(){value:library.Exported;_=value;consume({1});other()}
'''
        (self.app/'sibling.thor').write_text('package main\nother::proc(){value:SiblingUsed;_=value}\n')
        errors, updated = self.reports(text, '--apply')
        self.assertEqual(errors, [])
        self.assertEqual(updated, text.encode())

    def test_recursive_types_need_an_external_use_and_type_shapes_are_recognized(self):
        text = '''package main
Node::struct{next:^Node}
Callback::proc(int)->int
Numbers::[]int
Number::distinct int
Choice::union{int,string}
Generic::struct($T:typeid){value:T}
Used::struct{next:^Used}
Alias::Used
main::proc(){value:Alias;_=value}
'''
        errors, updated = self.reports(text, '--apply')
        self.assertEqual({error['msgs'][0].split("'")[1] for error in errors},
                         {'Node','Callback','Numbers','Number','Choice','Generic'})
        self.assertTrue(all(error['code']=='thor/unused-type' and 'fix' not in error for error in errors))
        self.assertEqual(updated, text.encode())

    def test_native_type_aliases_are_distinguished_from_native_value_and_proc_aliases(self):
        for imports, alias, value in [('import "core:time" / Duration, Second','Duration','Second'),
                                      ('import "core:time"','time.Duration','time.Second')]:
            text = ('package main\n'+imports+'\nimport "core:fmt"\n'
                    'Unused::'+alias+'\nDelay::'+value+'\nPrint::fmt.println\nmain::proc(){_=Print}\n')
            errors, updated = self.reports(text,'--apply')
            self.assertEqual([error['code'] for error in errors], ['thor/unused-type'])
            self.assertIn("'Unused'", errors[0]['msgs'][0])
            self.assertEqual(updated, text.encode())

    def test_instantiated_generic_type_aliases_are_unused_types(self):
        text = 'package main\nBox::struct($T:typeid){value:T}\nUnused::Box(int)\nmain::proc(){}\n'
        errors, updated = self.reports(text,'--apply')
        self.assertEqual([error['code'] for error in errors], ['thor/unused-type'])
        self.assertIn("'Unused'", errors[0]['msgs'][0])
        self.assertEqual(updated, text.encode())

    def test_removal_preserves_comments_crlf_neighbors_and_updates_warning_ranges(self):
        text = ('// 😀\r\npackage main\r\n'
                'import "core:thread"; import "core:fmt" // keep comment\r\n'
                'import _ "core:math" / /* middle */ sin,\r\n cos // tail\r\n'
                'Unused::struct{x:int}\r\nmain::proc(){fmt.println("ready")}\r\n')
        errors, updated = self.reports(text, '--apply')
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]['code'], 'thor/unused-type')
        self.assertIn(b'import "core:fmt" // keep comment\r\n', updated)
        self.assertIn(b'/* middle */', updated)
        self.assertIn(b'// tail\r\n', updated)
        self.assertNotIn(b'\n', updated.replace(b'\r\n', b''))
        self.assertEqual(errors[0]['pos']['offset'], updated.index(b'Unused'))
        self.assertEqual(errors[0]['pos']['line'], updated[:updated.index(b'Unused')].count(b'\n')+1)

    def test_multiline_line_comments_never_swallow_neighboring_imports(self):
        text = ('package main\nimport _ "core:math" / sin, // keep middle\n'
                'cos; import "core:fmt"\nmain::proc(){fmt.println("ready")}\n')
        errors, updated = self.reports(text, '--apply')
        self.assertEqual(errors, [])
        self.assertIn(b'// keep middle\n', updated)
        self.assertIn(b'import "core:fmt"', updated)
        self.assertNotIn(b'import _', updated)

    def test_types_do_not_prompt_and_import_removal_requires_an_affirmative_answer(self):
        text = 'package main\nUnused::struct{x:int}\nmain::proc(){}\n'
        for engine in compiler_engines():
            self.write(text)
            result, errors = self.invoke(engine, '--vet=unused', '--ask', input='y\n')
            self.assertNotIn('[y/N]', result.stdout)
            self.assertEqual(self.source.read_text(), text)
            self.assertEqual([error['code'] for error in errors], ['thor/unused-type'])
            imported = text.replace('Unused::', 'import "core:thread"\nUnused::')
            for answer in ('n\n','y\n'):
                self.write(imported)
                result, errors = self.invoke(engine, '--vet=unused', '--ask', input=answer)
                self.assertIn('[y/N]', result.stdout)
                self.assertEqual('import ' in self.source.read_text(), answer=='n\n')

    def test_warning_policy_and_opt_in_behavior_match_other_vet_rules(self):
        text = 'package main\nimport "core:thread"\nUnused::struct{x:int}\nmain::proc(){}\n'
        for engine in compiler_engines():
            self.write(text)
            _, errors = self.invoke(engine, command='check')
            self.assertEqual(errors, [])
            for command, flags, status in [('vet',['--vet=unused','-ignore-warnings'],0),
                                           ('vet',['--vet=unused','-warnings-as-errors'],1),
                                           ('build',['--vet=unused'],1)]:
                _, errors = self.invoke(engine, *flags, command=command, status=status)
                self.assertEqual(len(errors), 0 if status==0 else 2, errors)
                self.assertTrue(all(error['type']=='error' for error in errors))
            _, errors = self.invoke(engine, '--vet=immutable')
            self.assertEqual(errors, [])

    def test_default_apply_coalesces_import_and_semicolon_edits(self):
        text = 'package main\nimport "core:thread";\nUnused::struct{x:int}\nmain::proc(){\nvalue:>1;\n_=value\n}\n'
        for engine in compiler_engines():
            self.write(text)
            _, errors = self.invoke(engine, '--apply')
            updated = self.source.read_text()
            self.assertNotIn('import ', updated)
            self.assertNotIn(';', updated)
            self.assertIn('Unused::struct', updated)
            self.assertEqual([error['code'] for error in errors], ['thor/unused-type'])

    def test_namespace_aliases_of_one_package_and_comptime_uses_are_distinguished(self):
        text = '''package main
import first "core:math"
import second "core:math"
VALUE::#run second.sqrt(f64(4))
main::proc(){_=VALUE}
'''
        errors, updated = self.reports(text, '--apply')
        self.assertEqual(errors, [])
        self.assertNotIn(b'import first', updated)
        self.assertIn(b'import second', updated)

    def test_generated_set_support_does_not_keep_an_unused_source_import_alive(self):
        text = 'package main\nimport "thor:sets"\nmain::proc(){s:=set[int]{1};delete(s)}\n'
        errors, updated = self.reports(text, '--apply')
        self.assertEqual(errors, [])
        self.assertNotIn(b'import ', updated)

    def test_import_removals_share_one_compiler_validation_batch(self):
        (self.app/'library').mkdir()
        (self.app/'library/main.thor').write_text('package library\nmarker::1\n')
        self.write('package main\n'+''.join(f'import unused_{i} "library"\n' for i in range(48))+'main::proc(){}\n')
        profile = self.root/'profile.json'
        result = subprocess.run([str(native.RUST),'vet',str(self.app),'--vet=unused','-json-errors',
                                 '--output-dir',str(self.output)],cwd=self.root,capture_output=True,text=True,
                                env={**os.environ,'THOR_PROFILE':str(profile)},timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        errors = json.loads(result.stderr)['errors']
        self.assertEqual(len(errors), 48, errors)
        self.assertTrue(all(error['code']=='thor/unused-import' and 'fix' in error for error in errors))
        self.assertEqual(json.loads(profile.read_text())['counters'].get('vet.validation_checks'), 1)

    def test_apply_preserves_runtime_and_intentional_init_imports_in_unchecked_builds(self):
        (self.app/'startup').mkdir()
        (self.app/'startup/main.thor').write_text('package startup\nimport "core:fmt"\nimport "base:runtime"\n@(init) start::proc "contextless"(){context=runtime.default_context();fmt.println("initialized")}\n')
        text = ('package main\nimport "core:thread"\nimport _ "startup"\nimport "core:fmt"\n'
                'Unused::struct{x:int}\nmain::proc(){fmt.println("ready")}\n')
        for engine in compiler_engines():
            self.write(text)
            self.invoke(engine,'--vet=unused','--apply')
            for flags in (['-debug'], native.UNCHECKED):
                result, errors = self.invoke(engine,'--vet=unused',*flags,command='run')
                self.assertEqual(result.stdout, 'initialized\nready\n')
                self.assertEqual([error['code'] for error in errors], ['thor/unused-type'])


if __name__ == '__main__':
    unittest.main()

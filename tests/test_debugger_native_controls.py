"""Keep every native debug-info control tied to its maintained Thor regression."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from tests.debugger_pairs import CASES,FIXTURES,fixture_source,prepare
from tests import test_rust_compiler as native


class DebuggerPairFixtureTests(unittest.TestCase):
    def test_each_of_the_nine_live_regressions_has_an_explicit_native_pair(self):
        self.assertEqual(len(CASES),9)
        self.assertEqual(len(set(CASES.values())),9)
        for name in CASES:
            with self.subTest(case=name):
                self.assertEqual((FIXTURES/name/'main.thor').read_text(),fixture_source(name))
                self.assertTrue((FIXTURES/name/'main.odin').is_file())
                # Runtime verdicts must survive Odin's assertion-elision flags.
                for suffix in ('thor','odin'):
                    self.assertNotIn('assert(', (FIXTURES/name/('main.'+suffix)).read_text())

    def test_print_control_keeps_the_complete_installed_procedure_body(self):
        original=(native.ROOT/'runtime/basic/basic.thor').read_text()
        procedure=original[original.index('print :: proc('):original.index('\ntype :: proc(')]
        control=(FIXTURES/'basic/helper/basic.odin').read_text()
        self.assertEqual(control[control.index('print :: proc('):],procedure)

    def test_native_pairs_preserve_function_names_and_key_source_coordinates(self):
        for name in ('deferred','unicode'):
            with self.subTest(case=name):
                self.assertEqual((FIXTURES/name/'main.thor').read_bytes(),(FIXTURES/name/'main.odin').read_bytes())
        using=(FIXTURES/'using/main.odin').read_text().splitlines()
        self.assertEqual(using[0],'#+feature using-stmt // 😀 original header')
        self.assertEqual(using[1:],(FIXTURES/'using/main.thor').read_text().splitlines()[1:])
        for name,marker,line in [('allocated','text :=',3),('pmap','sum=a+b',5),('static','total:=',5),('fallback','value:=x+1',4),('switch','doubled :=',3)]:
            for suffix in ('thor','odin'):
                with self.subTest(case=name,language=suffix):
                    text=(FIXTURES/name/('main.'+suffix)).read_text().splitlines()
                    self.assertIn(marker,text[line-1])


@unittest.skipUnless(shutil.which('odin') or os.environ.get('THOR_ODIN'),'Odin is required')
class DebuggerPairRuntimeTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)

    def test_matching_programs_pass_checked_and_unchecked_without_the_debugger(self):
        compiler=Path(os.environ.get('THOR_TEST_COMPILER',native.RUST))
        odin=os.environ.get('THOR_ODIN',shutil.which('odin'))
        with tempfile.TemporaryDirectory(prefix='debugger native pair ') as directory:
            root=Path(directory).resolve()
            for name in CASES:
                for config,flags in [('checked',['-debug','-o:none']),('unchecked',['-debug',*native.UNCHECKED])]:
                    with self.subTest(case=name,configuration=config):
                        result=prepare(name,root/name/config,compiler,odin,[*flags,'-thread-count:1'])
                        self.assertEqual(result['thor']['runtime_exit'],0)
                        self.assertEqual(result['odin']['runtime_exit'],0)
                        self.assertEqual(result['thor']['runtime_stdout'],result['odin']['runtime_stdout'])


class DebuggerPairComparisonTests(unittest.TestCase):
    def test_native_failures_do_not_hide_additional_thor_or_identity_failures(self):
        from tests.debugger_pairs import compare_sessions
        sessions=[{'mode':mode,'checks':[{'key':key,'matched':ok} for key,ok in checks]}
            for mode,checks in [('native',[('entry',False),('after',True)]),
                                ('thor',[('entry',False),('after',False)]),
                                ('identity',[('entry',False),('after',True),('scope',False)])]]
        comparison=compare_sessions(sessions)
        self.assertEqual(comparison['native_failures'],['entry'])
        self.assertEqual(comparison['reproduced_native'],['entry'])
        self.assertEqual(comparison['thor_only'],['after'])
        self.assertEqual(comparison['identity_only'],['scope'])

    def test_a_failure_is_not_masked_by_a_different_native_repeat(self):
        from tests.debugger_pairs import compare_sessions
        sessions=[{'mode':mode,'repeat':repeat,'checks':[{'key':'entry','matched':matched}]}
                  for repeat,values in [(0,{'native':True,'thor':False,'identity':True}),
                                        (1,{'native':False,'thor':False,'identity':False})]
                  for mode,matched in values.items()]
        comparison=compare_sessions(sessions)
        self.assertEqual(comparison['thor_only'],['entry'])

"""Contextual vet rule sets, validated metadata, and safe suppressions."""
import json
import unittest
from tests import test_rust_compiler as base


class VetIgnoreSetTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke

    def inspect(self,text,*,command='check',flags=(),status=0):
        self.source.write_text(text,newline='')
        result=self.invoke('rust',command,flags=['--comptime=required','-json-errors','-thread-count:1',*flags])
        self.assertEqual(result.returncode,status,result.stdout+result.stderr)
        return json.loads(result.stderr)['errors'] if result.stderr.strip() else []

    def test_single_multiple_trailing_comma_multiline_and_aliases(self):
        for rules in ['{.unused}','{.immutable, .pure, .unused, .semicolon}',
                      '{\n .fn,\n .unused,\n}',
                      '{.unused, .unused, .pure, .fn}',
                      '({.unused})']:
            with self.subTest(rules=rules):
                findings=self.inspect('package main\n@('+'vet_ignore='+rules+')\nreserved::proc(){}\nmain::proc(){@(vet_ignore='+rules+') value:=7}\n',command='vet',flags=['--vet=unused,pure,immutable'])
                # Only .unused is common to every set; other advice is selected
                # independently below so this fixture can isolate name parsing.
                self.assertFalse(any(f.get('code','').startswith('thor/unused') for f in findings),findings)

    def test_all_ignorable_rules_are_contextual_and_do_not_use_ordinary_symbols(self):
        source='''package main
import "core:fmt"
unused::7
immutable::8
main::proc(){
 @(vet_ignore={.immutable, .unused})
 value:=unused+immutable
 fmt.println(value)
}
'''
        findings=self.inspect(source,command='vet',flags=['--vet=immutable,unused'])
        self.assertEqual(findings,[])

    def test_apply_keeps_ignored_declarations_and_applies_other_corrections(self):
        source='''package main
import "core:fmt"
@(vet_ignore={.pure})
keep::proc(x:int)->int{return x+1}
change::proc(x:int)->int{return x+2}
main::proc(){
 @(vet_ignore={.immutable, .semicolon})
 kept:=keep(1);
 changed:=change(1)
 fmt.println(kept,changed)
}
'''
        self.inspect(source,command='vet',flags=['--vet=immutable,pure,semicolon','--apply'])
        updated=self.source.read_text()
        self.assertIn('keep::proc',updated)
        self.assertIn('kept:=keep(1);',updated)
        self.assertIn('change::fn',updated)
        self.assertIn('changed:>change(1)',updated)
        self.assertIn('vet_ignore={.immutable, .semicolon}',updated)

    def test_unknown_suppressions_work_and_definite_errors_remain_visible(self):
        source='''package main
work::proc(callback:proc(v:[]int)){
 @(vet_ignore={.lifetime_unknown, .leak_unknown})
 values:=make([]int,2)
 callback(values)
}
main::proc(){}
'''
        findings=self.inspect(source,command='vet',flags=['--vet=lifetime,leak'])
        self.assertEqual(findings,[])
        source=source.replace('callback(values)','delete(values);_=values[0]')
        findings=self.inspect(source,command='vet',flags=['--vet=lifetime,leak'])
        self.assertEqual([f['code'] for f in findings],['thor/lifetime'])
        self.assertEqual(findings[0]['certainty'],'definite')

    def test_import_suppression_and_unrelated_attributes(self):
        source='''package main
@(vet_ignore={.unused})
import "core:thread"
@(vet_ignore={.pure, .unused}, deprecated="example")
retained::proc(){}
main::proc(){}
'''
        findings=self.inspect(source,command='vet',flags=['--vet=unused,pure','--apply'])
        self.assertEqual(findings,[])
        self.assertIn('import "core:thread"',self.source.read_text())
        self.assertIn('deprecated="example"',self.source.read_text())

    def test_invalid_sets_and_unsuppressible_rules_have_clear_original_ranges(self):
        for value,message in [
            ('{}','at least one'),
            ('{unused}','set entry must'),
            ('{"unused"}','set entry must'),
            ('{.unused, "pure"}','set entry must'),
            ('{.unused=1}','set entry must'),
            ('{.unused | .pure}','set entry must'),
            ('{{.unused}}','set entry must'),
            ('{.unused()}',"expected '}'"),
            ('{(.unused)()}','set entry must'),
            ('{Rules.unused}','set entry must'),
            ('{.lifetime}','cannot be suppressed'),
            ('{.leak}','cannot be suppressed'),
            ('{.unused, .typo}','Unknown vet check'),
            ('Rules{.unused}','nonempty rule set'),
        ]:
            with self.subTest(value=value):
                text='// 😀\npackage main\nRules::enum{unused}\n@(vet_ignore='+value+')\nVALUE::7\nmain::proc(){}\n'
                findings=self.inspect(text,status=1)
                self.assertIn(message,json.dumps(findings))
                if '.typo' in value:
                    pos=findings[0]['pos'];raw=text.encode()
                    self.assertEqual(raw[pos['offset']:pos['end_offset']],b'.typo')

    def test_sets_are_not_evaluated_and_cannot_hide_compiler_errors(self):
        source='package main\n@(vet_ignore={.unused})\nVALUE::missing\nmain::proc(){}\n'
        findings=self.inspect(source,status=1)
        self.assertIn('Undeclared name: missing',json.dumps(findings))
        source='package main\nside_effect::proc()->int{panic("never execute")}\n@(vet_ignore={side_effect()})\nVALUE::7\nmain::proc(){}\n'
        findings=self.inspect(source,status=1)
        self.assertIn('set entry must',json.dumps(findings))

    def test_duplicate_attributes_are_still_rejected(self):
        source='package main\n@(vet_ignore={.unused},vet_ignore={.pure})\nVALUE::7\nmain::proc(){}\n'
        findings=self.inspect(source,status=1)
        self.assertIn('Duplicate @(vet_ignore)',json.dumps(findings))

    def test_legacy_constant_strings_remain_compatible(self):
        for value in ['"unused, pure"','`unused, fn`','CHECKS']:
            source='package main\nCHECKS::"unused, fn"\n@(vet_ignore='+value+')\nreserved::proc(){}\nmain::proc(){_=CHECKS}\n'
            findings=self.inspect(source,command='vet',flags=['--vet=unused,pure'])
            self.assertEqual(findings,[])

    def test_rule_sets_work_in_compiletime_helpers_and_generic_functions(self):
        self.source.write_text('''package main
import "core:fmt"
@(vet_ignore={.pure, .unused})
twice::fn(value:$T)->T{
 @(vet_ignore={.immutable})
 result:=value+value
 return result
}
VALUE::#run twice(7)
main::proc(){fmt.println(VALUE)}
''')
        result=self.invoke('rust','run',flags=['-thread-count:1'])
        self.assertEqual((result.returncode,result.stdout.strip()),(0,'14'),result.stderr)

    def test_metadata_adds_no_runtime_storage_in_checked_or_unchecked_builds(self):
        source='''package main
import "core:fmt"
main::proc(){
 @(vet_ignore={.immutable, .unused})
 value:=7
 if value!=7 {panic("value")}
 fmt.println(value)
}
'''
        self.source.write_text(source,newline='')
        for flags in [[],base.UNCHECKED]:
            with self.subTest(flags=flags):
                result=self.invoke('rust','run',flags=['-thread-count:1',*flags])
                self.assertEqual((result.returncode,result.stdout.strip()),(0,'7'),result.stderr)
        result=self.invoke('rust','transpile',flags=['--comptime=required','-thread-count:1'])
        self.assertEqual(result.returncode,0,result.stderr)
        from pathlib import Path
        generated=Path(result.stdout.strip()).read_text()
        self.assertNotIn('vet_ignore',generated)
        self.assertNotIn('.unused',generated)


if __name__=='__main__':unittest.main()

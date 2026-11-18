"""Whole-line import removal and review-only unused procedure diagnostics."""
import json
import os
import subprocess
import unittest
from tests import test_vet_declarations as declarations
from tests import test_rust_compiler as native


class VetProcedureTests(unittest.TestCase):
    setUpClass = declarations.VetDeclarationTests.__dict__['setUpClass']
    setUp = declarations.VetDeclarationTests.setUp
    write = declarations.VetDeclarationTests.write
    invoke = declarations.VetDeclarationTests.invoke
    reports = declarations.VetDeclarationTests.reports

    def test_import_removal_consumes_complete_lines_without_collapsing_existing_spacing(self):
        for newline in ('\n','\r\n'):
            text = ('package main\n\nimport "core:thread"\n  import "core:math";  \n'
                    'main::proc(){}\n').replace('\n',newline)
            errors, updated = self.reports(text,'--apply')
            self.assertEqual(errors, [])
            self.assertEqual(updated, ('package main\n\nmain::proc(){}\n').replace('\n',newline).encode())

    def test_multiline_selective_and_wildcard_imports_leave_no_gaps(self):
        text = 'package main\nimport _ "core:math" / sin,\n cos\nimport * "core:fmt"\nmain::proc(){}\n'
        errors, updated = self.reports(text,'--apply')
        self.assertEqual(errors, [])
        self.assertEqual(updated, b'package main\nmain::proc(){}\n')

    def test_comments_are_retained_without_an_extra_blank_line(self):
        for text, expected in [
            ('import /* keep */ "core:thread"\n','/* keep */\n'),
            ('import _ "core:math" / sin, // keep\n cos\n','// keep\n'),
            ('import "core:thread" // keep\n',' // keep\n'),
        ]:
            errors, updated = self.reports('package main\n'+text+'main::proc(){}\n','--apply')
            self.assertEqual(errors, [])
            self.assertEqual(updated, ('package main\n'+expected+'main::proc(){}\n').encode())

    def test_shared_lines_preserve_used_imports_and_their_newline(self):
        text = 'package main\nimport "core:thread"; import "core:fmt"\nmain::proc(){fmt.println("ok")}\n'
        errors, updated = self.reports(text,'--apply')
        self.assertEqual(errors, [])
        self.assertEqual(updated, b'package main\n import "core:fmt"\nmain::proc(){fmt.println("ok")}\n')

    def test_final_import_without_trailing_newline_is_completely_removed(self):
        text = 'package main\nmain::proc(){}\n  import "core:thread"'
        errors, updated = self.reports(text,'--apply')
        self.assertEqual(errors, [])
        self.assertEqual(updated, b'package main\nmain::proc(){}\n')

    def test_unused_procs_fns_generic_and_recursive_procedures_are_warning_only(self):
        text = '''package main
unused_proc::proc(){}
unused_fn::fn()->int{return 1}
generic::proc(x:$T)->T{return x}
recursive::proc(n:int)->int{if n==0{return 0};return recursive(n-1)}
main::proc(){}
'''
        errors, updated = self.reports(text,'--apply')
        self.assertEqual(updated, text.encode())
        self.assertEqual({error['msgs'][0].split("'")[1] for error in errors},
                         {'unused_proc','unused_fn','generic','recursive'})
        self.assertTrue(all(error['type']=='warning' and error['code']=='thor/unused-procedure'
                            and 'fix' not in error for error in errors), errors)

    def test_calls_callbacks_deferred_calls_and_comptime_calls_are_uses(self):
        text = '''package main
identity::proc(x:int)->int{return x}
calculate::fn(x:int)->int{return x+1}
run::proc(callback:proc(int)->int)->int{return callback(1)}
cleanup::proc(){}
VALUE::#run calculate(3)
main::proc(){defer cleanup();_=run(identity);_=VALUE}
'''
        errors, updated = self.reports(text,'--apply')
        self.assertEqual(errors, [])
        self.assertEqual(updated, text.encode())

    def test_procedure_aliases_and_overload_groups_count_exact_binding_uses(self):
        text = '''package main
integer::proc(x:int)->int{return x}
floating::proc(x:f64)->f64{return x}
dispatch::proc{integer,floating}
Alias::dispatch
UnusedAlias::dispatch
main::proc(){_=Alias(3)}
'''
        errors, updated = self.reports(text,'--apply')
        self.assertEqual([error['code'] for error in errors], ['thor/unused-procedure'])
        self.assertIn("'UnusedAlias'",errors[0]['msgs'][0])
        self.assertEqual(updated, text.encode())

    def test_local_procedures_shadow_outer_bindings(self):
        text = '''package main
shadowed::proc(){}
main::proc(){
    shadowed::proc(){}
    shadowed()
    unused_local::fn()->int{return 1}
}
'''
        errors, updated = self.reports(text,'--apply')
        self.assertEqual({error['msgs'][0].split("'")[1] for error in errors}, {'shadowed','unused_local'})
        self.assertTrue(all(error['code']=='thor/unused-procedure' for error in errors))
        self.assertEqual(updated, text.encode())

    def test_entry_lifecycle_export_and_required_procedures_are_not_unused(self):
        text = '''package main
@(init) started::proc "contextless"(){}
@(fini) finished::proc "contextless"(){}
@(export) external::proc "c"(){}
@(require) retained::proc(){}
@(private="file") unused::proc(){}
main::proc(){}
'''
        errors, updated = self.reports(text,'--apply')
        self.assertEqual([error['code'] for error in errors], ['thor/unused-procedure'])
        self.assertIn("'unused'",errors[0]['msgs'][0])
        self.assertEqual(updated, text.encode())

    def test_native_test_procedures_are_automatically_used(self):
        text = '''package main
import "core:testing"
@(test) example::proc(t:^testing.T){_=t}
main::proc(){}
'''
        for engine in ('python','rust'):
            self.write(text)
            compiler = native.PYTHON if engine == 'python' else native.RUST
            result = subprocess.run([str(compiler),'test',str(self.app),'--vet=unused','-json-errors',
                                     '--output-dir',str(self.output)],cwd=self.root,capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            packet, _ = json.JSONDecoder().raw_decode(result.stderr.lstrip())
            self.assertEqual(packet['errors'], [])

    def test_unused_procedure_also_gets_pure_advice_and_is_converted_without_removal(self):
        text = 'package main\nanswer::proc()->int{return 1}\nmain::proc(){}\n'
        for engine in ('python','rust'):
            self.write(text)
            _, errors = self.invoke(engine)
            self.assertEqual([error['code'] for error in errors],['thor/unused-procedure','thor/prefer-fn'])
            for option in ('--apply','--ask'):
                self.write(text)
                result, errors = self.invoke(engine,option,input='y\n')
                self.assertEqual('[y/N]' in result.stdout, option=='--ask')
                self.assertEqual([error['code'] for error in errors],['thor/unused-procedure'])
                self.assertEqual(self.source.read_text(),text.replace('answer::proc','answer::fn'))
            self.write(text.replace('main::proc(){}','main::proc(){_=answer()}'))
            _, errors = self.invoke(engine,'--apply')
            self.assertEqual(errors, [])
            self.assertIn('answer::fn()',self.source.read_text())

    def test_explicit_discards_silence_unused_procedures_but_keep_pure_advice(self):
        text = 'package main\nanswer::proc()->int{return 1}\nmain::proc(){_=answer}\n'
        for engine in ('python','rust'):
            self.write(text)
            _, errors = self.invoke(engine,'--apply')
            self.assertEqual(errors, [])
            self.assertEqual(self.source.read_text(),text.replace('answer::proc','answer::fn'))

    def test_procedure_warning_positions_follow_removed_import_lines(self):
        text = '// 😀\npackage main\nimport "core:thread"\nunused::proc(){}\nmain::proc(){}\n'
        errors, updated = self.reports(text,'--apply')
        self.assertEqual([error['code'] for error in errors],['thor/unused-procedure'])
        self.assertEqual(errors[0]['pos']['offset'],updated.index(b'unused'))
        self.assertEqual(errors[0]['pos']['line'],3)

    def test_cross_package_and_native_callers_count_as_procedure_uses(self):
        (self.app/'library').mkdir()
        (self.app/'library/main.thor').write_text('package library\ncalled::proc(){}\nunused::proc(){}\n')
        (self.app/'native.odin').write_text('package main\nnative_call::proc(){from_native()}\n')
        text = 'package main\nimport "library"\nfrom_native::proc(){}\nmain::proc(){library.called();native_call()}\n'
        errors, updated = self.reports(text,'--apply')
        self.assertEqual([error['code'] for error in errors], ['thor/unused-procedure'])
        self.assertIn("'unused'",errors[0]['msgs'][0])
        self.assertTrue(errors[0]['pos']['file'].endswith('/library/main.thor'))
        self.assertEqual(updated,text.encode())

    def test_unused_procedure_pure_suggestions_share_one_validation_batch(self):
        self.write('package main\n'+''.join(f'unused_{i}::proc(x:int)->int{{return x+1}}\n' for i in range(64))+'main::proc(){}\n')
        profile = self.root/'profile.json'
        result = subprocess.run([str(native.RUST),'vet',str(self.app),'-json-errors',
                                 '--output-dir',str(self.output)],cwd=self.root,capture_output=True,text=True,
                                env={**os.environ,'THOR_PROFILE':str(profile)},timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        errors = json.loads(result.stderr)['errors']
        self.assertEqual(len(errors),128,errors)
        self.assertEqual(sum(error['code']=='thor/unused-procedure' for error in errors),64)
        self.assertEqual(sum(error['code']=='thor/prefer-fn' for error in errors),64)
        counters = json.loads(profile.read_text())['counters']
        self.assertEqual(counters.get('vet.validation_checks'),1,counters)
        self.assertEqual(counters.get('vet.portability_body_scans'),64,counters)

    def test_foreign_prototypes_are_not_unused_procedure_definitions(self):
        text = 'package main\nforeign import libc "system:c"\nforeign libc {puts::proc "c"(text:cstring)->i32 ---}\nmain::proc(){}\n'
        errors, updated = self.reports(text,'--apply')
        self.assertEqual(errors, [])
        self.assertEqual(updated,text.encode())

    def test_procedure_value_variables_retain_the_existing_unused_binding_rule(self):
        text = 'package main\nmain::proc(){mutable:=proc(){};immutable:>proc(){}}\n'
        errors, updated = self.reports(text,'--apply')
        self.assertEqual([error['code'] for error in errors],['thor/unused']*2)
        self.assertEqual(updated,text.encode())

    def test_unused_procedure_warnings_and_source_survive_checked_and_unchecked_runs(self):
        text = 'package main\nimport "core:fmt"\nunused::proc(){fmt.println("never")}\nmain::proc(){fmt.println("ready")}\n'
        for engine in ('python','rust'):
            self.write(text)
            self.invoke(engine,'--apply')
            self.assertEqual(self.source.read_text(),text)
            for flags in (['-debug'],native.UNCHECKED):
                result, errors = self.invoke(engine,'--vet=unused',*flags,command='run')
                self.assertEqual(result.stdout,'ready\n')
                self.assertEqual([error['code'] for error in errors],['thor/unused-procedure'])
                self.assertNotIn('still allocated',result.stderr)


if __name__ == '__main__':
    unittest.main()

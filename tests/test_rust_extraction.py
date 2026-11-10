"""Isolated helper dependency closures, before native execution is connected."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from thorlang.driver import Project, frontend_binary
from thorlang.evaluation import Evaluator
from tests import test_rust_compiler as native


def location(node):
    return [str(node.source.path), node.kind, node.start, node.end]


class RustExtractionTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['cargo', 'build', '--release', '--locked', '--offline',
                                 '--manifest-path', str(native.ROOT/'rust/Cargo.toml'),
                                 '--example', 'extraction_protocol'],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout+result.stderr)
        cls.frontend = frontend_binary('odin')[0]

    def compare(self, prefix, expression, files=None, body_prefix=""):
        with tempfile.TemporaryDirectory(prefix='thor-extraction-') as directory:
            entry = Path(directory).resolve()/'main.thor'
            entry.write_text('package main\n'+prefix+'\nmain::proc(){'+body_prefix+';_=#run '+expression+'}\n')
            for name, text in (files or {}).items():
                path = entry.parent/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            project = Project(entry, self.frontend, ['-debug'], cache_frontend=False)
            evaluator = Evaluator(project, 'odin')
            project.resolver.native_enabled = True
            request = next(node.role('expr') for node in project.sources[entry].nodes if node.kind == 'Run')
            keep, imports, aliases, procedures, queries, calls, external = evaluator.closure([request])
            import_nodes = [node for source, names in imports.items() for node, name in source.import_names.items()
                            if name in names]
            targets = []
            for callee, target in calls.items():
                if isinstance(target, str):
                    value = ['builtin', target]
                else:
                    signature = next((signature for symbol, signature in project.resolver.procedure_candidates(callee)
                                      if symbol is target), None)
                    value = ['procedure', location(signature.parent if signature else target.value)]
                targets.append([location(callee), value])
            helper_imports = []
            for source in project.resolver.sources:
                for namespace, alias in source.hidden_namespaces.items():
                    imported = source.import_dir(source.imports[namespace])
                    if imported:
                        helper_imports.append([str(source.path), alias, str(imported)])
            expected = {'keep': [location(node) for node in keep],
                        'imports': [location(node) for node in import_nodes],
                        'aliases': [[str(source.path), name] for source, names in aliases.items() for name in names],
                        'procedures': [location(node) for node in procedures],
                        'queries': [[location(node), code] for node, code in queries.items()],
                        'calls': targets, 'external': [[location(node), list(value)] for node, value in external.items()],
                        'helper_imports': helper_imports}
            result = subprocess.run([str(native.ROOT/'rust/target/release/examples/extraction_protocol'), str(entry)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            def canonical(packet):
                namespaces = {alias: '<import:'+path+'>' for _, alias, path in packet.pop('helper_imports')}
                for query in packet['queries']:
                    for alias, replacement in namespaces.items():
                        query[1] = re.sub(r'\b'+re.escape(alias)+r'\b', replacement, query[1])
                for values in packet.values():
                    values.sort(key=lambda value: json.dumps(value))
                return packet
            self.assertEqual(canonical(json.loads(result.stdout)), canonical(expected))

    def test_only_transitive_selected_declarations_enter_the_helper(self):
        self.compare('''
BASE::3
unused::proc()->int{return 9}
leaf::proc(n:int)->int{return n+BASE}
work::proc(n:int)->int{return leaf(n)}
''', 'work(2)')

    def test_recursive_helpers_keep_their_own_local_declarations(self):
        self.compare('''
even::proc(n:int)->bool{local:=n-1;return n==0 ? true : odd(local)}
odd::proc(n:int)->bool{return n==0 ? false : even(n-1)}
''', 'even(4)')

    def test_selected_overload_does_not_pull_in_the_other_body(self):
        self.compare('''
state:int
integer::proc(n:int)->int{return n}
real::proc(n:f64)->int{return int(n)+state}
pick::proc{integer,real}
''', 'pick(3)')

    def test_type_queries_keep_type_definitions_and_skip_runtime_operands(self):
        self.compare('''
Row::struct{value:int}
runtime::proc()->Row{panic("must not execute")}
work::proc()->int{return size_of(runtime())}
''', 'work()')

    def test_type_queries_of_runtime_globals_need_no_global_initializer(self):
        self.compare('''
state:[4]int
work::proc()->int{return size_of(state)}
''', 'work()')

    def test_runtime_globals_and_package_callbacks_are_not_copied(self):
        self.compare('''
state:int
@(init) initialize::proc(){state=7}
@(fini) finalize::proc(){state=9}
work::proc(n:int)->int{return n+state}
''', 'work(3)')

    def test_imported_procedures_preserve_the_defining_package(self):
        self.compare('import "models"', 'models.value(3)', files={
            'models/model.thor': 'package models\nBASE::2\nvalue::proc(n:int)->int{return n+BASE}\n'})

    def test_selected_constants_keep_their_import_and_selected_procedures_do_not_need_aliases(self):
        self.compare('''
import "models" / BASE, value
''', 'value(BASE)', files={
            'models/model.thor': 'package models\nBASE::3\nvalue::proc(n:int)->int{return n+1}\n'})

    def test_builtin_aliases_are_rewritten_without_copying_alias_declarations(self):
        self.compare('''
LENGTH::len
work::proc(values:[3]int)->int{return LENGTH(values)}
''', 'work([3]int{1,2,3})')

    def test_io_calls_retain_only_their_arguments_for_lazy_purity_errors(self):
        self.compare('''
import "core:fmt"
work::proc(n:int)->int{if n<0{fmt.println(n)};return n}
''', 'work(3)')

    def test_local_constants_are_recorded_for_original_scope_reconstruction(self):
        self.compare('work::proc(n:int)->int{return n+1}', 'work(COUNT)', body_prefix='COUNT::3')

    def test_invalid_deferred_contract_does_not_expand_unrelated_body_dependencies(self):
        self.compare('''
cleanup::proc(){}
unrelated::proc(n:int)->int{return n+1}
@(deferred_none=cleanup)
work::proc(n:int)->int{return unrelated(n)}
''', 'work(3)')

    def test_unresolved_using_contract_does_not_expand_the_body(self):
        self.compare('''
Row::struct{value:int}
unrelated::proc()->int{return 1}
work::proc(using input:$T)->int{return unrelated()+value}
''', 'work(Row{3})')

    def test_wildcard_import_does_not_turn_builtin_types_into_selected_aliases(self):
        self.compare('''
import * "models"
work::proc(n:int)->int{return n+BASE}
''', 'work(3)', files={'models/model.thor': 'package models\nBASE::2\n'})

    def test_reviewed_native_clone_stays_an_external_contract(self):
        self.compare('import "core:slice"', 'slice.clone([]int{1,2})')

    def test_standard_library_scalar_calls_use_inspected_bodies(self):
        self.compare('import "core:math"', 'math.sqrt(f64(4))')

    def test_qualified_builtin_aliases_keep_native_identity(self):
        self.compare('''
import b "base:builtin"
LENGTH::b.len
work::proc(values:[3]int)->int{return LENGTH(values)}
''', 'work([3]int{1,2,3})')

    def test_native_type_alias_keeps_compiler_runtime_external(self):
        self.compare('''
import "core:mem"
work::proc()->(int,mem.Allocator_Error) #optional_allocator_error {return 7,nil}
''', 'work()')

    def test_conditional_declarations_keep_their_selection_inputs(self):
        self.compare('''
ENABLED::true
when ENABLED{value::proc(n:int)->int{return n+1}}else{value::proc(n:int)->int{return n+2}}
''', 'value(3)')


if __name__ == '__main__':
    unittest.main()

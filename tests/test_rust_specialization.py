"""Lexical evaluator bindings and helper type spelling against the reference."""
import json
import hashlib
import re
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from thorlang.driver import Project, frontend_binary
from thorlang.semantics import Type
from thorlang.specialization import Binding, Specializations, Unresolved, type_code
from tests.native_paths import rust_binary
from tests import test_rust_compiler as native


class RustSpecializationTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['cargo', 'build', '--release', '--locked', '--offline',
                                 '--manifest-path', str(native.ROOT/'rust/Cargo.toml'),
                                 '--example', 'specialization_protocol'],
                                capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise RuntimeError(result.stdout+result.stderr)
        cls.frontend = frontend_binary('odin')[0]

    def compare(self, source, files=None):
        with tempfile.TemporaryDirectory(prefix='thor-specialization-') as directory:
            entry = Path(directory).resolve()/'main.thor'
            entry.write_text('package main\n'+source+'\nmain::proc(){}\n')
            for name, text in (files or {}).items():
                path = entry.parent/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            project = Project(entry, self.frontend, ['-debug'], cache_frontend=False)
            resolver = project.resolver
            expected_types = {}
            type_imports = {}
            def evaluation_import(owner, caller):
                alias = caller.prefix+'evaluation_dependency_'+hashlib.sha256(str(owner.path.parent).encode()).hexdigest()[:12]
                type_imports[alias] = str(owner.path.parent)
                return alias+'.'
            for symbol in resolver.by_decl.values():
                if not symbol.name.startswith('sample_'):
                    continue
                typ = resolver.type_of(symbol.node)
                source = symbol.node.source
                packets = {}
                for label, context in [('local', symbol.node), ('detached', None), ('bridged', None)]:
                    source.evaluation_context = context
                    if hasattr(source, 'evaluation_import'):
                        del source.evaluation_import
                    if label == 'bridged':
                        source.evaluation_import = lambda owner, caller=source: evaluation_import(owner, caller)
                    try:
                        packets[label] = {'code': type_code(typ, source, resolver)}
                    except Unresolved as error:
                        packets[label] = {'error': str(error)}
                if hasattr(source, 'evaluation_import'):
                    del source.evaluation_import
                expected_types[str(source.path)+':'+str(symbol.node.start)] = packets
            state = Specializations(SimpleNamespace(r=resolver))
            for source in resolver.sources:
                for proc in source.nodes:
                    if proc.kind != 'Procedure' or not proc.parent or not any(
                            name.text == 'generic' for name in proc.parent.group('names')):
                        continue
                    for field in proc.role('type').role('params').children:
                        for node in field.group('names'):
                            name = node.text.lstrip('$')
                            if name not in {'T', 'N'}:
                                continue
                            symbol = resolver.by_decl[field, name]
                            value = {'kind': 'integer', 'value': '7'} if name == 'N' else None
                            state.bindings[field, name] = Binding(symbol, Type('scalar', 'int'), value)
            expected_lexical = {
                str(source.path)+':'+str(node.start): sorted(name for _, name in state.relevant(node))
                for source in resolver.sources if source.path.is_relative_to(entry.parent) for node in source.nodes
                if node.kind in {'Ident', 'PolyType'}
            }
            main = project.sources[entry]
            for source in resolver.sources:
                for namespace, alias in source.hidden_namespaces.items():
                    imported = source.import_dir(source.imports[namespace])
                    if imported is not None:
                        type_imports[alias] = str(imported.resolve())
            expected = {'types': expected_types, 'imports': type_imports, 'lexical': expected_lexical,
                        'declarations': {'code': state.declarations(main, state.bindings)},
                        'predicate': {'code': state.predicate(main.root, state.bindings)},
                        'restored': True, 'changed': bool(state.bindings), 'frames': [1, 0]}
            result = subprocess.run([str(rust_binary('specialization_protocol', example=True)),
                                     str(entry)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            def normalize(packet):
                imports = packet.pop('imports')
                def code(value):
                    if isinstance(value, dict):
                        for key, item in value.items():
                            if key == 'code':
                                for alias, path in imports.items():
                                    item = re.sub(r'\b'+re.escape(alias)+r'\b', '<import:'+path+'>', item)
                                value[key] = item
                            else:
                                code(item)
                code(packet)
                return packet
            self.assertEqual(normalize(json.loads(result.stdout)), normalize(expected))

    def test_structural_sequences_and_pointer_types(self):
        self.compare('''
sample_number:i16
sample_string:string
sample_array:[3]u64
sample_slice:[][]int
sample_dynamic:[dynamic]string
sample_pointer:^^int
''')

    def test_named_nominal_record_enum_union_and_alias_types(self):
        self.compare('''
Count::distinct int
Row::struct{value:int}
Rows::[2]Row
Kind::enum{one,two}
Choice::union{int,Row}
sample_count:Count
sample_row:Row
sample_rows:Rows
sample_kind:Kind
sample_choice:Choice
''')

    def test_generic_record_constant_dimensions(self):
        self.compare('''
Row::struct($T:typeid,$N:int){values:[N]T}
sample_record:Row(f64,3)
''')

    def test_local_types_require_the_original_lexical_scope(self):
        self.compare('''
outer::proc(){
    Row::struct{value:int}
    sample_row:Row
    sample_slice:[]Row
    {Count::distinct int;sample_count:Count}
}
''')

    def test_imported_types_keep_the_visible_namespace(self):
        self.compare('''
import other "models"
sample_row:other.Row
sample_rows:[]other.Row
''', files={'models/model.thor': 'package models\nRow::struct{value:int}\n'})

    def test_reexported_types_need_helper_only_imports(self):
        self.compare('''
import "bridge"
sample_row:bridge.Row
''', files={'bridge/model.thor': 'package bridge\nimport "../models"\nRow::models.Row\n',
           'models/model.thor': 'package models\nRow::struct{value:int}\n'})

    def test_lexical_bindings_shadowing_and_saved_frames(self):
        self.compare('''
generic::proc($T:typeid,$N:int,value:T){
    _=value
    {N::2;_=N;other:T;_=other}
    _=N
    nested::proc(x:T)->T{return x}
    _=nested
}
''')


if __name__ == '__main__':
    unittest.main()

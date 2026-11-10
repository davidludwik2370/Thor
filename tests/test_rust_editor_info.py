"""TLS mutation metadata belongs to the exact current UTF-8 source snapshot."""
import json
from pathlib import Path
import subprocess
import unittest
from tests import test_rust_compiler as native

class RustEditorInfoTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp

    def invoke(self, engine, overlay=None):
        compiler = native.ROOT/'thor' if engine == 'python' else native.RUST
        args = [str(compiler), 'check', str(self.source), '--output-dir', str(self.root/engine),
                '--cache-dir', str(self.root/'cache'), '--editor-info', '-json-errors']
        if overlay:
            args.extend(['--overlay', str(overlay)])
        result = subprocess.run(args, capture_output=True, text=True, cwd=self.root, timeout=90)
        self.assertNotEqual(result.returncode, 3, result.stderr)
        packet = json.loads(result.stderr)
        rows = {row['file']: row for row in packet.get('slice_access', [])}
        for row in rows.values():
            row['parameters'].sort(key=lambda p:p['offset'])
        return result, packet, rows

    def compare(self, source, *, fail=False, overlay=None, files=None):
        self.source.write_text(source)
        for name, content in (files or {}).items():
            path = self.app/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        python, packet, expected = self.invoke('python', overlay)
        rust, actual_packet, actual = self.invoke('rust', overlay)
        self.assertEqual(rust.returncode, python.returncode, rust.stderr+'\n'+python.stderr)
        self.assertEqual(rust.returncode != 0, fail, rust.stderr)
        self.assertIn(str(self.source), actual)
        for path, row in expected.items():
            if Path(path).is_relative_to(self.app):
                self.assertEqual(actual.get(path), row)
        self.assertEqual(self.source.read_text(), source)
        return actual[str(self.source)], actual_packet

    def test_direct_writes_readers_and_explicit_annotations(self):
        row, _ = self.compare('''package main
write::proc(destination:[]int){destination[0]=2}
read::proc(source:[]int)->int{return source[0]}
explicit::proc(#mut a,b:[]int){_=a;_=b}
main::proc(){values:=[]int{1};readonly:>values;_=read(readonly);write(values)}
''')
        self.assertEqual([(p['name'], p['explicit']) for p in row['parameters']],
                         [('destination',False),('a',True)])

    def test_generic_and_transitive_write_effects(self):
        row, _ = self.compare('''package main
write::proc(destination:[]$T){destination[0]=T{}}
read::proc(source:[]$T)->T{return source[0]}
forward::proc(values:[]int){write(values)}
main::proc(){values:=[]int{1};readonly:>values;_=read(readonly);forward(values)}
''')
        self.assertEqual([p['name'] for p in row['parameters']], ['destination','values'])

    def test_readonly_fields_and_inline_value_copies(self):
        row, _ = self.compare('''package main
Record::struct{values:[]int,n:int}
main::proc(){r:>Record{[]int{1},2};n:=r.n;_=n;_=r.values[0]}
''')
        self.assertTrue(row['readonly_access'])
        self.assertEqual(row['parameters'], [])

    def test_unused_procedures_and_per_name_annotations(self):
        row, _ = self.compare('''package main
write::proc(#mut a,b:[]int){_=a;_=b}
unused::proc(values:[]int){values[0]=3}
main::proc(){}
''')
        self.assertEqual([(p['name'],p['explicit']) for p in row['parameters']],
                         [('a',True),('values',False)])

    def test_readonly_diagnostic_retains_metadata_and_source_positions(self):
        source='''package main
write::proc(values:[]int){values[0]=2}
main::proc(){values:>[]int{1};write(values)}
'''
        row, packet = self.compare(source, fail=True)
        self.assertEqual([p['name'] for p in row['parameters']], ['values'])
        self.assertGreater(packet['error_count'],0)
        errors=[error for error in packet['errors'] if error['type']=='error']
        self.assertTrue(any(error['pos']['file']==str(self.source) and error['pos']['line']==3 for error in errors))
        self.assertNotIn('.thor-rust-build', json.dumps(errors))

    def test_overlay_hash_and_hint_changes_do_not_attach_to_saved_snapshot(self):
        source='''package main
change::proc(values:[]int){_=values}
main::proc(){}
'''
        row, _ = self.compare(source)
        self.assertEqual(row['parameters'], [])
        replacement = self.root/'snapshot.thor'
        replacement.write_text(source.replace('_=values','values[0]=2')+'// 😀\n')
        overlay=self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(self.source):str(replacement)}}))
        changed, _ = self.compare(source, overlay=overlay)
        self.assertNotEqual(changed['source_hash'], row['source_hash'])
        self.assertEqual([p['name'] for p in changed['parameters']], ['values'])
        restored, _ = self.compare(source)
        self.assertEqual(restored, row)

    def test_imported_helper_effects_use_original_parameter_locations(self):
        source='''package main
import "library"
forward::proc(values:[]int){library.write(values)}
main::proc(){values:=[]int{1};forward(values)}
'''
        row, _ = self.compare(source, files={'library/main.thor':
            'package library\nwrite::proc(destination:[]int){destination[0]=2}\n'})
        self.assertEqual([p['name'] for p in row['parameters']], ['values'])

    def test_uninstantiated_generic_and_signature_contracts_match_reference(self):
        cases=[
            'write::proc(values:[]$T){when T==int{values[0]=2}else{_=values}}\nmain::proc(){}',
            'work::proc(values:$T){copy:=values;copy[0]=2}\nmain::proc(){values:>[1,2];work(values)}',
            'work::proc(#mut values:..int){_=values}\nmain::proc(){}',
            'Callback::proc(#mut values:[]int)\nmain::proc(){}',
            'work::proc(values:[][]$T){values[0][0]=T{}}\nmain::proc(){}',
        ]
        for source in cases:
            with self.subTest(source=source):self.compare('package main\n'+source+'\n')

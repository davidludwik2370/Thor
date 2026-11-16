"""Zipped SOA rows retain their inferred fields through editor requests."""
import json
import subprocess
import unittest

from tests import test_tls as base


class SoaZipTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    position = staticmethod(base.TLSTests.position)
    hover = base.TLSTests.hover

    def complete(self, client, path, text, needle):
        result = client.request('textDocument/completion', {
            'textDocument': {'uri': path.as_uri()},
            'position': self.position(text, needle, len(needle)),
            'context': {'triggerKind': 2, 'triggerCharacter': '.'},
        })
        return {item['label']: item for item in result['items']}

    def test_builtin_and_aliases_infer_rows_with_an_incomplete_selector(self):
        for imports, alias, callee in [
            ('', '', 'soa_zip'),
            ('', 'zip::soa_zip\nagain::(zip)\n', 'again'),
            ('import "thor:basic"', '', 'basic.zip'),
            ('import b "thor:basic"', '', 'b.zip'),
            ('import * "thor:basic"', '', 'zip'),
            ('import _ "thor:basic" / zip', '', 'zip'),
        ]:
            with self.subTest(callee=callee, imports=imports):
                text = '''// 😀 caller-local column types
package main
IMPORTS
main::proc(){
    ALIAS
    Gender::enum{male,female}
    names:=[]string{"Tim","Sally","Tom"}
    ages:=[]int{22,5,12,7}
    genders:=[]Gender{.male,.female,.male}
    people:>CALLEE(name=names,age=ages,gender=genders)
    for person,i in people{
        name:=person.name;age:=person.age;gender:=person.gender
        _=name;_=age;_=gender;_=i
        _=person.
    }
}
'''.replace('IMPORTS', imports).replace('ALIAS', alias).replace('CALLEE', callee)
                client, path = self.open(text, compiler=False)
                row = self.hover(client, path, text, 'person,i')
                self.assertIsNotNone(row)
                row_text = row['contents']['value']
                self.assertRegex(row_text, r'name:\s+string')
                self.assertRegex(row_text, r'age:\s+int')
                self.assertRegex(row_text, r'gender:\s+Gender')
                use = self.hover(client, path, text, 'person.name')
                self.assertIsNotNone(use)
                self.assertRegex(use['contents']['value'], r'gender:\s+Gender')
                items = self.complete(client, path, text, '_=person.')
                self.assertEqual(set(items), {'name', 'age', 'gender'})
                self.assertIn('string', json.dumps(items['name']))
                self.assertIn('int', json.dumps(items['age']))
                self.assertIn('Gender', json.dumps(items['gender']))
                for needle, typ in [('name:=', 'string'), ('age:=', 'int'), ('gender:=', 'Gender')]:
                    value = self.hover(client, path, text, needle)
                    self.assertIsNotNone(value, needle)
                    self.assertIn(typ, value['contents']['value'])

    def test_unsaved_column_changes_refresh_row_and_column_completions(self):
        text = '''package main
main::proc(){
    names:=[]string{"Tim"};ages:=[]int{22}
    people:=soa_zip(name=names,age=ages)
    for person in people{_=person.}
    _=people.
}
'''
        client, path = self.open(text, compiler=False)
        for version, column, typ in [(1, 'age', 'int'), (2, 'score', 'f64'), (3, 'age', 'int')]:
            changed = text if column == 'age' else text.replace('ages:=[]int{22}', 'ages:=[]f64{2.5}').replace('age=ages', 'score=ages')
            if version > 1:
                client.send('textDocument/didChange', {
                    'textDocument': {'uri': path.as_uri(), 'version': version},
                    'contentChanges': [{'text': changed}],
                })
            for needle in ('_=person.', '_=people.'):
                items = self.complete(client, path, changed, needle)
                self.assertTrue({'name', column} <= items.keys(), items)
                self.assertNotIn('score' if column == 'age' else 'age', items)
                self.assertIn(typ, json.dumps(items[column]))
            self.assertIn(typ, json.dumps(self.hover(client, path, changed, 'person in')))
        self.assertEqual(path.read_text(), text)

    def test_positional_columns_and_imported_generic_element_types(self):
        text = '''package main
import lib "library"
identity::proc(values:[]$T)->[]T{return values}
main::proc(){
    Records::distinct []lib.Person
    records:Records
    pointers:=[]^lib.Person{nil}
    rows:=soa_zip(identity(records),lib.people(),pointers)
    for row,i in rows{_=row.;_=row._0.;_=row._1.;_=row._2.;_=i}
    item:=rows[0]
    _=item.
}
'''
        library = '''package library
Person::struct{name:string,age:int}
people::proc()->[]Person{return nil}
'''
        client, path = self.open(text, files={'library/library.odin': library}, compiler=False)
        self.assertEqual(set(self.complete(client, path, text, '_=row.')), {'_0', '_1', '_2'})
        self.assertEqual(set(self.complete(client, path, text, '_=item.')), {'_0', '_1', '_2'})
        for column in ('_0', '_1', '_2'):
            items = self.complete(client, path, text, '_=row.'+column+'.')
            self.assertEqual(set(items), {'name', 'age'})
        hover = self.hover(client, path, text, 'i in rows')
        self.assertIsNotNone(hover)
        self.assertIn('int', hover['contents']['value'])

    def test_user_procedures_named_like_the_builtin_keep_their_return_type(self):
        text = '''package main
Row::struct{custom:bool}
main::proc(){
    soa_zip::proc()->Row{return {true}}
    zip::soa_zip
    result:=zip()
    _=result.
}
'''
        client, path = self.open(text, compiler=False)
        self.assertEqual(set(self.complete(client, path, text, '_=result.')), {'custom'})

    def test_valid_zip_program_and_compiler_diagnostics_keep_editor_inference(self):
        text = '''package main
import * "thor:basic"
main::proc(){
    names:=[]string{"Tim"};ages:=[]int{22}
    people:>zip(name=names,age=ages)
    for person in people{
        if person.name!="Tim"||person.age!=22{panic("zip row values")}
        _=person.age
    }
}
'''
        client, path = self.open(text)
        client.diagnostic(path.as_uri(), empty=True)
        self.assertIn('struct', json.dumps(self.hover(client, path, text, 'person in')))
        # Validate the normal compiler entry point as well as native editor inference.
        for flags in ([], ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']):
            result = subprocess.run([str(base.THOR), 'run', str(path),
                '--output-dir', str(path.parent/'checked'), *flags], cwd=path.parent,
                capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        broken = text.replace('_=person.age', '_=person.missing')
        client.notifications.clear()
        client.send('textDocument/didChange', {
            'textDocument': {'uri': path.as_uri(), 'version': 2},
            'contentChanges': [{'text': broken}],
        })
        errors = client.diagnostic(path.as_uri(), contains='missing')
        self.assertTrue(any('missing' in error['message'] for error in errors))
        self.assertIn('struct', json.dumps(self.hover(client, path, broken, 'person in')))


if __name__ == '__main__':
    unittest.main()

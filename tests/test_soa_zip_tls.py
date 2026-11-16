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
    semantic_spans = base.TLSTests.semantic_spans

    def assert_column_modifiers(self, client, path, text, expected):
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        spans=self.semantic_spans(client,path,text)
        for name,immutable in expected.items():
            fields=[span for span in spans if span[2]=='property' and span[3]==name]
            self.assertTrue(fields,name)
            for field in fields:
                self.assertEqual(bool(field[4]&readonly),immutable,(name,field))

    def test_named_column_modifiers_follow_the_view_and_each_input_through_aliases(self):
        for imports,alias,callee in [
            ('','','soa_zip'),
            ('','zip::soa_zip\nagain::(zip)\n','again'),
            ('import "thor:basic"','','basic.zip'),
            ('import b "thor:basic"','','b.zip'),
            ('import * "thor:basic"','','zip'),
            ('import _ "thor:basic" / zip','','zip'),
        ]:
            with self.subTest(callee=callee):
                text='''// 😀 original column positions
package main
IMPORTS
main::proc(){
    ALIAS
    readonly_values:>[]int{1}
    mutable_values:=[]int{2}
    mixed:=CALLEE(protected_column=readonly_values,editable_column=mutable_values)
    frozen:>(CALLEE(frozen_column=mutable_values))
    _=mixed;_=frozen
}
'''.replace('IMPORTS',imports).replace('ALIAS',alias).replace('CALLEE',callee)
                client,path=self.open(text,compiler=False)
                self.assert_column_modifiers(client,path,text,{
                    'protected_column':True,'editable_column':False,'frozen_column':True})

    def test_named_column_modifiers_preserve_provenance_copies_and_shallow_boundaries(self):
        text='''package main
import "core:slice"
import * "thor:basic"
Holder::struct{items:[]int}
identity::proc(values:[]int)->[]int{return values}
count_rows::proc(rows:#soa[]$T)->int{return len(rows)}
main::proc(){
    Kind::enum{a}
    mutable_kinds:=[]Kind{.a}
    immutable_kinds:>[]Kind{.a}
    immutable_values:>[]int{1}
    mutable_values:=[]int{2}
    fixed_values:>[1]int{3}
    dynamic_values:>[dynamic]int{}
    holder:=Holder{mutable_values}
    pointer:>&holder
    frozen_holder:>Holder{mutable_values}
    copied_holder:=frozen_holder
    view:=zip(
        forwarded_column=identity(immutable_values),
        copied_column=copied_holder.items,
        fixed_column=fixed_values[:],
        dynamic_column=dynamic_values[:],
        pointer_column=pointer.items,
        fresh_column=slice.clone(immutable_values),
        mutable_enum_column=mutable_kinds,
        immutable_enum_column=immutable_kinds,
    )
    choice:=true
    chosen:>choice ? soa_zip(branch_column=mutable_values) : soa_zip(branch_column=mutable_values)
    count:>count_rows(soa_zip(temporary_column=mutable_values))
    {
        soa_zip::proc(ordinary_argument:[]int)->int{return len(ordinary_argument)}
        ordinary:>soa_zip(ordinary_argument=mutable_values)
        _=ordinary
    }
    _=view;_=chosen;_=count
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        self.assert_column_modifiers(client,path,text,{
            'forwarded_column':True,'copied_column':True,'fixed_column':True,
            'dynamic_column':False,'pointer_column':False,'fresh_column':False,
            'mutable_enum_column':False,'immutable_enum_column':True,
            'branch_column':True,'temporary_column':False,'ordinary_argument':False})

    def test_named_column_modifiers_refresh_after_unsaved_mutability_changes(self):
        text='package main\nmain::proc(){values:=[]int{1};rows:=soa_zip(column=values);_=rows}\n'
        client,path=self.open(text,compiler=False)
        variants=[(text,False),(text.replace('values:=','values:>'),True),
                  (text.replace('rows:=','rows:>'),True),(text,False)]
        for version,(changed,immutable) in enumerate(variants,1):
            if version>1:
                client.send('textDocument/didChange',{
                    'textDocument':{'uri':path.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
            self.assert_column_modifiers(client,path,changed,{'column':immutable})
        self.assertEqual(path.read_text(),text)

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

    def test_using_zip_row_fields_have_hover_types_inside_fstrings(self):
        text='''package main
import * "thor:basic"
main::proc(){
    Gender::enum{male,female}
    names:>[]string{"Tim"};ages:>[]int{22};genders:>[]Gender{.male}
    people:>zip(name=names,age=ages,gender=genders)
    for person in people{
        using person
        print(name,age,gender)
        print(f"{name} is a {age} year old {gender}")
    }
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        for needle,typ in [('{name}','string'),('{age}','int'),('{gender}','Gender')]:
            hover=self.hover(client,path,text,needle)
            self.assertIsNotNone(hover,needle)
            self.assertIn(typ,hover['contents']['value'])
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        spans=self.semantic_spans(client,path,text)
        by_start={start:(kind,word,bool(modifiers&readonly)) for start,_,kind,word,modifiers in spans}
        for fragment in ('print(name,age,gender)','{name}','{age}','{gender}'):
            for name in ('name','age','gender'):
                if name not in fragment:continue
                offset=len(text[:text.index(fragment)+fragment.index(name)].encode())
                self.assertEqual(by_start.get(offset),('variable',name,True),(fragment,by_start))


if __name__ == '__main__':
    unittest.main()

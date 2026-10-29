"""Rename through real TLS, checking the exact original-source edits."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import unquote, urlparse

from tests.test_tls import Client, ROOT


class RenameTests(unittest.TestCase):
    def test_global_immutable_enum_array_renames_from_declaration_and_references(self):
        text='''package main
import rl "palette"
NodeType::enum{Empty,Wall,Start,End}
// colors in this comment and string must not be renamed.
colors :> [NodeType]rl.Color {
    .Empty = rl.BLACK,
    .Wall = rl.LIGHTGRAY,
    .Start = rl.BLUE,
    .End = rl.YELLOW,
}
main::proc(){_=colors[.Start]; _="colors"}
shadow::proc(){colors:=1; _=colors}
'''
        palette='''package palette
Color::struct{r,g,b,a:u8}
BLACK::Color{0,0,0,255}
LIGHTGRAY::Color{200,200,200,255}
BLUE::Color{0,0,255,255}
YELLOW::Color{255,255,0,255}
'''
        files={'main.thor':text,'other.odin':'package main\nread::proc()->u8{return colors[.End].r}\n',
               'palette/main.odin':palette}
        client,root,main=self.project(files)
        expected=dict(files)
        expected['main.thor']=text.replace('colors :>','theme :>').replace('colors[','theme[')
        expected['other.odin']=files['other.odin'].replace('colors[','theme[')
        for needle,offset in [('colors :>',0),('colors :>',3),('colors :>',6),('colors[.Start]',2)]:
            with self.subTest(needle=needle,offset=offset):
                edit=self.rename(client,main,text,needle,'theme',offset)
                self.assertEqual(self.apply(root,files,edit),expected)
        self.assert_builds(root,expected)

    def test_global_immutable_rename_tracks_unsaved_mutability_and_unicode(self):
        saved='package main\n/* 😀 */ café :> 2\nmain::proc(){_=café}\n'
        client,root,main=self.project({'main.thor':saved})
        for version,operator in enumerate([':>',':=',':>'],2):
            text=saved.replace(':>',operator)
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},
                                                'contentChanges':[{'text':text}]})
            with self.subTest(operator=operator,version=version):
                edit=self.rename(client,main,text,'café '+operator,'résultat',len('café'))
                self.assertEqual(self.apply(root,{'main.thor':text},edit),{'main.thor':text.replace('café','résultat')})
        self.assertEqual(main.read_text(),saved)

    def test_parameter_shadowing_in_same_block_keeps_initializer_on_outer_binding(self):
        text='''package main
Board::struct{values:[2]int}
read::proc(board:Board,i:int)->int{
    before:=board.values[i]
    board:=board
    return before+board.values[i]
}
main::proc(){_=read({},0)}
'''
        files={'main.thor':text}
        client,root,main=self.project(files)
        edits=self.rename(client,main,text,'board:Board','input_board',1)
        expected=text.replace('board:Board','input_board:Board').replace('before:=board.values','before:=input_board.values').replace('board:=board','board:=input_board')
        self.assertEqual(self.apply(root,files,edits),{'main.thor':expected})
        self.assert_builds(root,{'main.thor':expected})

    def test_public_symbol_rename_through_wildcard_and_namespace_free_imports(self):
        for declaration in ['import * "library"', 'import _ "library" / work']:
            with self.subTest(declaration=declaration):
                text='package main\n'+declaration+'\nmain::proc(){_=work(2)}\n'
                files={'main.thor':text,'library/main.thor':'package library\nwork::proc(value:int)->int{return value}\n'}
                client,root,main=self.project(files)
                edit=self.rename(client,main,text,'work(2)','compute',1)
                expected={name:value.replace('work','compute') for name,value in files.items()}
                self.assertEqual(self.apply(root,files,edit),expected)
                self.assert_builds(root,expected)

    def test_new_import_markers_cannot_be_renamed_as_namespaces(self):
        for declaration,needle,message in [
            ('import * "core:fmt"','fmt.println','default package name'),
            ('import _ "core:fmt" / println','_ "core:fmt"','blank import marker'),
        ]:
            with self.subTest(declaration=declaration):
                call='fmt.println("hello")' if '*' in declaration else 'println("hello")'
                text='package main\n'+declaration+'\nmain::proc(){'+call+'}\n'
                client,root,main=self.project({'main.thor':text})
                params={'textDocument':{'uri':main.as_uri()},'position':self.pos(text,needle)}
                self.assertIsNone(client.request('textDocument/prepareRename',params))
                with self.assertRaisesRegex(AssertionError,message):
                    client.request('textDocument/rename',{**params,'newName':'format'})

    @classmethod
    def setUpClass(cls):
        if not shutil.which(os.environ.get('THOR_ODIN', 'odin')):
            raise unittest.SkipTest('Odin is required')
        subprocess.run(['python3', str(ROOT/'tools/build_tls.py')], check=True, capture_output=True, timeout=90)

    def project(self, files, main='main.thor', opened=()):
        temporary = tempfile.TemporaryDirectory(prefix='tls-rename-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        for name, text in files.items():
            path = root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        client = Client(root, compiler=False)
        self.addCleanup(client.close)
        self.assertEqual(client.capabilities['renameProvider'], {'prepareProvider': True})
        for name in dict.fromkeys([main, *opened]):
            self.open_document(client, root/name, files[name])
        return client, root, root/main

    @staticmethod
    def open_document(client, path, text):
        client.send('textDocument/didOpen', {'textDocument': {'uri': path.as_uri(), 'languageId': 'thor', 'version': 1, 'text': text}})

    @staticmethod
    def pos(text, needle, offset=0):
        before = text[:text.index(needle)+offset]
        return {'line': before.count('\n'), 'character': len(before.rsplit('\n', 1)[-1].encode('utf-16-le'))//2}

    def rename(self, client, main, text, needle, name, offset=0):
        params = {'textDocument': {'uri': main.as_uri()}, 'position': self.pos(text, needle, offset)}
        prepared = client.request('textDocument/prepareRename', params)
        self.assertIsNotNone(prepared, (needle, text))
        return client.request('textDocument/rename', {**params, 'newName': name})

    def apply(self, root, files, edit):
        output = dict(files)
        for uri, edits in edit['changes'].items():
            path = Path(unquote(urlparse(uri).path))
            name = str(path.relative_to(root))
            self.assertIn(name, output, 'Rename escaped the test sources')
            original = output[name]
            lines = original.splitlines(keepends=True)
            def absolute(pos):
                return len(''.join(lines[:pos['line']]))+len(lines[pos['line']].encode('utf-16-le')[:2*pos['character']].decode('utf-16-le'))
            spans = sorted((absolute(e['range']['start']), absolute(e['range']['end']), e['newText']) for e in edits)
            for previous, current in zip(spans, spans[1:]):
                self.assertLessEqual(previous[1], current[0], 'Duplicate or overlapping rename edits')
            for a, b, new in reversed(spans):
                original = original[:a]+new+original[b:]
            output[name] = original
        return output

    def assert_builds(self, root, files):
        for name, text in files.items():
            (root/name).write_text(text)
        result = subprocess.run([str(ROOT/'thor'), 'check', str(root), '-no-entry-point',
                                 '--output-dir', str(root/'generated')], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_in_interpolation_switch_and_comments(self):
        text = 'package main\nmain::proc(){value:=1;text:=f"value {value}";_=switch value{case 1:value;case:0};delete(text) /* value */}\n'
        files = {'main.thor': text}
        client, root, main = self.project(files)
        edit = self.rename(client, main, text, 'value:=', 'count')
        expected = text.replace('value:=', 'count:=').replace('{value}', '{count}').replace('switch value', 'switch count').replace('case 1:value', 'case 1:count')
        self.assertEqual(self.apply(root, files, edit)['main.thor'], expected)

    def test_shadowed_locals_and_loop_bindings(self):
        text = 'package main\nmain::proc(){value:=1;{value:=2;_=value};_=value;for value in [3,4]{_=value}}\n'
        files = {'main.thor': text}
        client, root, main = self.project(files)
        edit = self.rename(client, main, text, 'value:=1', 'count')
        self.assertEqual(self.apply(root, files, edit)['main.thor'], text.replace('value:=1', 'count:=1').replace('};_=value;', '};_=count;'))

    def test_unicode_name_and_unsaved_ranges(self):
        saved = 'package main\nmain::proc(){value:=1;_=value}\n'
        text = 'package main\nmain::proc(){_="😀";café:=1;_=f"{café}"}\n'
        client, root, main = self.project({'main.thor': saved})
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2}, 'contentChanges': [{'text': text}]})
        edit = self.rename(client, main, text, 'café:=', 'résultat')
        self.assertEqual(self.apply(root, {'main.thor': text}, edit)['main.thor'], text.replace('café', 'résultat'))
        self.assertEqual(main.read_text(), saved)

    def test_generic_parameter_and_immutable_binding(self):
        for text, old, new in [
            ('package main\nidentity::proc(value:$T)->T{local:T=value;return local}\n', 'T', 'Element'),
            ('package main\nmain::proc(){value:>2;items:=[value,3];_=value in items}\n', 'value', 'count'),
        ]:
            with self.subTest(old=old):
                client, root, main = self.project({'main.thor': text})
                edit = self.rename(client, main, text, old, new)
                self.assertEqual(self.apply(root, {'main.thor': text}, edit)['main.thor'], text.replace(old, new))

    def test_one_letter_generic_container_parameter_at_word_end(self):
        # The unresolved 'data' is intentional: type errors must not prevent
        # renaming the correctly declared a, including its named arguments.
        text = '''package main
import "core:slice"
sorted :: proc(
    a: $T/[]$E,
    less := proc(i,j:E)->bool{return i<j},
    allocator := context.allocator,
)->[]E {
    ret := make([]E,len(a),allocator)
    copy(ret,a)
    slice.sort_by(data,less)
    return ret
}
'''
        files = {'main.thor': text,
                 'caller.thor': 'package main\ncaller::proc(){result:=sorted(a=[]int{1,2});delete(result)}\n'}
        client, root, main = self.project(files)
        expected = dict(files)
        expected['main.thor'] = text.replace('a: $T', 'values: $T').replace('len(a)', 'len(values)').replace('copy(ret,a)', 'copy(ret,values)')
        expected['caller.thor'] = files['caller.thor'].replace('sorted(a=', 'sorted(values=')
        for needle, offset in [('a: $T', 0), ('a: $T', 1), ('len(a)', 5), ('copy(ret,a)', 10)]:
            with self.subTest(needle=needle, offset=offset):
                edit = self.rename(client, main, text, needle, 'values', offset)
                self.assertEqual(self.apply(root, files, edit), expected)

    def test_unicode_word_end_and_interpolation_closing_brace(self):
        text = 'package main\nmain::proc(){_="😀";é:=1;value:=f"{é}";delete(value);_=é}\n'
        files = {'main.thor': text}
        client, root, main = self.project(files)
        for needle, offset in [('é:=', 1), ('{é}', 2), ('_=é', 3)]:
            with self.subTest(needle=needle):
                edit = self.rename(client, main, text, needle, 'number', offset)
                self.assertEqual(self.apply(root, files, edit)['main.thor'], text.replace('é', 'number'))

    def test_struct_fields_and_enum_members(self):
        for text, needle, old, new in [
            ('package main\nRecord::struct{value:int}\nmain::proc(){r:=Record{value=2};_=r.value}\n', 'r.value', 'value', 'count'),
            ('package main\nColor::enum{Red,Blue}\nmain::proc(){c:Color=.Red;_=c==.Red}\n', '.Red', 'Red', 'Crimson'),
        ]:
            with self.subTest(old=old):
                client, root, main = self.project({'main.thor': text})
                edit = self.rename(client, main, text, needle, new, needle.index(old))
                self.assertEqual(self.apply(root, {'main.thor': text}, edit)['main.thor'], text.replace(old, new))

    def test_type_declarations_aliases_and_overload_groups(self):
        for text, old, new in [
            ('package main\nRecord::struct{value:int}\nAlias::Record\nmain::proc(){r:=Record{1};p:^Record=&r;_=p;_=Alias{2}}\n', 'Record', 'Row'),
            ('package main\nfirst::proc(v:int)->int{return v}\nsecond::proc(v:string)->string{return v}\nwork::proc{first,second}\nmain::proc(){_=work(1);_=work("a")}\n', 'work', 'compute'),
        ]:
            with self.subTest(old=old):
                client, root, main = self.project({'main.thor': text})
                edit = self.rename(client, main, text, old+'::', new)
                expected = {'main.thor': text.replace(old, new)}
                self.assertEqual(self.apply(root, {'main.thor': text}, edit), expected)
                self.assert_builds(root, expected)

    def test_record_member_in_closed_imported_file(self):
        files = {'main.thor': 'package main\nimport "lib"\nmain::proc(){r:=lib.Record{value=1};_=r.value}\n',
                 'lib/main.thor': 'package lib\nRecord::struct{value:int}\n'}
        client, root, main = self.project(files)
        edit = self.rename(client, main, files['main.thor'], 'r.value', 'count', len('r.'))
        expected = {name: text.replace('value', 'count') for name, text in files.items()}
        self.assertEqual(self.apply(root, files, edit), expected)
        self.assert_builds(root, expected)

    def test_compile_time_conditional_declarations_and_conflicts(self):
        for directive in ['when true', '#if true']:
            with self.subTest(directive=directive):
                text = 'package main\nother::3\n'+directive+'{value::2}\nmain::proc(){_=value}\n'
                client, root, main = self.project({'main.thor': text})
                edit = self.rename(client, main, text, 'value::', 'count')
                expected = {'main.thor': text.replace('value', 'count')}
                self.assertEqual(self.apply(root, {'main.thor': text}, edit), expected)
                self.assert_builds(root, expected)
                with self.assertRaisesRegex(AssertionError, 'already|conflict'):
                    self.rename(client, main, text, 'value::', 'other')

    def test_selected_imports_and_closed_mixed_source_files(self):
        files = {
            'main.thor': 'package main\nimport "lib" / work\nmain::proc(){_=work(1);_=lib.work(2)}\n',
            'other.thor': 'package main\nimport l "lib" / work\nother::proc()->int{return work(3)+l.work(4)}\n',
            'native.odin': 'package main\nimport l "lib"\nnative::proc()->int{return l.work(5)}\n',
            'lib/main.thor': 'package lib\nwork::proc(value:int)->int{return value}\n',
        }
        for needle in ['work(1)', '/ work']:
            with self.subTest(needle=needle):
                client, root, main = self.project(files)
                edit = self.rename(client, main, files['main.thor'], needle, 'compute', needle.index('work'))
                actual = self.apply(root, files, edit)
                self.assertEqual(actual, {name: text.replace('work', 'compute') for name, text in files.items()})
                self.assert_builds(root, actual)

    def test_unsaved_imported_declaration_and_unsaved_caller(self):
        files = {'main.thor': 'package main\nimport "lib"\nmain::proc(){_=lib.work(1)}\n',
                 'lib/main.thor': 'package lib\nwork::proc(value:int)->int{return value}\n'}
        client, root, main = self.project(files, opened=('lib/main.thor',))
        changed = dict(files)
        changed['lib/main.thor'] = '// 😀 unsaved declaration shift\n'+files['lib/main.thor']
        changed['main.thor'] = files['main.thor'].replace('lib.work(1)', 'lib.work(1)+lib.work(2)')
        for name in changed:
            client.send('textDocument/didChange', {'textDocument': {'uri': (root/name).as_uri(), 'version': 2}, 'contentChanges': [{'text': changed[name]}]})
        edit = self.rename(client, main, changed['main.thor'], 'lib.work', 'compute', len('lib.'))
        self.assertEqual(self.apply(root, changed, edit), {name: text.replace('work', 'compute') for name, text in changed.items()})

    def test_named_argument_locations_use_the_declarations_unicode_source(self):
        files = {'main.thor': 'package main\nimport "lib"\nmain::proc(){_=lib.work(value=2)}\n',
                 'lib/main.thor': 'package lib\n/* 😀 */ work::proc(value:int)->int{return value}\n'}
        client, root, main = self.project(files)
        owner = root/'lib/main.thor'
        self.open_document(client, owner, files['lib/main.thor'])
        edit = self.rename(client, owner, files['lib/main.thor'], 'value:int', 'input')
        expected = {name: text.replace('value', 'input') for name, text in files.items()}
        self.assertEqual(self.apply(root, files, edit), expected)
        self.assert_builds(root, expected)

    def test_import_namespace_renames_never_edit_the_path(self):
        for declaration in ['import "core:fmt"', 'import f "core:fmt" / println']:
            old = 'f' if 'import f ' in declaration else 'fmt'
            text = 'package main\n'+declaration+'\nmain::proc(){'+old+'.println("hello")}\n'
            client, root, main = self.project({'main.thor': text})
            edit = self.rename(client, main, text, old+'.println', 'format')
            expected = text.replace(declaration, declaration.replace('import ', 'import format ', 1) if old == 'fmt' else declaration.replace('import f ', 'import format ')).replace(old+'.println', 'format.println')
            self.assertEqual(self.apply(root, {'main.thor': text}, edit)['main.thor'], expected)

    def test_import_namespace_is_file_local(self):
        text = 'package main\nimport "core:fmt"\nmain::proc(){fmt.println("hello")}\n'
        files = {'main.thor': text, 'other.thor': 'package main\nimport "core:fmt"\nother::proc(){fmt.println("other")}\n'}
        client, root, main = self.project(files)
        edit = self.rename(client, main, text, 'fmt.println', 'format')
        self.assertEqual(set(edit['changes']), {main.as_uri()})

    def test_parameter_renames_include_named_arguments_in_other_files(self):
        files = {'main.thor': 'package main\nwork::proc(value:int)->int{return value}\nmain::proc(){_=work(value=1)}\n',
                 'caller.thor': 'package main\ncaller::proc()->int{return work(value=2)}\n'}
        client, root, main = self.project(files)
        edit = self.rename(client, main, files['main.thor'], 'value:int', 'input')
        self.assertEqual(self.apply(root, files, edit), {name: text.replace('value', 'input') for name, text in files.items()})

    def test_global_collision_in_another_file_is_rejected(self):
        files = {'main.thor': 'package main\nwork::proc(){}\nmain::proc(){work()}\n',
                 'other.thor': 'package main\ncompute::proc(){}\n'}
        client, root, main = self.project(files)
        with self.assertRaisesRegex(AssertionError, 'already declared|already bound|conflict'):
            self.rename(client, main, files['main.thor'], 'work::', 'compute')

    def test_incomplete_related_source_prevents_partial_global_rename(self):
        files = {'main.thor': 'package main\nwork::proc(){}\nmain::proc(){work()}\n',
                 'other.thor': 'package main\nbad::proc(){work(\n'}
        client, root, main = self.project(files)
        with self.assertRaisesRegex(AssertionError, 'syntax errors'):
            self.rename(client, main, files['main.thor'], 'work::', 'compute')

    def test_unresolved_contextual_member_does_not_produce_partial_edits(self):
        text = 'package main\nColor::enum{Red,Blue}\nmain::proc(){colors:=[.Red,.Blue];_=colors}\n'
        client, root, main = self.project({'main.thor': text})
        with self.assertRaisesRegex(AssertionError, 'Cannot safely resolve'):
            self.rename(client, main, text, 'Red,Blue', 'Crimson')

    def test_contextual_enum_rename_updates_switch_arrays_and_preserves_other_enums(self):
        text='''package main
Color::enum{Red,Blue}
Other::enum{Red,Green}
take::proc(colors:[2]Color){}
main::proc(){/* 😀 */mode:Other=.Red
    colors:[2]Color=switch mode{case .Green:[.Red,.Blue];case .Red:[.Blue,.Red];case:[.Blue,.Blue]}
    take(switch 1{case 1:[.Red,.Blue];case:[.Blue,.Red]})
    _=colors
}
'''
        expected=text.replace('Color::enum{Red','Color::enum{Crimson').replace('[.Red','[.Crimson').replace(',.Red]',',.Crimson]')
        for needle,offset in [('Red,Blue',0),('Green:[.Red',8)]:
            with self.subTest(origin=needle):
                files={'main.thor':text}
                client,root,main=self.project(files)
                edit=self.rename(client,main,text,needle,'Crimson',offset)
                self.assertEqual(self.apply(root,files,edit),{'main.thor':expected})
                references=client.request('textDocument/references',{'textDocument':{'uri':main.as_uri()},'position':self.pos(text,needle,offset),'context':{'includeDeclaration':True}})
                ranges=lambda items:{(e['range']['start']['line'],e['range']['start']['character'],e['range']['end']['character']) for e in items}
                self.assertEqual(ranges(references),ranges(edit['changes'][main.as_uri()]))
                self.assert_builds(root,{'main.thor':expected})

    def test_generated_outputs_are_not_renamed(self):
        text = 'package main\nwork::proc(){}\nmain::proc(){work()}\n'
        files = {'main.thor': text, 'output/thor-manifest.json': '{}', 'output/main.odin': text,
                 '.thor-cache/helper.odin': text, '.thor-build/main.odin': text, '.vscode/bin/main.odin': text}
        client, root, main = self.project(files)
        edit = self.rename(client, main, text, 'work::', 'compute')
        self.assertEqual(set(edit['changes']), {main.as_uri()})

    def test_conflicting_and_invalid_names_are_rejected(self):
        for body in ['value:=1;other:=2;_=value;_=other', 'value:=1;_=value;other:=2;_=other',
                     'value:=1;{other:=2;_=value;_=other}']:
            text = 'package main\nmain::proc(){'+body+'}\n'
            client, root, main = self.project({'main.thor': text})
            for new in ['other', '_', 'if', 'two words', '']:
                with self.subTest(body=body, new=new), self.assertRaises(AssertionError):
                    client.request('textDocument/rename', {'textDocument': {'uri': main.as_uri()}, 'position': self.pos(text, 'value:='), 'newName': new})

    def test_builtin_comment_and_string_are_not_rename_targets(self):
        text = 'package main\nimport "core:fmt"\n// value\nmain::proc(){value:=1;_=len([]int{value});_="value";fmt.println(value)}\n'
        client, root, main = self.project({'main.thor': text})
        for needle, offset in [('// value', 3), ('"value"', 2), ('len(', 0), ('fmt.println', len('fmt.'))]:
            result = client.request('textDocument/prepareRename', {'textDocument': {'uri': main.as_uri()}, 'position': self.pos(text, needle, offset)})
            self.assertIsNone(result, needle)


if __name__ == '__main__':
    unittest.main()

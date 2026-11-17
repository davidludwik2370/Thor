"""Readonly access paths retain their colors through aliases, using and f-strings."""
import unittest

from tests import test_tls as base


class ImmutableHighlightingTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    semantic_spans = base.TLSTests.semantic_spans
    position = staticmethod(base.TLSTests.position)
    hover = base.TLSTests.hover

    def assert_accesses(self, client, path, text, expected):
        legend = client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        readonly = 1 << legend.index('readonly')
        comptime = 1 << legend.index('thorCompileTime')
        spans = self.semantic_spans(client, path, text)
        by_start = {start: (kind, word, flags) for start, _, kind, word, flags in spans}
        for fragment, word, immutable, constant in expected:
            offset = len(text[:text.index(fragment) + fragment.rindex(word)].encode())
            token = by_start.get(offset)
            self.assertIsNotNone(token, fragment)
            self.assertIn(token[0], {'variable', 'parameter', 'property'}, fragment)
            self.assertEqual(token[1], word, fragment)
            self.assertEqual(bool(token[2] & readonly), immutable, fragment)
            self.assertEqual(bool(token[2] & comptime), constant, fragment)

    def test_using_preserves_nested_slice_storage_without_freezing_parameter_targets(self):
        text = '''package main
Leaf::struct{value:int}
Row::struct{items:[]Leaf,fixed:[1]Leaf}
inspect::proc(input:Row){
    using input
    _=f"parameter_slice {items[0].value}"
    _=f"parameter_fixed {fixed[0].value}"
}
main::proc(){
    frozen:>Row{[]Leaf{{1}},[1]Leaf{{2}}}
    using frozen
    _=items[0].value // plain
    _=f"promoted_slice {items[0].value}"
    _=f"promoted_fixed {fixed[0].value}"
    _=f"explicit_slice {frozen.items[0].value}"
}
'''
        client, path = self.open(text, compiler=False)
        self.assert_accesses(client, path, text, [
            ('parameter_slice {items[0].value}', 'value', False, False),
            ('parameter_fixed {fixed[0].value}', 'value', True, False),
            ('items[0].value // plain', 'value', True, False),
            ('promoted_slice {items[0].value}', 'value', True, False),
            ('promoted_fixed {fixed[0].value}', 'value', True, False),
            ('explicit_slice {frozen.items[0].value}', 'value', True, False),
        ])
        client.diagnostic(path.as_uri(), empty=True)

    def test_nested_using_and_type_aliases_preserve_shallow_reference_boundaries(self):
        text = '''package main
Leaf::struct{value:int}
Cell::struct{items:[]Leaf,pointer:^Leaf,growing:[dynamic]Leaf}
Cell_Alias::Cell
Outer::struct{inner:Cell_Alias}
main::proc(){
    leaf:=Leaf{1}
    frozen:>Outer{Cell_Alias{[]Leaf{{2}},&leaf,nil}}
    using frozen
    using inner
    _=f"slice {items[0].value}"
    _=f"pointer {pointer.value}"
    _=f"dynamic {growing[0].value}"
    _=f"pointer_binding {pointer}"
    copy:=leaf
    using copy
    _=f"copy {value}"
}
'''
        client, path = self.open(text, compiler=False)
        self.assert_accesses(client, path, text, [
            ('slice {items[0].value}', 'value', True, False),
            ('pointer {pointer.value}', 'value', False, False),
            ('dynamic {growing[0].value}', 'value', False, False),
            ('pointer_binding {pointer}', 'pointer', True, False),
            ('copy {value}', 'value', False, False),
        ])
        client.diagnostic(path.as_uri(), empty=True)

    def test_constant_slice_and_record_aliases_keep_readonly_storage(self):
        text = '''package main
Leaf::struct{value:int}
ROWS::[]Leaf{{1}}
ROWS_AGAIN::ROWS
RECORD::struct{items:[]Leaf}{ROWS}
main::proc(){
    _=f"direct {ROWS_AGAIN[0].value}"
    record:>RECORD
    using record
    _=f"promoted {items[0].value}"
    _=f"constant {ROWS_AGAIN}"
    mutable:=1
    _=f"mutable {mutable}"
}
'''
        client, path = self.open(text, compiler=False)
        self.assert_accesses(client, path, text, [
            ('direct {ROWS_AGAIN[0].value}', 'value', True, False),
            ('promoted {items[0].value}', 'value', True, False),
            ('constant {ROWS_AGAIN}', 'ROWS_AGAIN', True, True),
            ('mutable {mutable}', 'mutable', False, False),
        ])
        client.diagnostic(path.as_uri(), empty=True)

    def test_imported_generic_types_and_selected_aliases_keep_binding_permissions(self):
        library = '''package library
Box::struct($T:typeid){item:T}
FROZEN:>Box(int){3}
MUTABLE:=Box(int){4}
COUNT::5
'''
        text = '''package main
import lib "library"
import _ "library" / Box, COUNT
Alias::Box(int)
AGAIN::COUNT
inspect::proc(using input:Alias){
    _=f"parameter {item}"
    _=f"parameter_explicit {input.item}"
}
inspect_pointer::proc(using pointer:^lib.Box(int)){
    _=f"pointer {pointer.item}"
    _=f"pointer_promoted {item}"
}
main::proc(){
    frozen_copy:>lib.FROZEN
    mutable_copy:=lib.MUTABLE
    _=f"qualified {lib.FROZEN.item}"
    _=f"qualified_mutable {lib.MUTABLE.item}"
    _=f"qualified_binding {lib.FROZEN}"
    _=f"mutable_binding {lib.MUTABLE}"
    _=f"copied {frozen_copy.item}"
    _=f"mutable {mutable_copy.item}"
    _=f"constant {AGAIN}"
    {using lib.FROZEN;_=f"using {item}"}
    {using lib.MUTABLE;_=f"using_mutable {item}"}
    {using lib;_=f"package {FROZEN.item}"}
}
'''
        client, path = self.open(text, files={'library/main.thor': library})
        client.diagnostic(path.as_uri(), empty=True)
        self.assert_accesses(client, path, text, [
            ('parameter {item}', 'item', True, False),
            ('parameter_explicit {input.item}', 'item', True, False),
            ('pointer {pointer.item}', 'item', False, False),
            ('pointer_promoted {item}', 'item', False, False),
            ('qualified {lib.FROZEN.item}', 'item', True, False),
            ('qualified_mutable {lib.MUTABLE.item}', 'item', False, False),
            ('qualified_binding {lib.FROZEN}', 'FROZEN', True, False),
            ('mutable_binding {lib.MUTABLE}', 'MUTABLE', False, False),
            ('copied {frozen_copy.item}', 'item', True, False),
            ('mutable {mutable_copy.item}', 'item', False, False),
            ('constant {AGAIN}', 'AGAIN', True, True),
            ('using {item}', 'item', True, False),
            ('using_mutable {item}', 'item', False, False),
            ('package {FROZEN.item}', 'item', True, False),
        ])

    def test_fstring_forms_dynamic_specs_and_nested_shadowing_use_lexical_permissions(self):
        text = '''// 😀 byte positions and UTF-16 ranges
package main
Mode::enum{A}
Row::struct{value:f64,width:int,mode:Mode}
main::proc(){
    frozen:>Row{1.25,8,.A}
    using frozen
    mutable_width:=8
    _=f"ordinary {value:{width}.2f}"
    _=f"mutable_width {value:{mutable_width}.2f}"
    _=f!"manual {value}"
    _=f`raw {value}`
    _=f!`manual_raw {value}`
    _=f`nested {f"inner {value}"}`
    _=f"enum {mode}"
    {copy:=frozen;using copy;_=f"mutable_enum {mode}"}
    {value:=value;_=f"shadow {value}"}
    _=f"restored {value}"
    LOCAL::3
    _=f"constant {LOCAL}"
}
'''
        client, path = self.open(text, compiler=False)
        self.assert_accesses(client, path, text, [
            ('ordinary {value:', 'value', True, False),
            ('{width}', 'width', True, False),
            ('{mutable_width}', 'mutable_width', False, False),
            ('manual {value}', 'value', True, False),
            ('raw {value}', 'value', True, False),
            ('manual_raw {value}', 'value', True, False),
            ('inner {value}', 'value', True, False),
            ('enum {mode}', 'mode', True, False),
            ('mutable_enum {mode}', 'mode', False, False),
            ('shadow {value}', 'value', False, False),
            ('restored {value}', 'value', True, False),
            ('constant {LOCAL}', 'LOCAL', True, True),
        ])
        client.diagnostic(path.as_uri(), empty=True)

    def test_procedure_alias_results_and_named_results_do_not_inherit_input_qualifiers(self):
        text = '''package main
Row::struct{value:int}
make_row::proc()->Row{return {1}}
first::make_row
again::(first)
read::proc(using input:Row)->(result:int){
    _=f"input {value}"
    result=value
    _=f"named_result {result}"
    return
}
main::proc(){
    frozen:>again()
    mutable:=again()
    {using frozen;_=f"frozen {value}"}
    {using mutable;_=f"mutable {value}"}
    callback:>again
    copy:=callback
    _=f"callback {callback}"
    _=f"callback_copy {copy}"
}
'''
        client, path = self.open(text)
        self.assert_accesses(client, path, text, [
            ('input {value}', 'value', True, False),
            ('named_result {result}', 'result', False, False),
            ('frozen {value}', 'value', True, False),
            ('mutable {value}', 'value', False, False),
            ('callback {callback}', 'callback', True, False),
            ('callback_copy {copy}', 'copy', False, False),
        ])
        client.diagnostic(path.as_uri(), empty=True)

    def test_value_and_reference_bindings_in_comprehensions_and_type_switches(self):
        text = '''package main
Row::struct{value:int}
Choice::union{Row,^Row}
inspect::proc(choice:Choice){
    switch row in choice{
    case Row:
        using row;_=f"switch_value {value}"
        {row:=row;using row;_=f"switch_copy {value}"}
    case ^Row:using row;_=f"switch_pointer {value}"
    }
}
main::proc(){
    rows:=[2]Row{{1},{2}}
    values:=[f"comprehension_value {row.value}" for row in rows]
    references:=[f"comprehension_reference {row.value}" for &row in rows]
    _=values;_=references
    for row in rows{using row;_=f"loop_value {value}"}
    for &row in rows{using row;_=f"loop_reference {value}"}
}
'''
        client, path = self.open(text)
        self.assert_accesses(client, path, text, [
            ('switch_value {value}', 'value', True, False),
            ('switch_copy {value}', 'value', False, False),
            ('switch_pointer {value}', 'value', False, False),
            ('comprehension_value {row.value}', 'value', True, False),
            ('comprehension_reference {row.value}', 'value', False, False),
            ('loop_value {value}', 'value', True, False),
            ('loop_reference {value}', 'value', False, False),
        ])
        client.diagnostic(path.as_uri(), empty=True)

    def test_compiler_permissions_survive_record_copies_and_fstring_using_projections(self):
        text = '''package main
Leaf::struct{value:int}
Holder::struct{items:[]Leaf}
identity::proc(values:[]Leaf)->[]Leaf{return values}
main::proc(){
    frozen:>Holder{[]Leaf{{1}}}
    copy:=frozen
    using copy
    _=f"copied_slice {items[0].value}"
    view:>identity(frozen.items)
    _=f"forwarded_slice {view[0].value}"
    _=f"mutable_header {items}"
}
'''
        client, path = self.open(text)
        client.diagnostic(path.as_uri(), empty=True)
        self.assert_accesses(client, path, text, [
            ('copied_slice {items[0].value}', 'value', True, False),
            ('forwarded_slice {view[0].value}', 'value', True, False),
            ('mutable_header {items}', 'items', False, False),
        ])

    def test_unsaved_using_owner_and_parameter_changes_clear_stale_readonly_tokens(self):
        text = '''package main
Row::struct{value:int}
inspect::proc(input:Row){using input;_=f"parameter {value}"}
main::proc(){owner:>Row{1};using owner;_=f"owner {value}"}
'''
        client, path = self.open(text)
        variants = [(text, True, True),
                    (text.replace('owner:>', 'owner:='), False, True),
                    (text.replace('input:Row', 'input:^Row'), True, False),
                    (text, True, True)]
        for version, (changed, owner, parameter) in enumerate(variants, 1):
            if version > 1:
                client.notifications.clear()
                client.send('textDocument/didChange', {
                    'textDocument': {'uri': path.as_uri(), 'version': version},
                    'contentChanges': [{'text': changed}]})
            client.diagnostic(path.as_uri(), empty=True)
            self.assert_accesses(client, path, changed, [
                ('owner {value}', 'value', owner, False),
                ('parameter {value}', 'value', parameter, False),
            ])
        self.assertEqual(path.read_text(), text)

    def test_unsaved_imported_storage_changes_refresh_using_and_qualified_accesses(self):
        library = 'package library\nRow::struct{value:int}\nOWNER:>Row{1}\n'
        text = '''package main
import lib "library"
main::proc(){
    _=f"qualified {lib.OWNER.value}"
    using lib.OWNER
    _=f"promoted {value}"
}
'''
        client, path = self.open(text, files={'library/main.thor': library})
        library_path = path.parent/'library/main.thor'
        client.diagnostic(path.as_uri(), empty=True)
        client.send('textDocument/didOpen', {'textDocument': {
            'uri': library_path.as_uri(), 'languageId': 'thor', 'version': 1, 'text': library}})
        client.diagnostic(library_path.as_uri(), empty=True)
        for version, immutable in enumerate((True, False, True), 1):
            if version > 1:
                changed = library if immutable else library.replace('OWNER:>', 'OWNER:=')
                client.send('textDocument/didChange', {
                    'textDocument': {'uri': library_path.as_uri(), 'version': version},
                    'contentChanges': [{'text': changed}]})
            self.assert_accesses(client, path, text, [
                ('qualified {lib.OWNER.value}', 'value', immutable, False),
                ('promoted {value}', 'value', immutable, False),
            ])
        client.send('textDocument/didChange', {
            'textDocument': {'uri': library_path.as_uri(), 'version': 4},
            'contentChanges': [{'text': library.replace('OWNER:>', 'OWNER:=')}]})
        self.assert_accesses(client, path, text, [('qualified {lib.OWNER.value}', 'value', False, False)])
        client.send('textDocument/didClose', {'textDocument': {'uri': library_path.as_uri()}})
        self.assert_accesses(client, path, text, [('qualified {lib.OWNER.value}', 'value', True, False)])
        self.assertEqual(library_path.read_text(), library)

    def test_publishing_open_declarations_keeps_call_result_and_procedure_group_types(self):
        text = '''package main
get_foo::proc()->string{return "value"}
global_foo:=get_foo()
raw_data_slice::proc(v:$T/[]$E)->[^]E{return nil}
zzcool::proc{raw_data_slice}
main::proc(){_=global_foo;_=zzcool
    global_fo
    zzco
}
'''
        client, path = self.open(text, compiler=False)
        hover = self.hover(client, path, text, 'global_foo;')
        self.assertIsNotNone(hover)
        self.assertIn('global_foo: string', hover['contents']['value'])
        for prefix, expected in [('global_fo\n', 'global_foo: string'), ('zzco\n', 'proc(v: $T/[]$E) -> [^]E')]:
            result = client.request('textDocument/completion', {
                'textDocument': {'uri': path.as_uri()},
                'position': self.position(text, prefix, len(prefix)-1),
                'context': {'triggerKind': 1}})
            items = [item for item in result['items'] if item['label'] in {'global_foo', 'zzcool'}]
            self.assertTrue(items, result)
            docs = '\n'.join(item.get('documentation', {}).get('value', '') for item in items)
            self.assertIn(expected, docs)


if __name__ == '__main__':
    unittest.main()

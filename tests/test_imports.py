"""Wildcard and namespace-free imports through the real Thor/Odin pipeline."""
import os
import unittest
from tests import test_thor as base


class ImportTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_basic_wildcard_keeps_namespace_and_generic_defaults(self):
        self.invoke(self.program('''
values := sorted([]int{3, 1, 2})
defer delete(values)
assert(values[0] == 1 && basic.asc(1, 2, 3))
assert(_i16("42") == 42 && type(values) == []int)
print("wildcard works")
''', 'import * "thor:basic"\n'), expected='wildcard works')

    def test_native_iterator_calls_retain_their_range_protocol(self):
        self.invoke(self.program('''
it:=utf8.decode_grapheme_iterator_make("é界😀")
count:=0
for text,cluster in utf8.decode_grapheme_iterate(&it){assert(len(text)>0&&cluster.width>0);count+=1}
assert(count==3)
''','import "core:unicode/utf8"\n'))

    def test_namespace_free_selection_and_multiple_blank_imports(self):
        self.invoke(self.program('''
values := sorted([]int{2, 1})
defer delete(values)
println("selected", values[0])
''', 'import _ "thor:basic" / sorted\nimport _ "core:fmt" / println\n'), expected='selected 1')
        self.invoke(self.program('basic.print("not in scope")', 'import _ "thor:basic" / print\n'),
                    command='check', fail='Undeclared name: basic')
        self.invoke(self.program('other()', 'import _ "lib" / chosen\n'),
                    files={'lib/library.odin': 'package lib\nchosen::proc(){}\nother::proc(){}\n'},
                    command='check', fail='Undeclared name: other')
        self.invoke(self.program('assert(asc(1,2) && desc(2,1))',
                    'import _ "thor:basic" / asc\nimport _ "thor:basic" / desc\n'))

    def test_all_public_declaration_kinds_and_live_globals(self):
        self.invoke(self.program('''
thing:=Thing{value=VALUE}
assert(identity(thing).value == 7)
assert(overloaded(2) == 2 && overloaded(f64(3)) == 3)
counter=8; assert(lib.counter==8)
counter+=1;ptr:=&counter;ptr^+=1;assert(lib.counter==10)
assert(_public == 3)
''', 'import * "lib"\n'), files={'lib/library.thor': '''package lib
Thing::struct{value:int}
VALUE::7
counter:int
identity::proc(value:$T)->T{return value}
integer::proc(value:int)->int{return value}
floating::proc(value:f64)->f64{return value}
overloaded::proc{integer,floating}
_public::3
@private
secret::8
'''})

    def test_private_names_and_imports_are_not_exposed(self):
        files={'lib/library.thor': '''package lib
import "core:fmt"
@private
secret::9
@(private="file")
file_secret::8
public::7
''', 'lib/private.odin': '#+private\npackage lib\nhidden::6\n'}
        for name in ['secret', 'file_secret', 'hidden', 'fmt', 'println']:
            with self.subTest(name=name):
                self.invoke(self.program('_='+name, 'import * "lib"\n'), files=files,
                            command='check', fail='Undeclared name: '+name)

    def test_unused_conditionals_do_not_force_missing_exports(self):
        self.invoke(self.program('assert(chosen==7 && choose(2)==2)', 'import * "lib"\n'), files={
            'lib/library.thor': '''package lib
#if true {chosen::7} #else {not_chosen::9}
when false {absent::3}
choose::proc(value:$T)->T{return value}
'''})

    def test_excluded_files_do_not_reserve_wildcard_names(self):
        other_os='windows' if os.name!='nt' else 'linux'
        self.invoke(self.program('assert(VALUE==7 && absent==8)', 'import * "lib"\nabsent::8\n'), files={
            'lib/main.odin':'package lib\nVALUE::7\n',
            'lib/ignored.thor':'#+ignore\npackage lib\nabsent::9\n',
            'lib/testing.thor':'#+test\npackage lib\nabsent::10\n',
            f'lib/platform_{other_os}.odin':'package lib\nabsent::11\n'})

    def test_local_shadowing_fields_and_package_file_isolation(self):
        self.invoke(self.program('''
assert(value==7)
{value:=8; assert(value==8)}
item:=Record{value=9};assert(item.value==9)
''', 'import * "lib"\nRecord::struct{value:int}\n'), files={
            'lib/library.thor':'package lib\nvalue::7\n'})
        self.invoke(self.program('other()', 'import * "lib"\n'), directory=True, files={
            'lib/library.thor':'package lib\nvalue::7\n',
            'other.thor':'package main\nother::proc(){_=value}\n'}, fail='Undeclared name: value')
        self.invoke(self.program('_=wrapper.value', 'import "wrapper"\n'), files={
            'wrapper/library.thor':'package wrapper\nimport * "../lib"\n',
            'lib/library.odin':'package lib\nvalue::7\n'}, command='check', fail='value')

    def test_conflicts_at_imports_and_own_declarations(self):
        lib='package lib\nVALUE::7\n'
        self.invoke(self.program('', 'import * "lib"\nVALUE::8\n'), files={'lib/main.odin':lib},
                    command='check', fail='conflicts with wildcard import')
        self.invoke(self.program('', 'import * "lib"\nimport * "other"\n'),
                    files={'lib/main.odin':lib, 'other/main.odin':'package other\nVALUE::9\n'},
                    command='check', fail='Conflicting wildcard import: VALUE')
        self.invoke(self.program('', 'import * "lib"\nimport * "other"\n'),
                    files={'lib/main.odin':lib, 'other/main.odin':'package other\nwhen false {VALUE::9}\n'},
                    command='check', fail='Conflicting wildcard import: VALUE')
        self.invoke(self.program('assert(VALUE==7)', 'import * "lib"\n#if false {VALUE::8}\n'),
                    files={'lib/main.odin':lib})
        self.invoke(self.program('', 'import * "lib"\n#if true {VALUE::8}\n'),
                    files={'lib/main.odin':lib}, command='check', fail='conflicts with wildcard import')

    def test_native_core_exports_and_source_comments(self):
        self.invoke(self.program('println("native", 7)', 'import /* note */ * /* note */ "core:fmt"\n'),
                    expected='native 7')

    def test_wildcard_syntax_rejects_aliases_and_selection_lists(self):
        self.invoke(self.program('', 'import * named "thor:basic"\n'),
                    command='check', fail="a wildcard import uses the package's default name")
        self.invoke(self.program('', 'import * "thor:basic" / sorted\n'),
                    command='check', fail='a wildcard import cannot also select names')

    def test_namespace_free_selection_preserves_privacy_validation(self):
        self.invoke(self.program('', 'import _ "lib" / secret\n'),
                    files={'lib/main.odin':'package lib\n@(private) secret::7\n'},
                    command='check', fail='secret')

    def test_pure_run_and_imported_default_callback_see_wildcard_origins(self):
        self.invoke(self.program('assert(ANSWER==9)', 'import * "lib"\nANSWER::#run square(3)\n'),
                    files={'lib/main.thor':'package lib\nsquare::fn(value:int)->int{return value*value}\n'})
        self.invoke(self.program('assert(ANSWER==7)', '''import * "core:math"
bounded::fn(values:[]int)->int{return max(7,min(..values))}
ANSWER::#run bounded([]int{2,3})
'''))


if __name__ == '__main__':
    unittest.main()

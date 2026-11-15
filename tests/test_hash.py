"""Runtime value hashing through basic, native Odin, and checked/release/debug builds."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests import test_thor as base

UNCHECKED=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']
IMPORTS='import "thor:basic"\n'


class HashTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def both(self,body,prefix=IMPORTS,**options):
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.invoke(self.program(body,prefix),flags=flags,**options)

    def test_native_hash_suite_checked_and_unchecked(self):
        with tempfile.TemporaryDirectory(prefix='thor-hash-native-') as directory:
            for flags in [[],UNCHECKED]:
                with self.subTest(flags=flags):
                    result=subprocess.run([os.environ.get('THOR_ODIN','odin'),'test',str(base.ROOT/'runtime/hash'),
                        '-collection:thor='+str(base.ROOT/'runtime'),'-out:'+str(Path(directory)/('hash-test.exe' if os.name=='nt' else 'hash-test')),
                        '-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true',*flags],capture_output=True,text=True,timeout=60)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_default_algorithm_selection_seeds_and_native_counterparts(self):
        self.both('''
text:="hello 😀"
bytes:=transmute([]u8)text
default:=basic.hash(text)
standard:=xxhash.XXH3_64(bytes)
if default!=standard {panic("default XXH3")}
fnv:=basic.hash(text,algorithm=.FNV64a)
if fnv!=#hash("hello 😀","fnv64a") {panic("FNV compile-time counterpart")}
for algorithm in basic.Hash_Algorithm {
 h:=basic.hash(bytes,algorithm,seed=7)
 h2,err:=basic.try_hash(text,algorithm,seed=7)
 if h!=h2 || err!=.None {panic("algorithm/seed")}
}
basic.print("hash algorithms passed")
''',IMPORTS+'import "core:hash/xxhash"\n',expected='hash algorithms passed')

    def test_thor_literals_immutables_and_contextual_composite_values(self):
        self.both('''
literal :> [1,2,3]
ha,hb:=basic.hash(literal),basic.hash([1,2,3])
if ha!=hb {panic("Thor array literals")}
if hash_generic([1,2,3])!=ha {panic("generic hash literal")}
mutable:=literal
record :> struct{name:string,values:[]int}{"record",mutable[:]}
copy:=record
h1,h2:=basic.hash(record),basic.hash(copy)
if h1!=h2 {panic("immutable aggregate")}
chosen:=switch{case true:record;case:copy}
if basic.hash(chosen)!=h1 {panic("switch value")}
view:=mutable[-2:]
if basic.hash(view)!=basic.hash([2,3]) {panic("negative view")}
''',IMPORTS+'hash_generic::proc(value:$T)->u64{return basic.hash(value)}\n')

    def test_logical_numbers_constants_clones_and_nested_generic_calls(self):
        self.both('''
a:Integer=123456789012345678901234567890
b:=a
defer delete(a);defer delete(b)
ZERO::Integer(0)
zero:Integer
if basic.hash(a)!=basic.hash(b)||basic.hash(ZERO)!=basic.hash(zero) {panic("Integer ownership")}
r:=Rational(22,14);s:=Rational(11,7)
defer delete(r);defer delete(s)
if basic.hash(r)!=basic.hash(s) {panic("Rational normalization")}
one:=struct{a:Integer,r:Rational}{a,r}
two:=struct{a:Integer,r:Rational}{b,s}
if hash_any(one)!=hash_any(two) {panic("nested logical values")}
''',IMPORTS+'hash_any::proc(value:$T)->u64{return basic.hash(value)}\n')

    def test_cycle_shared_storage_and_pointer_identity_contract(self):
        self.both('''
data:=[1,2,3]
one:=struct{x,y:[]int}{data[:],data[:]}
copy:=[1,2,3]
two:=struct{x,y:[]int}{data[:],copy[:]}
if basic.hash(one)!=basic.hash(two) {panic("shared storage")}
Node::struct{next:^Node}
node:Node;node.next=&node
_,pointer_error:=basic.try_hash(node)
if pointer_error!=.None {panic("pointer cycle")}
Tree::struct{children:[]Tree}
nodes:=make([]Tree,1);defer delete(nodes)
nodes[0].children=nodes
h,err:=basic.try_hash(nodes[0])
if h!=0||err!=.Cyclic_Data {panic("container cycle")}
''')

    def test_known_resources_maps_sets_and_inactive_resource_fields_are_rejected(self):
        self.both('''
lock:sync.Mutex
_,resource_error:=basic.try_hash(lock)
if resource_error!=.Unsupported_Type {panic("mutex")}
_,pointer_error:=basic.try_hash(&lock)
if pointer_error!=.None {panic("explicit address")}
table:map[string]int
_,map_error:=basic.try_hash(table)
names:set[string]
_,set_error:=basic.try_hash(names)
if map_error!=.Unsupported_Type||set_error!=.Unsupported_Type {panic("unordered collections")}
Record::struct{values:[]sync.Mutex}
empty:Record
_,empty_error:=basic.try_hash(empty)
if empty_error!=.Unsupported_Type {panic("empty unsupported schema")}
''',IMPORTS+'import "core:sync"\n')

    def test_debug_asan_fstrings_and_scoped_arena_do_not_leak(self):
        _,result=self.invoke(self.program('''
{
 context.allocator=basic.scoped_arena()
 values:=basic.range(100)
 integer:Integer=123456789012345678901234567890
 record:=struct{values:[]int,n:Integer}{values,integer}
 h:=basic.hash(record)
 basic.print(f"hash has bits: {h!=0}")
}
''',IMPORTS),flags=['-debug'],expected='hash has bits: true')
        self.assertNotIn('Thor allocation tracker:',result.stderr)
        self.assertNotIn('AddressSanitizer:',result.stderr)

    def test_successful_calls_and_nonpanicking_errors_need_no_allocator(self):
        self.both('''
data:=[1,2,3]
expected:=basic.hash(data)
{
 context.allocator=mem.nil_allocator()
 context.temp_allocator=mem.nil_allocator()
 actual:=basic.hash(data)
 _,err:=basic.try_hash(map[string]int{})
 if actual!=expected||err!=.Unsupported_Type {panic("allocation-free")}
}
''',IMPORTS+'import "core:mem"\n')

    def test_panicking_error_does_not_depend_on_the_failing_allocator(self):
        self.both('''
context.allocator=mem.nil_allocator()
context.temp_allocator=mem.nil_allocator()
_=basic.hash(map[string]int{})
''',IMPORTS+'import "core:mem"\n',fail='hash: unsupported value type')

    def test_unsupported_type_panic_has_field_path_in_release_too(self):
        self.both('''
Data::struct{inner:struct{callback:proc()}}
value:Data
_=basic.hash(value)
''',fail='hash: unsupported value type')
        _,result=self.invoke(self.program('''
Data::struct{inner:struct{callback:proc()}}
value:Data
_=basic.hash(value)
''',IMPORTS),fail='value.inner.callback')
        self.assertNotIn('__thor_',result.stdout+result.stderr)

    def test_cyclic_panic_in_debug_has_the_field_path(self):
        _,result=self.invoke(self.program('''
Tree::struct{children:[]Tree}
nodes:=make([]Tree,1)
nodes[0].children=nodes
_=basic.hash(nodes[0])
''',IMPORTS),flags=['-debug'],fail='hash: cyclic container data at value.children[0]')
        self.assertNotIn('__thor_',result.stdout+result.stderr)

    def test_selected_wildcard_aliases_and_native_odin_consumer(self):
        for declaration,call in [('import * "thor:basic"','hash'),
                                  ('import _ "thor:basic" / hash, Hash_Algorithm','hash'),
                                  ('import "thor:basic"\ncalculate::basic.hash','calculate')]:
            with self.subTest(declaration=declaration):
                self.invoke(self.program(f'h:={call}("hello",algorithm=.FNV64a);if h!=#hash("hello","fnv64a"){{panic("import")}}',declaration+'\n'))
        self.invoke(self.program('native()'),directory=True,files={'native.odin':'''package main
import "thor:basic"
native::proc(){h:=basic.hash("hello",algorithm=.FNV64a);if h!=#hash("hello","fnv64a"){panic("native consumer")}}
'''})

    def test_bad_algorithm_argument_stays_a_normal_source_error(self):
        _,result=self.invoke(self.program('_=basic.hash(1,algorithm="wrong")',IMPORTS),
                             command='check',fail='Cannot convert')
        self.assertIn('main.thor',result.stderr)
        self.assertNotIn('__thor_',result.stderr)


if __name__=='__main__':unittest.main()

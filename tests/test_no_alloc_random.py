"""RNG source discovery, callback provenance and zero-allocation execution."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from tests import test_rust_compiler as base

CALLBACK='''fill::proc(data:rawptr,mode:runtime.Random_Generator_Mode,buffer:[]byte){
 if mode==.Read {for &byte in buffer {byte=7}}
}
'''

class NoAllocRandomTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    compare=base.RustCompilerTests.compare
    invoke=base.RustCompilerTests.invoke

    def check(self,body,decl='',*,fail=None,flags=(),files=None):
        header='#+feature global-context\n' if '@(init)' in decl else ''
        return self.compare(header+'package main\nimport "core:math/rand"\nimport "base:runtime"\n'+decl+
            '\n@(no_alloc)\nwork::proc(){'+body+'}\nmain::proc(){work()}\n',
            command='check',fail=fail,flags=['-thread-count:1',*flags],files=files)

    def test_default_generator_distributions_ranges_and_borrowed_operations(self):
        for body in ['_=rand.uint64();_=rand.uint32();_=rand.uint128()',
                     '_=rand.int_max(17);_=rand.int_range(-3,7);_=rand.uint64_max(17)',
                     '_=rand.float32();_=rand.float64()',
                     'buffer:[37]byte;_=rand.read(buffer[:])',
                     'values:=[1,2,3];rand.shuffle(values[:]);_=rand.choice(values[:])']:
            with self.subTest(body=body):self.check(body)

    def test_seeded_lazy_and_reset_generators(self):
        for body in ['state:rand.Default_Random_State;gen:=rand.default_random_generator(&state);_=rand.uint64(gen)',
                     'state:=rand.create(u64(7));gen:=rand.default_random_generator(&state);_=rand.int_max(17,gen)',
                     'state:=rand.create([]byte{1,2,3});gen:=rand.default_random_generator(&state);rand.reset(u64(9),gen);_=rand.uint64(gen)',
                     'rand.reset(u64(7));_=rand.query_info();_=rand.uint64()']:
            with self.subTest(body=body):self.check(body)

    def test_linux_target_uses_the_reviewed_entropy_and_rng_sources(self):
        self.check('buffer:[128]byte;_=rand.read(buffer[:]);_=rand.int_max(17)',flags=['-target:linux_arm64'])

    def test_constructor_and_distribution_aliases_and_readonly_copies(self):
        self.check('ctor::rand.default_random_generator;sample::rand.int_max;state:rand.Default_Random_State;'
                   'a:>ctor(state=&state);b:>a;_=sample(17,gen=b)')
        self.check('generator:=runtime.Random_Generator{procedure=fill};copy:=generator;_=rand.uint64(copy)',CALLBACK)

    def test_explicit_custom_callbacks_and_forwarded_generator_arguments(self):
        self.check('gen:=rand.Generator{procedure=fill};_=sample(gen)',CALLBACK+
                   'sample::proc(gen:rand.Generator)->u64{return rand.uint64(gen)}')
        self.check('gen:=rand.Generator{procedure=fill};_=rand.uint64(gen);_=rand.uint64(gen)',CALLBACK)

    def test_callbacks_are_checked_in_the_requested_mode(self):
        callback=CALLBACK.replace('if mode==.Read','if mode==.Reset {_=make([]int,1)};if mode==.Read')
        self.check('gen:=rand.Generator{procedure=fill};_=rand.uint64(gen)',callback)
        self.check('gen:=rand.Generator{procedure=fill};rand.reset(u64(7),gen)',callback,fail="builtin 'make'")

    def test_allocating_and_unknown_generators_fail_with_a_useful_path(self):
        callback=CALLBACK.replace('byte=7','_=make([]int,1);byte=7')
        self.check('gen:=rand.Generator{procedure=fill};_=rand.uint64(gen)',callback,fail="builtin 'make'")
        self.check('sample(input)','input:rand.Generator\nsample::proc(gen:rand.Generator){_=rand.uint64(gen)}',fail='RNG callback target is unknown')
        self.check('_=rand.perm(4)',fail="builtin 'make'")

    def test_callback_reassignments_and_pointer_escapes_do_not_keep_old_targets(self):
        decl=CALLBACK+'bad::proc(data:rawptr,mode:runtime.Random_Generator_Mode,buffer:[]byte){_=make([]int,1)}\n'
        for body in ['gen:=rand.Generator{procedure=fill};gen.procedure=bad;_=rand.uint64(gen)',
                     'gen:=rand.Generator{procedure=fill};using gen;procedure=bad;_=rand.uint64(gen)',
                     'gen:=rand.Generator{procedure=fill};change(&gen);_=rand.uint64(gen)',
                     'gen:=rand.Generator{procedure=fill};for i in 0..<2 {_=rand.uint64(gen);gen.procedure=bad}']:
            with self.subTest(body=body):self.check(body,decl+'change::proc(gen:^rand.Generator){gen.procedure=bad}',fail='no_alloc:')

    def test_escaped_addresses_cannot_regain_a_stale_callback_fact(self):
        decl='''safe::proc(){}
bad::proc(){_=make([]int,1)}
saved:^proc()
remember::proc(value:^proc()){saved=value}
mutate::proc(){saved^=bad}
'''
        self.check('callback:=safe;remember(&callback);callback=safe;mutate();callback()',decl,fail='no_alloc:')
        self.check('record:=Box{safe};remember(&record.callback);record=Box{safe};mutate();record.callback()',
                   decl+'Box::struct{callback:proc()}',fail='no_alloc:')

    def test_callback_data_mutation_invalidates_the_original_storage(self):
        decl='''safe::proc(){}
bad::proc(){_=make([]int,1)}
Box::struct{callback:proc()}
rewrite::proc(data:rawptr,mode:runtime.Random_Generator_Mode,buffer:[]byte){
 box:=cast(^Box)data
 box.callback=bad
}
'''
        self.check('box:=Box{safe};gen:=rand.Generator{procedure=rewrite,data=&box};_=rand.uint64(gen);box.callback()',decl,fail='no_alloc:')

    def test_ambient_replacements_are_verified_even_outside_the_contract(self):
        self.check('context.random_generator=rand.default_random_generator();_=rand.uint64()')
        self.check('_=rand.uint64()',CALLBACK+'@(init)\nconfigure::proc(){context.random_generator=rand.Generator{procedure=fill}}')
        callback=CALLBACK.replace('byte=7','_=make([]int,1);byte=7')
        self.check('_=rand.uint64()',callback+'@(init)\nconfigure::proc(){context.random_generator=rand.Generator{procedure=fill}}',fail="builtin 'make'")

    def test_ambient_opaque_replacement_and_context_addresses_are_rejected(self):
        for decl,body in [('input:rand.Generator','context.random_generator=input;_=rand.uint64()'),
                          ('','pointer:=&context.random_generator;_=pointer;_=rand.uint64()')]:
            with self.subTest(body=body):self.check(body,decl,fail='ambient RNG')

    def test_unrelated_callbacks_outside_the_contract_do_not_poison_context(self):
        self.check('_=rand.uint64()',
                   'callback:proc()\n@(export)\nforeign_entry::proc(){callback()}')
        self.check('state:=rand.create(u64(7));gen:=rand.default_random_generator(&state);_=rand.uint64(gen)',
                   'callback:proc()\n@(export)\nforeign_entry::proc(){callback()}')
        self.check('_=rand.uint64()',
                   'callback:proc "contextless"()\n@(export)\nforeign_entry::proc(){callback()}')
        self.check('callback();_=rand.uint64()','callback:proc()',fail='indirect or unresolved')

    def test_mutated_generator_initializers_do_not_prove_ambient_replacements(self):
        decl=CALLBACK+'bad::proc(data:rawptr,mode:runtime.Random_Generator_Mode,buffer:[]byte){_=make([]int,1)}\n'
        for mutation in ['gen.procedure=bad','using gen;procedure=bad']:
            with self.subTest(mutation=mutation):
                self.check('_=rand.uint64()',decl+'@(init)\nconfigure::proc(){gen:=rand.Generator{procedure=fill};'+
                           mutation+';context.random_generator=gen}',fail='ambient RNG')

    def test_catalog_handles_commented_declarations_without_indexing_literals(self):
        from thorlang.native import declared_names
        names=declared_names('/* fake::proc(){} /* nested */ */\ntext::`wrong: int`\na,\n β: int\nreal /* gap */ :: proc(){} // nope:int\n')
        self.assertTrue({'text','a','β','real'}<=names)
        self.assertTrue({'fake','wrong','nope'}.isdisjoint(names))

    def test_ambient_facts_do_not_prove_callback_equality_or_non_nil(self):
        self.check('context.random_generator=runtime.Random_Generator{};if context.random_generator.procedure==nil {_=make([]int,1)}',fail="builtin 'make'")
        self.check('if context.random_generator.procedure!=nil {_=make([]int,1)}',fail="builtin 'make'")

    def test_local_context_copies_do_not_replace_the_ambient_generator(self):
        self.check('copy:=context;copy.random_generator=runtime.Random_Generator{};_=copy;_=rand.uint64()')

    def test_inactive_and_unreachable_context_replacements_do_not_poison_default(self):
        self.check('when false {context.random_generator=input};_=rand.uint64()',
                   'input:rand.Generator\nunreachable_helper::proc(){context.random_generator=input}')

    def test_familiar_rng_names_are_not_a_callback_exemption(self):
        self.check('gen:=rand.Generator{procedure=default_random_generator_proc};_=rand.uint64(gen)',
                   CALLBACK.replace('fill::','default_random_generator_proc::').replace('byte=7','_=make([]int,1);byte=7'),fail="builtin 'make'")

    def test_imported_native_initializers_cannot_hide_context_replacement(self):
        self.check('_=rand.uint64()','import "hook"',fail="builtin 'make'",files={
            'hook/configure.odin':'''#+feature global-context
package hook
import "base:runtime"
fill::proc(data:rawptr,mode:runtime.Random_Generator_Mode,buffer:[]byte){_=make([]int,1)}
@(init)
configure::proc(){context.random_generator=runtime.Random_Generator{procedure=fill}}
'''})

    def test_sdk_overlays_cannot_inherit_rng_summaries(self):
        root=Path(subprocess.check_output([os.environ.get('THOR_ODIN','odin'),'root'],text=True).strip())
        for relative in ['base/runtime/random_generator_chacha8.odin','base/runtime/os_specific_darwin.odin',
                         'base/runtime/__thor_added_unreviewed.odin']:
            with self.subTest(relative=relative):
                original=root/relative;replacement=self.root/'replacement.odin'
                replacement.write_bytes(original.read_bytes()+b'\n// changed SDK snapshot\n' if original.is_file()
                                        else b'package runtime\nadded_symbol::7\n')
                packet=self.root/'overlay.json';packet.write_text(json.dumps({'Replace':{str(original):str(replacement)}}))
                self.check('_=rand.uint64()',flags=['--overlay',str(packet)],fail='effect profile does not match')

    def test_untyped_layout_queries_match_unsigned_parameters_and_aliases(self):
        self.check('size::size_of;alignment::align_of;offset::offset_of;value:int;'
                   'take(size_of(value));take(size(value));take(alignment(int));take_ptr(offset(Pair,b))',
                   'Pair::struct{a,b:int}\ntake::proc(value:uint){}\ntake_ptr::proc(value:uintptr){}')

    def test_partial_runtime_packages_load_requested_declarations_only(self):
        self.source.write_text('package main\nimport "base:runtime"\n@(no_alloc)\nmain::proc(){_=runtime.default_random_generator()}')
        profile=self.root/'profile.json'
        result=self.invoke('rust','check',flags=['-thread-count:1'],env={**os.environ,'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(profile.read_text())
        self.assertGreater(data['counters'].get('no_alloc.discovery_files',0),0,data)
        self.assertLess(data['counters']['no_alloc.discovery_files'],10,data)

    def test_repeated_rng_calls_share_sdk_and_context_proofs(self):
        self.source.write_text('package main\nimport "core:math/rand"\n@(no_alloc)\nmain::proc(){'+
                               '_=rand.int_max(17);'*100+'}')
        profile=self.root/'profile.json'
        result=self.invoke('rust','check',flags=['-thread-count:1'],env={**os.environ,'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode,0,result.stderr)
        counters=json.loads(profile.read_text())['counters']
        self.assertEqual(counters.get('no_alloc.sdk_snapshots'),1,counters)
        self.assertEqual(counters.get('no_alloc.rng_graphs'),1,counters)

    def test_runtime_counts_and_results_checked_and_unchecked(self):
        source='''package main
import "base:runtime"
import "core:math/rand"
import "core:mem"
import "core:fmt"
Counter::struct{count:int}
counted::proc(data:rawptr,mode:runtime.Random_Generator_Mode,buffer:[]byte){
 if mode==.Read {counter:=cast(^Counter)data;counter.count+=1;buffer[0]=u8(counter.count)}
}
@(no_alloc)
exercise::proc()->u64{
 buffer:[2049]byte
 _=rand.read(buffer[:]) // First use and several generator refills.
 a:=rand.create(u64(42));b:=rand.create(u64(42))
 ga:=rand.default_random_generator(&a);gb:=rand.default_random_generator(&b)
 for i in 0..<400 {if rand.uint64(ga)!=rand.uint64(gb){panic("seeded RNG mismatch")}}
 rand.reset(u64(42),ga);rand.reset(u64(42),gb)
 result:=rand.uint64(ga)
 if result!=rand.uint64(gb){panic("reset mismatch")}
 values:=[1,2,3,4];rand.shuffle(values[:],ga)
 if rand.int_range(1,5,ga)<1{panic("range failure")}
 counter:Counter
 custom:=rand.Generator{procedure=counted,data=&counter}
 _=rand.uint64(custom);_=rand.uint64(custom)
 if counter.count!=2{panic("callback state mismatch")}
 return result
}
main::proc(){
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,context.allocator)
 previous:=context.allocator;previous_temp:=context.temp_allocator
 context.allocator=mem.tracking_allocator(&tracker);context.temp_allocator=context.allocator
 result:=exercise();count:=tracker.total_allocation_count
 context.allocator=previous;context.temp_allocator=previous_temp
 mem.tracking_allocator_destroy(&tracker)
 if count!=0||result==0{panic("RNG allocation or result regression")}
 fmt.println("zero allocations")
}
'''
        for flags in [[],base.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(source,flags=['-thread-count:1',*flags],expected='zero allocations')

if __name__=='__main__':unittest.main()

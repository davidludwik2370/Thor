"""Vet-only graph lifetimes: violations, explicit unknowns, and safe ownership.

Unsafe fixtures are checked, never executed. Runtime oracles remain enabled
with native assertions and bounds/type checks disabled.
"""
import json
import unittest
from tests import test_rust_compiler as base


class LifetimeVetTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke

    def vet(self,body,*,prefix='',checks='lifetime,leak',flags=(),command='vet',apply=False,files=None,status=None):
        self.source.write_text('package main\n'+prefix+'\n'+body+'\n', newline='')
        for name,text in (files or {}).items():
            (self.app/name).write_text(text, newline='')
        result=self.invoke('rust',command,directory=bool(files),flags=[f'--vet={checks}','--comptime=required','-json-errors','-thread-count:1',*(['--apply'] if apply else []),*flags])
        packet=json.loads(result.stderr) if result.stderr.strip() else {'errors':[]}
        expected=status if status is not None else int(command=='build' and any(f.get('certainty')=='definite' for f in packet['errors']))
        self.assertEqual(result.returncode,expected,result.stdout+result.stderr)
        self.assertFalse(any(e['type']=='error' for e in packet['errors']) and command!='build',packet)
        return packet['errors']

    def codes(self,body,**kw):
        return [e.get('code') for e in self.vet(body,**kw)]

    def test_scoped_and_explicit_deferred_cleanup_are_verified(self):
        codes=self.codes('main::proc(){a~:=make([]int,2);b:=new(int);defer mem.free(b);a[0]=7;b^=a[0]}',prefix='import "core:mem"')
        self.assertEqual(codes,[])

    def test_unknown_allocator_is_not_misreported_as_a_definite_leak(self):
        findings=self.vet('main::proc(){values:=make([]int,2);_=values}')
        self.assertEqual([f['code'] for f in findings],['thor/leak-unknown'])
        self.assertEqual(findings[0]['certainty'],'unverified')
        self.assertIn('allocator lifetime is unknown',str(findings))
        self.assertNotIn('fix',findings[0])

    def test_known_heap_lost_owner_is_a_definite_leak(self):
        findings=self.vet('main::proc(){v:=make([]int,2,allocator=runtime.heap_allocator());_=v}',prefix='import "base:runtime"')
        self.assertEqual([f['code'] for f in findings],['thor/leak'])
        self.assertEqual(findings[0]['certainty'],'definite')

    def test_alias_and_view_reads_after_delete(self):
        for alias in ['value','value[:]','value[1:]']:
            with self.subTest(alias=alias):
                codes=self.codes(f'main::proc(){{value:=make([]int,3);alias:={alias};delete(value);_=alias[0]}}')
                self.assertIn('thor/lifetime',codes)

    def test_double_free_and_rebased_cleanup(self):
        for body in ['v:=make([]int,3);delete(v);delete(v)',
                     'v:=make([]int,3);view:=v[1:];delete(view);delete(v)']:
            with self.subTest(body=body):
                findings=self.vet('main::proc(){'+body+'}')
                self.assertIn('thor/lifetime',[f['code'] for f in findings])

    def test_inner_scope_view_used_after_cleanup(self):
        codes=self.codes('main::proc(){view:[]int;{v~:=make([]int,2);view=v};_=view[0]}')
        self.assertIn('thor/lifetime',codes)

    def test_container_append_keeps_scoped_element_identity(self):
        findings=self.vet('bad::proc()->[dynamic][]int{v~:=make([]int,2);out:=make([dynamic][]int);append(&out,v);return out}\nmain::proc(){}')
        self.assertEqual([f['code'] for f in findings],['thor/lifetime'])
        self.assertIn('out',self.source.read_text()[findings[0]['pos']['offset']:findings[0]['pos']['end_offset']])

    def test_output_parameter_retention_across_helpers(self):
        findings=self.vet('stash::proc(out:^[]int,v:[]int){out^=v}\nbad::proc()->[]int{v~:=make([]int,2);out:[]int;stash(&out,v);return out}\nmain::proc(){}')
        self.assertEqual([f['code'] for f in findings],['thor/lifetime'])
        self.assertTrue(findings[0]['related'])

    def test_global_retention_through_helper(self):
        codes=self.codes('saved:[]int\nstash::proc(v:[]int){saved=v}\nmain::proc(){v~:=make([]int,2);stash(v)}')
        self.assertIn('thor/lifetime',codes)

    def test_owning_outputs_and_caller_cleanup_are_valid(self):
        codes=self.codes('get::proc(out:^[]int){out^=make([]int,2)}\nmain::proc(){v:[]int;get(&v);defer delete(v);_=v[0]}')
        self.assertEqual(codes,[])

    def test_direct_owning_returns_and_multi_results_transfer_ownership(self):
        codes=self.codes('get::proc()->([]int,int){return make([]int,2),7}\nmain::proc(){v~,n:=get();_=v[0]+n}')
        self.assertEqual(codes,[])

    def test_independent_scalar_and_fixed_record_copies_are_safe(self):
        codes=self.codes('Row::struct{n:int}\nget::proc()->Row{p~:=new(Row);p.n=7;return p^}\nmain::proc(){_=get()}')
        self.assertEqual(codes,[])

    def test_clone_is_shallow_but_breaks_outer_storage_aliases(self):
        codes=self.codes('get::proc()->[]int{v~:=make([]int,2);return slice.clone(v)}\nmain::proc(){v~:=get();_=v[0]}',prefix='import "core:slice"')
        self.assertEqual(codes,[])
        codes=self.codes('main::proc(){v:=make([]int,2);outer:=[1][]int{v};copy:=outer;delete(v);_=copy[0][0]}')
        self.assertIn('thor/lifetime',codes)

    def test_maps_structs_and_nested_containers_preserve_edges(self):
        for store,read in [('b:=Box{v}','b.value[0]'),
                           ('m:=make(map[int][]int);m[1]=v;defer delete(m)','m[1][0]'),
                           ('outer:=make([][]int,1);outer[0]=v;defer delete(outer)','outer[0][0]')]:
            with self.subTest(store=store):
                codes=self.codes('Box::struct{value:[]int}\nmain::proc(){v:=make([]int,2);'+store+';delete(v);_='+read+'}')
                self.assertIn('thor/lifetime',codes)

        codes=self.codes('bad::proc()->map[^int]int{v:=make([]int,1);m:=make(map[^int]int);m[&v[0]]=1;delete(v);return m}\nmain::proc(){}')
        self.assertIn('thor/lifetime',codes)

    def test_outer_cleanup_does_not_discharge_nested_allocations(self):
        findings=self.vet('main::proc(){inner:=make([]int,2);outer~:=make([][]int,1);outer[0]=inner}')
        self.assertEqual([f['code'] for f in findings],['thor/leak-unknown'])

    def test_borrowed_factory_result_must_not_be_cleaned(self):
        findings=self.vet('get::proc()->string{return "static"}\nmain::proc(){text~:=get();_=text}')
        self.assertEqual([f['code'] for f in findings],['thor/lifetime'])
        self.assertIn('static storage',str(findings))

    def test_unknown_callback_distinguished_from_visible_callback(self):
        codes=self.codes('work::proc(callback:proc(v:[]int)){v~:=make([]int,2);callback(v)}\nmain::proc(){}')
        self.assertEqual(codes,['thor/lifetime-unknown'])
        codes=self.codes('read::proc(v:[]int)->int{return v[0]}\nmain::proc(){v~:=make([]int,2);callback:=read;_=callback(v)}')
        self.assertEqual(codes,[])

    def test_readonly_is_not_a_no_retain_promise(self):
        codes=self.codes('work::proc(callback:proc(#ro v:[]int)){v~:>make([]int,2);callback(v)}\nmain::proc(){}')
        self.assertIn('thor/lifetime-unknown',codes)

    def test_conditional_cleanup_does_not_become_a_definite_use_after_free(self):
        findings=self.vet('work::proc(b:bool){v:=make([]int,2);if b{delete(v)};_=v[0]}\nmain::proc(){}')
        self.assertIn('thor/lifetime-unknown',[f['code'] for f in findings])
        self.assertNotIn('thor/lifetime',[f['code'] for f in findings])

    def test_two_branch_cleanup_is_verified(self):
        codes=self.codes('work::proc(b:bool){v:=make([]int,2);if b{delete(v)}else{delete(v)}}\nmain::proc(){}')
        self.assertEqual(codes,[])

    def test_early_return_does_not_run_later_defers(self):
        codes=self.codes('work::proc(b:bool){if b{return};v:=make([]int,2);defer delete(v);_=v}\nmain::proc(){}')
        self.assertEqual(codes,[])

    def test_lifo_deferred_reader_runs_before_cleanup(self):
        codes=self.codes('read::proc(v:[]int){_=v[0]}\nmain::proc(){v:=make([]int,2);defer delete(v);defer read(v)}')
        self.assertEqual(codes,[])
        codes=self.codes('read::proc(v:[]int){_=v[0]}\nmain::proc(){v:=make([]int,2);defer read(v);defer delete(v)}')
        self.assertIn('thor/lifetime',codes)

    def test_loop_scoped_cleanup_and_zero_allocations(self):
        codes=self.codes('main::proc(){for i in 0..<5{v~:=make([]int,2);v[0]=i};empty~:=make([]int,0);_=empty}')
        self.assertEqual(codes,[])

    def test_recursive_graph_and_cycles_terminate(self):
        findings=self.vet('Node::struct{next:^Node}\nmain::proc(){n:=new(Node);n.next=n}')
        self.assertEqual([f['code'] for f in findings],['thor/leak-unknown'])
        findings=self.vet('go::proc(v:[]int,n:int){if n>0{go(v,n-1)}}\nmain::proc(){v~:=make([]int,2);go(v,2)}')
        self.assertEqual(findings,[])
        findings=self.vet('Node::struct{next:^Node}\ngo::proc(n:^Node){if n!=nil{go(n.next)}}\nmain::proc(){}')
        self.assertEqual(findings,[])
        findings=self.vet('Node::struct{next:^Node}\ngo::proc(n:^Node){if n!=nil{go(n.next)}}\nmain::proc(){n:=new(Node);n.next=n;free(n);go(n)}')
        self.assertIn('thor/lifetime',[f['code'] for f in findings])

    def test_known_panic_path_does_not_require_cleanup(self):
        codes=self.codes('main::proc(){v:=make([]int,2);_=v;panic("stop")}')
        self.assertEqual(codes,[])

    def test_lifetime_and_leak_checks_can_be_selected_separately(self):
        body='main::proc(){v:=make([]int,2);delete(v);_=v[0];lost:=make([]int,1);_=lost}'
        codes=self.codes(body,checks='lifetime')
        self.assertEqual(codes,['thor/lifetime'])
        codes=self.codes(body,checks='leak')
        self.assertEqual(codes,['thor/leak-unknown'])

    def test_unknown_suppression_follows_allocation_through_alias(self):
        body='work::proc(callback:proc(v:[]int)){@(vet_ignore="lifetime_unknown") v~:=make([]int,2);alias:=v[:];callback(alias)}\nmain::proc(){}'
        codes=self.codes(body)
        self.assertEqual(codes,[])
        codes=self.codes('main::proc(){@(vet_ignore="leak_unknown") v:=make([]int,2);_=v}')
        self.assertEqual(codes,[])

    def test_suppression_does_not_hide_definite_lifetime_or_leaks(self):
        findings=self.vet('main::proc(){@(vet_ignore="lifetime_unknown,leak_unknown") v:=make([]int,2);delete(v);_=v[0]}')
        self.assertEqual([f['code'] for f in findings],['thor/lifetime'])
        findings=self.vet('main::proc(){@(vet_ignore="leak_unknown") v:=make([]int,2,allocator=runtime.heap_allocator());_=v}',prefix='import "base:runtime"')
        self.assertEqual([f['code'] for f in findings],['thor/leak'])

    def test_suppression_is_local_and_does_not_invent_a_callee_contract(self):
        findings=self.vet('work::proc(callback:proc(v:[]int)){@(vet_ignore="lifetime_unknown") first~:=make([]int,2);second~:=make([]int,2);callback(first);callback(second)}\nmain::proc(){}')
        self.assertEqual([f['code'] for f in findings],['thor/lifetime-unknown'])
        self.assertEqual(len(findings[0]['related']),1)

    def test_invalid_suppressions_still_compile_errors(self):
        for name in ['lifetime','leak','lifetime_unknown,','typo']:
            self.source.write_text('package main\n@(vet_ignore="'+name+'")\nVALUE::7\nmain::proc(){}\n', newline='')
            result=self.invoke('rust','check',flags=['-thread-count:1'])
            self.assertEqual(result.returncode,1,result.stderr)
            self.assertIn('Unknown vet check',result.stderr)

    def test_build_vet_separates_unverified_and_never_applies_ownership_edits(self):
        findings=self.vet('main::proc(){v:=make([]int,2);_=v}',command='build')
        self.assertEqual(findings[0]['type'],'info')
        strict=self.vet('main::proc(){v:=make([]int,2);_=v}',command='build',flags=['--unverified=error'],status=1)
        self.assertEqual(strict[0]['type'],'error')
        before=self.source.read_bytes()
        findings=self.vet('main::proc(){v:=make([]int,2);_=v}',apply=True)
        self.assertEqual(self.source.read_bytes(),before)
        self.assertNotIn('fix',findings[0])

    def test_ordinary_checked_and_unchecked_builds_do_not_run_vet(self):
        # Never run this unsafe fixture; only verify ordinary builds stay opt-in.
        self.source.write_text('package main\nmain::proc(){v:=make([]int,2);delete(v);_=v[0]}\n', newline='')
        for flags in [[],base.UNCHECKED]:
            result=self.invoke('rust','build',flags=['--comptime=required','-thread-count:1','-json-errors',*flags])
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertNotIn('thor/lifetime',result.stderr)

    def test_cross_file_alias_helper_and_named_results(self):
        findings=self.vet('bad::proc()->(result:[]int){v~:=make([]int,2);alias::stash;alias(&result,v);return}\nmain::proc(){}',files={'helpers.thor':'package main\nstash::proc(out:^[]int,v:[]int){out^=v}\n'})
        self.assertEqual([f['code'] for f in findings],['thor/lifetime'])


    def test_header_queries_and_unrelated_elements_do_not_read_freed_data(self):
        codes=self.codes('main::proc(){a:=make([]int,2);b:=make([]int,2);outer~:=make([][]int,2);outer[0]=a;outer[1]=b;delete(a);_=len(a);_=outer[1][0];delete(b)}')
        self.assertEqual(codes,[])

    def test_temporary_struct_projections_keep_nested_references(self):
        codes=self.codes('Box::struct{v:[]int}\nget::proc(v:[]int)->Box{return Box{v}}\nmain::proc(){v:=make([]int,2);alias:=get(v).v;delete(v);_=alias[0]}')
        self.assertIn('thor/lifetime',codes)

    def test_readonly_rodata_and_native_static_storage_survive_scopes(self):
        codes=self.codes('get::proc()->[]int{v:>[]int{1,2};return v}\nmain::proc(){_=get()[0]}')
        self.assertEqual(codes,[])
        codes=self.codes('get::proc()->^int{@(static) v:int;return &v}\nmain::proc(){_=get()^}')
        self.assertEqual(codes,[])

    def test_fstrings_temporary_cleanup_and_named_owners(self):
        codes=self.codes('main::proc(){n:=7;fmt.println(f"number {n}");text~:=f"number {n}";fmt.println(text)}',prefix='import "core:fmt"')
        self.assertEqual(codes,[])
        findings=self.vet('main::proc(){n:=7;text:=f"number {n}";_=text}')
        self.assertEqual([f['code'] for f in findings],['thor/leak-unknown'])
        codes=self.codes('saved:string\nretain::proc(text:string){saved=text}\nmain::proc(){n:=7;retain(f"number {n}") }')
        self.assertIn('thor/lifetime',codes)
        codes=self.codes('saved:string\nretain::proc(text:string){saved=text}\nmain::proc(){n:=7;retain(f!"number {n}");delete(saved);saved=""}')
        self.assertEqual(codes,[])

    def test_comprehensions_keep_element_reference_edges(self):
        codes=self.codes('main::proc(){v~:=[i*i for i in 0..<4];_=v[0]}')
        self.assertEqual(codes,[])
        codes=self.codes('bad::proc()->[][]int{v:=make([]int,2);defer delete(v);out:=[v for i in 0..<2];return out}\nmain::proc(){}')
        self.assertIn('thor/lifetime',codes)

    def test_pure_scratch_and_return_promotion_are_managed(self):
        codes=self.codes('get::fn()->[]int{v:=make([]int,2);v[0]=7;unused:=make([]int,1);_=unused;return v}\nmain::proc(){v~:=get();_=v[0]}')
        self.assertEqual(codes,[])
        codes=self.codes('get::fn()->int{v:=make([]int,2);v[0]=7;return v[0]}\nmain::proc(){_=get()}')
        self.assertEqual(codes,[])

    def test_partial_return_ownership_is_unverified(self):
        findings=self.vet('get::proc(b:bool)->[]int{v:=make([]int,2);if b{return v};return nil}\nmain::proc(){}')
        self.assertEqual([f['code'] for f in findings],['thor/leak-unknown'])

    def test_explicit_arena_teardown_discharges_region_allocations(self):
        codes=self.codes('main::proc(){arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;defer virtual.arena_destroy(&arena);context.allocator=virtual.arena_allocator(&arena);v:=make([]int,2);_=v[0]}',prefix='import "core:mem/virtual"')
        self.assertEqual(codes,[])

    def test_switch_branch_cleanup_and_fallthrough(self):
        codes=self.codes('work::proc(i:int){v:=make([]int,2);switch i{case 0:delete(v);case:delete(v)}}\nmain::proc(){}')
        self.assertEqual(codes,[])
        codes=self.codes('work::proc(i:int){v:=make([]int,2);switch i{case 0:delete(v);case:};_=v[0]}\nmain::proc(){}')
        self.assertIn('thor/lifetime-unknown',codes)

    def test_using_projections_and_shadowing_have_distinct_storage(self):
        codes=self.codes('Box::struct{v:[]int}\nmain::proc(){first:=make([]int,2);box:=Box{first};using box;delete(first);_=v[0]}')
        self.assertIn('thor/lifetime',codes)
        codes=self.codes('main::proc(){v~:=make([]int,2);{v~:=make([]int,2);_=v[0]};_=v[0]}')
        self.assertEqual(codes,[])

    def test_polymorphic_callbacks_and_parameter_packs(self):
        codes=self.codes('read::proc(v:$T)->int{return v[0]}\nmain::proc(){v~:=make([]int,2);_=read(v)}')
        self.assertEqual(codes,[])

    def test_append_with_outstanding_view_is_explicitly_unverified(self):
        codes=self.codes('main::proc(){v:=make([dynamic]int,1);view:=v[:];append(&v,2);_=view[0];delete(v)}')
        self.assertIn('thor/lifetime-unknown',codes)

    def test_unknown_callee_contract_tag_applies_only_to_its_parameters(self):
        findings=self.vet('@(vet_ignore="lifetime_unknown")\nwork::proc(callback:proc(v:[]int),v:[]int){callback(v);local~:=make([]int,2);callback(local)}\nmain::proc(){}')
        self.assertEqual([f['code'] for f in findings],['thor/lifetime-unknown'])

    def test_ignored_rules_never_hide_compiler_errors(self):
        self.source.write_text('package main\nmain::proc(){@(vet_ignore="lifetime_unknown,leak_unknown") v:=missing;_=v}\n', newline='')
        result=self.invoke('rust','check',flags=['-thread-count:1'])
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn('Undeclared name: missing',result.stderr)


    def test_native_deferred_result_cleanup_and_escape(self):
        prefix='cleanup::proc(v:[]int){delete(v)}\n@(deferred_out=cleanup)\ncreate::proc()->[]int{return make([]int,2)}\n'
        codes=self.codes(prefix+'main::proc(){v:=create();_=v[0]}')
        self.assertEqual(codes,[])
        codes=self.codes(prefix+'bad::proc()->[]int{v:=create();return v}\nmain::proc(){}')
        self.assertIn('thor/lifetime',codes)

    def test_basic_scoped_arena_has_implicit_region_cleanup(self):
        codes=self.codes('main::proc(){context.allocator=basic.scoped_arena();v:=make([]int,2);_=v[0]}',prefix='import "thor:basic"')
        self.assertEqual(codes,[])

    def test_factory_result_suppression_is_owned_by_the_receiving_declaration(self):
        codes=self.codes('create::proc()->[]int{internal:=make([]int,2);return internal}\nmain::proc(){@(vet_ignore="leak_unknown") v:=create();_=v}')
        self.assertEqual(codes,[])

    def test_assertion_elision_does_not_hide_ordinary_cleanup_obligations(self):
        body='main::proc(){v:=make([]int,2,allocator=runtime.heap_allocator());_=v;assert(false)}'
        codes=self.codes(body,prefix='import "base:runtime"')
        self.assertEqual(codes,[])
        codes=self.codes(body,prefix='import "base:runtime"',flags=base.UNCHECKED,command='build')
        self.assertEqual(codes,['thor/leak'])

    def test_lifetime_verdicts_are_the_same_in_unchecked_release(self):
        body='main::proc(){v:=make([]int,2);delete(v);_=v[0]}'
        checked=self.codes(body)
        unchecked=self.codes(body,flags=base.UNCHECKED,command='build')
        self.assertEqual(checked,unchecked)
        self.assertEqual(unchecked,['thor/lifetime'])

    def test_statically_inactive_when_code_is_not_analyzed(self):
        codes=self.codes('main::proc(){when false{v:=make([]int,2);delete(v);_=v[0]}else{v~:=make([]int,2);_=v[0]}}')
        self.assertEqual(codes,[])

    def test_ownership_checks_never_offer_a_source_fix(self):
        findings=self.vet('main::proc(){a:=make([]int,2);delete(a);_=a[0];b:=make([]int,2);_=b}',apply=True)
        self.assertTrue(all('fix' not in f for f in findings))
        self.assertNotIn('~',self.source.read_text())
        self.assertNotIn('vet_ignore',self.source.read_text())

    def test_analysis_budget_is_an_explicit_unknown(self):
        calls=';'.join('read()' for _ in range(280))
        findings=self.vet('read::proc(){p~:=new(int,allocator=context.allocator);_=p^}\nmain::proc(){'+calls+'}')
        self.assertTrue(any(f['certainty']=='unverified' and 'budget' in str(f['msgs']) for f in findings),findings)
        self.assertFalse(any(f['certainty']=='definite' for f in findings))

    def test_allocator_and_callback_expressions_are_evaluated_once(self):
        # Static graph effects must not execute the allocator argument twice.
        codes=self.codes('clean::proc(v:[]int)->mem.Allocator{delete(v);return context.allocator}\nmain::proc(){v:=make([]int,2);out~:=make([]int,2,allocator=clean(v));_=out[0]}',prefix='import "core:mem"')
        self.assertEqual(codes,[])

    def test_fixed_capacity_append_and_return_do_not_create_heap_obligations(self):
        codes=self.codes('Hand::[dynamic;2]int\nget::proc()->(hand:Hand){append(&hand,7);return}\nmain::proc(){hand:=get();_=hand[0]}')
        self.assertEqual(codes,[])

    def test_homonymous_clone_and_reader_still_follow_their_bodies(self):
        codes=self.codes('clone::proc(v:[]int)->[]int{return v}\nmain::proc(){v:=make([]int,2);alias:=clone(v);delete(v);_=alias[0]}')
        self.assertIn('thor/lifetime',codes)

    def test_checked_struct_conversions_preserve_reference_fields(self):
        codes=self.codes('Box::struct{v:[]int}\nmain::proc(){v:=make([]int,2);box:=Box{v};copy:=Box(box);delete(v);_=copy.v[0]}')
        self.assertIn('thor/lifetime',codes)


    def test_nested_allocator_override_restores_enclosing_allocator(self):
        codes=self.codes('main::proc(){{context.allocator=basic.scoped_arena();v:=make([]int,2);_=v[0]};v~:=make([]int,2);_=v[0]}',prefix='import "thor:basic"')
        self.assertEqual(codes,[])

    def test_printing_scalar_pointer_addresses_does_not_read_their_targets(self):
        codes=self.codes('main::proc(){p:=new(int);mem.free(p);fmt.println(p)}',prefix='import "core:mem"\nimport "core:fmt"')
        self.assertEqual(codes,[])

    def test_formatting_slice_content_after_free_is_reported(self):
        codes=self.codes('main::proc(){v:=make([]int,2);delete(v);text~:=f"values {v}";_=text}')
        self.assertIn('thor/lifetime',codes)

    def test_branch_selected_pointer_is_not_called_a_definite_access(self):
        codes=self.codes('work::proc(flag:bool){a:=make([]int,2);b~:=make([]int,2);delete(a);view:=flag?a:b;_=view[0]}\nmain::proc(){}')
        self.assertIn('thor/lifetime-unknown',codes)
        self.assertNotIn('thor/lifetime',codes)


    def test_index_side_effects_are_evaluated_once_and_increments_read_storage(self):
        codes=self.codes('index::proc(v:[]int)->int{delete(v);return 0}\nmain::proc(){v:=make([]int,2);a:=[2]int{};a[index(v)]=7}')
        self.assertEqual(codes,[])
        codes=self.codes('main::proc(){v:=make([]int,2);delete(v);v[0]+=1}')
        self.assertIn('thor/lifetime',codes)

    def test_map_value_iteration_preserves_nested_references(self):
        codes=self.codes('main::proc(){v:=make([]int,2);m~:=make(map[int][]int);m[1]=v;delete(v);for _,value in m{_=value[0]}}')
        self.assertIn('thor/lifetime',codes)

    def test_optional_early_return_reports_unprotected_cleanup_paths(self):
        body='try::proc()->(int,bool){return 0,false}\nwork::proc()->(result:int,ok:bool){v:=make([]int,2);n:=try() or_return;delete(v);return n,true}\nmain::proc(){}'
        codes=self.codes(body)
        self.assertIn('thor/leak-unknown',codes)
        protected=body.replace('n:=try()', 'defer delete(v);n:=try()').replace(';delete(v);return',';return')
        codes=self.codes(protected)
        self.assertEqual(codes,[])


    def test_subslice_projection_and_clone_exclude_unselected_elements(self):
        setup='a:=make([]int,1);b:=make([]int,1);outer~:=make([][]int,2);outer[0]=a;outer[1]=b;delete(a);'
        for use in ['view:=outer[1:];_=view[0][0]',
                    'view:=outer[1:2];j:=0;_=view[j][0]',
                    'copy~:=slice.clone(outer[1:]);_=copy[0][0]']:
            with self.subTest(use=use):
                codes=self.codes('main::proc(){'+setup+use+';delete(b)}',prefix='import "core:slice"')
                self.assertEqual(codes,[])
        codes=self.codes('main::proc(){a~:=make([]int,1);b:=make([]int,1);outer~:=make([][]int,2);outer[0]=a;outer[1]=b;view:=outer[1:];delete(b);_=view[0][0]}')
        self.assertIn('thor/lifetime',codes)


    def test_acknowledged_opaque_call_does_not_hide_later_known_use_after_free(self):
        codes=self.codes('work::proc(callback:proc(v:[]int)){@(vet_ignore="lifetime_unknown") v:=make([]int,2);callback(v);delete(v);_=v[0]}\nmain::proc(){}')
        self.assertEqual(codes,['thor/lifetime'])


    def test_returning_only_an_interior_view_does_not_prove_allocation_ownership(self):
        codes=self.codes('get::proc()->[]int{v:=make([]int,2);return v[1:]}\nmain::proc(){}')
        self.assertEqual(codes,['thor/leak-unknown'])

    def test_scoped_set_cleanup_uses_its_backing_map(self):
        codes=self.codes('main::proc(){v~:=set[int]{1,2,3};_=len(v)}')
        self.assertEqual(codes,[])


    def test_apply_updates_primary_and_related_origin_offsets(self):
        findings=self.vet('main::proc(){v:=make([]int,2);delete(v);_=v[0]}',prefix='import "core:thread"',checks='unused,lifetime,leak',apply=True)
        text=self.source.read_bytes()
        fault=next(f for f in findings if f.get('code')=='thor/lifetime')
        pos=fault['pos']
        self.assertEqual(text[pos['offset']:pos['end_offset']],b'v[0]')
        origin=fault['related'][0]['pos']
        self.assertEqual(text[origin['offset']:origin['end_offset']],b'v')
        self.assertNotIn(b'import',text)


    def test_soa_index_rows_preserve_reference_fields(self):
        codes=self.codes('main::proc(){a:=make([]int,1);columns:=[][]int{a};people:=soa_zip(values=columns);delete(a);_=people[0].values[0]}')
        self.assertIn('thor/lifetime',codes)
        codes=self.codes('main::proc(){a~:=make([]int,1);columns:=[][]int{a};people:=soa_zip(values=columns);_=people[0].values[0]}')
        self.assertEqual(codes,[])


    def test_clear_drops_element_ownership_and_scalar_pop_does_not_allocate(self):
        codes=self.codes('get::proc()->[dynamic][]int{inner:=make([]int,1);out:=make([dynamic][]int);append(&out,inner);clear(&out);return out}\nmain::proc(){}')
        self.assertIn('thor/leak-unknown',codes)
        codes=self.codes('main::proc(){v~:=make([dynamic]int);append(&v,7);_=pop(&v)}')
        self.assertEqual(codes,[])


    def test_imported_global_symbol_indices_use_the_defining_source(self):
        codes=self.codes('main::proc(){_=abs(-1)}',prefix='import "core:math" / abs')
        self.assertEqual(codes,[])
        codes=self.codes('main::proc(){v~:=make([]int,2);print(v)}',prefix='import * "thor:basic"')
        self.assertEqual(codes,[])


    def test_deep_abstract_output_storage_keeps_definite_escape(self):
        codes=self.codes('Row::struct{next:^Row,values:[]int}\nwork::proc(out:^Row){values:=make([]int,1);defer delete(values);out.next.next.next.next.values=values}\nmain::proc(){}')
        self.assertIn('thor/lifetime-unknown',codes)
        self.assertIn('thor/lifetime',codes)



if __name__=='__main__':unittest.main()

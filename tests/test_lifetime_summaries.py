"""Cached proofs must be independent of caller storage identity and lifetime."""
import json
import os
import unittest
from tests import test_lifetime_vet as base


class LifetimeSummaryTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp=base.LifetimeVetTests.setUp
    invoke=base.LifetimeVetTests.invoke
    vet=base.LifetimeVetTests.vet
    codes=base.LifetimeVetTests.codes

    def problem(self,codes):
        self.assertTrue(any(c in {'thor/lifetime','thor/lifetime-unknown'} for c in codes),codes)

    def profiled(self,body):
        self.source.write_text('package main\n'+body+'\n')
        path=self.root/'profile.json'
        result=self.invoke('rust','vet',flags=['--vet=lifetime,leak','--comptime=required','-json-errors','-thread-count:1'],env={**os.environ,'THOR_PROFILE':str(path)})
        self.assertEqual(result.returncode,0,result.stderr)
        packet=json.loads(result.stderr) if result.stderr.strip() else {'errors':[]}
        return packet['errors'],json.loads(path.read_text())

    def test_repeated_scalar_helpers_reuse_the_verified_summary(self):
        body='twice::proc(n:int)->int{return n*2}\nmain::proc(){v:=make([]int,2,allocator=context.allocator);defer delete(v);n:=0;'+''.join(f'n+=twice({i});' for i in range(100))+';_=n}'
        findings,profile=self.profiled(body)
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.summary_hits',0),90,profile['counters'])
        self.assertGreater(profile['counters'].get('lifetime.summary_applied',0),90,profile['counters'])

    def test_result_fact_discovery_does_not_visit_unused_runtime_helpers(self):
        findings,profile=self.profiled('import basic "thor:basic"\nmain::proc(){basic.print(7)}')
        self.assertEqual(findings,[])
        self.assertEqual(profile['counters'].get('lifetime.result_fact_calls'),1,profile['counters'])

    def test_scalar_recursion_component_is_verified_as_a_whole(self):
        codes=self.codes('a::proc(n:int)->int{if n<=0{return 0};return b(n-1)}\nb::proc(n:int)->int{if n<=0{return 0};return a(n-1)}\nmain::proc(){v:=make([]int,2);defer delete(v);_=a(7)}')
        self.assertEqual(codes,[])

    def test_recursive_reader_masks_propagate_across_the_component(self):
        codes=self.codes('a::proc(v:[]int,n:int)->int{if n<=0{return 0};return b(v,n-1)}\nb::proc(v:[]int,n:int)->int{if n<=0{return v[0]};return a(v,n-1)}\nmain::proc(){v:=make([]int,2);_=a(v,2);delete(v);_=a(v,2)}')
        self.problem(codes)

    def test_failed_recursive_component_is_not_partially_published(self):
        codes=self.codes('a::proc(v:[]int,n:int)->int{if n<=0{return b(v,n)};return b(v,n-1)}\nb::proc(v:[]int,n:int)->int{if n<=0{delete(v);return 0};return a(v,n-1)}\nmain::proc(){v:=make([]int,2);_=a(v,0);_=v[0]}')
        self.problem(codes)

    def test_cached_readers_recheck_each_callers_live_storage(self):
        codes=self.codes('first::proc(v:[]int)->int{return v[0]}\nforward::proc(v:[]int)->int{return first(v)}\nmain::proc(){a:=make([]int,2);b:=make([]int,2);_=forward(a);delete(a);_=forward(b);delete(b);_=forward(a)}')
        self.problem(codes)

    def test_header_only_calls_do_not_read_freed_backing(self):
        codes=self.codes('count::proc(v:[]int)->int{return len(v)}\nforward::proc(v:[]int)->int{return count(v)}\nmain::proc(){v:=make([]int,2);delete(v);_=forward(v)}')
        self.assertEqual(codes,[])

    def test_sliced_reader_and_forwarding_keep_source_lifetimes(self):
        for call in ['sum_values(v)','forward(v)']:
            with self.subTest(call=call):
                codes=self.codes('sum_values::fn(v:[]int)->int{sum:=0;for n in v[1:]{sum+=n};return sum}\nforward::proc(v:[]int)->int{return sum_values(v[:])}\nmain::proc(){v:=make([]int,3);_='+call+';delete(v);_='+call+'}')
                self.problem(codes)

    def test_header_only_sliced_forwarding_remains_non_reading(self):
        codes=self.codes('count::proc(v:[]int)->int{return len(v)}\nforward::proc(v:[]int)->int{return count(v[1:])}\nmain::proc(){v:=make([]int,3);delete(v);_=forward(v)}')
        self.assertEqual(codes,[])

    def test_pointer_readers_recheck_freed_pointees(self):
        self.problem(self.codes('read::proc(p:^int)->int{return p^}\nmain::proc(){p:=new(int);_=read(p);free(p);_=read(p)}'))

    def test_named_arguments_preserve_reader_parameter_order(self):
        codes=self.codes('read::proc(n:int,v:[]int)->int{return n+v[0]}\nmain::proc(){v:=make([]int,2);delete(v);_=read(v=v,n=2)}')
        self.problem(codes)

    def test_parameter_forwarding_through_reordered_named_arguments(self):
        codes=self.codes('read::proc(a,b:[]int)->int{return b[0]}\nforward::proc(first,second:[]int)->int{return read(b=first,a=second)}\nmain::proc(){a:=make([]int,2);b:=make([]int,2);defer delete(b);delete(a);_=forward(a,b)}')
        self.problem(codes)

    def test_global_reference_reads_are_not_graph_neutral(self):
        self.problem(self.codes('saved:[]int\nread_saved::proc()->int{return saved[0]}\nmain::proc(){saved=make([]int,2);_=read_saved();delete(saved);_=read_saved()}'))

    def test_global_retention_is_not_mistaken_for_a_reader(self):
        self.problem(self.codes('saved:^int\nstash::proc(p:^int){saved=p}\nmain::proc(){p~:=new(int);stash(p)}'))

    def test_argument_evaluation_cannot_hide_allocations(self):
        codes=self.codes('allocate::proc()->int{v:=make([]int,2,allocator=runtime.heap_allocator());_=v;return 1}\nuse::proc(n:int)->int{return n}\nmain::proc(){_=use(allocate())}',prefix='import "base:runtime"')
        self.assertIn('thor/leak',codes)

    def test_context_allocator_defaults_are_not_graph_neutral(self):
        codes=self.codes('use::proc(allocator:=context.allocator)->int{v:=make([]int,2,allocator=allocator);_=v;return 1}\nmain::proc(){_=use()}')
        self.assertIn('thor/leak-unknown',codes)

    def test_scalar_defaults_and_named_results_remain_eligible(self):
        codes=self.codes('add::proc(n:int,extra:=1)->(result:int){result=n+extra;return}\nmain::proc(){v~:=make([]int,2);_=add(3)}')
        self.assertEqual(codes,[])

    def test_runtime_callback_targets_are_not_frozen_in_a_summary(self):
        codes=self.codes('saved:^int\ncallback:proc()->int\nrelease::proc()->int{free(saved);return 0}\ninvoke::proc()->int{return callback()}\nmain::proc(){saved=new(int);callback=release;_=invoke();_=saved^}')
        self.problem(codes)

    def test_initialized_mutable_callback_is_not_a_constant_target(self):
        codes=self.codes('saved:^int\nsafe::proc()->int{return 0}\ncallback:=safe\nrelease::proc()->int{free(saved);return 0}\ninvoke::proc()->int{return callback()}\nmain::proc(){saved=new(int);_=invoke();callback=release;_=invoke();_=saved^}')
        self.problem(codes)

    def test_multiple_runtime_targets_without_reference_arguments_still_require_review(self):
        codes=self.codes('saved:^int\nchoose:bool\nsafe::proc()->int{return 0}\nrelease::proc()->int{free(saved);return 0}\nmain::proc(){saved=new(int);callback:proc()->int;if choose{callback=release}else{callback=safe};_=callback();_=saved^}')
        self.assertIn('thor/lifetime-unknown',codes)

    def test_opaque_callback_contracts_stay_unverified(self):
        codes=self.codes('work::proc(f:proc(v:[]int)){v~:=make([]int,2);f(v)}\nmain::proc(){}')
        self.assertIn('thor/lifetime-unknown',codes)

    def test_generic_instantiations_do_not_share_a_reference_verdict(self):
        for calls in ['_=identity(3);copy:=identity(v)','copy:=identity(v);_=identity(3)']:
            with self.subTest(calls=calls):
                codes=self.codes('identity::proc(v:$T)->T{return v}\nmain::proc(){v:=make([]int,2);'+calls+';delete(v);_=copy[0]}')
                self.problem(codes)

    def test_generic_reader_is_checked_at_concrete_call_sites(self):
        codes=self.codes('read::proc(v:$T)->int{return v[0]}\nmain::proc(){v:=make([]int,2);_=read(v);delete(v);_=read(v)}')
        self.problem(codes)

    def test_integer_provenance_casts_cannot_be_erased_by_value_summaries(self):
        codes=self.codes('identity::proc(n:uintptr)->uintptr{return n}\nmain::proc(){v:=make([]int,2);bits:=uintptr(raw_data(v));copy:=identity(bits);delete(v);_=((^int)(copy))^}')
        self.problem(codes)

    def test_reader_summary_does_not_erase_addresses_stored_in_integer_elements(self):
        codes=self.codes('first::proc(v:[]uintptr)->uintptr{return v[0]}\nmain::proc(){p:=new(int);values:=[1]uintptr{uintptr(p)};bits:=first(values[:]);free(p);_=((^int)(bits))^}')
        self.assertIn('thor/lifetime',codes)

    def test_imported_mutable_integer_provenance_is_not_graph_neutral(self):
        package=self.app/'state'
        package.mkdir()
        (package/'state.thor').write_text('package state\nbits:uintptr\n')
        codes=self.codes('read::proc()->uintptr{return state.bits}\nmain::proc(){v:=make([]int,2);state.bits=uintptr(raw_data(v));bits:=read();delete(v);_=((^int)(bits))^}',prefix='import "state"')
        self.problem(codes)

    def test_direct_pointer_field_reader_rechecks_the_pointee(self):
        self.problem(self.codes('V::struct{x,y:int}\nread::proc(p:^V)->int{return p.x+p.y}\nmain::proc(){p:=new(V);_=read(p);free(p);_=read(p)}'))

    def test_borrowed_outputs_are_not_treated_as_value_results(self):
        self.problem(self.codes('view::proc(v:[]int)->[]int{return v}\nmain::proc(){v:=make([]int,2);alias:=view(v);delete(v);_=alias[0]}'))

    def test_deferred_retention_contract_is_not_skipped(self):
        codes=self.codes('saved:[]int\nretain::proc(v:[]int){saved=v}\n@(deferred_in=retain) use::proc(v:[]int){}\nmain::proc(){v~:=make([]int,2);use(v)}')
        self.problem(codes)

    def test_reference_state_mutation_is_not_a_neutral_summary(self):
        self.problem(self.codes('set::proc(out:^[]int,v:[]int){out^=v}\nmain::proc(){v:=make([]int,2);out:[]int;set(&out,v);delete(v);_=out[0]}'))

    def test_direct_reader_follows_using_projection_fields(self):
        codes=self.codes('V::struct{x,y:int}\nread::proc(v:^V)->int{using v;return x+y}\nmain::proc(){v:=new(V);_=read(v);free(v);_=read(v)}')
        self.problem(codes)

    def test_summary_work_is_bounded_and_does_not_hide_budget_boundaries(self):
        statements=''.join(f'n{i}:={i};_=n{i};' for i in range(2500))
        findings,profile=self.profiled('large::proc()->int{'+statements+';return 0}\nmain::proc(){v:=make([]int,2);defer delete(v);_=large()}')
        self.assertTrue(any('budget' in str(d['msgs']) for d in findings),findings)
        self.assertLessEqual(profile['counters'].get('lifetime.summary_nodes',0),16384)


if __name__=='__main__':unittest.main()

"""Proofs for common safe ownership idioms and matching unsafe counterexamples."""
import unittest
from tests import test_lifetime_vet as base


class LifetimeSafeModelTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp=base.LifetimeVetTests.setUp
    invoke=base.LifetimeVetTests.invoke
    vet=base.LifetimeVetTests.vet
    codes=base.LifetimeVetTests.codes

    def problem(self,codes):
        self.assertTrue(any(c in {'thor/lifetime','thor/lifetime-unknown'} for c in codes),codes)

    def test_fixed_buffer_builder_reads_writes_aliases_and_returns_views(self):
        codes=self.codes('main::proc(){buf:[64]byte;b:=strings.builder_from_bytes(buf[:]);strings.write_string(&b,"hello");text:=fmt.sbprintf(&b," %d",7);_=text[0];c:=strings.to_cstring(&b);_=len(c)}',prefix='import "core:strings"\nimport "core:fmt"')
        self.assertEqual(codes,[])

    def test_fixed_builder_named_arguments_and_aliases(self):
        codes=self.codes('main::proc(){buf:[64]byte;construct::strings.builder_from_slice;write::fmt.sbprintf;b:=construct(buf[:]);text:=write(buf=&b,fmt="%d",args=[]any{7});_=text[0]}',prefix='import "core:strings"\nimport "core:fmt"')
        self.assertEqual(codes,[])

    def test_builder_view_does_not_outlive_backing_array(self):
        for result in ['strings.to_string(b)','fmt.sbprintf(&b,"hello")']:
            with self.subTest(result=result):
                codes=self.codes('bad::proc()->string{buf:[64]byte;b:=strings.builder_from_bytes(buf[:]);return '+result+'}\nmain::proc(){}',prefix='import "core:strings"\nimport "core:fmt"')
                self.problem(codes)

    def test_builder_checks_released_backing_and_never_owns_it(self):
        for tail in ['delete(buf);_=strings.to_string(b)[0]','delete(strings.to_string(b));delete(buf)']:
            with self.subTest(tail=tail):
                codes=self.codes('main::proc(){buf:=make([]byte,64);b:=strings.builder_from_bytes(buf);strings.write_string(&b,"hello");'+tail+'}',prefix='import "core:strings"')
                self.problem(codes)

    def test_unknown_builder_implementation_is_not_certified(self):
        codes=self.codes('work::proc(b:^strings.Builder){_=strings.to_cstring(b)}\nmain::proc(){}',prefix='import "core:strings"')
        self.assertIn('thor/lifetime-unknown',codes)

    def test_user_names_do_not_receive_library_ownership_contracts(self):
        for body in [
            'sum::proc(v:[]int)->int{delete(v);return 0}\nmain::proc(){v:=make([]int,2);_=sum(v);_=v[0]}',
            'pmap::proc(v:[]int)->[]int{return v}\nmain::proc(){v:=make([]int,2);out:=pmap(v);delete(out);_=v[0]}',
            'builder_from_bytes::proc(v:[]byte)->string{delete(v);return "done"}\nmain::proc(){v:=make([]byte,2);_=builder_from_bytes(v);_=v[0]}',
        ]:
            with self.subTest(body=body):
                self.problem(self.codes(body))

    def test_sort_by_known_readers_preserves_element_references(self):
        codes=self.codes('Row::struct{key:int}\nless::proc(a,b:Row)->bool{return a.key<b.key}\nmain::proc(){v~:=make([]Row,2);compare::less;slice.sort_by(v,compare);_=v[0]}',prefix='import "core:slice"')
        self.assertEqual(codes,[])
        codes=self.codes('less::proc(a,b:[]int)->bool{return a[0]<b[0]}\nmain::proc(){v:=make([]int,2);rows:=[1][]int{v};slice.sort_by(rows[:],less);delete(v);_=rows[0][0]}',prefix='import "core:slice"')
        self.problem(codes)

    def test_sort_by_unknown_or_releasing_comparators_require_review(self):
        codes=self.codes('work::proc(v:[][]int,less:proc(a,b:[]int)->bool){slice.sort_by(v,less)}\nmain::proc(){}',prefix='import "core:slice"')
        self.assertIn('thor/lifetime-unknown',codes)
        codes=self.codes('less::proc(a,b:[]int)->bool{delete(a);return false}\nmain::proc(){v:=make([]int,2);rows:=[1][]int{v};slice.sort_by(rows[:],less);_=v[0]}',prefix='import "core:slice"')
        self.problem(codes)

    def test_iterators_borrow_text_and_do_not_allocate(self):
        for call in ['strings.fields_iterator(&remaining)','strings.split_iterator(&remaining," ")']:
            with self.subTest(call=call):
                codes=self.codes('main::proc(){remaining:="a b";for token in '+call+'{_=token[0]}}',prefix='import "core:strings"')
                self.assertEqual(codes,[])

    def test_iterator_tokens_keep_their_sources_lifetime(self):
        codes=self.codes('main::proc(){text:=strings.clone("a b");remaining:=text;token,ok:=strings.fields_iterator(&remaining);_=ok;delete(text);_=token[0]}',prefix='import "core:strings"')
        self.problem(codes)

    def test_iterator_consumption_of_freed_source_is_diagnosed(self):
        codes=self.codes('main::proc(){text:=strings.clone("a b");remaining:=text;delete(text);for token in strings.fields_iterator(&remaining){_=token[0]}}',prefix='import "core:strings"')
        self.problem(codes)

    def test_procedure_iterator_is_reinvoked_for_its_effects(self):
        codes=self.codes('next::proc(v:[]int)->(int,bool){delete(v);return 1,true}\nmain::proc(){v:=make([]int,2);for n in next(v){_=n}}')
        self.problem(codes)

    def test_scalar_helpers_with_unrelated_live_aliases_converge(self):
        for flavor in ['proc','fn']:
            with self.subTest(flavor=flavor):
                codes=self.codes('twice::'+flavor+'(x:int)->int{return 2*x}\nmain::proc(){v~:=make([]int,2);alias:>v;total:=0;for i in 0..<10{total+=twice(i)};_=total+alias[0]}')
                self.assertEqual(codes,[])

    def test_temporary_stack_addresses_survive_analysis_compaction(self):
        codes=self.codes('read::proc(p:^int,n:int)->int{return p^+n}\nclear_and_loop::proc(p:^^int)->int{p^=nil;for _ in 0..<3{};return 0}\nmain::proc(){p:^int;{local:=7;p=&local};_=read(p,clear_and_loop(&p))}')
        self.problem(codes)

    def test_array_views_survive_analysis_compaction(self):
        codes=self.codes('read::proc(v:[]int,n:int)->int{return v[0]+n}\nclear_and_loop::proc(p:^[]int)->int{p^=nil;for _ in 0..<3{};return 0}\nmain::proc(){v:[]int;{local:[2]int;v=local[:]};_=read(v,clear_and_loop(&v))}')
        self.problem(codes)

    def test_new_generation_does_not_revive_an_old_dangling_alias(self):
        codes=self.codes('main::proc(){old:[]int;for i in 0..<2{v:=make([]int,2);if i==0{old=v}else{_=old[0]};delete(v)}}')
        self.problem(codes)

    def test_by_reference_fixed_array_iteration_preserves_stored_edges(self):
        for slots in ['slots:[1][]int','slots:=make([][]int,1);defer delete(slots)']:
            with self.subTest(slots=slots):
                codes=self.codes('main::proc(){v:=make([]int,2);'+slots+';for &slot in slots{slot=v};delete(v);_=slots[0][0]}')
                self.problem(codes)

    def test_reference_range_read_and_address_are_element_places(self):
        codes=self.codes('main::proc(){v:=make([]int,2);slots:=[1][]int{v};for &slot in slots{p:=&slot;p^=nil};delete(v)}')
        self.assertEqual(codes,[])
        codes=self.codes('main::proc(){v:=make([]int,2);slots:=[1][]int{v};delete(v);for &slot in slots{_=slot[0]}}')
        self.problem(codes)

    def test_contextual_record_literals_preserve_nested_borrows(self):
        for container,read in [('rows:=[1]Row{{v}}','rows[0].data[0]'),
                               ('row:Row={v}','row.data[0]')]:
            with self.subTest(container=container):
                codes=self.codes('Row::struct{data:[]int}\nmain::proc(){v:=make([]int,2);'+container+';delete(v);_='+read+'}')
                self.problem(codes)

    def test_pop_append_transfers_nested_ownership_without_inventing_leaks(self):
        codes=self.codes('Row::struct{points:[]int}\nmain::proc(){a:[dynamic]Row;append(&a,Row{make([]int,2)});b:[dynamic]Row;defer delete(a);defer delete(b);removed:=pop(&a);append(&b,removed);delete(removed.points)}')
        self.assertEqual(codes,[])

    def test_discarded_popped_reference_still_leaks(self):
        codes=self.codes('main::proc(){v:=make([]int,2,allocator=runtime.heap_allocator());a:[dynamic][]int;append(&a,v);defer delete(a);_=pop(&a)}',prefix='import "base:runtime"')
        self.assertIn('thor/leak',codes)

    def test_popped_reference_still_tracks_released_elements(self):
        codes=self.codes('main::proc(){v:=make([]int,2);a:[dynamic][]int;append(&a,v);defer delete(a);removed:=pop(&a);delete(v);_=removed[0]}')
        self.problem(codes)

    def test_rune_conversion_is_owned_and_uses_its_selected_allocator(self):
        codes=self.codes('main::proc(){runes:=[2]rune{\'a\',\'b\'};for _ in 0..<2{text:=utf8.runes_to_string(runes[:]);defer delete(text);_=text[0]}}',prefix='import "core:unicode/utf8"')
        self.assertEqual(codes,[])

    def test_budget_failure_does_not_invent_missed_deferred_cleanup(self):
        body=''.join(f'x{i}:={i};_=x{i};' for i in range(4500))
        findings=self.vet('main::proc(){v:=make([]int,2);defer delete(v);'+body+'}')
        self.assertTrue(any('budget' in str(f['msgs']) for f in findings),findings)
        self.assertFalse(any('not visibly released' in str(f['msgs']) for f in findings),findings)

    def test_pmap_owned_scalar_results_and_sum_are_readers(self):
        codes=self.codes('twice::proc(x:$T)->T{return 2*x}\nmain::proc(){jobs:=make([]int,2);defer delete(jobs);out:=basic.pmap(jobs,twice);defer delete(out);_=f64(basic.sum(out))}',prefix='import basic "thor:basic"')
        self.assertEqual(codes,[])

    def test_pmap_result_cleanup_does_not_free_its_jobs(self):
        codes=self.codes('same::proc(x:int)->int{return x}\nmain::proc(){jobs:=make([]int,2);out:=basic.pmap(jobs,same);delete(out);_=jobs[0];delete(jobs)}',prefix='import basic "thor:basic"')
        self.assertEqual(codes,[])

    def test_pmap_results_and_jobs_still_need_cleanup(self):
        codes=self.codes('same::proc(x:int)->int{return x}\nmain::proc(){jobs:=make([]int,2);out:=basic.pmap(jobs,same);_=out}',prefix='import basic "thor:basic"')
        self.assertIn('thor/leak-unknown',codes)

    def test_pmap_result_elements_keep_borrowed_aliases(self):
        codes=self.codes('same::proc(x:[]int)->[]int{return x}\nmain::proc(){v:=make([]int,2);jobs:=[1][]int{v};out:=basic.pmap(jobs[:],same);defer delete(out);delete(v);_=out[0][0]}',prefix='import basic "thor:basic"')
        self.problem(codes)

    def test_dependent_type_cache_keeps_generic_instantiations_separate(self):
        for order in [0,1]:
            with self.subTest(order=order):
                calls=['scalars:=basic.pmap(numbers[:],same);defer delete(scalars)',
                       'references:=basic.pmap(borrows[:],same);defer delete(references)']
                if order: calls.reverse()
                codes=self.codes('same::proc(x:$T)->T{return x}\nmain::proc(){v:=make([]int,2);numbers:=[1]int{1};borrows:=[1][]int{v};'+';'.join(calls)+';delete(v);_=references[0][0]}',prefix='import basic "thor:basic"')
                self.problem(codes)

    def test_pmap_multi_results_bundles_and_result_override(self):
        for results,tail in [('(value:[]int,count:int)','out[0].value[0]'),('([]int,int)','out[0].v0[0]')]:
            for override in ['',',result_type=Row']:
                with self.subTest(results=results,override=override):
                    read='out[0].data[0]' if override else tail
                    codes=self.codes('Job::struct{a:[]int,n:int}\nRow::struct{data:[]int,count:int}\nborrow::proc(a:[]int,n:int)->'+results+'{return a,n}\nmain::proc(){v:=make([]int,2);jobs:=[1]Job{{v,1}};out:=basic.pmap(jobs[:],borrow'+override+');defer delete(out);delete(v);_='+read+'}',prefix='import basic "thor:basic"')
                    self.problem(codes)

    def test_pmap_single_record_input_alias_and_default_parameter(self):
        for callback in ['add::proc(job:Job)->int{return job.n+2}','add::proc(n:int,extra:=2)->int{return n+extra}']:
            with self.subTest(callback=callback):
                codes=self.codes('Job::struct{n:int}\n'+callback+'\nmain::proc(){jobs:=[1]Job{{3}};callback::add;map_jobs::basic.pmap;out:=map_jobs(jobs[:],callback);defer delete(out);_=basic.sum(out)}',prefix='import basic "thor:basic"')
                self.assertEqual(codes,[])

    def test_pmap_actions_zero_inputs_and_callback_leaks(self):
        codes=self.codes('act::proc(){}\nmain::proc(){jobs:=[1]struct{}{{}};basic.pmap(jobs[:],act)}',prefix='import basic "thor:basic"')
        self.assertEqual(codes,[])
        codes=self.codes('act::proc(){v:=make([]int,2,allocator=runtime.heap_allocator());_=v}\nmain::proc(){jobs:=[1]struct{}{{}};basic.pmap(jobs[:],act)}',prefix='import basic "thor:basic"\nimport "base:runtime"')
        self.assertTrue(any(c in {'thor/leak','thor/leak-unknown'} for c in codes),codes)

    def test_opaque_pmap_callbacks_are_not_certified(self):
        codes=self.codes('work::proc(callback:proc(x:int)->int){jobs:=[1]int{1};out:=basic.pmap(jobs[:],callback);defer delete(out)}\nmain::proc(){}',prefix='import basic "thor:basic"')
        self.assertIn('thor/leak-unknown',codes)

    def test_pmap_shared_mutation_requires_review(self):
        codes=self.codes('write::proc(p:^int)->int{p^=7;return 1}\nmain::proc(){n:=0;jobs:=[1]^int{&n};out:=basic.pmap(jobs[:],write);defer delete(out)}',prefix='import basic "thor:basic"')
        # Scalar changes cannot affect provenance, but reference changes can.
        self.assertEqual(codes,[])
        codes=self.codes('clear_value::proc(p:^[]int){p^=nil}\nmain::proc(){v:=make([]int,2);defer delete(v);jobs:=[1]^[]int{&v};basic.pmap(jobs[:],clear_value)}',prefix='import basic "thor:basic"')
        self.assertIn('thor/lifetime-unknown',codes)

    def test_pmap_outer_cleanup_leaves_nested_output_obligations(self):
        codes=self.codes('allocate::proc(n:int)->[]int{return make([]int,n,allocator=runtime.heap_allocator())}\nmain::proc(){jobs:=[1]int{2};out:=basic.pmap(jobs[:],allocate);delete(out)}',prefix='import basic "thor:basic"\nimport "base:runtime"')
        self.assertTrue(any(c in {'thor/leak','thor/leak-unknown'} for c in codes),codes)

    def test_pmap_outputs_use_explicit_allocator_and_keep_jobs_independent(self):
        codes=self.codes('same::proc(x:int)->int{return x}\nmain::proc(){jobs:=[1]int{2};out:=basic.pmap(jobs[:],same,allocator=runtime.heap_allocator());_=out}',prefix='import basic "thor:basic"\nimport "base:runtime"')
        self.assertIn('thor/leak-unknown',codes)

    def test_pmap_scoped_output_is_independent_of_job_buffer(self):
        codes=self.codes('same::proc(x:int)->int{return x}\nmain::proc(){jobs~:=make([]int,2);out~:=basic.pmap(jobs,same);_=out[0]}',prefix='import basic "thor:basic"')
        self.assertEqual(codes,[])


if __name__=='__main__':unittest.main()

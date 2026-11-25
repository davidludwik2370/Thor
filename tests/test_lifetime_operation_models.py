"""Reviewed operations, copy provenance and exclusive backing storage proofs."""
import unittest
from tests import test_lifetime_vet as base


class LifetimeOperationModelTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp=base.LifetimeVetTests.setUp
    invoke=base.LifetimeVetTests.invoke
    vet=base.LifetimeVetTests.vet
    codes=base.LifetimeVetTests.codes

    def problem(self,codes):
        self.assertTrue(any(c in {'thor/lifetime','thor/lifetime-unknown'} for c in codes),codes)

    def test_formatting_families_and_aliases_are_readers(self):
        for name in ['printfln','eprint','eprintln','eprintf','eprintfln']:
            with self.subTest(name=name):
                args='"%v",v' if 'f' in name else 'v'
                codes=self.codes('main::proc(){v:=make([]int,2);defer delete(v);output::fmt.'+name+';output('+args+')}',prefix='import "core:fmt"')
                self.assertEqual(codes,[])

    def test_formatters_still_check_freed_arguments(self):
        for name in ['printfln','eprintln','eprintf','eprintfln']:
            with self.subTest(name=name):
                args='v' if name=='eprintln' else '"%v",v'
                self.problem(self.codes('main::proc(){v:=make([]int,2);delete(v);fmt.'+name+'('+args+')}',prefix='import "core:fmt"'))

    def test_formatted_assert_true_does_not_read_the_message_buffer(self):
        codes=self.codes('main::proc(){v:=make([]int,2);delete(v);fmt.assertf(true,"%v",v)}',prefix='import "core:fmt"')
        self.assertEqual(codes,[])
        self.problem(self.codes('main::proc(){v:=make([]int,2);delete(v);fmt.assertf(false,"%v",v)}',prefix='import "core:fmt"'))

    def test_formatted_assert_elision_preserves_language_semantics(self):
        body='release::proc(v:[]int)->bool{delete(v);return true}\nmain::proc(){v:=make([]int,2);fmt.assertf(release(v),"ok");_=v[0]}'
        self.problem(self.codes(body,prefix='import "core:fmt"'))
        self.assertEqual(self.codes(body,prefix='import "core:fmt"',flags=['-disable-assert']),['thor/leak-unknown'])

    def test_builtin_free_alias_is_cleanup_not_a_reference_result(self):
        codes=self.codes('main::proc(){p:=new(int);release::free;release(p)}')
        self.assertEqual(codes,[])
        self.problem(self.codes('main::proc(){p:=new(int);release::free;release(p);_=p^}'))
        self.problem(self.codes('main::proc(){p:=new(int);free(p);free(p)}'))

    def test_user_free_name_is_not_given_a_cleanup_contract(self):
        codes=self.codes('free::proc(p:^int){_=p^}\nmain::proc(){p:=new(int);free(p)}')
        self.assertIn('thor/leak-unknown',codes)

    def test_remove_range_preserves_fixed_value_storage(self):
        codes=self.codes('Card::struct{n:int,suit:enum{a,b}}\nHand::[dynamic;7]Card\nmain::proc(){v:Hand;append(&v,Card{1,.a});append(&v,Card{2,.b});erase::remove_range;erase(&v,0,1);_=v[0].n}')
        self.assertEqual(codes,[])

    def test_remove_range_never_discharges_nested_ownership(self):
        codes=self.codes('main::proc(){v:=make([]int,2,allocator=runtime.heap_allocator());a:[dynamic][]int;append(&a,v);defer delete(a);remove_range(&a,0,1)}',prefix='import "base:runtime"')
        self.assertTrue(any(c in {'thor/leak','thor/leak-unknown'} for c in codes),codes)
        codes=self.codes('bad::proc()->[dynamic][]int{v:=make([]int,2,allocator=runtime.heap_allocator());a:[dynamic][]int;append(&a,v);remove_range(&a,0,1);return a}\nmain::proc(){}',prefix='import "base:runtime"')
        self.assertIn('thor/leak-unknown',codes)

    def test_remove_range_checks_released_headers(self):
        self.problem(self.codes('main::proc(){p:=new([dynamic;2]int);free(p);remove_range(p,0,0)}'))

    def test_endian_readers_and_writers_accept_borrowed_buffers(self):
        codes=self.codes('main::proc(){v:=make([]byte,8);defer delete(v);put::endian.unchecked_put_u64le;get::endian.unchecked_get_u32le;put(v,7);_=get(v[0:4])}',prefix='import "core:encoding/endian"')
        self.assertEqual(codes,[])

    def test_endian_operations_still_check_released_storage(self):
        for call in ['endian.unchecked_get_u32le(v)','endian.unchecked_put_u32le(v,7)']:
            with self.subTest(call=call):
                self.problem(self.codes('main::proc(){v:=make([]byte,8);delete(v);_=0;'+call+'}',prefix='import "core:encoding/endian"'))

    def test_fixed_append_spread_copies_elements_not_backing_addresses(self):
        for source in ['local:[dynamic;7]Card;append(&local,Card{7})','local:=make([]Card,2);defer delete(local)']:
            with self.subTest(source=source):
                codes=self.codes('Card::struct{n:int}\ncopy_values::proc()->[dynamic;7]Card{'+source+';out:[dynamic;7]Card;append(&out,..local[:]);return out}\nmain::proc(){_=copy_values()}')
                self.assertEqual(codes,[])

    def test_append_spread_still_preserves_nested_references(self):
        codes=self.codes('Row::struct{data:[]int}\nmain::proc(){v:=make([]int,2);source:=[1]Row{{v}};out:[dynamic;2]Row;append(&out,..source[:]);delete(v);_=out[0].data[0]}')
        self.problem(codes)

    def test_append_spread_checks_source_before_copying_inline_values(self):
        self.problem(self.codes('main::proc(){source:=make([]int,2);delete(source);out:[dynamic;3]int;append(&out,..source)}'))

    def test_copy_fixed_values_is_independent_and_reads_source(self):
        codes=self.codes('copy_values::proc()->[2]int{v:=make([]int,2);out:[2]int;copy(out[:],v);delete(v);return out}\nmain::proc(){_=copy_values()}')
        self.assertEqual(codes,[])
        self.problem(self.codes('main::proc(){v:=make([]int,2);delete(v);out:[2]int;copy(out[:],v)}'))

    def test_exclusive_resize_reserve_and_named_allocator_are_safe(self):
        for call in ['resize(&v,8)','reserve(&v,8)']:
            with self.subTest(call=call):
                codes=self.codes('main::proc(){v:=make([dynamic]int,2);defer delete(v);'+call+';_=v[0]}')
                self.assertEqual(codes,[])

    def test_resize_with_direct_or_nested_view_requires_review(self):
        for alias in ['view:=v[:];_=view','box:=Box{v[:]};_=box']:
            with self.subTest(alias=alias):
                codes=self.codes('Box::struct{data:[]int}\nmain::proc(){v:=make([dynamic]int,2);defer delete(v);'+alias+';resize(&v,8)}')
                self.assertIn('thor/lifetime-unknown',codes)

    def test_resize_cannot_forget_views_held_by_an_outer_expression(self):
        codes=self.codes('grow::proc(v:^[dynamic]int)->int{resize(v,8);return 0}\nPair::struct{view:[]int,n:int}\nmain::proc(){v:=make([dynamic]int,2);defer delete(v);pair:=Pair{v[:],grow(&v)};_=pair}')
        self.assertIn('thor/lifetime-unknown',codes)

    def test_reference_free_records_do_not_acquire_unknown_references_at_depth_limit(self):
        codes=self.codes('Vec::struct{x,y:f32}\nContainer::struct{values:[]Vec}\nread::proc(v:Vec){}\nwork::proc(c:Container){for v in c.values{read(v)}}\nmain::proc(){}')
        self.assertEqual(codes,[])

    def test_borrowed_record_paths_expand_on_demand(self):
        codes=self.codes('Vec::struct{data:[][]int}\nContainer::struct{values:[]Vec}\nwork::proc(c:Container){for v in c.values{_=v.data[0][0]}}\nmain::proc(){}')
        self.assertEqual(codes,[])
        codes=self.codes('Vec::struct{data:[][][][]int}\nContainer::struct{values:[]Vec}\nwork::proc(c:Container){for v in c.values{_=v.data[0][0][0][0]}}\nmain::proc(){}')
        self.assertEqual(codes,[])

    def test_reference_casts_are_not_erased_by_scalar_type_facts(self):
        codes=self.codes('main::proc(){v:=make([]int,2);bits:=uintptr(raw_data(v));delete(v);p:=(^int)(bits);_=p^}')
        self.problem(codes)

    def test_using_pointer_field_stores_keep_the_pointees_edges(self):
        codes=self.codes('Box::struct{data:[]int}\nstore::proc(out:^Box,values:[]int){using out;data=values}\nmain::proc(){v:=make([]int,2);box:Box;store(&box,v);delete(v);_=box.data[0]}')
        self.problem(codes)

    def test_known_foreign_scalar_result_has_no_ownership_claim(self):
        findings=self.vet('main::proc(){using rl;_=MeasureText("hello",12)}',prefix='import rl "vendor:raylib"')
        self.assertFalse(any('reference result' in str(d['msgs']) or d['code']=='thor/leak-unknown' for d in findings),findings)

    def test_same_named_foreign_pointer_result_is_still_unverified(self):
        codes=self.codes('foreign sys {MeasureText::proc()->^int---}\nmain::proc(){p:=MeasureText();_=p^}',prefix='foreign import sys "system:c"')
        self.assertIn('thor/leak-unknown',codes)
        self.assertIn('thor/lifetime-unknown',codes)


if __name__=='__main__':unittest.main()

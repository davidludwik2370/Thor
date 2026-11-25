"""Physical sizes, initialization and temporary resets are vet-only proofs."""
import unittest
from tests import test_lifetime_summaries as base

class RawMemoryTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled

    def messages(self,code,prefix='import "core:mem"'):
        return [str(f['msgs'])for f in self.vet(code,prefix=prefix)if f.get('certainty')=='definite']

    def test_typed_pointer_requires_enough_physical_bytes(self):
        for n in [1,4,7]:
            fs=self.messages(f'main::proc(){{p,_:=mem.alloc({n});defer free(p);_=((^u64)(p))^}}')
            self.assertTrue(any('byte extent'in m for m in fs),fs)
        self.assertFalse(any('byte extent'in m for m in self.messages('main::proc(){p,_:=mem.alloc(8);defer free(p);_=((^u64)(p))^}')))

    def test_cloned_scalar_allocation_keeps_its_physical_size(self):
        code='main::proc(){p:=new_clone(u8(7));defer free(p);_=((^u64)(p))^}'
        self.assertTrue(any('byte extent'in m for m in self.messages(code)))

    def test_matrix_native_indexing_does_not_guess_array_stride(self):
        for typ,index in [('matrix[2,2]f32','0,1')]:
            code='main::proc(){p:=new('+typ+');defer free(p);p^['+index+']=7;_=p^['+index+']}'
            self.assertFalse(any('byte extent'in m for m in self.messages(code)),code)

    def test_typed_allocation_byte_operations_scale_by_element_size(self):
        for count,expected in [(16,False),(17,True)]:
            fs=self.messages(f'main::proc(){{a~:=make([]u64,2);mem.zero(raw_data(a),{count})}}')
            self.assertEqual(any('byte extent'in m for m in fs),expected,fs)

    def test_raw_multipointer_indices_scale_by_access_type(self):
        fs=self.messages('main::proc(){p,_:=mem.alloc(8);defer free(p);v:=([^]u64)(p);_=v[1]}')
        self.assertTrue(any('byte extent'in m for m in fs),fs)

    def test_offset_alignment_and_extent_follow_aliases(self):
        fs=self.messages('main::proc(){p,_:=mem.alloc(16,alignment=8);defer free(p);q:=mem.ptr_offset((^u8)(p),1);_=((^u64)(q))^}')
        self.assertTrue(any('misaligned'in m for m in fs),fs)
        safe=self.messages('main::proc(){p,_:=mem.alloc(16,alignment=8);defer free(p);q:=mem.ptr_offset((^u8)(p),8);_=((^u64)(q))^}')
        self.assertFalse(any('misaligned'in m or 'byte extent'in m for m in safe),safe)

    def test_unknown_and_insufficient_alignment_are_not_assumed_misaligned(self):
        fs=self.messages('work::proc(n:int){p,_:=mem.alloc(16,alignment=1);defer free(p);q:=mem.ptr_offset((^u8)(p),n);_=((^u64)(q))^}\nmain::proc(){}')
        self.assertFalse(any('misaligned'in m for m in fs),fs)

    def test_pointer_offset_preserves_rebased_slice_origin(self):
        code='main::proc(){a~:=make([]u64,4);p:=mem.ptr_offset(raw_data(a[2:]),1);_=p[0]}'
        self.assertFalse(any('byte extent'in m for m in self.messages(code)))
        code=code.replace(']),1',']),2')
        self.assertTrue(any('byte extent'in m for m in self.messages(code)))

    def test_relative_thor_indices_are_not_raw_negative_offsets(self):
        code='main::proc(){a~:=make([]int,3);_=a[-1];_=a[-2:][0];b:=[1,2,3];_=b[-1]}'
        self.assertFalse(any('byte extent'in m for m in self.messages(code)))

    def test_cleanup_size_zero_and_exact_size_are_safe(self):
        for n in [0,16]:
            self.assertFalse(any('cleanup byte size'in m for m in self.messages(f'main::proc(){{p,_:=mem.alloc(16);mem.free_with_size(p,{n})}}')))
        fs=self.messages('main::proc(){p,_:=mem.alloc(16);mem.free_with_size(p,8)}')
        self.assertTrue(any('cleanup byte size'in m for m in fs),fs)

    def test_borrowed_pointer_slice_construction_checks_physical_capacity(self):
        for call in ['mem.slice_ptr((^u64)(p),2)','mem.ptr_to_bytes((^u64)(p),2)','mem.byte_slice(p,16)']:
            fs=self.messages('main::proc(){p,_:=mem.alloc(8);defer free(p);_='+call+'}')
            self.assertTrue(any('byte extent'in m for m in fs),fs)

    def test_byte_views_of_wider_elements_keep_byte_stride(self):
        for view in ['mem.ptr_to_bytes(raw_data(a))','mem.byte_slice(rawptr(raw_data(a)),16)','([^]u8)(raw_data(a))[:16]']:
            code='main::proc(){a~:=make([]u64,2);b:='+view+';_=b[1];_=b[8:][1]}'
            self.assertFalse(any('byte extent'in m for m in self.messages(code)),code)

    def test_typed_views_of_byte_storage_keep_element_stride(self):
        code='main::proc(){a~:=make([]u8,16);b:=mem.slice_ptr((^u64)(raw_data(a)),2);_=b[1]}'
        self.assertFalse(any('byte extent'in m for m in self.messages(code)))

    def test_view_metadata_preserves_termination_and_nonoverlap_checks(self):
        code='main::proc(){p,_:=mem.alloc(8);defer free(p);b:=mem.byte_slice(p,8);mem.set(raw_data(b),65,8);_=len(cstring(raw_data(b)))}'
        self.assertTrue(any('zero terminator'in m for m in self.messages(code)))
        code='main::proc(){a~:=make([]u64,2);b:=mem.ptr_to_bytes(raw_data(a),2);mem.copy_non_overlapping(raw_data(b[1:]),raw_data(b),8)}'
        self.assertTrue(any('overlapping storage'in m for m in self.messages(code)))

    def test_size_arithmetic_tracks_copies_narrowing_and_compound_writes(self):
        for expr in ['n:u8=200;m:=n*2','n:=256;m:=u8(n)','m:u8=200;m*=2']:
            fs=self.vet('main::proc(){'+expr+';p,_:=mem.alloc(int(m));defer free(p)}',prefix='import "core:mem"')
            self.assertTrue(any('size arithmetic wraps'in str(f['msgs']) for f in fs),fs)
            self.assertTrue(all(f.get('certainty')=='unverified'for f in fs if 'size arithmetic'in str(f['msgs'])),fs)

    def test_intentional_bit_arithmetic_and_safe_sizes_do_not_warn(self):
        code='main::proc(){n:u8=200;mask:=n*2;_=mask;m:=8*2;p,_:=mem.alloc(m);defer free(p)}'
        self.assertFalse(any('size arithmetic'in str(f['msgs']) for f in self.vet(code,prefix='import "core:mem"')))

    def test_nonzeroed_heap_reads_and_partial_writes(self):
        for write,expected in [('',True),('v[0]=7;',True),('v[0]=7;v[1]=8;',False)]:
            code='main::proc(){b,_:=mem.alloc_bytes_non_zeroed(16);defer delete(b);v:=([^]u64)(raw_data(b));'+write+'_=v[1]}'
            fs=self.messages(code)
            self.assertEqual(any('uninitialized heap'in m for m in fs),expected,fs)

    def test_interprocedural_partial_initialization_is_not_a_full_write(self):
        fs=self.messages('set::proc(p:[^]u64){p[0]=7}\nmain::proc(){b,_:=mem.alloc_bytes_non_zeroed(16);defer delete(b);v:=([^]u64)(raw_data(b));set(v);_=v[1]}')
        self.assertTrue(any('uninitialized heap'in m for m in fs),fs)

    def test_zero_and_byte_transport_initialization_contracts(self):
        good='main::proc(){b,_:=mem.alloc_bytes_non_zeroed(16);defer delete(b);mem.zero(raw_data(b),16);_=((^u64)(raw_data(b)))^}'
        self.assertFalse(any('uninitialized heap'in m for m in self.messages(good)))
        transport='main::proc(){a,_:=mem.alloc_bytes_non_zeroed(8);defer delete(a);b~:=make([]u8,8);mem.copy(raw_data(b),raw_data(a),8)}'
        self.assertEqual(self.messages(transport),[])
        for call in ['mem.copy','mem.copy_non_overlapping']:
            code='main::proc(){a,_:=mem.alloc_bytes_non_zeroed(8);defer delete(a);b~:=make([]u8,8);'+call+'(raw_data(b),raw_data(a),8);mem.zero(raw_data(b),8);_=((^u64)(raw_data(b)))^}'
            self.assertEqual(self.messages(code),[],code)

    def test_uninitialized_transport_still_checks_source_lifetime_and_extent(self):
        for ending in ['free(a);mem.copy(raw_data(b),a,8)','mem.copy(raw_data(b),a,9)']:
            code='main::proc(){a,_:=mem.alloc(8);b~:=make([]u8,8);'+ending+'}'
            codes=self.codes(code,prefix='import "core:mem"')
            self.assertTrue(any(c in {'thor/lifetime','thor/lifetime-unknown'}for c in codes),(code,codes))

    def test_copy_preserves_uninitialized_ranges_in_destination(self):
        code='main::proc(){a,_:=mem.alloc_bytes_non_zeroed(8);defer delete(a);b,_:=mem.alloc_bytes_non_zeroed(8);defer delete(b);mem.copy(raw_data(b),raw_data(a),8);_=((^u64)(raw_data(b)))^}'
        fs=self.vet(code,prefix='import "core:mem"')
        read=next(f for f in fs if 'uninitialized heap'in str(f['msgs']) and f['pos']['offset']>self.source.read_text().index(';_=((^u64)'))
        self.assertEqual(read['certainty'],'definite')

    def test_opaque_writes_do_not_leave_stale_uninitialized_certainty(self):
        code='work::proc(callback:proc(rawptr)){b,_:=mem.alloc_bytes_non_zeroed(8);defer delete(b);callback(raw_data(b));_=((^u64)(raw_data(b)))^}\nmain::proc(){}'
        fs=self.vet(code,prefix='import "core:mem"')
        self.assertTrue(any(f['code']=='thor/lifetime-unknown' for f in fs),fs)
        self.assertFalse(any(f.get('certainty')=='definite'and'uninitialized heap'in str(f['msgs'])for f in fs),fs)

    def test_partial_io_does_not_initialize_unwritten_suffix(self):
        code='main::proc(){b,_:=mem.alloc_bytes_non_zeroed(8);defer delete(b);small:=mem.byte_slice(raw_data(b),4);_,_=os.read(os.stdin,small);_=((^u64)(raw_data(b)))^}'
        fs=self.messages(code,prefix='import "core:mem"\nimport "core:os"')
        self.assertTrue(any('uninitialized heap'in m for m in fs),fs)

    def test_custom_raw_layout_and_reference_loads_stay_unverified(self):
        code='Box::struct{x:int,p:^int}\nmain::proc(){p,_:=mem.alloc(64);defer free(p);box:=(^Box)(p);_=box.x;_=box.p^}'
        fs=self.vet(code,prefix='import "core:mem"')
        self.assertTrue(any(f['code']=='thor/lifetime-unknown'for f in fs),fs)

    def test_conditional_initialization_only_warns_when_definite(self):
        code='work::proc(ok:bool){b,_:=mem.alloc_bytes_non_zeroed(8);defer delete(b);p:=(^u64)(raw_data(b));if ok{p^=7};_=p^}\nmain::proc(){}'
        self.assertFalse(any('uninitialized heap'in m for m in self.messages(code)))

    def test_temporary_aliases_end_at_reset(self):
        for call in ['free_all(context.temp_allocator)','mem.free_all(context.temp_allocator)']:
            fs=[str(f['msgs']) for f in self.vet('main::proc(){s:=fmt.tprintf("%d",7);alias:=s;'+call+';fmt.println(alias)}',prefix='import "core:mem"\nimport "core:fmt"')]
            self.assertTrue(any('lifetime'in m or 'storage'in m for m in fs),fs)

    def test_temporary_new_generation_does_not_revive_old_strings(self):
        code='main::proc(){a:=fmt.tprint(1);free_all(context.temp_allocator);b:=fmt.tprint(2);fmt.println(b);fmt.println(a)}'
        fs=[str(f['msgs']) for f in self.vet(code,prefix='import "core:fmt"')]
        self.assertTrue(any('lifetime'in m or 'storage'in m for m in fs),fs)

    def test_temporary_reset_preserves_independent_heap_copy(self):
        code='main::proc(){s:=fmt.tprint(7);copy:=strings.clone(s);defer delete(copy);free_all(context.temp_allocator);fmt.println(copy)}'
        self.assertEqual(self.codes(code,prefix='import "core:fmt"\nimport "core:strings"'),[])

    def test_temporary_and_heap_cleanup_allocators_are_distinct(self):
        for allocate,cleanup in [('context.temp_allocator','runtime.heap_allocator()'),('runtime.heap_allocator()','context.temp_allocator')]:
            code='main::proc(){p:=new(int,allocator='+allocate+');free(p,allocator='+cleanup+')}'
            fs=self.messages(code,prefix='import "base:runtime"')
            self.assertTrue(any('allocator does not match'in m for m in fs),fs)

    def test_whole_context_and_lexical_restoration_keep_temp_identity(self):
        code='main::proc(){s:=fmt.tprint(7);{ctx:=context;ctx.temp_allocator=runtime.heap_allocator();context=ctx;t:=fmt.tprint(8);delete(t,allocator=runtime.heap_allocator())};free_all(context.temp_allocator);fmt.println(s)}'
        fs=self.vet(code,prefix='import "core:fmt"\nimport "base:runtime"')
        self.assertTrue(any(f['code'].startswith('thor/lifetime') for f in fs),fs)

    def test_user_selected_temporary_allocator_retains_its_identity(self):
        code='main::proc(){arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;defer virtual.arena_destroy(&arena);context.temp_allocator=virtual.arena_allocator(&arena);s:=fmt.tprint(7);virtual.arena_free_all(&arena);fmt.println(s)}'
        fs=[str(f['msgs']) for f in self.vet(code,prefix='import "core:fmt"\nimport "core:mem/virtual"')]
        self.assertTrue(any('storage'in m or 'lifetime'in m for m in fs),fs)

    def test_copied_context_cannot_hide_an_expired_allocator_header(self):
        for field in ['allocator','temp_allocator']:
            code='get::proc()->runtime.Context{arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;context.'+field+'=virtual.arena_allocator(&arena);return context}\nmain::proc(){_=get()}'
            fs=self.vet(code,prefix='import "base:runtime"\nimport "core:mem/virtual"')
            self.assertTrue(any(f['code'].startswith('thor/lifetime')for f in fs),fs)

if __name__=='__main__':unittest.main()

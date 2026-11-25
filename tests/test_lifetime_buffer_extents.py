"""Only proven byte extents/contents produce definite buffer diagnostics."""
import unittest
from tests import test_lifetime_summaries as base
class BufferExtentTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled

    def messages(self,code,prefix=''):
        return [str(f['msgs'])for f in self.vet(code,prefix=prefix)if f['certainty']=='definite']

    def test_raw_buffer_source_and_destination_extents(self):
        for call in ['mem.zero(raw_data(a[:]),5)','mem.set(raw_data(a[:]),1,5)','mem.copy(raw_data(a[:]),raw_data(b[:]),5)','mem.copy(raw_data(b[:]),raw_data(a[:]),5)']:
            fs=self.messages('main::proc(){a:[4]u8;b:[8]u8;'+call+'}',prefix='import "core:mem"')
            self.assertTrue(any('byte extent'in f for f in fs),fs)

    def test_positional_format_fields_named_like_metadata_are_still_read(self):
        for field in ['loc','allocator']:
            code='Box::struct{'+field+':cstring}\nmain::proc(){bytes:=[3]u8{65,66,67};holder:=Box{cstring(raw_data(bytes[:]))};fmt.println(holder.'+field+')}'
            fs=self.messages(code,prefix='import "core:fmt"')
            self.assertTrue(any('zero terminator'in f for f in fs),fs)

    def test_cached_scalar_constants_are_separate_for_generic_instantiations(self):
        code='size::proc($N:u8){a:=[1]u8{N};_=len(cstring(raw_data(a[:])))}\nmain::proc(){size(0);size(65);size(0)}'
        fs=self.messages(code)
        self.assertTrue(any('zero terminator'in f for f in fs),fs)

    def test_byte_facts_are_preserved_in_mixed_record_layouts(self):
        code='Box::struct{data:[2]u8,count:int}\nmain::proc(){box:=Box{data={65,66},count=7};bytes:=box.data;_=len(cstring(raw_data(bytes[:])))}'
        self.assertTrue(any('zero terminator'in f for f in self.messages(code)))

    def test_heap_byte_buffer_and_named_reordered_arguments(self):
        fs=self.messages('main::proc(){a~:=make([]u8,4);mem.zero(len=5,data=raw_data(a))}',prefix='import "core:mem"')
        self.assertTrue(any('byte extent'in f for f in fs),fs)

    def test_cached_endian_readers_keep_size_requirements(self):
        code='read::proc(b:[]u8)->u64{return endian.unchecked_get_u64le(b)}\nforward::proc(b:[]u8)->u64{return read(b)}\nmain::proc(){a:[2]u8;_=forward(a[:])}'
        fs=self.messages(code,prefix='import "core:encoding/endian"')
        self.assertTrue(any('byte extent'in f for f in fs),fs)

    def test_sliced_forwarding_keeps_offsets(self):
        code='read::proc(b:[]u8)->u32{return endian.unchecked_get_u32le(b)}\nforward::proc(b:[]u8)->u32{return read(b[2:])}\nmain::proc(){a:[4]u8;_=forward(a[:])}'
        self.assertTrue(any('byte extent'in f for f in self.messages(code,prefix='import "core:encoding/endian"')))

    def test_safe_extents_zero_lengths_and_unknown_sizes(self):
        code='work::proc(n:int){a:[4]u8;mem.zero(raw_data(a[:]),n)}\nmain::proc(){a:[4]u8;mem.zero(raw_data(a[:]),4);mem.zero(nil,0)}'
        self.assertFalse(any('byte extent'in f for f in self.messages(code,prefix='import "core:mem"')))

    def test_dynamic_capacity_is_not_mistaken_for_length(self):
        self.assertFalse(any('byte extent'in f for f in self.messages('main::proc(){a~:=make([dynamic]u8,1,8);mem.zero(raw_data(a),8)}',prefix='import "core:mem"')))

    def test_unterminated_cstring_and_added_terminator(self):
        bad='main::proc(){a:=[3]u8{65,66,67};_=len(cstring(raw_data(a[:])))}'
        self.assertTrue(any('zero terminator'in f for f in self.messages(bad)))
        good=bad.replace('_=len','a[2]=0;_=len')
        self.assertFalse(any('zero terminator'in f for f in self.messages(good)))

    def test_slices_and_aliases_share_written_termination(self):
        code='main::proc(){a~:=make([]u8,3);alias:=a;mem.set(raw_data(alias),65,3);_=len(cstring(raw_data(a)))}'
        self.assertTrue(any('zero terminator'in f for f in self.messages(code,prefix='import "core:mem"')))
        self.assertFalse(any('zero terminator'in f for f in self.messages(code.replace('_=len','alias[2]=0;_=len'),prefix='import "core:mem"')))

    def test_unknown_writes_and_branch_termination_are_not_false_positives(self):
        code='work::proc(n:u8,choose:bool){a:=[3]u8{65,66,67};if choose{a[2]=0}else{a[2]=n};_=len(cstring(raw_data(a[:])))}\nmain::proc(){}'
        self.assertFalse(any('zero terminator'in f for f in self.messages(code)))

    def test_physical_terminator_can_follow_a_short_slice(self):
        code='main::proc(){a:=[4]u8{65,66,67,0};_=len(cstring(raw_data(a[:2])))}'
        self.assertFalse(any('zero terminator'in f for f in self.messages(code)))

    def test_literal_and_zero_initialized_cstrings_are_safe(self):
        self.assertEqual(self.codes('main::proc(){_=len(cstring("hello"));a:[4]u8;_=len(cstring(raw_data(a[:])))}'),[])

    def test_unknown_raw_alloc_contents_are_not_assumed_zero_or_unterminated(self):
        fs=self.messages('main::proc(){p,err:=mem.alloc(4);_=err;defer free(p);_=len(cstring(p));mem.zero(p,5)}',prefix='import "core:mem"')
        self.assertFalse(any('zero terminator'in f for f in fs),fs)
        self.assertTrue(any('byte extent'in f for f in fs),fs)

    def test_compound_byte_writes_do_not_invent_a_nonzero_value(self):
        code='work::proc(){a:=[1]u8{255};a[0]+=1;_=len(cstring(raw_data(a[:])))}\nmain::proc(){}'
        self.assertFalse(any('zero terminator'in f for f in self.messages(code)))

    def test_formatting_and_summarized_writers_forget_old_contents(self):
        code='write::proc(b:[]u8){_=fmt.bprint(b,0)}\nmain::proc(){a:=[4]u8{65,65,65,65};write(a[:]);_=len(cstring(raw_data(a[:])))}'
        self.assertFalse(any('zero terminator'in f for f in self.messages(code,prefix='import "core:fmt"')))

    def test_byte_copies_propagate_termination_through_aliases(self):
        code='main::proc(){a:=[3]u8{65,66,67};b:[3]u8;mem.copy(raw_data(b[:]),raw_data(a[:]),3);_=len(cstring(raw_data(b[:])))}'
        self.assertTrue(any('zero terminator'in f for f in self.messages(code,prefix='import "core:mem"')))

    def test_cached_cstring_reader_rechecks_each_caller(self):
        code='size::proc(p:cstring)->int{return len(p)}\nalias::size\nmain::proc(){_=alias(cstring("good"));b:=[2]u8{65,66};_=alias(cstring(raw_data(b[:])))}'
        self.assertTrue(any('zero terminator'in f for f in self.messages(code)))

    def test_negative_thor_bounds_do_not_become_invalid_byte_offsets(self):
        code='main::proc(){a:=[4]u8{65,66,67,0};_=len(cstring(raw_data(a[-2:])));mem.zero(raw_data(a[:-1]),2)}'
        self.assertFalse(any('zero terminator'in f or 'byte extent'in f for f in self.messages(code,prefix='import "core:mem"')))

    def test_growth_does_not_reuse_a_stale_capacity(self):
        code='main::proc(){a~:=make([dynamic]u8,1);resize(&a,8);mem.zero(raw_data(a),8)}'
        self.assertFalse(any('byte extent'in f for f in self.messages(code,prefix='import "core:mem"')))

    def test_loop_local_bytes_are_reinitialized_each_iteration(self):
        code='main::proc(){for _ in 0..<10{a:[3]u8;_=len(cstring(raw_data(a[:])));mem.set(raw_data(a[:]),65,3)}}'
        self.assertFalse(any('zero terminator'in f for f in self.messages(code,prefix='import "core:mem"')))

    def test_nonoverlapping_copy_rejects_proven_overlap(self):
        code='main::proc(){a:[8]u8;mem.copy_non_overlapping(raw_data(a[1:]),raw_data(a[:]),4)}'
        self.assertTrue(any('overlapping storage'in f for f in self.messages(code,prefix='import "core:mem"')))

    def test_cached_scalar_writer_cannot_leave_a_stale_fill_byte(self):
        code='zero::proc(p:^u8){p^=0}\nmain::proc(){b:[4]u8;byte_value:u8=65;zero(&byte_value);mem.set(raw_data(b[:]),byte_value,4);_=len(cstring(raw_data(b[:])))}'
        self.assertFalse(any('zero terminator'in f for f in self.messages(code,prefix='import "core:mem"')))

    def test_formatting_cstrings_checks_termination_even_for_pointer_verbs(self):
        for call in ['fmt.println(text)','fmt.printf("%p",text)']:
            code='main::proc(){b:=[2]u8{65,66};text:=cstring(raw_data(b[:]));'+call+'}'
            self.assertTrue(any('zero terminator'in f for f in self.messages(code,prefix='import "core:fmt"')))

    def test_cstring_identity_conversion_does_not_read_the_buffer(self):
        self.assertEqual(self.codes('main::proc(){b:=[2]u8{65,66};text:=cstring(raw_data(b[:]));alias:=cstring(text);_=alias}'),[])

if __name__=='__main__':unittest.main()

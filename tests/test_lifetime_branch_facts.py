"""Resource predicates retain failure paths and alias provenance."""
import unittest
from tests import test_lifetime_summaries as base
class BranchFactTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes

    def test_conditional_file_or_stdin_cleanup(self):
        code='''work::proc(use_file:bool){
 handle:=os.stdin
 if use_file {f,err:=os.open("file");if err!=nil{return};handle=f}
 defer {if handle!=os.stdin{_=os.close(handle)}}
 bytes:[8]u8;_,_=os.read(handle,bytes[:])
}
main::proc(){}'''
        self.assertEqual(self.codes(code,prefix='import "core:os"'),[])

    def test_status_alias_and_reversed_comparison(self):
        self.assertEqual(self.codes('main::proc(){f,err:=os.open("file");status:=err;if nil!=status{return};_=os.close(f)}',prefix='import "core:os"'),[])

    def test_successful_file_without_close_is_still_lost(self):
        codes=self.codes('main::proc(){f,err:=os.open("file");if err!=nil{return};_=f}',prefix='import "core:os"')
        self.assertTrue(any(c.startswith('thor/leak')for c in codes),codes)

    def test_wrong_predicate_and_overwritten_status_do_not_prove_cleanup(self):
        for body in ['if handle==os.stdin{_=os.close(handle)}','if choose{_=os.close(handle)}']:
            code='work::proc(choose:bool){handle:=os.stdin;f,err:=os.open("file");if err!=nil{return};handle=f;defer{'+body+'}}\nmain::proc(){}'
            self.assertTrue(any(c.startswith('thor/leak')for c in self.codes(code,prefix='import "core:os"')))

    def test_closed_alias_and_double_close_remain_definite_after_success(self):
        codes=self.codes('main::proc(){f,err:=os.open("file");if err!=nil{return};alias:=f;_=os.close(f);b:[8]u8;_,_=os.read(alias,b[:]);_=os.close(alias)}',prefix='import "core:os"')
        self.assertIn('thor/lifetime',codes)

    def test_unchecked_failure_is_not_assumed_success(self):
        findings=self.vet('main::proc(){f,err:=os.open("file");_=err;_=f}',prefix='import "core:os"')
        self.assertTrue(any(f['code']=='thor/leak-unknown' for f in findings),findings)

    def test_independent_file_is_not_forgotten_by_a_handle_choice(self):
        code='work::proc(choose:bool){f,err:=os.open("file");if err!=nil{return};handle:=os.stdin;if choose{handle=f};defer{if handle!=os.stdin{_=os.close(handle)}}}\nmain::proc(){}'
        self.assertTrue(any(c.startswith('thor/leak')for c in self.codes(code,prefix='import "core:os"')))

    def test_cached_bool_writer_cannot_leave_a_stale_branch_fact(self):
        code='disable::proc(p:^bool){p^=false}\nmain::proc(){p:=new(int);enabled:=true;disable(&enabled);if enabled{}else{free(p)};_=p^;free(p)}'
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code)))

    def test_cached_global_writer_cannot_leave_a_stale_branch_fact(self):
        code='enabled:=true\ndisable::proc(){enabled=false}\nmain::proc(){p:=new(int);enabled=true;disable();if enabled{}else{free(p)};_=p^;free(p)}'
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code)))

    def test_recursive_writer_forgets_traversed_child_facts(self):
        code='Node::struct{enabled:bool,next:^Node}\ndisable::proc(p:^Node){if p==nil{return};p.enabled=false;disable(p.next)}\nmain::proc(){p:=new(int);child:=Node{enabled=true};root:=Node{next=&child};disable(&root);enabled:=child.enabled;if enabled{}else{free(p)};_=p^;free(p)}'
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code)))

    def test_readonly_global_summary_preserves_a_proven_branch_fact(self):
        code='enabled:=true\nread::proc()->bool{return enabled}\nmain::proc(){p:=new(int);enabled=true;_=read();if enabled{}else{free(p)};_=p^;free(p)}'
        self.assertEqual(self.codes(code),[])

    def test_forwarded_field_writers_invalidate_the_original_record(self):
        code='Box::struct{flags:[2]bool}\nclear_flag::proc(v:[]bool){v[0]=false}\nclear_box::proc(p:^Box){clear_flag(p.flags[:])}\nmain::proc(){p:=new(int);box:=Box{flags={true,true}};clear_box(&box);enabled:=box.flags[0];if enabled{}else{free(p)};_=p^;free(p)}'
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code)))

    def test_library_field_writes_do_not_keep_old_boolean_elements(self):
        code='Box::struct{flags:[2]bool}\nshuffle::proc(p:^Box){rand.shuffle(p.flags[:])}\nmain::proc(){p:=new(int);box:=Box{flags={true,false}};shuffle(&box);enabled:=box.flags[0];if enabled{}else{free(p)};_=p^;free(p)}'
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code,prefix='import "core:math/rand"')))

    def test_forwarded_global_array_writes_invalidate_scalar_facts(self):
        code='flags:=[2]bool{true,true}\nclear_flag::proc(v:[]bool){v[0]=false}\nclear_global::proc(){clear_flag(flags[:])}\nmain::proc(){p:=new(int);flags[0]=true;clear_global();enabled:=flags[0];if enabled{}else{free(p)};_=p^;free(p)}'
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code)))

    def test_writing_an_inline_array_copy_preserves_the_source_facts(self):
        code='Box::struct{flags:[2]bool}\nclear_flag::proc(v:[]bool){v[0]=false}\nclear_copy::proc(v:[2]bool){copy:=v;clear_flag(copy[:])}\nwork::proc(p:^Box){clear_copy(p.flags)}\nmain::proc(){p:=new(int);box:=Box{flags={true,true}};work(&box);enabled:=box.flags[0];if enabled{}else{free(p)};_=p^;free(p)}'
        self.assertEqual(self.codes(code),[])

    def test_parallel_scalar_writes_do_not_hide_later_callback_cleanup(self):
        code='enabled:=true\njob::proc(p:^int){if enabled{}else{free(p)};enabled=false}\nmain::proc(){p:=new(int);jobs:=[2]^int{p,p};basic.pmap(jobs[:],job);_=p^;free(p)}'
        self.assertIn('thor/lifetime-unknown',self.codes(code,prefix='import basic "thor:basic"'))

if __name__=='__main__':unittest.main()

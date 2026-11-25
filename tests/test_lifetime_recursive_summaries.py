"""Bounded recursive read programs are precise about traversed fields."""
import unittest
from tests import test_lifetime_summaries as base


class RecursiveSummaryTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled

    node='Node::struct{value:int,left,right:^Node}\n'
    reader='sum::proc(p:^Node)->int{if p==nil{return 0};return p.value+sum(p.left)+sum(p.right)}\n'

    def issue(self,codes):self.assertTrue(any(c.startswith('thor/lifetime') for c in codes),codes)

    def test_repeated_tree_readers_use_a_finite_cached_program(self):
        findings,profile=self.profiled(self.node+self.reader+'main::proc(){a:=Node{value=1};b:=Node{value=2};root:=Node{value=3,left=&a,right=&b};'+''.join('_=sum(&root);' for _ in range(100))+'}')
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.graph_summary_applied',0),90,profile)

    def test_cached_tree_reads_recheck_released_children(self):
        self.issue(self.codes(self.node+self.reader+'main::proc(){child:=new(Node);root:=Node{left=child};_=sum(&root);free(child);_=sum(&root)}'))

    def test_unused_edges_are_not_treated_as_traversed(self):
        code=self.node+'left_depth::proc(p:^Node)->int{if p==nil{return 0};return 1+left_depth(p.left)}\nmain::proc(){left:=Node{};right:=new(Node);root:=Node{left=&left,right=right};free(right);_=left_depth(&root)}'
        self.assertEqual(self.codes(code),[])

    def test_pointer_comparison_does_not_read_the_record(self):
        self.assertEqual(self.codes(self.node+'empty::proc(p:^Node)->bool{return p==nil}\nmain::proc(){p:=new(Node);free(p);_=empty(p)}'),[])

    def test_inferred_scalar_defaults_do_not_block_recursive_reader_proofs(self):
        code=self.node+'depth::proc(p:^Node,n:=0)->int{if p==nil{return n};return max(depth(p.left,n+1),depth(p.right,n+1))}\nmain::proc(){child:=Node{};root:=Node{left=&child};_=depth(&root)}'
        findings,profile=self.profiled(code)
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.graph_summary_applied',0),0,profile)

    def test_mutual_recursion_and_reordered_arguments_preserve_edges(self):
        code='A::struct{value:int,next:^B}\nB::struct{value:int,next:^A}\nread_a::proc(p:^A,n:int)->int{if p==nil{return 0};return p.value+read_b(n=n,p=p.next)}\nread_b::proc(n:int,p:^B)->int{if p==nil{return 0};return p.value+read_a(p.next,n)}\nmain::proc(){b:=new(B);a:=A{next=b};_=read_a(&a,1);free(b);_=read_a(&a,1)}'
        self.issue(self.codes(code))

    def test_generic_recursive_readers_and_aliases_keep_specializations_separate(self):
        code=self.node+'Other::struct{value:int,left,right:^Other}\nread::proc(p:$T)->int{if p==nil{return 0};return p.value+read(p.left)+read(p.right)}\nalias::read\nmain::proc(){a:=Node{};b:=Other{};_=alias(&a);_=alias(&b)}'
        self.assertEqual(self.codes(code),[])
        code=self.node+'read::proc(p:$T)->int{if p==nil{return 0};return p.value+read(p.left)+read(p.right)}\nmain::proc(){a:=Node{};_=read(&a);p:=new(Node);root:=Node{left=p};free(p);_=read(&root)}'
        self.issue(self.codes(code))

    def test_cycle_read_program_visits_each_state_once(self):
        code=self.node+'read::proc(p:^Node,n:int)->int{if p==nil || n==0{return 0};return p.value+read(p.left,n-1)}\nmain::proc(){a:=Node{value=1};a.left=&a;_=read(&a,3)}'
        self.assertEqual(self.codes(code),[])

    def test_inline_writers_do_not_erase_integer_address_provenance(self):
        code='Node::struct{bits:uintptr,next:^Node}\nclear::proc(p:^Node){if p==nil{return};p.bits=0;clear(p.next)}\nmain::proc(){value:=new(int);n:=Node{bits=uintptr(value)};clear(&n)}'
        self.assertTrue(any(c.startswith('thor/leak') for c in self.codes(code)))

    def test_scalar_results_do_not_erase_integer_address_provenance(self):
        code='Node::struct{bits:uintptr,next:^Node}\nread::proc(p:^Node)->uintptr{if p==nil{return 0};return p.bits+read(p.next)}\nmain::proc(){value:=new(int);n:=Node{bits=uintptr(value)};bits:=read(&n);free(value);_=((^int)(bits))^}'
        self.issue(self.codes(code))

    def test_reference_writes_and_releases_do_not_get_reader_contracts(self):
        code=self.node+'reset::proc(p:^Node){if p==nil{return};free(p.left);p.left=p.right;reset(p.left)}\nmain::proc(){child:=new(Node);root:=Node{left=child};reset(&root);_=child.value}'
        self.issue(self.codes(code))

    def test_failed_recursive_components_are_never_partially_published(self):
        code=self.node+'a::proc(p:^Node,n:int){if p==nil{return};b(p,n)}\nb::proc(p:^Node,n:int){if n==0{free(p);return};a(p.left,n-1)}\nmain::proc(){p:=new(Node);a(p,0);_=p.value}'
        self.issue(self.codes(code))

    def test_opaque_pointer_results_and_mutated_borrowed_tails_stay_unverified(self):
        code=self.node+self.reader+'work::proc(root:^Node,callback:proc()->^Node){root.left=callback();_=sum(root)}\nmain::proc(){}'
        self.assertIn('thor/lifetime-unknown',self.codes(code))
        code=self.node+self.reader+'work::proc(callback:proc()->^Node){value:=callback();_=sum(value)}\nmain::proc(){}'
        self.assertIn('thor/lifetime-unknown',self.codes(code))

    def test_graph_read_budget_keeps_an_explicit_review_boundary(self):
        setup='n0:=Node{};'+''.join('n'+str(i)+':=Node{left=&n'+str(i-1)+'};' for i in range(1,140))
        self.assertIn('thor/lifetime-unknown',self.codes(self.node+self.reader+'main::proc(){'+setup+'_=sum(&n139)}'))


if __name__=='__main__':unittest.main()

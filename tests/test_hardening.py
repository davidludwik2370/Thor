"""Compatibility and allocation-lifetime regressions for the hardening batch."""
import unittest
from tests import test_thor as base


class HardeningTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def memory(self, body, prefix=''):
        return self.invoke('package main\nimport "core:testing"\n'+prefix+
                           '\n@(test) lifetime::proc(t:^testing.T){\n'+body+'\n}\n',
                           command='test', flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_native_xor_precedence_and_parentheses(self):
        self.memory('''
a:=set[int]{1};b:=set[int]{2};c:=set[int]{2}
x:=a~b&c;y:=a~(b)&c
testing.expect(t,x==y&&len(x)==2)
a~=b;testing.expect(t,len(a)==2)
delete(x);delete(y);delete(a);delete(b);delete(c)
''')

    def test_native_pointer_assignment_and_call_are_unambiguous(self):
        self.invoke(self.program('''
value:=0;x:>&value;x^=7;x^~=2;assert(x^==5)
callable:=identity;p:=&callable;assert(p^(4)==4)
''','identity::proc(x:int)->int{return x}\n'))

    def test_c_and_contextless_callbacks_index_without_context(self):
        for cc in ['"c"', '`c`', '"contextless"', '"system"']:
            with self.subTest(cc=cc):
                self.invoke(self.program('a:=[1,2];assert(last(a[:],-1)==2)',
                    f'last::proc {cc}(a:[]int,i:int)->int{{return a[i]}}\n'))

    def test_c_callback_switch_and_write_helpers(self):
        self.invoke(self.program('a:=[1,2];assert(work(a[:],-1)==9&&a[1]==9)', '''
work::proc "c"(a:[]int,i:int)->int{
 a[i]+=7
 return switch a[i]{case 9:a[i];case:0}
}
'''))

    def test_contextless_word_in_default_is_not_a_convention(self):
        self.invoke(self.program('text:=message();assert(text=="contextless");delete(text)', '''
message::proc(value:string="contextless")->string{return f"{value}"}
'''))

    def test_c_callback_with_explicit_context_can_allocate(self):
        self.memory('text:=message(context,7);testing.expect(t,text=="value 7");delete(text)', '''
import "base:runtime"
message::proc "c"(ctx:runtime.Context,n:int)->string{
 context=ctx
 return f"value {n}"
}
''')

    def test_exported_procedure_conventions(self):
        for cc in ['c', 'odin', 'contextless']:
            with self.subTest(cc=cc):
                prefix=f'@(export) last::proc "{cc}"(a:[]int,i:int)->int{{return a[i]}}\n'
                self.invoke(self.program('a:=[1,2];assert(last(a[:],-1)==2)', prefix))

    def test_union_chain_cleans_temporaries_and_keeps_inputs(self):
        self.memory('''
a:=set[int]{1};b:=set[int]{2};c:=set[int]{3}
result:=a|b|c
testing.expect(t,len(result)==3&&len(a)==1&&len(b)==1&&len(c)==1)
delete(result);delete(a);delete(b);delete(c)
''')

    def test_union_chain_reuses_one_result_table(self):
        self.memory('''
a:=set[int]{1};b:=set[int]{2};c:=set[int]{3}
backing:=context.allocator
tracker:mem.Tracking_Allocator
mem.tracking_allocator_init(&tracker,backing)
context.allocator=mem.tracking_allocator(&tracker)
result:=a|b|c
testing.expect(t,tracker.total_allocation_count==1)
delete(result)
testing.expect(t,len(tracker.allocation_map)==0)
context.allocator=backing
mem.tracking_allocator_destroy(&tracker)
delete(a);delete(b);delete(c)
''','import "core:mem"\n')

    def test_nested_set_operations_queries_membership_and_discard(self):
        self.memory('''
a:=set[int]{1,2};b:=set[int]{2,3};c:=set[int]{3,4}
result:=(a|b)&(b~c)
testing.expect(t,result==set[int]{2})
testing.expect(t,len(a|b)==3&&3 in (a|b)&&8 not_in (a|b))
_=a|b;_=set[int]{7};_=f"discard {1}"
delete(result);delete(a);delete(b);delete(c)
''')

    def test_generic_set_chains_and_native_xor(self):
        self.memory('''
a:=set[int]{1};b:=set[int]{2};c:=set[int]{3}
r:=chain(a,b,c);testing.expect(t,len(r)==3)
testing.expect(t,chain(int(1),int(2),int(4))==7)
delete(r);delete(a);delete(b);delete(c)
''',
                    'chain::proc(a,b,c:$T)->T{return a|b|c}\n')

    def test_owned_conditional_and_switch_operands(self):
        self.memory('''
a:=set[int]{1};b:=set[int]{2}
for flag in [false,true]{
 r:=(flag ? set[int]{3} : a)|b
 q:=(switch flag{case true:set[int]{4};case:a})|b
 testing.expect(t,len(r)==2&&len(q)==2&&len(a)==1)
 delete(r);delete(q)
}
delete(a);delete(b)
''')

    def test_set_updates_release_anonymous_operands(self):
        self.memory('''
a:=set[int]{1,2};a|=set[int]{3};a&=set[int]{2,3};a-=set[int]{2};a~=set[int]{4}
testing.expect(t,a==set[int]{3,4});delete(a)
''')

    def test_iteration_temporary_lasts_through_break_continue_and_return(self):
        self.memory('''
count:=0
outer:for item in set[int]{1,2,3}{
 count+=1
 if count==1{continue outer}
 break outer
}
testing.expect(t,count==2&&early()==7)
''','early::proc()->int{for item in set[int]{7}{return item};return 0}\n')

    def test_mixed_nested_interpolation_selected_ownership(self):
        self.memory('''
borrowed:="existing"
for flag in [false,true]{
 text:=f"{(flag ? f"value {7}" : borrowed)}"
 testing.expect(t,text==(flag ? "value 7" : borrowed));delete(text)
 text=f"{switch flag{case true:f"other {8}";case:borrowed}}"
 testing.expect(t,text==(flag ? "other 8" : borrowed));delete(text)
}
''')

    def test_string_queries_and_scoped_borrowed_views(self):
        self.memory('''
testing.expect(t,len(f"hello {7}")==7)
testing.expect(t,f"value {7}"=="value 7"&&f"value {7}"==f"value {7}")
testing.expect(t,f"abc {7}"[-1]=='7'&&len(f"abc {7}"[1:-1])==3)
text:=f"{f"abc {7}"[1:]}";testing.expect(t,text=="bc 7");delete(text)
''')

    def test_escaping_temporary_view_requests_named_owner(self):
        self.invoke(self.program('view:=f"abc {1}"[1:];_=view'),command='check',fail='named owner')

    def test_unknown_procedure_can_return_or_consume_argument(self):
        self.memory('''
a:=set[int]{1};b:=set[int]{2};result:=identity(a|b)
testing.expect(t,len(result)==2);delete(result);delete(a);delete(b)
consume(f"hello {7}")
''','identity::proc(value:set[int])->set[int]{return value}\nconsume::proc(value:string){delete(value)}\n')

    def test_mixed_blank_destinations_release_only_discarded_values(self):
        self.memory('''
_, value := f"unused {1}", 7
_, value = set[int]{8}, value+1
testing.expect(t,value==8)
for i:=0;i<2;_,i=f"post {i}",i+1 {}
''')

    def test_unused_expression_statements_release_owned_results(self):
        self.memory('''
f"unused {1}"
(set[int]{1}|set[int]{2})
defer f"deferred {7}"
''')

    def test_owned_branch_forms_are_lazy_and_keep_result_ownership(self):
        self.memory('''
borrowed:="existing"
for flag in [false,true]{
 text:=f"{switch{case flag:f"owned {7}";case:borrowed}}"
 testing.expect(t,text==(flag ? "owned 7" : borrowed));delete(text)
}
text:=f"{f"chosen {9}" when true else missing()}";delete(text)
text=f"{#switch{case false:missing();case true:f"selected {3}";case:missing()}}"
testing.expect(t,text=="selected 3");delete(text)
text=f"{#switch 2{case 1:missing();case 2:f"value {2}";case:missing()}}";delete(text)
when false {_=f"{missing()}";_=set[int]{missing()}|set[int]{1}}
''')

    def test_selected_set_temporaries_queries_and_loop_reevaluation(self):
        self.memory('''
a:=set[int]{1};defer delete(a)
for flag in [false,true]{
 testing.expect(t,len(flag ? set[int]{1,2} : a)==(flag ? 2 : 1))
 testing.expect(t,1 in (switch{case flag:set[int]{1,2};case:a}))
}
testing.expect(t,len(#switch 2{case 1:missing();case 2:set[int]{1,2};case:missing()})==2)
testing.expect(t,1 in (set[int]{1} when true else missing()))
for i:=0;len(set[int]{i})==1&&i<3;i+=1 {}
''')

    def test_membership_releases_owned_needles_and_container_views(self):
        self.memory('''
names:=make(map[string]int);names["hello 7"]=1;defer delete(names)
testing.expect(t,f"hello {7}" in names)
testing.expect(t,.Red in set[Color]{.Red,.Blue})
testing.expect(t,-1 in (set[int]{-1,2}).table)
_= (set[int]{-1}).table[-1]
''','Color::enum{Red,Blue}\n')

    def test_union_operands_execute_once_in_source_order(self):
        self.memory('''
a:=set[int]{1};b:=set[int]{2};c:=set[int]{3}
order=0
result:=observe(a,1)|observe(b,2)|observe(c,3)
testing.expect(t,order==123&&len(result)==3)
delete(result);delete(a);delete(b);delete(c)
''','order:int\nobserve::proc(value:set[int],n:int)->set[int]{order=order*10+n;return value}\n')

    def test_intermediates_are_released_before_later_fields(self):
        self.memory('''
backing:=context.allocator
tracker:mem.Tracking_Allocator
mem.tracking_allocator_init(&tracker,backing)
context.allocator=mem.tracking_allocator(&tracker)
text:=f"{f"inner {7}"} {active(&tracker)}"
testing.expect(t,text=="inner 7 1")
delete(text);testing.expect(t,len(tracker.allocation_map)==0)
context.allocator=backing;mem.tracking_allocator_destroy(&tracker)
''','import "core:mem"\nactive::proc(tracker:^mem.Tracking_Allocator)->int{return len(tracker.allocation_map)}\n')

    def test_anonymous_string_range_cleanup_and_normal_loop_exit(self):
        self.memory('''
count:=0
for char in f"hello {7}"{count+=1}
testing.expect(t,count==7)
for item in set[int]{} {testing.expect(t,false)}
''')

    def test_temporary_addresses_require_a_named_owner(self):
        for body in ['p:=&f"abc {1}"[-1];_=p', 'p:=raw_data(f"abc {1}");_=p',
                     'table:=(set[int]{1}).table;_=table']:
            with self.subTest(body=body):
                self.invoke(self.program(body),command='check',fail='named owner')

    def test_borrowed_views_of_named_owners_still_work(self):
        self.memory('''
text:=f"hello {7}";defer delete(text)
view:=text[1:];testing.expect(t,view=="ello 7")
p:=raw_data(text);testing.expect(t,p[0]=='h')
''')

    def test_native_generic_xor_stays_contextless(self):
        self.invoke(self.program('assert(bits(u8(1),u8(3))==2)',
                    'bits::proc "contextless"(a,b:$T)->T{x:=a~b;x~=b;x~=b;return x}\n'))

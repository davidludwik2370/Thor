"""Reference anonymous-producer ownership across known expression consumers."""
import unittest
from tests import test_rust_checked_calls as checked


class RustTemporaryParityTests(unittest.TestCase):
    setUpClass=classmethod(checked.RustCheckedCallTests.setUpClass.__func__)
    setUp=checked.RustCheckedCallTests.setUp
    invoke=checked.RustCheckedCallTests.invoke
    compare=checked.RustCheckedCallTests.compare
    program=checked.RustCheckedCallTests.program
    tracked=checked.RustCheckedCallTests.tracked

    def test_nested_set_math_comparisons_queries_and_discard(self):
        self.tracked(self.program('''a:=set[int]{1,2};b:=set[int]{2,3};c:=set[int]{3,4}
result:=(a|b)&(b~c)
if result!=set[int]{2}||len(a|b)!=3||!(3 in (a|b))||!(8 not_in (a|b)){panic("set consumers")}
_=a|b;_=set[int]{7};_=f"discard {1}"
delete(result);delete(a);delete(b);delete(c)
'''))

    def test_unconstrained_generic_set_and_native_operators(self):
        self.tracked(self.program('''a:=set[int]{1};b:=set[int]{2};c:=set[int]{3}
r:=chain(a,b,c);defer delete(r);defer delete(a);defer delete(b);defer delete(c)
if len(r)!=3||chain(int(1),int(2),int(4))!=7{panic("generic chain")}
''','chain::proc(a,b,c:$T)->T{return a|b|c}\n'))

    def test_conditional_owned_and_named_sets_keep_separate_lifetimes(self):
        self.tracked(self.program('''a:=set[int]{1};b:=set[int]{2}
for flag in [false,true]{
 r:=(flag?set[int]{3}:a)|b
 q:=(switch flag{case true:set[int]{4};case:a})|b
 if len(r)!=2||len(q)!=2||len(a)!=1{panic("selected set")}
 delete(r);delete(q)
}
delete(a);delete(b)
'''))

    def test_runtime_and_compile_switch_consumers_are_lazy(self):
        self.tracked(self.program('''a:=set[int]{1};defer delete(a)
for flag in [false,true]{
 if len(flag?set[int]{1,2}:a)!=(flag?2:1){panic("selected query")}
 if !(1 in (switch{case flag:set[int]{1,2};case:a})){panic("selected member")}
}
if len(#switch 2{case 1:missing();case 2:set[int]{1,2};case:missing()})!=2{panic("compile selection")}
if !(1 in (set[int]{1} when true else missing())){panic("when selection")}
when false{_=set[int]{missing()}|set[int]{1}}
'''))

    def test_updates_capture_target_before_temporary_operand(self):
        self.tracked(self.program('''a:=set[int]{1,2};a|=set[int]{3};a&=set[int]{2,3};a-=set[int]{2};a~=set[int]{4}
if a!=set[int]{3,4}{panic("set update")};delete(a)
b:=set[int]{1};defer delete(b);for ;len(b)>0;b-=set[int]{1}{}
'''))

    def test_loops_release_on_break_continue_and_return(self):
        self.tracked(self.program('''count:=0
outer:for item in set[int]{1,2,3}{count+=1;if count==1{continue outer};break outer}
if count!=2||early()!=7{panic("loop exit")}
count=0;for char in f"hello {7}"{count+=1};if count!=7{panic("string loop")}
for item in set[int]{}{panic("empty loop")}
''','early::proc()->int{for item in set[int]{7}{return item};return 0}\n'))

    def test_views_indexing_membership_and_interpolation_release_owners(self):
        self.tracked(self.program('''if len(f"hello {7}")!=7||f"value {7}"!="value 7"||f"value {7}"!=f"value {7}"{panic("string consumer")}
if f"abc {7}"[-1]!='7'||len(f"abc {7}"[1:-1])!=3{panic("view index")}
text:=f"{f"abc {7}"[1:]}";defer delete(text);if text!="bc 7"{panic("view format")}
names:=make(map[string]int);names["hello 7"]=1;defer delete(names)
if !(f"hello {7}" in names)||!(.Red in set[Color]{.Red,.Blue})||!(-1 in (set[int]{-1,2}).table){panic("membership")}
_= (set[int]{-1}).table[-1]
''','Color::enum{Red,Blue}\n'))

    def test_nested_formatting_selects_only_owned_string_cleanup(self):
        self.tracked(self.program('''borrowed:="existing"
for flag in [false,true]{
 text:=f"{(flag?f"value {7}":borrowed)}"
 if text!=(flag?"value 7":borrowed){panic("conditional format")};delete(text)
 text=f"{switch flag{case true:f"other {8}";case:borrowed}}"
 if text!=(flag?"other 8":borrowed){panic("switch format")};delete(text)
}
text:=f"{#switch 2{case 1:missing();case 2:f"value {2}";case:missing()}}";delete(text)
'''))

    def test_mixed_blanks_unused_statements_and_deferred_discard(self):
        self.tracked(self.program('''_,value:=f"unused {1}",7
_,value=set[int]{8},value+1
if value!=8{panic("blank destination")}
for i:=0;i<2;_,i=f"post {i}",i+1{}
f"unused {1}"
(set[int]{1}|set[int]{2})
defer f"deferred {7}"
'''))

    def test_ordinary_calls_transfer_sets_and_manual_strings(self):
        self.tracked(self.program('''a:=set[int]{1};b:=set[int]{2};result:=identity(a|b)
if len(result)!=2{panic("transferred result")};delete(result);delete(a);delete(b)
consume(f!"hello {7}")
''','identity::proc(value:set[int])->set[int]{return value}\nconsume::proc(value:string){delete(value)}\n'))

    def test_operands_execute_once_in_source_order(self):
        self.tracked(self.program('''a:=set[int]{1};b:=set[int]{2};c:=set[int]{3}
order=0;result:=observe(a,1)|observe(b,2)|observe(c,3)
if order!=123||len(result)!=3{panic("operand order")}
delete(result);delete(a);delete(b);delete(c)
''','order:int\nobserve::proc(value:set[int],n:int)->set[int]{order=order*10+n;return value}\n'))

    def test_escaping_views_addresses_and_raw_data_request_named_owner(self):
        for body in ['view:=f"abc {1}"[1:];_=view','p:=&f"abc {1}"[-1];_=p',
                     'p:=raw_data(f"abc {1}");_=p','table:=(set[int]{1}).table;_=table']:
            with self.subTest(body=body):
                self.compare(self.program(body),command='check',fail='named owner')

    def test_type_queries_do_not_execute_temporary_producers(self):
        self.tracked(self.program('''_=size_of(set[int]{step()})
_=size_of(f"{step()}")
if count!=0{panic("eager query")}
''','count:int\nstep::proc()->int{count+=1;return count}\n'))

    def test_exhaustive_enum_selection_keeps_trapping_arms_and_cleanup(self):
        self.tracked(self.program('''borrowed:="named"
for state in [State.Own,State.Borrow]{
 size:=len(switch state{case .Own:f"owned {7}";case .Borrow:borrowed;case .Trap:unreachable()})
 if size!=(state==.Own?7:5){panic("enum temporary")}
}
''','State::enum{Own,Borrow,Trap}\n'))

    def test_loop_conditions_rebuild_and_release_temporary_sets(self):
        self.tracked(self.program('''count:=0
for len(set[int]{count})==1&&count<3{count+=1}
if count!=3{panic("condition temporary")}
'''))

    def test_checked_pure_helpers_keep_temporary_string_consumers(self):
        self.tracked(self.program('n:=7;if work(n)!=7{panic("checked cleanup")}',
            'work::fn(n:int)->int{return len(f"value {n}")}\n'))

    def test_borrowed_field_is_captured_before_width_mutation(self):
        self.tracked(self.program('''original:=f"abc {7}";defer delete(original)
text:=original
result:=f"{text:{replace(&text)}}";defer delete(result)
if result!="abc 7"||text!="changed"{panic("width snapshot")}
''','replace::proc(text:^string)->int{text^="changed";return 5}\n'))


if __name__=='__main__':
    unittest.main()

"""Anonymous f-string arguments retain tuple arity and enclosing lifetimes."""
import unittest
from tests import test_rust_checked_calls as checked


class RustFStringTupleTests(unittest.TestCase):
    setUpClass=classmethod(checked.RustCheckedCallTests.setUpClass.__func__)
    setUp=checked.RustCheckedCallTests.setUp
    invoke=checked.RustCheckedCallTests.invoke
    compare=checked.RustCheckedCallTests.compare
    program=checked.RustCheckedCallTests.program
    tracked=checked.RustCheckedCallTests.tracked

    def test_two_and_three_results_are_returned_before_argument_cleanup(self):
        self.tracked(self.program('''a,b:=pair(f"value {7}")
x,y,z:=triple(f"other {8}")
if a!=7||b!='v'||x!=7||y!='o'||z!='8'{panic("tuple result")}
''','''pair::proc(text:string)->(int,u8){return len(text),text[0]}
triple::proc(text:string)->(int,u8,u8){return len(text),text[0],text[len(text)-1]}
'''))

    def test_optional_ok_and_allocator_error_statuses_stay_optional(self):
        self.tracked(self.program('''a:=read(f"value {7}")
b,ok:=read(f"value {8}")
c:=allocate(f"value {9}")
d,err:=allocate(f"value {0}")
if a!=7||b!=7||!ok||c!=7||d!=7||err!=.None{panic("optional status")}
''','''import "core:mem"
read::proc(text:string)->(int,bool)#optional_ok{return len(text),true}
allocate::proc(text:string)->(int,mem.Allocator_Error)#optional_allocator_error{return len(text),.None}
'''))

    def test_native_sdk_allocator_error_signatures_are_available(self):
        self.tracked(self.program('''a:=strings.clone(f"value {7}");defer delete(a)
b,err:=strings.clone(f"value {8}");defer delete(b)
if a!="value 7"||b!="value 8"||err!=.None{panic("SDK clone")}
''','import "core:strings"\n'))

    def test_early_return_continue_and_fallback_keep_caller_control_flow(self):
        self.tracked(self.program('''if early(){panic("early return")}
count:=0
for _ in 0..<3 {value:=read(f"skip {7}") or_continue;_=value;count+=1}
fallback:=read(f"fallback {8}") or_else 12
if count!=0||fallback!=12{panic("optional control flow")}
''','''read::proc(text:string)->(int,bool){return len(text),false}
early::proc()->bool{value:=read(f"return {8}") or_return;return value>0}
'''))

    def test_switch_arm_borrows_live_until_the_outer_call_finishes(self):
        self.tracked(self.program('''for n in 0..<2 {
check(switch n {case 0:identity(f"value {1}");case:identity(f"value {2}")})
}
check(#switch {case true:identity(f"value {3}");case:missing(f"unused {4}")})
''','''identity::proc(text:string)->string{return text}
check::proc(text:string){if text!="value 1"&&text!="value 2"&&text!="value 3"{panic("borrow lifetime")}}
'''))

    def test_checked_helper_uses_tuple_temporary_cleanup_in_its_scratch_context(self):
        self.tracked(self.program('n:=7;if work(n)!=7{panic("checked tuple")}',
            'read::proc(text:string)->(int,bool)#optional_ok{return len(text),true}\n'
            'work::fn(n:int)->int{value,ok:=read(f"value {n}");return ok?value:0}'))

    def test_pure_owned_tuple_results_survive_input_temporary_cleanup(self):
        self.tracked(self.program('''n:=7
text,count:=pair(f"value {n}");defer delete(text)
if text!="value 7"||count!=7{panic("owned tuple")}
''','pair::fn(text:string)->(string,int){return text,len(text)}\n'))

    def test_named_and_manual_arguments_keep_their_ownership_contracts(self):
        self.tracked(self.program('''named:=f"named {2}";defer delete(named)
a,b:=read(f"auto {1}",named)
c,d:=consume(f!"manual {3}")
if a!=6||b!=7||c!=8||d!=8||named!="named 2"{panic("argument ownership")}
''','''read::proc(a,b:string)->(int,int){return len(a),len(b)}
consume::proc(text:string)->(int,int){n:=len(text);delete(text);return n,n}
'''))

    def test_overloaded_and_indirect_calls_keep_selected_tuple_signatures(self):
        self.tracked(self.program('''a,b:=read(f"value {7}");callback:proc(string)->(int,bool)=text
c,ok:=callback(f"other {8}")
if a!=7||!b||c!=7||!ok{panic("call signature")}
''','''text::proc(value:string)->(int,bool){return len(value),true}
number::proc(value:int)->int{return value}
read::proc{text,number}
'''))

    def test_lazy_and_type_query_arguments_are_not_evaluated(self):
        self.tracked(self.program('''n:=-1
for _ in 0..<2 {if false {a,b:=read(f"unused {step(&n)}");_=a;_=b}}
if size_of(single(f"unused {step(&n)}"))<0{panic("type query")}
if n!=-1{panic("eager argument")}
''','''step::proc(n:^int)->int{n^+=1;return n^}
read::proc(text:string)->(int,bool){return len(text),true}
single::proc(text:string)->int{return len(text)}
'''))


if __name__=='__main__':
    unittest.main()

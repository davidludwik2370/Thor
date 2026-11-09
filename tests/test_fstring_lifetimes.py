"""Expression-local f-string arguments and explicit manual-lifetime literals."""
import unittest
from tests import test_thor as base
from tests import test_hardening as hardening


class FStringLifetimeTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program
    memory=hardening.HardeningTests.memory

    def test_type_conversions_preserve_owned_strings_and_enclosing_call_cleanup(self):
        self.memory('''
named:=string(f"owned {7}");defer delete(named)
returned:=plain(9);defer delete(returned)
casted:=cast(string)f"cast {10}";defer delete(casted)
consume(string(f"borrowed {8}"))
testing.expect(t,named=="owned 7"&&returned=="returned 9"&&casted=="cast 10")
''','''
consume::proc(text:string){if text!="borrowed 8"{panic("converted temporary")}}
plain::proc(n:int)->string{return string(f"returned {n}")}
''')

    def test_fibonacci_print_and_named_numeric_owners_do_not_leak(self):
        self.memory('''
a,b:Integer=0,1
defer delete(a);defer delete(b)
for _ in 0..<20{print(f"{a:,}");a,b=b,a+b}
testing.expect(t,a==6765 && b==10946)
''','import * "thor:basic"\n')

    def test_arbitrary_and_native_functions_release_anonymous_inputs(self):
        self.memory('''
consume(f"hello {7}")
fmt.println(f"native {8}",f`raw {9}`)
''','import "core:fmt"\nconsume::proc(text:string){assert(text=="hello 7")}\n')

    def test_nested_calls_keep_borrowed_string_alive_until_outer_expression_finishes(self):
        self.memory('''
assert(identity(f"value {7}")=="value 7")
assert(length(identity(f"value {8}"))==7)
''','identity::proc(text:string)->string{return text}\nlength::proc(text:string)->int{return len(text)}\n')

    def test_multiple_named_and_literal_arguments_preserve_evaluation_order(self):
        self.memory('''
order:=0
named:=f"kept {9}"
defer delete(named)
check(f"first {step(&order,1)}",named,f"last {step(&order,2)}",end=f"end {step(&order,3)}")
assert(order==123 && named=="kept 9")
''','''step::proc(order:^int,n:int)->int{order^=order^*10+n;return n}
check::proc(a,b,c:string,end:string){assert(a=="first 1" && b=="kept 9" && c=="last 2" && end=="end 3")}
''')

    def test_lazy_mixed_owned_borrowed_and_manual_branches(self):
        self.memory('''
borrowed:="saved"
for flag in [false,true]{
 assert(length(flag?f"value {7}":borrowed)==(flag?7:5))
 assert(length(switch flag{case true:f"other {8}";case:borrowed})==(flag?7:5))
 assert(length(#switch{case true:f"selected {3}";case:missing()})==10)
 assert(!(false && length(f"unused {9}")>0))
}
''','length::proc(text:string)->int{return len(text)}\n')

    def test_manual_literals_transfer_to_consumers_and_named_bindings_are_identical(self):
        self.memory('''
consume(f!"hello {7}")
consume(f!`raw {8}`)
a:=f"ordinary {1}";b:=f!"manual {2}"
defer delete(a);defer delete(b)
check(a);check(b);assert(a=="ordinary 1" && b=="manual 2")
''','consume::proc(text:string){delete(text)}\ncheck::proc(text:string){assert(len(text)>0)}\n')

    def test_manual_literals_can_escape_and_mixed_branches_keep_the_selected_contract(self):
        self.memory('''
store(f!"retained {7}")
assert(saved=="retained 7");delete(saved)
for flag in [false,true]{
 remember(flag?f!"manual {8}":"borrowed")
 assert(saved==(flag?"manual 8":"borrowed"))
 if flag {delete(saved)}
}
''','saved:string\nstore::proc(text:string){saved=text}\nremember::proc(text:string){saved=text}\n')

    def test_one_manual_and_one_automatic_argument_are_cleaned_exactly_once(self):
        self.memory('''
keep_second(f"automatic {1}",f!"manual {2}")
assert(saved=="manual 2");delete(saved)
''','saved:string\nkeep_second::proc(a,b:string){assert(a=="automatic 1");saved=b}\n')

    def test_nested_interpolation_queries_comparisons_and_constructors_do_not_double_free(self):
        self.memory('''
assert(len(f"value {7}")==7)
assert(f"value {7}"=="value 7")
check(f"outer {identity(f"inner {8}")}")
n,ok:=Integer(f"{123}");assert(ok && n==123);delete(n)
r,valid:=Rational(true?f"{22}/13":"bad");assert(valid && r==Rational(22,13));delete(r)
''','identity::proc(text:string)->string{return text}\ncheck::proc(text:string){assert(text=="outer inner 8")}\n')

    def test_loop_conditions_posts_defer_and_early_return_keep_cleanup_scoped(self):
        self.memory('''
for i:=0;length(f"{i}")>0 && i<3;i+=length(f"{i}"){if i==1 {continue}}
defer check(f"deferred {4}")
assert(early()==7)
''','''length::proc(text:string)->int{return len(text)}
check::proc(text:string){assert(text=="deferred 4")}
early::proc()->int{if length(f"early {7}")==7{return 7};return 0}
''')

    def test_generic_indirect_method_and_multiple_result_calls(self):
        self.memory('''
callback:=length
assert(callback(f"indirect {7}")==10)
assert(generic(f"generic {8}")==9)
record:=Record{method}
assert(record->apply(f"method {9}")==8)
a,b:=pair(f"pair {3}");assert(a==6 && b==7)
''','''length::proc(text:string)->int{return len(text)}
generic::proc(value:$T)->int{return len(value)}
Record::struct{apply:proc(self:^Record,text:string)->int}
method::proc(self:^Record,text:string)->int{return len(text)}
pair::proc(text:string)->(int,int){return len(text),len(text)+1}
''')

    def test_anonymous_container_arguments_and_variadic_spreads(self):
        self.memory('''
check([f"array {1}",f!"manual {2}"])
assert(saved=="manual 2");delete(saved)
variadic(**[f"first {3}",f"second {4}"])
''','''saved:string
check::proc(values:[2]string){assert(values[0]=="array 1");saved=values[1]}
variadic::proc(values:..string){assert(values[0]=="first 3" && values[1]=="second 4")}
''')

    def test_fstring_return_values_and_ordinary_factory_results_remain_owned(self):
        self.memory('''
a:=ordinary();b:=manual();defer delete(a);defer delete(b)
consume(ordinary());consume(manual())
assert(a=="ordinary 1" && b=="manual 2")
''','''ordinary::proc()->string{return f"ordinary {1}"}
manual::proc()->string{return f!"manual {2}"}
consume::proc(text:string){delete(text)}
''')

    def test_pure_runtime_and_compile_time_results_keep_independent_storage(self):
        self.memory('''
n:=7
a:=describe(n);b:=#run describe(8);c:=manual()
d:=#run identity(f"direct {9}")
defer delete(a);defer delete(b);defer delete(c)
defer delete(d)
assert(a=="length 7" && b=="length 7" && c=="manual 3")
assert(d=="direct 9")
''','''identity::proc(text:string)->string{return text}
describe::fn(n:int)->string{return f"length {len(identity(f"value {n}"))}"}
manual::fn()->string{return f!"manual {3}"}
''')

    def test_callback_literals_get_their_own_temporary_scope_when_invoked_later(self):
        self.memory('''
register(proc(){check(f"callback {7}")},f"registration {8}")
callback()
''','''callback:proc()
register::proc(body:proc(),text:string){assert(text=="registration 8");callback=body}
check::proc(text:string){assert(text=="callback 7")}
''')

    def test_optional_early_exit_cleans_arguments_before_return_or_continue(self):
        self.memory('''
assert(!early())
count:=0
for _ in 0..<3 {
 value:=read(f"skip {7}") or_continue
 _=value
 count+=1
}
assert(count==0)
''','''read::proc(text:string)->(int,bool){return len(text),false}
early::proc()->bool{value:=read(f"return {8}") or_return;return value>0}
''')

    def test_type_queries_do_not_evaluate_fstring_arguments(self):
        self.memory('''
assert(type_of(length(f"unused {7}"))==int)
assert(size_of(f"unused {8}")==size_of(string))
''','length::proc(text:string)->int{return len(text)}\n')

    def test_manual_fstrings_opt_out_of_existing_inner_format_cleanup(self):
        self.memory('''
text:=f"outer {remember(f!"inner {7}")}"
assert(saved=="inner 7" && text=="outer inner 7")
delete(saved);delete(text)
''','saved:string\nremember::proc(text:string)->string{saved=text;return text}\n')

    def test_parenthesized_arguments_are_registered_once(self):
        self.memory('''
assert(length(((f"hello {7}")))==7)
consume((f!"manual {8}"))
''','length::proc(text:string)->int{return len(text)}\nconsume::proc(text:string){delete(text)}\n')

    def test_native_optional_ok_and_allocator_error_results_remain_optional(self):
        self.memory('''
a:=read(f"value {7}")
b,ok:=read(f"value {8}")
c:=allocate(f"value {9}")
d,err:=allocate(f"value {0}")
assert(a==7 && b==7 && ok && c==7 && d==7 && err==.None)
''','''import "core:mem"
read::proc(text:string)->(int,bool)#optional_ok{return len(text),true}
allocate::proc(text:string)->(int,mem.Allocator_Error)#optional_allocator_error{return len(text),.None}
''')

    def test_manual_arguments_transfer_to_retaining_container_apis(self):
        self.memory('''
messages:[dynamic]string
append(&messages,f!"message {7}")
assert(messages[0]=="message 7")
for text in messages {delete(text)}
delete(messages)
names:set[string]
sets.insert(&names,f!"name {8}")
assert("name 8" in names)
for text in names {delete(text)}
delete(names)
''','import "thor:sets"\n')

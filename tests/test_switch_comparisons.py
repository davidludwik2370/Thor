"""Relational case syntax retains native comparisons and switch control flow."""
import unittest
from tests import test_thor as base
from tests.test_rust_compiler import UNCHECKED


class SwitchComparisonTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def both(self, body, declarations='', extra_flags=(), **kwargs):
        for flags in (['--comptime=required'], ['--comptime=required', *UNCHECKED]):
            with self.subTest(flags=flags):
                self.invoke(self.program(body, declarations), flags=[*flags, *extra_flags], **kwargs)

    def test_all_comparisons_boundaries_and_first_match(self):
        self.both('''
for n in [-100, -1, 0, 9, 10, 11, 20, 21, 100] {
 got := classify(n)
 expected := n < 0 ? -1 : n <= 10 ? 1 : n != 20 ? 2 : 3
 if got != expected {panic("comparison")}
}
a := switch 5 {case < 10:1;case < 20:2;case:3}
b := switch 100 {case > 100:1;case >= 100:2;case:3}
if a != 1 || b != 2 {panic("first match or boundary")}
''', 'classify::proc(n:int)->int{return switch n {case < 0:-1;case <= 10:1;case != 20:2;case:3}}\n')

    def test_selector_once_lazy_bounds_groups_and_default_order(self):
        self.both('''
switch mark(2) {
case: panic("default")
case < mark(1), >= mark(2), != mark(9):
 if order!=212 {panic("ordered statement")}
case > mark(8):panic("later")
}
order=0
v:=switch mark(2){case < mark(1), 2:mark(3);case > mark(8):mark(9);case:mark(7)}
if v!=3 || order!=213 {panic("ordered expression")}
''', 'order:int\nmark::proc(n:int)->int{order=order*10+n;return n}\n')

    def test_initializers_labels_break_continue_fallthrough_and_scopes(self):
        self.both('''
total:=0
outer:for i in 0..<4 {
 inner:switch n:=i+1; n {
 case < 2:continue outer
 case <= 3: total+=n;break inner
 case > 3: total+=1;fallthrough
 case: total+=10
 }
}
if total!=16 {panic("control flow")}
switch v:=3; v {case < 4: local:=v;total+=local;case:local:=100;total+=local}
if total!=19 {panic("initializer scope")}
switch total{case >= 0:fallthrough}
if total!=19 {panic("final fallthrough")}
''')

    def test_ordinary_ranges_and_multiple_results_and_break(self):
        self.both('''
a,b:=pair(3)
if a!=3 || b!="range" {panic("range result")}
a,b=switch 2 {case < 0:0,"negative";case 1..<4:4,"changed";case:break}
if a!=4 || b!="changed" {panic("assignment")}
a,b=switch 5 {case > 10:1,"bad";case:break}
if a!=4 || b!="changed" {panic("break")}
''', 'pair::proc(n:int)->(int,string){return switch n{case < 0:-1,"negative";case 1..<5:n,"range";case:0,"other"}}\n')

    def test_fn_run_and_compile_switch_lazy_inactive_branches(self):
        self.both('''
if A!=7 || B!=8 || choose(5)!=25 {panic("comptime")}
#switch 4 {case != 4:undefined();case <= 4:local::3;case:undefined()}
if local!=3 {panic("selected scope")}
value::#switch 4 {case < 0:undefined;case >= 4:9;case:undefined}
if value!=9 {panic("local constant")}
''', '''choose::fn(n:int)->int{return switch n{case < 0:-1;case >= 0:n*n;case:0}}
A::#switch 3{case < 2:undefined;case >= 3:7;case:undefined}
B::#run choose(-2)+9
''')

    def test_generic_enum_and_contextual_operands(self):
        self.both('''
if choose(3)!=1 || choose(f32(20))!=2 {panic("generic")}
if color(.Red)!=1 || color(.Blue)!=2 {panic("enum")}
x:Color=.Red
#partial switch x {case != .Red:panic("partial");case .Red:}
v:=#partial switch x {case != .Red:1;case:2}
if v!=2 {panic("partial expression")}
''', '''Color::enum{Red,Blue}
choose::proc(n:$T)->int{return switch n{case < 10:1;case >= 10:2;case:3}}
color::proc(n:Color)->int{return switch n{case .Red:1;case .Blue:2;case != .Red:3}}
''')

    def test_enum_statement_returns_and_partial_fallthrough(self):
        self.both('''
if choose(.Red)!=1 || generic(Color.Blue)!=2 {panic("termination")}
x:Color=.Red
#partial switch x {case != .Red:panic("unselected")}
x=Color(99)
switch x {case .Red:panic("fabricated");case .Blue:panic("fabricated");case < .Red:panic("fabricated")}
''', '''Color::enum{Red,Blue}
choose::proc(n:Color)->int{switch n{case .Red:return 1;case .Blue:return 2;case != .Red:return 3};return 4}
generic::proc(n:$T)->int{x:=n;switch x{case .Red:return 1;case .Blue:return 2;case != .Red:return 3};return 4}
''', extra_flags=['-vet-cast'])

    def test_all_comptime_modes_and_type_switching_at_compile_time(self):
        source=self.program('''
if classify(4)!=16 || X!=16 || typed(int)!=7 || typed(f64)!=8 {panic("mode")}
''', '''classify::fn(n:int)->int{return switch n{case < 0:-1;case >= 0:n*n;case:0}}
typed::proc($T:typeid)->int{return #switch T{case != int:8;case int:7;case:0}}
X::#run classify(4)
''')
        for mode in ['required','normal','aggressive','override']:
            with self.subTest(mode=mode):self.invoke(source,flags=['--comptime='+mode])

    def test_floats_nan_infinity_strings_and_pointer_inequality(self):
        self.both('''
nan:=math.nan_f64()
v:=switch nan {case < 0:1;case >= 0:2;case != nan:3;case:4}
if v!=3 {panic("nan")}
inf:=math.INF_F64
v=switch inf {case > 0:1;case:2}
if v!=1 {panic("infinity")}
text:="abc"
v=switch text{case < "a":1;case >= "abc":2;case:3}
if v!=2 {panic("string")}
x:=1;p:=&x
v=switch p{case != nil:1;case:0}
if v!=1 {panic("pointer")}
lo:=min(i64);hi:=max(i64)
if (switch lo{case < 0:1;case:0})!=1 || (switch hi{case >= 0:1;case:0})!=1 {panic("boundaries")}
''', 'import "core:math"\n')

    def test_comprehensions_fstrings_and_scoped_arms(self):
        self.both('''
a~ := [switch x{case < 2:x;case >= 2:x*2;case:0} for x in 0..<4]
if len(a)!=4 || a[3]!=6 {panic("comprehension")}
s:=f"{switch 3{case < 4:7;case:0}}"
defer delete(s)
if s!="7" {panic("fstring")}
switch 2 {case < 3:
 values~:=make([]int,2)
 values[0]=9
 if values[0]!=9{panic("scoped")}
case:panic("arm")}
''')

    def test_big_values_own_comparison_temporaries_and_compile_time_selection(self):
        self.both('''
a:=Integer(10)
defer delete(a)
v:=switch a {case < 3:1;case >= 10:2;case:3}
if v!=2 {panic("integer")}
r:=Rational(1,3)
defer delete(r)
v=switch r {case <= Rational(1,4):1;case != Rational(1,2):2;case:3}
if v!=2 {panic("rational")}
switch a {case < 0:panic("sign");case >= 10:v=7;case:v=0}
if v!=7 {panic("integer statement")}
C::#switch Integer(999999999999999999999999) {case < 0:undefined;case >= 999999999999999999999999:7;case:undefined}
if C!=7 {panic("big constant")}
''', 'import "thor:big"\n', extra_flags=['--track-allocations=basic'])

    def test_imported_distinct_types_and_nested_lazy_context(self):
        source=self.program('''
n:=model.Number(5)
a:=switch n {case < 0:1;case >= 4:2;case:3}
b:=false ? (switch n{case < fail():1;case:0}) : 7
if a!=2 || b!=7 {panic("imports")}
v:=switch 1{case < 2:switch 3{case < 4:9;case:0};case:0}
if v!=9 {panic("nested")}
''', 'import "model"\nfail::proc()->model.Number{panic("lazy")}\n')
        self.invoke(source,files={'model/model.thor':'package model\nNumber::distinct int\n'},flags=['--comptime=required',*UNCHECKED])

    def test_invalid_prefix_contexts_types_defaults_and_duplicates(self):
        for body,error in [
            ('switch {case < 10:}', 'require a value switch selector'),
            ('x:=switch {case != 10:1;case:0};_=x', 'require a value switch selector'),
            ('x:any=1;switch x in x {case != int:}', 'require a value switch selector'),
            ('switch 2{case == 2:}', "Use 'case value:'"),
            ('switch 2{case < "bad":}', 'Cannot convert'),
            ('switch true{case < false:}', 'not defined'),
            ('x:=switch 2{case < 3:1};_=x', 'requires a default'),
            ('switch 2{case < 0:case 2:case 2:}', 'Duplicate'),
            ('x:=switch 2{case < 0:1;case 2:2;case 2:3;case:4};_=x', 'Duplicate'),
            ('x:Color=.Red;switch x{case != .Red:}', 'Unhandled'),
            ('switch 2{case < 3:case:case:}', 'default'),
            ('switch Integer(2){case < 0:case 2:case 2:}', 'Duplicate'),
            ('x:=f(2);_=x', 'Missing return'),
        ]:
            with self.subTest(body=body):
                self.invoke(self.program(body, 'Color::enum{Red,Blue}\nf::proc(n:int)->int{switch n{case < 0:return 1;case >= 0:return 2}}\n' if body=='x:=f(2);_=x' else 'Color::enum{Red,Blue}\n'), command='check', fail=error)

    def test_native_vet_and_no_alloc(self):
        self.both('''
v:=choose(3)
if v!=7 {panic("no alloc")}
''', '''@(no_alloc)
choose::proc(n:int)->int{switch n{case < 0:return 0;case >= 0:return 7;case:return 9}}
''')
        self.invoke(self.program('x:=3;switch x{case < 2:case >= 2:}', ''), command='check', flags=['-vet', '-warnings-as-errors'])
        self.invoke(self.program('#switch 2{case < 0:undefined();case >= 0:x::3;case:undefined()};if x!=3{panic("native compile switch")}', ''), command='check', flags=['-vet-cast'])
        self.invoke(self.program('#switch 2{case < 0:undefined();case >= 0:p:^int;_=p^;case:undefined()}', ''), command='check', flags=['-vet-nil-deref'], fail='always nil')
        self.invoke(self.program('p:^int;switch 3{case < p^:case:}', ''), command='check', flags=['-vet-nil-deref'], fail='always nil')


if __name__=='__main__':unittest.main()

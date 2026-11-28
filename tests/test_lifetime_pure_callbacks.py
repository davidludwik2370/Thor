"""Lifetime vet respects checked pure callback types without hiding body errors."""
import unittest

from tests import test_lifetime_vet as base
from tests import test_rust_compiler as native


class PureCallbackLifetimeTests(unittest.TestCase):
    setUpClass = classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp = base.LifetimeVetTests.setUp
    invoke = base.LifetimeVetTests.invoke
    vet = base.LifetimeVetTests.vet
    codes = base.LifetimeVetTests.codes
    compare = native.RustCompilerTests.compare

    def test_unresolved_pure_callback_has_no_global_reference_effects(self):
        for outer in ('fn', 'proc'):
            for flags in ([], ['-disable-assert'], native.UNCHECKED):
                with self.subTest(outer=outer, flags=flags):
                    codes = self.codes('''apply :: '''+outer+'''(val: int, func: fn(x: int) -> int) -> int {
    return func(val)
}
main :: proc() {}
''', flags=['--unverified', *flags], command='build')
                    self.assertEqual(codes, [])

    def test_runtime_aliases_record_fields_and_generic_callbacks(self):
        codes = self.codes('''Callback :: fn(int) -> int
Alias :: Callback
Box :: struct { callback: Alias }
square :: fn(x: int) -> int { x*x }
triple :: fn(x: int) -> int { x*3 }
apply :: fn(x: $T, callback: fn(T) -> T) -> T { callback(x) }
indirect :: proc(x: int, box: Box) -> int { return box.callback(x) }
through_slice :: proc(x: int, callbacks: []Alias) -> int { return callbacks[0](x) }
ANSWER :: #run apply(4, square)
main :: proc() {
    callback: Alias = square
    if len(os.args) > 1 { callback = triple }
    copy := callback
    box := Box{copy}
    callbacks := [1]Alias{copy}
    _ = apply(4, copy)
    _ = indirect(4, box)
    _ = through_slice(4, callbacks[:])
    _ = ANSWER
}
''', prefix='import "core:os"', flags=['--unverified'])
        self.assertEqual(codes, [])

    def test_unrestricted_runtime_proc_still_requires_global_effect_review(self):
        findings = self.vet('''apply :: proc(val: int, callback: proc(int) -> int) -> int {
    return callback(val)
}
main :: proc() {}
''', flags=['--unverified'])
        self.assertTrue(any(finding.get('code') == 'thor/lifetime-unknown'
                            and 'global reference state' in str(finding) for finding in findings), findings)

    def test_known_pure_callback_body_is_still_checked_for_lifetime_errors(self):
        codes = self.codes('''bad :: fn(x: int) -> int {
    values := make([]int, 1)
    delete(values)
    return values[0] + x
}
apply :: fn(x: int, callback: fn(int) -> int) -> int { callback(x) }
main :: proc() { _ = apply(1, bad) }
''', flags=['--unverified'])
        self.assertIn('thor/lifetime', codes)

    def test_known_pure_reader_still_checks_its_borrowed_arguments(self):
        codes = self.codes('''read :: fn(values: []int) -> int { values[0] }
main :: proc() {
    callback: fn([]int) -> int = read
    values := make([]int, 1)
    delete(values)
    _ = callback(values)
}
''', flags=['--unverified'])
        self.assertIn('thor/lifetime', codes)

    def test_opaque_pure_readers_preserve_live_input_aliases(self):
        code = '''Reader :: fn([]int) -> int
Alias :: Reader
apply :: fn(values: []int, callback: Alias) -> int { callback(values) }
deep :: fn(values: [][]int, callback: fn([][]int)->int) -> int { callback(values) }
read :: fn(values: []int) -> int { values[0] }
main :: proc() { values~:=make([]int,2); _=apply(values,read); values[0]=3 }
'''
        self.assertEqual(self.codes(code, flags=['--unverified']), [])

    def test_opaque_pure_results_have_independent_cleanup(self):
        code = '''Copy :: fn([]int) -> []int
Pair :: struct { first, second: []int }
apply :: fn(values: []int, callback: Copy) -> []int { callback(values) }
pair :: fn(callback: fn()->([]int, []int)) -> ([]int, []int) { callback() }
record :: fn(callback: fn()->Pair) -> Pair { callback() }
text :: fn(value:string, callback:fn(string)->string)->string { callback(value) }
copy :: fn(values: []int) -> []int { slice.clone(values) }
generic :: fn(values: []$T, callback: fn([]T)->[]T) -> []T { callback(values) }
main :: proc() {
    values:=[2,3]
    out~:=apply(values[:],copy); _=out[0]; values[0]=4
    other~:=generic(callback=copy,values=values[:]); _=other[0]
    computed~:=#run generic([]int{4,5},copy); _=computed[0]
}
'''
        self.assertEqual(self.codes(code, prefix='import "core:slice"', flags=['--unverified']), [])

    def test_runtime_selected_callbacks_check_each_body_and_owned_results(self):
        code = '''copy :: fn(values: []int) -> []int { slice.clone(values) }
other :: fn(values: []int) -> []int { slice.clone(values) }
main :: proc() {
    callback: fn([]int)->[]int = copy
    if len(os.args)>1 { callback=other }
    values:=[2,3]
    out~:=callback(values[:])
    _=out[0]
}
'''
        prefix='import "core:slice"\nimport "core:os"'
        self.assertEqual(self.codes(code, prefix=prefix, flags=['--unverified']), [])
        bad=code.replace('slice.clone(values) }\nmain',
                         'out:=slice.clone(values); delete(out); return out }\nmain')
        self.assertIn('thor/lifetime', self.codes(bad, prefix=prefix, flags=['--unverified']))

    def test_pure_promotion_duplicates_returned_aliases(self):
        code = '''Pair :: struct { first, second: []int }
pair :: fn()->([]int,[]int) { values:=make([]int,2); return values,values }
record :: fn()->Pair { values:=make([]int,2); return {values,values} }
main :: proc() {
    a,b:=pair(); defer delete(a); defer delete(b)
    a[0]=7
    if b[0]!=0 { panic("aliased pure results") }
    p:=record(); defer delete(p.first); defer delete(p.second)
    p.first[0]=7
    if p.second[0]!=0 { panic("aliased pure fields") }
}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.assertEqual(self.codes(code, flags=['--unverified', *flags], command='build'), [])
                self.compare('package main\n'+code, flags=['--comptime=required', *flags])

    def test_return_promotion_checks_conditional_and_deferred_release(self):
        for release in ('if flag { delete(values) }', 'defer delete(values)'):
            with self.subTest(release=release):
                findings=self.vet('bad::fn(flag:bool)->[]int { values:=make([]int,2); '+release+
                                  ';return values }\nmain::proc(){}', flags=['--unverified'])
                self.assertTrue(any(f.get('code') in ('thor/lifetime', 'thor/lifetime-unknown') for f in findings), findings)

    def test_caller_aliases_and_opaque_proc_effects_remain_checked(self):
        code='''main::proc(){ values:=make([]int,2); alias:=values; delete(values); delete(alias) }'''
        self.assertIn('thor/lifetime', self.codes(code, flags=['--unverified']))
        code='''apply::proc(values:[]int,callback:proc([]int)->int)->int{return callback(values)}
main::proc(){}'''
        self.assertIn('thor/lifetime-unknown', self.codes(code, flags=['--unverified']))

    def test_nil_callbacks_are_errors_and_non_nil_guards_are_respected(self):
        for declaration in ('callback:fn(int)->int', 'callback:fn(int)->int=nil'):
            with self.subTest(declaration=declaration):
                findings=self.vet('main::proc(){'+declaration+';alias:=callback;_=alias(1)}', flags=['--unverified'])
                self.assertTrue(any(f.get('code')=='thor/lifetime' and 'nil procedure' in str(f) for f in findings), findings)
        code='''square::fn(x:int)->int{x*x}
main::proc(){
    callback:fn(int)->int
    if len(os.args)>1 {callback=square}
    if callback!=nil {_=callback(1)}
}'''
        self.assertEqual(self.codes(code, prefix='import "core:os"', flags=['--unverified']), [])
        findings=self.vet(code.replace('if callback!=nil {_=callback(1)}','_=callback(1)'),
                          prefix='import "core:os"', flags=['--unverified'])
        self.assertTrue(any('may be nil' in str(f) for f in findings), findings)
        reversed=code.replace('callback:fn(int)->int\n', 'callback:fn(int)->int=square\n').replace('callback=square}', 'callback=nil}')
        self.assertEqual(self.codes(reversed, prefix='import "core:os"', flags=['--unverified']), [])

    def test_constant_callback_defaults_use_the_declared_signature(self):
        code='''apply::fn(callback:fn(value:int=7)->int)->int{callback()}
square::fn(x:int)->int{x*x}
main::proc(){_=apply(callback=square)}'''
        self.assertEqual(self.codes(code, flags=['--unverified']), [])

    def test_opaque_owned_results_support_conditional_nil_cleanup(self):
        for cleanup in ('if out!=nil{delete(out)}', 'if out==nil{return};delete(out)'):
            with self.subTest(cleanup=cleanup):
                code='apply::proc(callback:fn()->[]int){out:=callback();'+cleanup+'}\nmain::proc(){}'
                self.assertEqual(self.codes(code, flags=['--unverified']), [])
        code='apply::proc(callback:fn()->[]int){out:=callback();_=out}\nmain::proc(){}'
        self.assertIn('thor/leak-unknown',self.codes(code,flags=['--unverified']))

    def test_selected_return_owner_keeps_possible_release_uncertain(self):
        code='''bad::fn(flag:bool)->[]int{
    first:=make([]int,1); second:=make([]int,1)
    delete(first)
    return flag ? first : second
}
main::proc(){}'''
        codes=self.codes(code,flags=['--unverified'])
        self.assertIn('thor/lifetime-unknown',codes)
        self.assertNotIn('thor/lifetime',codes)

    def test_pure_result_padding_does_not_create_caller_owners(self):
        code='''Row::struct{visible:int,_:[]int}
make_row::fn()->Row{Row{1,make([]int,1)}}
main::proc(){r:=make_row();if r.visible!=1{panic("field value")}}'''
        self.assertEqual(self.codes(code,flags=['--unverified']), [])
        for flags in ([],native.UNCHECKED):
            self.compare('package main\n'+code,flags=['--comptime=required',*flags])

    def test_opaque_fixed_array_and_dynamic_results_have_cleanup(self):
        code='''arrays::proc(callback:fn()->[2][]int){
    out:=callback();delete(out[0]);delete(out[1])
}
growable::proc(callback:fn()->[dynamic]int){out~:=callback();_=out}
main::proc(){}'''
        self.assertEqual(self.codes(code,flags=['--unverified']), [])
        findings=self.codes(code.replace('delete(out[1])','_=out[1]'),flags=['--unverified'])
        self.assertIn('thor/leak-unknown',findings)

    def test_unsupported_opaque_result_shapes_stay_unverified(self):
        code='''apply::proc(callback:fn()->[][]int){out:=callback();_=out}
main::proc(){}'''
        findings=self.vet(code,flags=['--unverified'])
        self.assertEqual(set(f['code'] for f in findings),
                         {'thor/lifetime-unknown','thor/leak-unknown'})
        self.assertTrue(any('ownership shape' in str(f) for f in findings),findings)

    def test_budget_exhaustion_preserves_constant_procedure_identity(self):
        # Never execute this intentionally over-budget allocation graph.
        items=','.join('make([]int,1)' for _ in range(540))
        code='view::proc(values:[][]int)->[][]int{return values}\n' \
             'main::proc(){out:=view([][]int{'+items+'});_=out[0][0]}'
        findings=self.vet(code,flags=['--unverified','--no-cache'])
        self.assertTrue(findings)
        self.assertEqual({f.get('reason') for f in findings},{'analysis-limit'},findings)


if __name__ == '__main__':
    unittest.main()

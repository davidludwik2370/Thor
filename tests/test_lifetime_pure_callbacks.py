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


if __name__ == '__main__':
    unittest.main()

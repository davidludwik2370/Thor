"""Pure-procedure advice is independent of whether the procedure is used."""
import unittest
from tests import test_vet_declarations as declarations
from tests import test_rust_compiler as native


SOURCE = '''package main
Board :: [9]int
Dir :: enum {over, under}
find_best_guess_counting :: proc(board: Board) -> (best_guess_index: int, dir: Dir) {
    midpoint :> 7.0
    best_guess_dist := -1.0
    for top_card, i in board {
        if top_card == 0 do continue
        diff :> midpoint - f64(top_card)
        guess_dist :> abs(diff)
        if guess_dist > best_guess_dist {
            best_guess_index = i
            best_guess_dist = guess_dist
            dir = (diff > 0) ? .over : .under
        }
    }
    assert(best_guess_index >= 0)
    assert(board[best_guess_index] > 0)
    return
}
main :: proc() {}
'''


class PureUnusedVetTests(unittest.TestCase):
    setUpClass = declarations.VetDeclarationTests.__dict__['setUpClass']
    setUp = declarations.VetDeclarationTests.setUp
    write = declarations.VetDeclarationTests.write
    invoke = declarations.VetDeclarationTests.invoke

    def test_named_results_enum_array_and_builtin_reader_get_both_warnings(self):
        for engine in ('python','rust'):
            for flags in ([], ['--vet=pure,unused']):
                with self.subTest(engine=engine,flags=flags):
                    self.write(SOURCE)
                    _, errors = self.invoke(engine,*flags)
                    self.assertEqual([error['code'] for error in errors],
                                     ['thor/unused-procedure','thor/prefer-fn'])
                    self.assertTrue(all('find_best_guess_counting' in error['msgs'][0] for error in errors))
                    self.assertTrue('fix' not in errors[0] and 'fix' in errors[1])
                    self.assertEqual(self.source.read_text(),SOURCE)

    def test_apply_converts_the_unused_procedure_and_retains_the_unused_warning(self):
        for engine in ('python','rust'):
            self.write(SOURCE)
            _, errors = self.invoke(engine,'--apply')
            expected = SOURCE.replace('find_best_guess_counting :: proc','find_best_guess_counting :: fn')
            self.assertEqual(self.source.read_text(),expected)
            self.assertEqual([error['code'] for error in errors], ['thor/unused-procedure'])
            _, errors = self.invoke(engine)
            self.assertEqual([error['code'] for error in errors], ['thor/unused-procedure'])
            # Exercise the converted named-result procedure at runtime and #run.
            runtime = expected.replace('package main\n','package main\nimport "core:os"\n')
            runtime = runtime.replace('main :: proc() {}','''
verify::fn()->bool {
    index, direction := find_best_guess_counting(Board{1,2,3,4,5,6,7,8,9})
    return index==0 && direction==.over
}
COMPTIME_RESULT :: #run verify()
main::proc(){
    board:Board={1,2,3,4,5,6,7,8,9}
    board[0]=len(os.args)
    index, direction := find_best_guess_counting(board)
    if index!=0 || direction!=.over || !COMPTIME_RESULT {panic("converted fn")}
}
''')
            self.write(runtime)
            for flags in (['-debug'], native.UNCHECKED):
                _, result = self.invoke(engine,*flags,command='run')
                self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()

"""Reviewed native text calls borrow; window titles retain until reset/close."""
import unittest
from tests import test_lifetime_vet as base

class LifetimeForeignContractTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp=base.LifetimeVetTests.setUp
    invoke=base.LifetimeVetTests.invoke
    vet=base.LifetimeVetTests.vet
    codes=base.LifetimeVetTests.codes

    def problem(self,codes):self.assertTrue(any(c.startswith('thor/lifetime') for c in codes),codes)

    def test_text_readers_and_aliases_do_not_retain(self):
        codes=self.codes('main::proc(){text:=strings.clone_to_cstring("hello");measure::rl.MeasureText;draw::rl.DrawText;_=measure(text,12);draw(text,0,0,12,rl.WHITE);delete(text)}',prefix='import rl "vendor:raylib"\nimport "core:strings"')
        self.assertEqual(codes,[])

    def test_freed_text_is_still_an_error_for_each_reader(self):
        for call in ['_=rl.MeasureText(text,12)','rl.DrawText(text,0,0,12,rl.WHITE)']:
            with self.subTest(call=call):
                self.problem(self.codes('main::proc(){text:=strings.clone_to_cstring("hello");delete(text);'+call+'}',prefix='import rl "vendor:raylib"\nimport "core:strings"'))

    def test_window_title_borrow_is_released_by_close(self):
        codes=self.codes('main::proc(){text:=strings.clone_to_cstring("hello");rl.InitWindow(100,100,text);rl.CloseWindow();delete(text)}',prefix='import rl "vendor:raylib"\nimport "core:strings"')
        self.assertEqual(codes,[])

    def test_window_title_replacement_releases_the_previous_borrow(self):
        codes=self.codes('main::proc(){text:=strings.clone_to_cstring("hello");rl.InitWindow(100,100,"first");rl.SetWindowTitle(text);rl.SetWindowTitle("static");delete(text);rl.CloseWindow()}',prefix='import rl "vendor:raylib"\nimport "core:strings"')
        self.assertEqual(codes,[])

    def test_retained_title_cannot_outlive_a_local_buffer(self):
        codes=self.codes('open::proc(){buf:[32]byte;buf[0]=\'a\';rl.InitWindow(100,100,cstring(&buf[0]))}\nmain::proc(){open()}',prefix='import rl "vendor:raylib"')
        self.problem(codes)

    def test_retained_title_cannot_be_released_while_still_exported(self):
        codes=self.codes('main::proc(){text:=strings.clone_to_cstring("hello");rl.InitWindow(100,100,"first");rl.SetWindowTitle(text);delete(text)}',prefix='import rl "vendor:raylib"\nimport "core:strings"')
        self.problem(codes)

    def test_empty_init_title_still_reads_the_previous_retained_pointer(self):
        codes=self.codes('main::proc(){text:=strings.clone_to_cstring("hello");rl.InitWindow(100,100,"first");rl.SetWindowTitle(text);delete(text);rl.InitWindow(100,100,"");rl.CloseWindow()}',prefix='import rl "vendor:raylib"\nimport "core:strings"')
        self.problem(codes)

    def test_registered_foreign_callbacks_keep_the_review_boundary(self):
        codes=self.codes('work::proc(callback:rl.TraceLogCallback){rl.SetTraceLogCallback(callback);_=rl.MeasureText("hello",12)}\nmain::proc(){}',prefix='import rl "vendor:raylib"')
        self.assertIn('thor/lifetime-unknown',codes)
        self.assertEqual(self.codes('main::proc(){rl.SetTraceLogCallback(nil);_=rl.MeasureText("hello",12)}',prefix='import rl "vendor:raylib"'),[])
        self.assertEqual(self.codes('work::proc(callback:rl.TraceLogCallback){when false{rl.SetTraceLogCallback(callback)};_=rl.MeasureText("hello",12)}\nmain::proc(){}',prefix='import rl "vendor:raylib"'),[])

    def test_same_named_local_proc_does_not_gain_a_foreign_contract(self):
        codes=self.codes('saved:[]int\nDrawText::proc(v:[]int){saved=v}\nmain::proc(){v~:=make([]int,2);DrawText(v)}')
        self.assertIn('thor/lifetime',codes)

if __name__=='__main__':unittest.main()

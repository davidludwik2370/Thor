"""Growing builders and scalar/random readers keep their real ownership edges."""
import unittest
from tests import test_lifetime_vet as base

class LifetimeLibraryExtensionTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp=base.LifetimeVetTests.setUp
    invoke=base.LifetimeVetTests.invoke
    vet=base.LifetimeVetTests.vet
    codes=base.LifetimeVetTests.codes

    def problem(self,codes):self.assertTrue(any(c in {'thor/lifetime','thor/lifetime-unknown'} for c in codes),codes)

    def test_growing_builder_transfers_backing_to_returned_string(self):
        codes=self.codes('text::proc()->string{b:strings.Builder;strings.write_rune(&b,\'a\');strings.write_string(&b,"bc");return strings.to_string(b)}\nmain::proc(){s:=text();_=s[0];delete(s)}',prefix='import "core:strings"')
        self.assertEqual(codes,[])

    def test_growing_builder_destroy_and_borrowed_view_lifetimes(self):
        self.assertEqual(self.codes('main::proc(){b:strings.Builder;strings.write_rune(&b,\'a\');s:=strings.to_string(b);_=s[0];strings.builder_destroy(&b)}',prefix='import "core:strings"'),[])
        self.problem(self.codes('main::proc(){b:strings.Builder;strings.write_rune(&b,\'a\');s:=strings.to_string(b);strings.builder_destroy(&b);_=s[0]}',prefix='import "core:strings"'))

    def test_growing_builder_lost_buffer_is_a_leak(self):
        codes=self.codes('main::proc(){b:strings.Builder;strings.write_rune(&b,\'a\')}',prefix='import "core:strings"')
        self.assertIn('thor/leak-unknown',codes)

    def test_builder_nil_branch_cannot_hide_a_dropped_buffer(self):
        codes=self.codes('flag:bool\nmain::proc(){b:strings.Builder;strings.write_rune(&b,\'a\');if flag{b.buf=nil};strings.write_rune(&b,\'b\');strings.builder_destroy(&b)}',prefix='import "core:strings"')
        self.assertTrue(any(c.startswith('thor/leak') for c in codes),codes)

    def test_builder_in_loop_transfers_its_header_on_empty_and_nonempty_paths(self):
        self.assertEqual(self.codes('text::proc(n:int)->string{b:strings.Builder;for i in 0..<n{strings.write_rune(&b,\'a\')};return strings.to_string(b)}\nmain::proc(){s:=text(2);delete(s)}',prefix='import "core:strings"'),[])

    def test_growing_builder_view_and_header_alias_prevent_growth_proof(self):
        for alias in ['s:=strings.to_string(b);_=s','other:=b;_=other']:
            with self.subTest(alias=alias):
                codes=self.codes('main::proc(){b:strings.Builder;strings.write_rune(&b,\'a\');'+alias+';strings.write_rune(&b,\'b\');strings.builder_destroy(&b)}',prefix='import "core:strings"')
                self.assertIn('thor/lifetime-unknown',codes)

    def test_fixed_buffer_destroy_does_not_free_the_borrowed_storage(self):
        codes=self.codes('main::proc(){buf:[8]byte;b:=strings.builder_from_bytes(buf[:]);strings.write_rune(&b,\'a\');s:=strings.to_string(b);strings.builder_destroy(&b);_=s[0]}',prefix='import "core:strings"')
        self.assertEqual(codes,[])

    def test_builder_constructor_uses_selected_allocator(self):
        self.assertEqual(self.codes('main::proc(){b:=strings.builder_make(allocator=runtime.heap_allocator());strings.write_rune(&b,\'a\');strings.builder_destroy(&b)}',prefix='import "core:strings"\nimport "base:runtime"'),[])
        codes=self.codes('main::proc(){b:=strings.builder_make(allocator=runtime.heap_allocator());strings.write_rune(&b,\'a\')}',prefix='import "core:strings"\nimport "base:runtime"')
        self.assertIn('thor/leak',codes)

    def test_empty_builder_view_does_not_invent_an_allocation(self):
        self.assertEqual(self.codes('main::proc(){b:strings.Builder;s:=strings.to_string(b);_=len(s)}',prefix='import "core:strings"'),[])
        self.assertEqual(self.codes('main::proc(){b:strings.Builder;strings.write_string(&b,"");_=b}',prefix='import "core:strings"'),[])
        self.assertEqual(self.codes('main::proc(){b:=strings.builder_make(allocator=runtime.heap_allocator());_=b}',prefix='import "core:strings"\nimport "base:runtime"'),[])

    def test_positional_builder_allocator_is_preserved_during_first_growth(self):
        for call in ['strings.builder_make(runtime.heap_allocator())','strings.builder_make(4,runtime.heap_allocator())','strings.builder_make(0,4,runtime.heap_allocator())']:
            with self.subTest(call=call):
                codes=self.codes('main::proc(){b:='+call+';strings.write_rune(&b,\'a\')}',prefix='import "core:strings"\nimport "base:runtime"')
                self.assertIn('thor/leak',codes)
        for call in ['strings.builder_make(runtime.heap_allocator())','strings.builder_make(0,runtime.heap_allocator())','strings.builder_make(0,0,runtime.heap_allocator())']:
            with self.subTest(empty=call):
                self.assertEqual(self.codes('main::proc(){b:='+call+';_=b}',prefix='import "core:strings"\nimport "base:runtime"'),[])

    def test_parse_and_exists_read_inputs_without_retaining_them(self):
        self.assertEqual(self.codes('main::proc(){s:=strings.clone("123");n:int;_,_=strconv.parse_int(s,n=&n);_=os.exists(s);delete(s)}',prefix='import "core:strings"\nimport "core:strconv"\nimport "core:os"'),[])
        for call in ['_,_=strconv.parse_int(s)','_=os.exists(s)']:
            self.problem(self.codes('main::proc(){s:=strings.clone("123");delete(s);'+call+'}',prefix='import "core:strings"\nimport "core:strconv"\nimport "core:os"'))

    def test_split_positional_fields_keep_their_backing_storage(self):
        for field in ['s','loc','allocator']:
            code='Box::struct{'+field+':string}\nmain::proc(){source:=strings.clone("a b");holder:=Box{source};parts:=strings.fields(holder.'+field+');defer delete(parts);delete(source);_=parts[0][0]}'
            self.assertIn('thor/lifetime',self.codes(code,prefix='import "core:strings"'))

    def test_parse_output_pointer_must_be_alive(self):
        self.problem(self.codes('main::proc(){n:=new(int);free(n);_,_=strconv.parse_int("123",n=n)}',prefix='import "core:strconv"'))

    def test_random_choice_copies_values_and_keeps_nested_references(self):
        self.assertEqual(self.codes('main::proc(){v:=make([]int,2);_=rand.choice(v);rand.shuffle(v);delete(v)}',prefix='import "core:math/rand"'),[])
        self.problem(self.codes('main::proc(){v:=make([]int,2);items:=[1][]int{v};picked:=rand.choice(items[:]);delete(v);_=picked[0]}',prefix='import "core:math/rand"'))
        self.problem(self.codes('main::proc(){v:=make([]int,2);delete(v);rand.shuffle(v)}',prefix='import "core:math/rand"'))

    def test_custom_ambient_rng_is_not_trusted_by_collection_models(self):
        codes=self.codes('work::proc(gen:runtime.Random_Generator){context.random_generator=gen;v:=make([]int,2);_=rand.choice(v);delete(v)}\nmain::proc(){}',prefix='import "core:math/rand"\nimport "base:runtime"')
        self.assertIn('thor/lifetime-unknown',codes)
        self.assertEqual(self.codes('main::proc(){context.random_generator={};v:=make([]int,2);_=rand.choice(v);delete(v)}',prefix='import "core:math/rand"'),[])
        self.assertEqual(self.codes('work::proc(gen:runtime.Random_Generator){when false{context.random_generator=gen};v:=make([]int,2);_=rand.choice(v);delete(v)}\nmain::proc(){}',prefix='import "core:math/rand"\nimport "base:runtime"'),[])

    def test_user_named_reader_does_not_get_a_library_contract(self):
        codes=self.codes('saved:[]int\nexists::proc(v:[]int)->bool{saved=v;return true}\nmain::proc(){v~:=make([]int,2);_=exists(v)}')
        self.assertIn('thor/lifetime',codes)

if __name__=='__main__':unittest.main()

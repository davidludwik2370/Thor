"""Regressions reduced from the local project audit; unsafe code is never run."""
import unittest
from tests import test_lifetime_vet as base


class LifetimeVetHardeningTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp=base.LifetimeVetTests.setUp
    invoke=base.LifetimeVetTests.invoke
    vet=base.LifetimeVetTests.vet
    codes=base.LifetimeVetTests.codes

    def test_explicit_cleanup_and_region_teardown_are_not_double_frees(self):
        for cleanup in ['delete(values)', 'alias:=values;delete(alias)', 'defer delete(values)']:
            with self.subTest(cleanup=cleanup):
                codes=self.codes('main::proc(){arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;defer virtual.arena_destroy(&arena);context.allocator=virtual.arena_allocator(&arena);values:=make([]int,2);'+cleanup+'}',prefix='import "core:mem/virtual"')
                self.assertEqual(codes,[])

    def test_scoped_cleanup_and_scoped_arena_cleanup_are_compatible(self):
        codes=self.codes('main::proc(){context.allocator=basic.scoped_arena();values~:=make([]int,2);alias:>values;_=alias[0]}',prefix='import basic "thor:basic"')
        self.assertEqual(codes,[])

    def test_region_reset_and_destroy_still_invalidate_aliases(self):
        for reset in ['virtual.arena_free_all(&arena)', 'virtual.arena_destroy(&arena)']:
            with self.subTest(reset=reset):
                codes=self.codes('main::proc(){arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;context.allocator=virtual.arena_allocator(&arena);values:=make([]int,2);alias:=values;'+reset+';_=alias[0]}',prefix='import "core:mem/virtual"')
                self.assertIn('thor/lifetime',codes)

    def test_real_double_free_in_region_still_warns(self):
        codes=self.codes('main::proc(){arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;context.allocator=virtual.arena_allocator(&arena);values:=make([]int,2);delete(values);delete(values);virtual.arena_destroy(&arena)}',prefix='import "core:mem/virtual"')
        self.assertIn('thor/lifetime',codes)

    def test_scalar_helpers_do_not_prevent_loop_convergence(self):
        for flavor in ['proc','fn']:
            with self.subTest(flavor=flavor):
                codes=self.codes('twice::'+flavor+'(n:int)->int{return n*2}\nmain::proc(){sum:=0;for i in 0..<10{sum+=twice(i)};_=sum}')
                self.assertEqual(codes,[])

    def test_loop_helpers_preserve_escaped_stack_aliases(self):
        codes=self.codes('save::proc(out:^^int){local:=7;out^=&local}\nmain::proc(){pointer:^int;for _ in 0..<2{save(&pointer)};_=pointer^}')
        self.assertTrue(any(c in {'thor/lifetime','thor/lifetime-unknown'} for c in codes),codes)

    def test_reading_aliases_in_loop_helpers_does_not_poison_lifetimes(self):
        for flavor in ['proc','fn']:
            with self.subTest(flavor=flavor):
                codes=self.codes('read::'+flavor+'(values:[]int)->int{return values[0]}\nmain::proc(){values~:=make([]int,2);alias:>values;sum:=0;for _ in 0..<10{sum+=read(alias)};_=sum}')
                self.assertEqual(codes,[])

    def test_or_else_producer_effects_happen_on_both_paths(self):
        for statement in ['_=release(values) or_else 2', 'value:=release(values) or_else 2;_=value']:
            with self.subTest(statement=statement):
                codes=self.codes('release::proc(values:[]int)->(int,bool){delete(values);return 0,false}\nmain::proc(){values:=make([]int,2);alias:=values;'+statement+';_=alias[0]}')
                self.assertIn('thor/lifetime',codes)

    def test_short_circuit_skips_unreachable_releases(self):
        for expression in ['false && release(values)', 'true || release(values)']:
            with self.subTest(expression=expression):
                codes=self.codes('release::proc(values:[]int)->bool{delete(values);return true}\nmain::proc(){values:=make([]int,2);_='+expression+';_=values[0];delete(values)}')
                self.assertEqual(codes,[])

    def test_unknown_short_circuit_release_is_not_definite(self):
        codes=self.codes('release::proc(values:[]int)->bool{delete(values);return true}\nwork::proc(flag:bool){values:=make([]int,2);_=flag && release(values);_=values[0]}\nmain::proc(){}')
        self.assertIn('thor/lifetime-unknown',codes)
        self.assertNotIn('thor/lifetime',codes)

    def test_slice_bounds_are_evaluated_for_lifetime_effects(self):
        for slicing in ['values[release(values):]', 'values[:release(values)]']:
            with self.subTest(slicing=slicing):
                codes=self.codes('release::proc(values:[]int)->int{delete(values);return 0}\nmain::proc(){values:=make([]int,2);alias:='+slicing+';_=alias[0]}')
                self.assertIn('thor/lifetime',codes)

    def test_allocated_formatting_results_are_independent_owned_storage(self):
        for name in ['aprint','aprintln','aprintf','aprintfln','caprint','caprintf','caprintfln']:
            with self.subTest(name=name):
                codes=self.codes('main::proc(){alias::fmt.'+name+';text~:=alias("value");_=len(text)}',prefix='import "core:fmt"')
                self.assertEqual(codes,[])

    def test_allocating_formatters_read_their_input_aliases(self):
        for name in ['aprint','caprint']:
            with self.subTest(name=name):
                codes=self.codes('main::proc(){values:=make([]int,2);alias:>values;delete(values);text~:=fmt.'+name+'(alias);_=text}',prefix='import "core:fmt"')
                self.assertIn('thor/lifetime',codes)

    def test_split_header_cleanup_does_not_free_borrowed_text(self):
        for call in ['strings.fields("a b")','strings.split("a b"," ")','strings.split_n("a b"," ",2)','strings.split_lines("a\\nb")','strings.split(sep=" ",s="a b")']:
            with self.subTest(call=call):
                codes=self.codes('main::proc(){parts~:='+call+';_=parts[0][0]}',prefix='import "core:strings"')
                self.assertEqual(codes,[])

    def test_split_elements_keep_source_alias_lifetimes(self):
        codes=self.codes('main::proc(){text:=strings.clone("a b");parts~:=strings.fields(text);delete(text);_=parts[0][0]}',prefix='import "core:strings"')
        self.assertTrue(any(c in {'thor/lifetime','thor/lifetime-unknown'} for c in codes),codes)

    def test_split_does_not_retain_the_separator(self):
        codes=self.codes('main::proc(){separator:=strings.clone(" ");parts~:=strings.split("a b",separator);delete(separator);_=parts[0][0]}',prefix='import "core:strings"')
        self.assertEqual(codes,[])

    def test_checked_and_unchecked_runtime_with_scoped_aliased_buffers(self):
        self.source.write_text('''package main
import "core:fmt"
import "core:strings"
import basic "thor:basic"
read::proc(values:[]int)->int{return values[0]}
main::proc(){
 context.allocator=basic.scoped_arena()
 values~:=make([]int,2)
 values[0]=7
 alias:>values
 text~:=fmt.caprint(alias)
 if len(text)==0 {panic("empty formatting")}
 parts~:=strings.fields("a b")
 if len(parts)!=2 || parts[0]!="a" || parts[1]!="b" {panic("split views")}
 sum:=0
 for _ in 0..<10 {sum+=read(alias)}
 if sum!=70 {panic("alias reader")}
 fmt.println("ok")
}
''')
        from tests import test_rust_compiler as compiler
        for flags in [[],compiler.UNCHECKED]:
            with self.subTest(flags=flags):
                result=self.invoke('rust','run',flags=['-thread-count:1',*flags])
                self.assertEqual((result.returncode,result.stdout.strip()),(0,'ok'),result.stderr)

    def test_allocating_cstring_formatters_still_fail_no_alloc(self):
        self.source.write_text('package main\nimport "core:fmt"\n@(no_alloc)\nmain::proc(){values:>[]int{1,2};_=fmt.caprint(values)}\n')
        result=self.invoke('rust','check',flags=['-thread-count:1'])
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertIn('no_alloc',result.stderr)

    def test_swapped_headers_and_helper_copies_keep_allocation_identity(self):
        codes=self.codes('main::proc(){left:=make([]int,2);right:=make([]int,2);defer{delete(left);delete(right)};basic.swap(&left,&right);_=left[0]+right[0]}',prefix='import basic "thor:basic"')
        self.assertEqual(codes,[])
        codes=self.codes('main::proc(){left:=make([]int,2);right:=make([]int,2);alias:=left;basic.swap(&left,&right);delete(right);_=alias[0];delete(left)}',prefix='import basic "thor:basic"')
        self.assertIn('thor/lifetime',codes)

    def test_fallback_allocations_and_aliases_keep_distinct_ownership(self):
        codes=self.codes('attempt::proc(v:[]int)->([]int,bool){return v,false}\nmain::proc(){values:=make([]int,2);alias:=attempt(values) or_else values;delete(values);_=alias[0]}')
        self.assertIn('thor/lifetime',codes)
        codes=self.codes('attempt::proc(v:[]int)->([]int,int,bool){return v,7,true}\nmain::proc(){values:=make([]int,2);alias,count:=attempt(values) or_else (make([]int,2),0);delete(values);_=alias;_=count}')
        self.assertIn('thor/leak-unknown',codes)

    def test_scoped_split_outer_owner_preserves_borrowed_elements(self):
        codes=self.codes('main::proc(){source~:=strings.clone("a b");parts~:=strings.fields(source);_=parts[0]}',prefix='import "core:strings"')
        self.assertEqual(codes,[])
        self.source.write_text('package main\nimport "core:strings"\nbad::proc()->[]string{source~:=strings.clone("a b");return strings.fields(source)}\nmain::proc(){}\n')
        result=self.invoke('rust','check',flags=['-thread-count:1'])
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertIn('cannot escape',result.stderr)

    def test_arena_fields_have_independent_lifetimes(self):
        prefix='import "core:mem/virtual"\nPair::struct{a,b:virtual.Arena}'
        setup='pair:Pair;virtual.arena_init_growing(&pair.a) or_panic;virtual.arena_init_growing(&pair.b) or_panic;context.allocator=virtual.arena_allocator(&pair.a);left:=make([]int,2);context.allocator=virtual.arena_allocator(&pair.b);right:=make([]int,2);'
        codes=self.codes('main::proc(){'+setup+'virtual.arena_destroy(&pair.a);_=right[0];virtual.arena_destroy(&pair.b)}',prefix=prefix)
        self.assertEqual(codes,[])
        codes=self.codes('main::proc(){'+setup+'alias:=left;virtual.arena_destroy(&pair.a);_=alias[0];virtual.arena_destroy(&pair.b)}',prefix=prefix)
        self.assertIn('thor/lifetime',codes)

    def test_arena_array_elements_and_ambiguous_aliases(self):
        prefix='import "core:mem/virtual"'
        setup='arenas:[2]virtual.Arena;virtual.arena_init_growing(&arenas[0]) or_panic;virtual.arena_init_growing(&arenas[1]) or_panic;context.allocator=virtual.arena_allocator(&arenas[0]);left:=make([]int,2);context.allocator=virtual.arena_allocator(&arenas[1]);right:=make([]int,2);'
        codes=self.codes('main::proc(){'+setup+'virtual.arena_destroy(&arenas[0]);_=right[0];virtual.arena_destroy(&arenas[1])}',prefix=prefix)
        self.assertEqual(codes,[])
        codes=self.codes('work::proc(flag:bool){'+setup+'chosen:=flag?&arenas[0]:&arenas[1];virtual.arena_destroy(chosen);_=left[0]+right[0]}\nmain::proc(){}',prefix=prefix)
        self.assertIn('thor/lifetime-unknown',codes)
        self.assertNotIn('thor/lifetime',codes)

    def test_region_operations_check_the_arena_headers_lifetime(self):
        for call in ['virtual.arena_destroy(pointer)', 'virtual.arena_free_all(pointer)', 'virtual.arena_init_growing(pointer) or_panic']:
            with self.subTest(call=call):
                codes=self.codes('main::proc(){pointer:^virtual.Arena;{arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;pointer=&arena};'+call+'}',prefix='import "core:mem/virtual"')
                self.assertIn('thor/lifetime',codes)


if __name__=='__main__':unittest.main()

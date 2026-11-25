"""Cached proofs reuse graph contracts without erasing call-time provenance."""
import unittest
from tests import test_lifetime_summaries as base


class SharedContractTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled

    def test_repeated_format_parse_and_endian_helpers_get_cached_proofs(self):
        body='''import "core:fmt"
import "core:strconv"
import "core:encoding/endian"
work::proc(text:string, bytes:[]u8)->int{
 value,ok:=strconv.parse_int(text)
 fmt.println(value,ok,endian.unchecked_get_u16le(bytes))
 return value
}
main::proc(){bytes~:=make([]u8,2,allocator=context.allocator);'''+''.join('_=work("12",bytes);' for _ in range(100))+'}'
        findings,profile=self.profiled(body)
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.summary_applied',0),90,profile)
        self.assertGreater(profile['counters'].get('lifetime.summary_library_contracts',0),0,profile)

    def test_string_reader_contract_rechecks_released_input(self):
        self.assertIn('thor/lifetime',self.codes('read::proc(text:string){fmt.println(text)}\nmain::proc(){text:=strings.clone("hello");read(text);delete(text);read(text)}',prefix='import "core:fmt"\nimport "core:strings"'))

    def test_parser_alias_and_named_arguments_preserve_reader_masks(self):
        self.assertIn('thor/lifetime',self.codes('parse::strconv.parse_int\nread::proc(text:string){_,_=parse(s=text,base=10)}\nmain::proc(){text:=strings.clone("12");read(text);delete(text);read(text)}',prefix='import "core:strconv"\nimport "core:strings"'))
        for call in ['strconv.parse_i64("12",n)','strconv.parse_u64("12",n)','strconv.parse_int("12",10,n)','strconv.parse_i64(str="12",n=n)']:
            self.assertIn('thor/lifetime',self.codes('main::proc(){n:=new(int);free(n);_,_='+call+'}',prefix='import "core:strconv"'))

    def test_endian_writer_contract_rechecks_output_storage(self):
        self.assertIn('thor/lifetime',self.codes('put::endian.unchecked_put_u16le\nwrite::proc(bytes:[]u8){put(bytes,7)}\nmain::proc(){v:=make([]u8,2);write(v);delete(v);write(v)}',prefix='import "core:encoding/endian"'))

    def test_borrowed_formatter_results_remain_traced(self):
        self.assertIn('thor/lifetime',self.codes('format::proc(bytes:[]u8)->string{return fmt.bprint(bytes,7)}\nmain::proc(){v:=make([]u8,32);s:=format(v);delete(v);_=s[0]}',prefix='import "core:fmt"'))
        self.assertIn('thor/lifetime',self.codes('main::proc(){v:=make([]u8,32);s:=fmt.bprintf(fmt="hello",buf=v);delete(v);_=s[0]}',prefix='import "core:fmt"'))

    def test_argument_allocations_are_not_covered_by_a_reader_contract(self):
        self.assertTrue(any(c.startswith('thor/leak') for c in self.codes('read::proc(text:string){fmt.println(text)}\nmain::proc(){read(strings.clone("hello"))}',prefix='import "core:strings"\nimport "core:fmt"')))

    def test_alias_with_same_name_as_library_retains_real_effects(self):
        self.assertIn('thor/lifetime',self.codes('saved:[]int\nprintln::proc(v:[]int){saved=v}\ncall::proc(v:[]int){println(v)}\nmain::proc(){v~:=make([]int,2);call(v)}'))

    def test_custom_file_streams_do_not_get_a_stdout_print_contract(self):
        self.assertIn('thor/lifetime-unknown',self.codes('output::proc(){fmt.println(7)}\nwork::proc(callback:os.File_Stream_Proc){os.stdout.stream.procedure=callback;output()}\nmain::proc(){}',prefix='import "core:fmt"\nimport "core:os"'))
        self.assertEqual(self.codes('output::proc(buf:[]u8){_=fmt.bprint(buf,7)}\nwork::proc(callback:os.File_Stream_Proc){when false{os.stdout.stream.procedure=callback};buf:[32]u8;output(buf[:])}\nmain::proc(){}',prefix='import "core:fmt"\nimport "core:os"'),[])

    def test_scalar_rng_helper_is_cached_without_an_opaque_generator(self):
        body='import "core:math/rand"\nroll::proc(n:int)->int{return rand.int_max(n)}\nmain::proc(){v~:=make([]int,2,allocator=context.allocator);'+''.join('_=roll(10);' for _ in range(100))+'}'
        findings,profile=self.profiled(body)
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.summary_applied',0),90,profile)

    def test_custom_rng_and_reference_elements_do_not_get_value_shortcuts(self):
        self.assertIn('thor/lifetime-unknown',self.codes('work::proc(gen:rand.Generator){context.random_generator=gen;v~:=make([]int,2);_=rand.int_max(7)}\nmain::proc(){}',prefix='import "core:math/rand"'))
        self.assertIn('thor/lifetime',self.codes('pick::proc(v:[]^int)->^int{return rand.choice(v)}\nmain::proc(){p:=new(int);v:=[1]^int{p};q:=pick(v[:]);free(p);_=q^}',prefix='import "core:math/rand"'))

    def test_integer_addresses_cannot_be_erased_by_a_formatter_summary(self):
        self.assertIn('thor/lifetime',self.codes('read::proc(v:[]uintptr)->uintptr{fmt.println(v);return v[0]}\nmain::proc(){p:=new(int);v:=[1]uintptr{uintptr(p)};bits:=read(v[:]);free(p);_=((^int)(bits))^}',prefix='import "core:fmt"'))

    def test_pointer_formatting_keeps_verb_dependent_uncertainty(self):
        findings=self.vet('output::proc(p:^int){fmt.printf("%p",p)}\nmain::proc(){p:=new(int);output(p);free(p);output(p)}',prefix='import "core:fmt"')
        self.assertTrue(all(f['certainty']=='unverified' for f in findings),findings)
        self.assertIn('thor/lifetime',self.codes('output::proc(p:^int){fmt.println(p^)}\nmain::proc(){p:=new(int);output(p);free(p);output(p)}',prefix='import "core:fmt"'))

    def test_native_output_writes_do_not_falsely_transfer_overwritten_addresses(self):
        code='saved:uintptr\nwork::proc(){p:=new(int);bits:=int(uintptr(p));_,_=strconv.parse_int("12",n=&bits);saved=uintptr(bits)}\nmain::proc(){work()}'
        self.assertTrue(any(c.startswith('thor/leak') for c in self.codes(code,prefix='import "core:strconv"')))
        code='saved:uintptr\nwork::proc(){p:=new(int);bytes:=[2]u8{u8(uintptr(p)),0};endian.unchecked_put_u16le(bytes[:],0);saved=uintptr(bytes[0])}\nmain::proc(){work()}'
        self.assertTrue(any(c.startswith('thor/leak') for c in self.codes(code,prefix='import "core:encoding/endian"')))

    def test_endian_roundtrip_keeps_encoded_reference_identity_with_named_arguments(self):
        code='read::proc(b:[]u8)->u64{return endian.unchecked_get_u64le(b)}\nmain::proc(){p:=new(int);bytes:[8]u8;endian.unchecked_put_u64le(v=u64(uintptr(p)),b=bytes[:]);bits:=read(bytes[:]);free(p);_=((^int)(uintptr(bits)))^}'
        findings=self.vet(code,prefix='import "core:encoding/endian"')
        start=self.source.read_text().index('((^int)(uintptr(bits)))^')
        self.assertTrue(any(f['pos']['offset']>=start and ('released' in str(f['msgs']) or 'lifetime ended' in str(f['msgs'])) for f in findings),findings)


if __name__=='__main__':unittest.main()

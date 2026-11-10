"""Native warning recovery preserves declaration work and explicit vet policy."""
import json
import unittest
from tests import test_rust_compiler as native


class RustUnusedLocalTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    program=native.RustCompilerTests.program
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare

    def warnings(self, source, *, flags=(), expected='', files=None, count=None):
        result=self.compare(source,files=files,flags=[*flags,'-json-errors'],expected=expected)
        packet=json.loads(result.stderr)
        self.assertEqual(packet['error_count'],0,packet)
        self.assertTrue(all(error['type']=='warning' for error in packet['errors']),packet)
        if count is not None:self.assertEqual(len(packet['errors']),count,packet)
        return packet['errors']

    def test_control_do_blocks_and_grouped_declarations(self):
        for body,names in [('if true {value:=1}',['value']),
                           ('if true do first,second:=1,2',['first','second']),
                           ('if false {}else{value:>1}',['value']),
                           ('for _ in 0..<2 {value:int=1}',['value']),
                           ('if true {VALUE::1}',['VALUE']),
                           ('if true {Local::struct{value:int}}',['Local'])]:
            with self.subTest(body=body):
                errors=self.warnings(self.program(body),count=len(names))
                self.assertEqual([error['msgs'][0] for error in errors],
                                 [repr(name)+' declared but not used' for name in names])

    def test_initializers_and_deferred_cleanup_run_once_in_both_build_modes(self):
        source=self.program('''if true {unused:=next()}
if false {never:=next()}
for _ in 0..<2 do first,second:=pair()
if true {resource:=acquire()}
if calls!=3||closed!=1 {panic("initializer/cleanup count")}
''','''calls:int
closed:int
cleanup::proc(value:int){closed+=value}
@(deferred_out=cleanup)
acquire::proc()->int{return 1}
next::proc()->int{calls+=1;return calls}
pair::proc()->(int,int){calls+=1;return calls,calls}
''')
        for flags in [[],['-debug'],native.UNCHECKED]:
            with self.subTest(flags=flags):self.warnings(source,flags=flags,count=5)

    def test_warning_controls_and_explicit_unused_vetting(self):
        source=self.program('if true {unused:=1}')
        self.warnings(source,flags=['-ignore-warnings'],count=0)
        self.compare(source,command='check',flags=['-json-errors','-warnings-as-errors'],fail='declared but not used')
        self.compare(self.program('unused:=1;value:=2;_=value'),command='check',
                     flags=['-vet-unused-variables'],fail='declared but not used')
        self.compare(self.program('if true {unused:=1}','import "core:fmt"\n'),command='check',
                     flags=['-vet-unused-imports'],fail="'fmt' declared but not used")
        self.compare(self.program('outer:=1;if true {outer:=2};_=outer'),command='check',
                     flags=['-vet-shadowing'],fail='shadows declaration')

    def test_native_selection_and_existing_block_rules_remain_unchanged(self):
        for body in ['when false {if true {unused:=missing}}','unused:=1','{unused:=1}',
                     'if true {used:=1;_=used}','if true {_=1}','switch 1 {case 1:unused:=1}']:
            with self.subTest(body=body):self.warnings(self.program(body),count=0)

    def test_independent_errors_keep_the_original_error_and_warning(self):
        source=self.program('/* 😀 */if true do unused:=1;value:int="wrong";_=value')
        for flags in [[],['-max-error-count:1']]:
            with self.subTest(flags=flags):
                result=self.compare(source,command='check',flags=[*flags,'-json-errors'],fail='Cannot convert')
                errors=json.loads(result.stderr)['errors']
                warning=next(error for error in errors if error['type']=='warning')
                self.assertEqual(warning['pos']['offset'],source.encode().index(b'unused'))
                self.assertEqual(warning['pos']['end_offset']-warning['pos']['offset'],len('unused'))
                error=next(error for error in errors if error['type']=='error')
                self.assertEqual(error['pos']['offset'],source.encode().index(b'"wrong"'))

    def test_unicode_and_small_error_budget_preserve_all_warnings(self):
        errors=self.warnings(self.program('if true {тест:=1}'),count=1)
        self.assertEqual(errors[0]['pos']['end_offset']-errors[0]['pos']['offset'],len('тест'.encode()))
        self.warnings(self.program('if true {a:=1};if true {b:=2};if true {c:=3}'),
                      flags=['-max-error-count:1'],count=3)

    def test_compile_time_helpers_with_unused_inner_declarations_still_execute(self):
        for signature in ['proc(value:$T)->T','fn(value:int)->int']:
            with self.subTest(signature=signature):
                source=self.program('if ANSWER!=7{panic("result")}',
                    'get::'+signature+'{if true do unused:=1;return value}\nANSWER::#run get(7)\n')
                self.warnings(source)
                self.warnings(source)

    def test_imported_native_file_uses_the_same_local_warning_policy(self):
        source=self.program('library.work();if library.calls!=1{panic("native initializer")}',
                            'import "library"\n')
        errors=self.warnings(source,files={'library/work.odin':
            'package library\ncalls:int\nnext::proc()->int{calls+=1;return calls}\nwork::proc(){if true {unused:=next()}}\n'},count=1)
        self.assertTrue(errors[0]['pos']['file'].endswith('/library/work.odin'))

    def test_requested_procedure_vetting_stays_strict(self):
        self.compare(self.program('local::proc($T:typeid){};_=1'),command='check',
                     flags=['-vet-unused-procedures','-vet-packages:main'],fail='declared but not used')
        self.compare('#+vet unused-procedures\n'+self.program('local::proc($T:typeid){};_=1'),
                     command='check',fail='declared but not used')

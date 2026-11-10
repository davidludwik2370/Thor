"""Definite Odin source errors are not Rust migration gaps."""
import unittest
from tests import test_rust_compiler as native

class RustPureCallErrorTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_unresolved_signature_types_keep_the_native_type_error(self):
        for definition in ('work::fn(value:Missing)->int{return 0}',
                           'helper::proc(value:Missing)->int{return 0}\nwork::fn(value:int)->int{return helper(value)}'):
            with self.subTest(definition=definition):
                self.compare(self.program('',definition+'\n'),command='check',fail='Undeclared name: Missing')

    def test_soa_schemas_keep_the_reference_native_rejection(self):
        for schema in ('#soa []Row','#soa [2]Row'):
            with self.subTest(schema=schema):
                self.compare(self.program('', 'Row::struct{x,y:int}\nwork::fn(value:'+schema+')->int{return len(value)}\n'),
                             command='check',fail="Expected a struct type for 'type_has_shared_fields'")

    def test_unresolved_auto_cast_is_a_source_contract_error(self):
        self.compare(self.program('n:=3;_=work(n)',
            'work::fn(n:int)->int{_=auto_cast n;return n}\n'),
            command='check',fail='Pure auto_cast requires a resolved target type')

    def test_checked_variant_does_not_make_invalid_proc_do_syntax_valid(self):
        self.compare(self.program('n:=3;_=work(n)','''
leaf::proc(n:int)->int do return n+1
work::fn(n:int)->int{return leaf(n)}
'''),command='check',fail="'do' for procedure bodies is not allowed")

    def test_missing_callee_keeps_the_native_undeclared_name_error(self):
        self.compare(self.program('n:=3;_=work(n)',
            'work::fn(n:int)->int{return missing(n)}\n'),command='check',fail='Undeclared name: missing')

    def test_definite_argument_errors_keep_the_native_call_signature(self):
        for expression,message in [('helper()','missing in procedure call'),
                                   ('helper(1,2)','helper'),
                                   ('helper(wrong=1)',"No parameter named 'wrong'"),
                                   ('helper("bad")','untyped string'),
                                   ('helper(true)','bool'),
                                   ('helper(f64(n))','f64')]:
            with self.subTest(expression=expression):
                result=self.compare(self.program('n:=3;_=work(n)',
                    'helper::proc(n:int)->int{return n}\nwork::fn(n:int)->int{return '+expression+'}\n'),
                    command='check',fail=message)
                if expression=='helper(1,2)':
                    self.assertIn('Too many arguments',result.stderr)
                    self.assertNotIn('Thor expression',result.stderr)

    def test_bad_arguments_to_an_overload_group_keep_native_candidates(self):
        for expression in ('pick("bad")','pick()','pick(wrong=1)'):
            with self.subTest(expression=expression):
                self.compare(self.program('n:=3;_=work(n)','''
integer::proc(value:int)->int{return value}
real::proc(value:f64)->int{return int(value)}
pick::proc{integer,real}
work::fn(n:int)->int{return '''+expression+'}\n'),command='check',fail='pick')

    def test_inline_proc_is_a_purity_error_instead_of_an_unported_named_body(self):
        self.compare(self.program('n:=3;_=work(n)',
            'work::fn(n:int)->int{return (proc(v:int)->int{return v})(n)}\n'),
            command='check',fail='resolved direct call')

    def test_parenthesized_named_helper_records_a_reference_false_positive(self):
        source=self.program('n:=3;if work(n)!=4{panic("named helper")}', '''
helper::(proc(n:int)->int{return n+1})
work::fn(n:int)->int{return helper(n)}
''')
        for flags in (['-debug'],native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                # The reference's procedure-candidate lookup does not unwrap
                # a parenthesized initializer, although it is still the same
                # named, inspectable Odin procedure body.
                self.source.write_text(source)
                reference=self.invoke('python',flags=flags)
                self.assertEqual(reference.returncode,1,reference.stderr)
                self.assertIn('resolved direct call',reference.stderr)
                result=self.invoke('rust',flags=flags)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout,'')
                self.assertEqual(result.stderr,'')

if __name__=='__main__':unittest.main()

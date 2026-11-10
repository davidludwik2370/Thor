"""Definite Odin source errors are not Rust migration gaps."""
import unittest
from tests import test_rust_compiler as native

class RustPureCallErrorTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

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

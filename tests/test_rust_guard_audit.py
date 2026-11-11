"""Migration guards must not reject valid work or disguise ordinary source errors."""
import json
import subprocess
import unittest

from tests import test_rust_compiler as native


class RustGuardAuditTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    program = native.RustCompilerTests.program
    compare = native.RustCompilerTests.compare
    invoke = native.RustCompilerTests.invoke

    def checked_runs(self, body, prefix='', files=None):
        for flags in [['-debug'], [*native.UNCHECKED, '--track-allocations=basic']]:
            with self.subTest(flags=flags):
                result = self.compare(self.program(body, prefix), files=files, flags=flags)
                self.assertEqual(result.stderr, '')

    def native_checked_runs(self, body, prefix='', files=None):
        # Some result-default bugs also affect the frozen Python reference.
        # Check native values, ASan and allocation reports independently.
        for name, content in (files or {}).items():
            path = self.app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        for flags in [['-debug'], [*native.UNCHECKED, '--track-allocations=basic']]:
            with self.subTest(flags=flags):
                self.source.write_text(self.program(body, prefix))
                result = self.invoke('rust', flags=flags)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, '')

    def test_nested_generic_requests_preserve_each_selected_value(self):
        prefix = '''leaf::proc($N:int)->int{return N}
inner::proc($T:typeid)->int{
 when T==int{return #run leaf(2)}else{return #run leaf(3)}
}
outer::fn($T:typeid)->int{return inner(T)}
'''
        self.checked_runs('''
A::#run outer(int); B::#run outer(string); C::#run outer(int)
if A!=2||B!=3||C!=2 {panic("nested generic result")}
''', prefix)

    def test_unchecked_generic_branch_does_not_resolve_an_absent_callee(self):
        prefix = '''inner::proc($T:typeid)->int{
 when T==int{return 3}else{return #run missing()}
}
outer::proc()->int{return inner(int)}
'''
        self.checked_runs('VALUE::#run outer();if VALUE!=3{panic("inactive generic request")}', prefix)
        result = self.compare(self.program('_=#run outer()', prefix.replace('inner(int)', 'inner(string)')),
                              command='check', flags=['-json-errors'], fail='#run requires a resolved direct call')
        self.assertNotIn('rust-migration', result.stderr)

    def test_unselected_trapping_request_and_selected_failure_keep_their_contracts(self):
        prefix = '''leaf::proc($N:int)->int{return N}
bad::proc()->int{panic("selected branch trap")}
inner::proc($N:int)->int{
 when N==0{return #run bad()}else{return #run leaf(N)}
}
outer::fn($N:int)->int{return inner(N)}
'''
        self.checked_runs('A::#run outer(1);B::#run outer(2);if A!=1||B!=2{panic("binding reuse")}', prefix)
        result = self.compare(self.program('_=#run outer(0)', prefix), command='check',
                              fail='selected branch trap')
        self.assertNotIn('Rust migration', result.stderr)

    def test_imported_generic_request_results_survive_a_warm_result_cache(self):
        prefix = 'import "library"\nouter::fn($T:typeid)->int{return library.inner(T)}\n'
        files = {'library/main.thor': '''package library
leaf::proc($N:int)->int{return N}
inner::proc($T:typeid)->int{
 when T==int{return #run leaf(2)}else{return #run leaf(3)}
}
'''}
        code = self.program('A::#run outer(int);B::#run outer(string);if A!=2||B!=3{panic("imported cases")}', prefix)
        for flags in [['-debug'], native.UNCHECKED]:
            for attempt in range(2):
                with self.subTest(flags=flags, attempt=attempt):
                    self.compare(code, files=files, flags=flags)

    def test_owned_nested_results_are_independent_and_do_not_leak(self):
        prefix = '''allocate::proc(n:int)->[]int{a:=make([]int,1);a[0]=n;return a}
inner::proc($T:typeid)->[]int{
 when T==int{return #run allocate(2)}else{return #run allocate(3)}
}
outer::fn($T:typeid)->[]int{return inner(T)}
'''
        self.checked_runs('''a:=#run outer(int);defer delete(a)
b:=#run outer(string);defer delete(b)
c:=#run outer(int);defer delete(c)
a[0]=9
if b[0]!=3||c[0]!=2{panic("owned specialized results")}
''', prefix)

    def test_runtime_inactive_generic_branch_remains_checked(self):
        prefix = '''text::proc()->string{return "wrong"}
inner::proc($T:typeid)->int{if false{return #run text()};return 3}
outer::proc()->int{return inner(int)}
'''
        # An inactive runtime branch needs its native result type. It must not
        # acquire the untyped zero used only for unchecked native when branches.
        self.source.write_text(self.program('_=#run outer()', prefix))
        for engine in ['python', 'rust']:
            result = self.invoke(engine, command='check', flags=['-json-errors'])
            self.assertEqual(result.returncode, 1, result.stderr)
            errors = json.loads(result.stderr)['errors']
            expected = self.source.read_bytes().index(b'#run text()')
            self.assertTrue(any(error['pos']['offset'] == expected for error in errors))
            self.assertNotIn('rust-migration', result.stderr)

    def test_unchecked_nested_requests_need_no_unused_local_type_identity(self):
        self.native_checked_runs('''Local::struct{x:int}
a:=#run outer(Local,false)
b:=#run outer(Local,true)
c:=#run outer(Local,false)
if a!=0||b!=size_of(Local)+1||c!=0{panic("local generic branch identity")}
''', '''leaf::proc(n:int)->int{return n+1}
inner::proc($T:typeid,$B:bool)->int{when B{return #run leaf(size_of(T))}else{return 0}}
outer::fn($T:typeid,$B:bool)->int{return inner(T,B)}
''')

    def test_wide_finite_call_graphs_do_not_exhaust_specialization_work(self):
        count = 1025
        prefix = '\n'.join(f'leaf_{n}::proc(n:int)->int{{return n}}' for n in range(count))
        prefix += '\nwork::fn(n:int)->int{total:=0;'
        prefix += ''.join(f'total+=leaf_{n}(n);' for n in range(count))
        prefix += 'return total}\n'
        # The previous guard rejected even unused, valid fn declarations.
        # Checking their bodies exercises graph eligibility without generating
        # an executable containing a thousand unrelated checked wrappers.
        source = self.program('', prefix)
        self.compare(source, command='check')
        self.compare(source, command='build', flags=native.UNCHECKED)

    def test_known_invalid_calls_report_source_errors_instead_of_migration_gaps(self):
        programs = [
            ('R::struct{value:int}\nwork::fn(n:R)->int{return n.missing()}\n', '_=work(R{})', 'unknown calls'),
            ('R::struct{value:int}\nwork::fn(n:R)->int{return n.value()}\n', '_=work(R{})', 'unknown calls'),
            ('import "core:fmt" / println\nwork::fn()->int{return absent()}\n', '_=work()', 'Undeclared name: absent'),
            ('work::fn()->int{return absent.value()}\n', '_=work()', 'Undeclared name: absent'),
        ]
        for prefix, body, message in programs:
            with self.subTest(prefix=prefix):
                source = self.program(body, '// café 🌩\n' + prefix)
                self.source.write_text(source)
                for engine in ['python', 'rust']:
                    result = self.invoke(engine, command='check', flags=['-json-errors'])
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertNotIn('rust-migration', result.stderr)
                    errors = json.loads(result.stderr)['errors']
                    self.assertTrue(any(error['pos']['file'] == str(self.source) for error in errors))
                    if engine == 'rust':
                        self.assertIn(message, result.stderr)

    def test_runtime_collection_inputs_cannot_initialize_compile_time_constants(self):
        bodies = [
            ('values:=[1,2];COPY::[x for x in values];_=COPY', 'Compile-time comprehension inputs'),
            ('values:=[1,2];COPY::max(**values);_=COPY', 'Constant min/max expansion inputs'),
            ('values:=[1,2];COPY::min(**values);_=COPY', 'Constant min/max expansion inputs'),
            ('values:=[]int{1,2};COPY::max(..values);_=COPY', 'Constant min/max expansion inputs'),
        ]
        for body, message in bodies:
            for flags in [[], native.UNCHECKED]:
                with self.subTest(body=body, flags=flags):
                    self.source.write_text(self.program(body, '// café 🌩\n'))
                    for engine in ['python', 'rust']:
                        result = self.invoke(engine, command='build', flags=[*flags, '-json-errors'])
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertNotIn('rust-migration', result.stderr)
                        if engine == 'rust':
                            self.assertIn(message, result.stderr)
                            error = json.loads(result.stderr)['errors'][0]
                            self.assertEqual(error['pos']['file'], str(self.source))

    def test_known_collection_operations_remain_native_constants(self):
        prefix = '''VALUES::[1,2,3]
COPY::[x*x for x in VALUES]
LOW::min(**VALUES)
HIGH::max(**VALUES)
'''
        self.checked_runs('if len(COPY)!=3||COPY[2]!=9||LOW!=1||HIGH!=3{panic("collection constants")}', prefix)

    def test_inferred_pure_results_keep_native_type_witnesses_and_owned_storage(self):
        prefix = '''number::fn()->(result:=7){return}
text::fn()->(result:="literal"){return}
values::fn()->(result:=[]int{1,2}){return}
'''
        self.native_checked_runs('''a:=values();defer delete(a)
b:=values();defer delete(b)
t:=text();defer delete(t)
a[0]=9
if number()!=7||t!="literal"||b[0]!=1{panic("inferred pure result")}
''', prefix)

    def test_result_defaults_initialize_once_and_preserve_mixed_named_returns(self):
        self.native_checked_runs('''n:=3
a,text,values:=work(n)
defer delete(text);defer delete(values)
if a!=7||text!="value"||len(values)!=2||values[0]!=1{panic("mixed defaults")}
''', '''work::fn(n:int)->(a:int=7,text:string="value",values:[]int=[]int{1,2}){_=n;return}
''')

    def test_inferred_allocated_and_checked_helper_results_outlive_scratch(self):
        for helper in ['fn', 'proc']:
            with self.subTest(helper=helper):
                self.native_checked_runs('''n:=3
a:=work(n);defer delete(a)
b:=work(n+1);defer delete(b)
a[0]=9
if b[0]!=4{panic("inferred owned allocation")}
''', '''leaf::'''+helper+'''(n:int)->(result:=[]int{0}){a:=make([]int,1);a[0]=n;return a}
work::fn(n:int)->[]int{return leaf(n)}
''')

    def test_result_defaults_cannot_hide_impure_effects_or_unsafe_types(self):
        for prefix, message in [
            ('work::fn()->(result:=rawptr(nil)){return}', 'cannot convert values to pointers'),
            ('work::fn()->(result:=map[int]int{}){return}', 'Pure local storage cannot contain'),
            ('state:=3\nwork::fn()->(result:=state){return}', 'outside the function'),
            ('import "core:fmt"\nwork::fn()->(result:=fmt.println("effect")){return}', 'performs I/O'),
        ]:
            for flags in [[], native.UNCHECKED]:
                with self.subTest(prefix=prefix, flags=flags):
                    self.source.write_text(self.program('', '// café 🌩\n'+prefix+'\n'))
                    result = self.invoke('rust', command='build', flags=[*flags, '-json-errors'])
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn(message, result.stderr)
                    self.assertNotIn('rust-migration', result.stderr)

    def test_imported_inferred_and_explicit_generic_results_preserve_native_types(self):
        self.native_checked_runs('''n:=3
a:=work(n);defer delete(a.values)
b:=generic(int,n);defer delete(b)
if a.values[0]!=3||len(b)!=1||b[0]!=3{panic("inferred record and generic types")}
''', '''import "library"
work::fn(n:int)->(result:=library.Box{[]int{0}}){a:=make([]int,1);a[0]=n;return {a}}
generic::fn($T:typeid,n:T)->(result:[]T){a:=make([]T,1);a[0]=n;return a}
''', {'library/main.thor':'package library\nBox::struct{values:[]int}\n'})

    def test_cross_file_generic_copies_preserve_array_and_slice_borrowing(self):
        prefix = 'work::fn(value:$T){write(value)}\n'
        for name in ['other.thor', 'other.odin']:
            for previous in ['other.thor', 'other.odin']:
                (self.app / previous).unlink(missing_ok=True)
            files = {name: 'package main\nwrite::proc(value:$T){copy:=value;copy[0]=9}\n'}
            for flags in [[], native.UNCHECKED]:
                with self.subTest(file=name, flags=flags):
                    self.compare(self.program('a:=[1,2];work(a);if a[0]!=1{panic("array copy")}', prefix),
                                 files=files, directory=True, flags=flags)
                    self.compare(self.program('a:=[]int{1,2};defer delete(a);work(a)', prefix),
                                 files=files, directory=True, flags=flags, command='build',
                                 fail='borrowed from an input')

    def test_capabilities_distinguish_parallel_work_from_remaining_semantic_work(self):
        result = subprocess.run([str(native.RUST), '--capabilities'], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertFalse(packet['complete'])
        self.assertFalse(packet['python_fallback'])
        self.assertEqual(packet['reference_command'], 'thor-python')
        self.assertTrue(packet['parallel_work']['source_parsing'])
        self.assertTrue(packet['parallel_work']['source_map_serialization'])
        self.assertFalse(packet['parallel_work']['semantic_lowering'])
        self.assertNotIn('performance characterization and parallel lowering', packet['pending'])

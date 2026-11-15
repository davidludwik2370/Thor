"""Native isolated helper parity; command scheduling is tested separately."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from thorlang.driver import Project, frontend_binary
from thorlang.evaluation import Evaluator
from tests import test_rust_compiler as native


class RustEvaluationTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            ['cargo', 'build', '--release', '--locked', '--offline',
             '--manifest-path', str(native.ROOT/'rust/Cargo.toml'),
             '--example', 'evaluation_protocol'], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout+result.stderr)
        cls.frontend = frontend_binary('odin')[0]
        cls.binary = native.ROOT/'rust/target/release/examples/evaluation_protocol'

    def evaluate(self, prefix, expression, *, body='', body_end='', files=None, flags=None,
                 error=None, compare=True, timeout=None, emit=False,
                 source_name='main.thor', package='main'):
        flags = ['-debug'] if flags is None else flags
        with tempfile.TemporaryDirectory(prefix='thor-native-evaluation-') as directory:
            entry = Path(directory).resolve()/source_name
            entry.write_text('package '+package+'\n'+prefix+'\nmain::proc(){'+body+'\n_=#run '+expression+'\n'+body_end+'}\n')
            for name, text in (files or {}).items():
                path = entry.parent/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            arguments = [str(self.binary), str(entry), *flags]
            if timeout is not None:
                arguments.append('timeout='+str(timeout))
            if emit:
                arguments.append('emit='+str(entry.parent/'generated'))
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=90)
            if error:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                packet = json.loads(result.stderr)
                messages = packet['message']+'\n'+'\n'.join(
                    message for diagnostic in packet['diagnostics'] for message in diagnostic.get('msgs', []))
                self.assertIn(error, messages, result.stderr)
                self.assertTrue(packet['diagnostics'], result.stderr)
                self.assertEqual(packet['diagnostics'][0]['pos']['file'], str(entry), result.stderr)
                return packet
            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            if emit:
                texts = {str(path.relative_to(entry.parent/'generated')):path.read_text()
                         for path in (entry.parent/'generated').rglob('*.odin') if not path.is_symlink()}
                return texts
            if compare:
                project = Project(entry, self.frontend, flags, test='-build-mode:test' in flags, cache_frontend=False)
                evaluator = Evaluator(project, 'odin')
                project.resolver.native_enabled = True
                request = next(node.role('expr') for node in project.sources[entry].nodes if node.kind == 'Run')
                expected = evaluator.execute(request)
                self.assertEqual(packet['value'], expected)
            return packet['value']

    def test_scalar_transitive_calls_and_constants(self):
        self.evaluate('BASE::3\nleaf::proc(x:int)->int{return x+BASE}\nwork::proc(n:int)->int{return leaf(n)*2}', 'work(5)')

    def test_checked_and_unchecked_helpers(self):
        for flags in (['-debug'], ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']):
            with self.subTest(flags=flags):
                self.evaluate('work::fn(n:int)->int{a:=[2]int{n,n+1};a[0]+=2;return a[0]+a[1]}', 'work(4)', flags=flags)

    def test_string_and_rune_results(self):
        self.evaluate('work::proc()->string{return "héllo 😀"}', 'work()')
        self.evaluate("work::proc()->rune{return '😀'}", 'work()')

    def test_records_enums_and_inline_arrays(self):
        self.evaluate('Kind::enum{first,second}\nRow::struct{kind:Kind,values:[2]int}\nwork::proc()->Row{return {.second,{4,9}}}', 'work()')

    def test_owned_slice_and_dynamic_results(self):
        self.evaluate('work::proc()->[]int{a:=make([]int,3);a[0]=4;a[2]=7;return a}', 'work()')
        self.evaluate('work::proc()->[dynamic]int{a:[dynamic]int;append(&a,2,3);return a}', 'work()')

    def test_owned_local_helper_mutation(self):
        self.evaluate('set::proc(x:^int){x^=9}\nwork::proc()->int{x:int;set(&x);return x}', 'work()')

    def test_surrounding_local_constants_and_local_helper(self):
        self.evaluate('', 'work(VALUE)', body='VALUE::7\nwork::proc(x:int)->int{return x+1}')

    def test_nested_lexical_constant_shadowing(self):
        self.evaluate('work::proc(x:int)->int{return x*2}', 'work(VALUE)',
                      body='VALUE::3\n{VALUE::8\n', body_end='}\n')

    def test_only_selected_overload_enters_helper(self):
        self.evaluate('state:int\na::proc(x:int)->int{return x+3}\nb::proc(x:f64)->int{return int(x)+state}\nwork::proc{a,b}', 'work(4)')

    def test_imported_private_reexport_and_local_namespace_shadow(self):
        self.evaluate('import "facade"', 'facade.work(4)',
                      files={'facade/main.thor':'package facade\nimport "../impl"\nwork::impl.work',
                             'impl/main.thor':'package impl\n@(private="file") leaf::proc(x:int)->int{return x+2}\nwork::leaf'})

    def test_sdk_math_and_qualified_builtin_alias(self):
        self.evaluate('import "core:math"\nwork::proc(n:f64)->f64{return math.sqrt(n)}', 'work(9)')
        self.evaluate('import b "base:builtin"\nSIZE::b.len\nwork::proc()->int{return SIZE([3]int{1,2,3})}', 'work()')

    def test_unevaluated_type_query_skips_runtime_global(self):
        self.evaluate('state:[4]int\nwork::proc()->int{return size_of(state)}', 'work()')

    def test_unrelated_runtime_globals_and_init_fini_are_pruned(self):
        texts = self.evaluate('state:int\n@(init) initialize::proc(){panic("init ran")}\n@(fini) finalize::proc(){panic("fini ran")}\nunused::proc(){unknown()}\nwork::proc()->int{return 7}', 'work()', emit=True)
        text = next(text for path,text in texts.items() if Path(path).name=='main.odin')
        for name in ('initialize', 'finalize', 'unused', 'state:int'):
            self.assertNotIn(name, text)
        self.evaluate('@(init) initialize::proc(){panic("init ran")}\nwork::proc()->int{return 7}', 'work()')

    def test_optional_ok_result_uses_primary_value(self):
        self.evaluate('work::proc()->(int,bool) #optional_ok {return 4,true}', 'work()')

    def test_runtime_panic_is_attached_to_run(self):
        packet = self.evaluate('work::proc()->int{panic("intentional evaluation panic")}', 'work()',
                               error='intentional evaluation panic', compare=False)
        self.assertFalse(packet['unsupported'])
        self.assertTrue(packet['diagnostics'][0].get('related'), packet)
        self.assertNotIn('thor-rust-run-', str(packet))
        self.assertNotIn('__thor_', str(packet))

    def test_infinite_loop_times_out(self):
        self.evaluate('work::proc()->int{for {};return 0}', 'work()', timeout=0.1,
                      error='execution exceeded the 0.1 second timeout', compare=False)

    def test_nonpure_root_is_rejected(self):
        self.evaluate('state:int\nwork::proc()->int{return state}', 'work()',
                      error='runtime storage', compare=False)

    def test_native_type_error_preserves_original_source(self):
        self.evaluate('work::proc(n:int)->int{return n+"bad"}', 'work(2)',
                      error='Cannot convert', compare=False)

    def test_generic_fixed_array_copy_and_borrowed_result(self):
        self.evaluate('work::proc(a:$T)->T{b:=a;b[0]+=2;return b}', 'work([3]int{1,2,3})')
        self.evaluate('work::proc(a:[]int)->[]int{return a[1:]}', 'work([]int{1,2,3})')

    def test_entry_call_cannot_mutate_its_slice_input(self):
        self.evaluate('work::proc(a:[]int)->int{a[0]=9;return a[0]}', 'work([]int{1,2})',
                      error='borrowed', compare=False)

    def test_lazy_generic_branch_does_not_require_impure_alternative(self):
        self.evaluate('import "core:fmt"\nwork::proc(x:$T)->T{when T==int{return x+1}else{fmt.println(x);return x}}', 'work(3)')

    def test_explicit_entry_can_evaluate_nested_ordinary_calls(self):
        self.evaluate('leaf::proc()->int{return 3}\nwork::proc(x:int)->int{return x*2}', 'work(leaf())')

    def test_library_entry_and_test_file_get_executable_helper(self):
        self.evaluate('work::proc()->int{return 6}', 'work()', package='library')
        self.evaluate('work::proc()->int{return 7}', 'work()', source_name='sample_test.thor', flags=['-debug','-build-mode:test'])

    def test_native_build_context_is_frozen_before_helper_selection(self):
        self.evaluate('work::proc()->bool{return ODIN_TEST && ODIN_BUILD_MODE==.Executable}', 'work()',
                      flags=['-debug','-build-mode:test'])

    def test_optional_allocator_error_primary_value(self):
        self.evaluate('import "core:mem"\nwork::proc()->(int,mem.Allocator_Error) #optional_allocator_error {return 7,nil}', 'work()')

    def test_checked_runtime_failures_keep_original_unicode_locations(self):
        for body, reason in [('assert(n!=7,"specific assertion");return n','specific assertion'),
                             ('a:=[1,2];return a[n]','out of range')]:
            with self.subTest(body=body):
                packet=self.evaluate('work::proc(n:int)->int{/* 😀 */'+body+'}', 'work(7)', error=reason, compare=False)
                note=packet['diagnostics'][0]['related'][0]
                self.assertEqual(note['pos']['line'],2,packet)
                self.assertNotIn('thor-rust-run-', str(packet))

    def test_scalar_type_query_alias_discards_runtime_operand(self):
        self.evaluate('import b "base:builtin"\nQUERY::b.size_of\nstate:[3]int\nwork::proc()->int{return QUERY(state)}', 'work()')

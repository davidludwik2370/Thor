"""Compile-time calls into inspected Odin distribution sources and dependencies."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from tests import test_thor as base
from thorlang.cache import EvaluationCache
from thorlang.driver import Project, frontend_binary
from thorlang.evaluation import Evaluator
from thorlang.compiler import ThorError


class NativeRunTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_original_round_and_power_array_length(self):
        self.invoke(self.program('''
SIZE :: 1_000
ARY_SIZE :: int(#run round(SIZE ** .33333))
data: [ARY_SIZE]int
assert(len(data) == 10)
''', 'import "core:math" / round\n'))

    def test_float_overloads_signed_zero_and_halfway_rounding(self):
        self.invoke(self.program('''
assert(A == f32(3) && B == -3 && transmute(u64)ZERO == 0x8000000000000000)
''', '''import "core:math"
A :: #run math.round(f32(2.5))
B :: #run math.round(-2.5)
ZERO :: #run math.round(-0.2)
'''))

    def test_math_dependencies_intrinsics_and_numeric_bitcasts(self):
        self.invoke(self.program('assert(ROOT == 3 && SINE == 0 && LOG == 0)', '''import "core:math"
ROOT :: #run math.sqrt(9.0)
SINE :: #run math.sin(0.0)
LOG :: #run math.ln(1.0)
'''))

    def test_deterministic_compiler_primitives_are_distinguished_from_state(self):
        self.invoke(self.program('assert(ROOT==3 && BITS==3)', '''import "base:intrinsics"
ROOT :: #run intrinsics.sqrt(f64(9))
BITS :: #run intrinsics.count_ones(u32(7))
'''))
        self.invoke(self.program('_=X', 'import "base:intrinsics"\nX :: #run intrinsics.read_cycle_counter()\n'),
                    command='check', fail='not an eligible pure operation')
        self.invoke(self.program('_=X', 'import "base:intrinsics"\nX :: #run intrinsics.type_map_info(map[int]int)\n'),
                    command='check', fail='not an eligible pure operation')

    def test_helper_source_identity_cannot_hide_behind_constant_aliases(self):
        self.invoke(self.program('_=X', '''import "base:intrinsics"
Record::struct{x:int}
NAME::intrinsics.type_canonical_name(Record)
work::proc()->string{return NAME}
X::#run work()
'''), command='check', fail='Source-location-dependent')
        self.invoke(self.program('_=X', '''
NAME::ODIN_BUILD_PROJECT_NAME
work::proc()->string{return NAME}
X::#run work()
'''), command='check', fail='Source-location-dependent')

    def test_original_test_context_and_test_tagged_odin_dependencies(self):
        self.invoke('''#+test
package demo
import "core:testing"
VALUE::#run context_value()
@(test) works::proc(t:^testing.T){testing.expect(t,VALUE)}
''', command='test', directory=True, files={'context.odin':'''#+test
package demo
context_value::proc()->bool{return ODIN_TEST && ODIN_BUILD_MODE==.Executable}
'''})

    def test_original_library_build_mode_is_retained(self):
        self.invoke('''package library
mode::proc()->bool{return ODIN_BUILD_MODE==.Dynamic && !ODIN_TEST}
VALUE::#run mode()
#assert(VALUE)
''', command='build', flags=['-build-mode:dynamic'])

    def test_context_macro_substitution_respects_lexical_bindings(self):
        self.invoke(self.program('assert(VALUE)', '''
Record::struct{ODIN_TEST:bool}
work::proc(ODIN_TEST:bool)->bool{return ODIN_TEST && Record{true}.ODIN_TEST}
VALUE::#run work(true)
'''))

    def test_selected_blank_wildcard_and_constant_aliases(self):
        for declaration in ['import "core:math" / round', 'import _ "core:math" / round', 'import * "core:math"']:
            with self.subTest(declaration=declaration):
                self.invoke(self.program('assert(X == 4)', declaration+'\nchosen :: round\nX :: #run chosen(3.6)\n'))

    def test_ordinary_wrapper_calls_native_procedures_transitively(self):
        self.invoke(self.program('assert(X == 10)', '''import "core:math"
work :: proc(value: f64) -> int { return 2 * int(math.round(value)) }
X :: #run work(4.6)
'''))

    def test_generic_slice_call_and_optional_status(self):
        self.invoke(self.program('assert(LOW == -2 && HIGH == 7 && EMPTY == 0)', '''import "core:slice"
LOW :: #run slice.min([]i16{3, -2, 7})
HIGH :: #run slice.max([]i16{3, -2, 7})
EMPTY :: #run slice.min([]i16{})
'''))

    def test_readonly_strings_and_native_constants(self):
        self.invoke(self.program('assert(PREFIX && VALUE == 180)', '''import "core:strings"
import "core:math"
PREFIX :: #run strings.has_prefix("Thor 😀", "Thor")
work :: proc() -> int { return int(math.RAD_PER_DEG * math.DEG_PER_RAD * 180) }
VALUE :: #run work()
'''))

    def test_pure_function_reads_inspected_constant_dependencies(self):
        self.invoke(self.program('assert(X > 1 && X < 2)', '''import "core:math"
work :: fn(value: f64) -> f64 { return value + math.LN2 }
X :: #run work(1.0)
'''))

    def test_generic_native_enum_identity_survives_helper_package_renaming(self):
        self.invoke(self.program('assert(generic(math.Float_Class(.Normal)) == size_of(math.Float_Class))', '''import "core:math"
type_size :: proc($T: typeid) -> int { return size_of(T) }
generic :: proc(value: $T) -> int { return #run type_size(T) }
'''))

    def test_foreign_standard_library_procedure_is_still_ineligible(self):
        self.invoke(self.program('_=X', 'import "core:c/libc"\nX :: #run libc.rand()\n'),
                    command='check', fail='foreign')

    def test_inspectable_odin_bodies_work_with_non_odin_calling_conventions(self):
        self.invoke(self.program('assert(X==9 && ROUNDED==3)', '''import "core:math/linalg/glsl"
square :: proc "c" (x: int) -> int { return x*x }
X :: #run square(3)
ROUNDED :: #run glsl.round_f64(2.7)
'''))

    def test_direct_approved_allocating_helpers_retain_their_input_contracts(self):
        self.invoke(self.program('''
text := #run format.format(42)
defer delete(text)
aligned := #run format.format_plain(42, align=.Right, width=4)
defer delete(aligned)
values := #run slice.clone([]int{2,3})
defer delete(values)
assert(text == "42" && aligned == "  42" && values[1] == 3)
''', 'import "thor:format"\nimport "core:slice"\n'))
        self.invoke(self.program('''
Record :: struct{value:int}
text := #run format.format(Record{7})
_=text
''', 'import "thor:format"\n'), command='check', fail='custom record formatters are not eligible')

    def test_allocator_named_scalar_is_not_an_allocator_selection(self):
        self.invoke(self.program('assert(X==8 && Y==16)', '''
work::proc(allocator:int)->int{return allocator+1}
twice::proc(value:int)->int{return value*2}
X::#run work(allocator=7)
Y::#run twice(work(allocator=7))
'''))

    def test_scalar_bitcasts_are_eligible_but_pointer_casts_are_not(self):
        self.invoke(self.program('assert(BITS == 0x3ff0000000000000)', '''
bits :: proc(x: f64) -> u64 { return transmute(u64)x }
BITS :: #run bits(1.0)
'''))
        self.invoke(self.program('_=X', '''
bad :: proc(x: u64) -> rawptr { return transmute(rawptr)x }
X :: #run bad(0)
'''), command='check', fail='pointer')

    def test_standard_library_runtime_state_is_rejected_at_the_call(self):
        self.invoke(self.program('_=X', '''import "core:os"
work :: proc() -> int { return len(os.args) }
X :: #run work()
'''), command='check', fail='outside the function')


class NativeSDKTests(unittest.TestCase):
    """A temporary SDK exercises arbitrary package names without editing Odin."""
    @classmethod
    def setUpClass(cls):
        cls.frontend, cls.odin = frontend_binary(os.environ.get('THOR_ODIN', 'odin'))

    def fixture(self, files, expression='fixture.compute(3)', overlays=None, flags=(), test=False):
        temporary = tempfile.TemporaryDirectory(prefix='thor-native-sdk-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        sdk = root / 'sdk'
        for name, text in files.items():
            file = sdk / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text)
        entry = root / 'input' / 'main.thor'
        entry.parent.mkdir()
        entry.write_text('package main\nimport "vendor:fixture"\nVALUE :: #run '+expression+'\nmain::proc(){}\n')
        def evaluate():
            project = Project(entry, self.frontend, list(flags), test=test, overlays=overlays or {})
            # This is the root the selected compiler would report for an SDK
            # installation; the real frontend/checker still compile the helper.
            for source in project.sources.values(): source.info['odin_root'] = str(sdk)
            cache = EvaluationCache(root / 'cache')
            evaluator = Evaluator(project, self.odin, cache=cache)
            evaluator.evaluate_all()
            site = next(n for n in project.sources[entry.resolve()].nodes if n.kind == 'Run')
            return evaluator.results[evaluator.key(site)], cache, project
        return root, sdk, entry, evaluate

    def test_arbitrary_packages_private_overloads_dependencies_and_unused_init(self):
        root, sdk, entry, evaluate = self.fixture({
            'vendor/fixture/main.odin': '''package fixture
import "core:dependency"
@private
integer :: proc(value: int) -> int { return dependency.twice(value) + 1 }
@private
text :: proc(value: string) -> int { _ = context; return 0 }
compute :: proc{integer, text}
@(init) unrelated :: proc(){panic("unrelated initializer must not run")}
''',
            'core/dependency/main.odin': 'package dependency\ntwice::proc(x:int)->int{return x*2}\n',
        })
        value, cache, project = evaluate()
        self.assertEqual(value, {'kind': 'integer', 'value': '7'})
        self.assertEqual(len(project.sources), len([s for s in project.sources.values() if not getattr(s, 'native_inspected', False)]))

    def test_native_test_tag_and_compilation_context_are_preserved(self):
        _,_,_,evaluate=self.fixture({
            'vendor/fixture/test.odin':'''#+test
package fixture
compute::proc(x:int)->int{return x+10 when ODIN_TEST else x}
''',
        },test=True)
        self.assertEqual(evaluate()[0]['value'],'13')

    def test_transitive_source_edits_invalidate_cached_results(self):
        root, sdk, entry, evaluate = self.fixture({
            'vendor/fixture/main.odin': 'package fixture\nimport "core:dependency"\ncompute::proc(x:int)->int{return dependency.times(x)}\n',
            'core/dependency/main.odin': 'package dependency\ntimes::proc(x:int)->int{return x*2}\n',
        })
        self.assertEqual(evaluate()[0]['value'], '6')
        self.assertEqual(evaluate()[1].stats['hits'], 1)
        file = sdk / 'core/dependency/main.odin'
        file.write_text(file.read_text().replace('x*2', 'x*3'))
        value, cache, _ = evaluate()
        self.assertEqual(value['value'], '9')
        self.assertEqual(cache.stats['hits'], 0)

    def test_native_resource_contents_invalidate_cached_results(self):
        root, sdk, entry, evaluate = self.fixture({
            'vendor/fixture/main.odin': 'package fixture\nTEXT::#load("text.txt",string)\ncompute::proc(x:int)->int{return len(TEXT)+x}\n',
            'vendor/fixture/text.txt': 'one',
        })
        self.assertEqual(evaluate()[0]['value'], '6')
        self.assertEqual(evaluate()[1].stats['hits'], 1)
        (sdk / 'vendor/fixture/text.txt').write_text('longer')
        self.assertEqual(evaluate()[0]['value'], '9')

    def test_owned_native_record_results_keep_independent_contents(self):
        root, sdk, entry, evaluate = self.fixture({
            'vendor/fixture/main.odin': '''package fixture
Report::struct{label:string,data:[]int}
compute::proc(x:int)->Report{data:=make([]int,2);data[0]=x;data[1]=x+1;return Report{"native",data}}
''',
        })
        value, _, _ = evaluate()
        self.assertEqual(value['kind'], 'record')
        fields = dict(value['fields'])
        self.assertEqual(fields['label']['bytes'], b'native'.hex())
        self.assertEqual([v['value'] for v in fields['data']['values']], ['3', '4'])

    def test_conditional_constants_keep_their_configured_dependencies(self):
        files={
            'vendor/fixture/main.odin':'package fixture\nimport "core:dependency"\ncompute::proc(x:int)->int{return dependency.VALUE+x}\n',
            'core/dependency/main.odin':'package dependency\nENABLED::#config(EXTRA,false)\nwhen ENABLED {VALUE::10} else {VALUE::20}\n',
        }
        for flags,expected in [((), '23'), (('-define:EXTRA=true',), '13')]:
            with self.subTest(flags=flags):
                _,_,_,evaluate=self.fixture(files,flags=flags)
                self.assertEqual(evaluate()[0]['value'],expected)

    def test_unsaved_native_body_overlay_is_used_and_invalidates_cache(self):
        overlays={}
        root,sdk,entry,evaluate=self.fixture({
            'vendor/fixture/main.odin':'package fixture\ncompute::proc(x:int)->int{return x+1}\n',
        },overlays=overlays)
        self.assertEqual(evaluate()[0]['value'],'4')
        file=sdk/'vendor/fixture/main.odin'
        overlays[file]=file.read_bytes().replace(b'x+1',b'x+2')
        value,cache,_=evaluate()
        self.assertEqual(value['value'],'5')
        self.assertEqual(cache.stats['hits'],0)
        self.assertIn(b'x+1',file.read_bytes())

    def test_private_access_is_not_granted_by_evaluation_bridges(self):
        root, sdk, entry, evaluate = self.fixture({
            'vendor/fixture/main.odin': 'package fixture\n@private\nsecret::proc(x:int)->int{return x}\n',
        }, expression='fixture.secret(3)')
        with self.assertRaises(ThorError) as caught: evaluate()
        self.assertIn('private', str(caught.exception))
        self.assertEqual(caught.exception.diagnostics[0]['pos']['file'], str(entry))

    def test_rejected_native_effect_highlights_original_run_with_source_note(self):
        root, sdk, entry, evaluate = self.fixture({
            'vendor/fixture/main.odin': 'package fixture\nstate:int\ncompute::proc(x:int)->int{return state+x}\n',
        })
        with self.assertRaises(ThorError) as caught: evaluate()
        errors = caught.exception.diagnostics
        self.assertTrue(any('outside the function' in '\n'.join(e['msgs']) for e in errors), errors)
        self.assertTrue(all(e['pos']['file'] == str(entry) for e in errors), errors)
        self.assertTrue(any('standard-library source' in '\n'.join(e['msgs']) for e in errors))
        self.assertNotIn('__thor_', json.dumps(errors))


if __name__ == '__main__':
    unittest.main()

# A commented Thor tour

[demo.thor](demo.thor) follows the layout of Odin's `examples/demo/demo.odin`:
one procedure per topic, small scoped variations, and explanations beside the
code. The examples are independent of Thor's earlier `demo/main.thor`.

From the repository root:

```sh
./thor run examples/demo
./thor run examples/demo/demo.thor
./thor run examples/demo -define:TOUR_MODE=1
./thor run examples/demo -define:TOUR_MODE=3
./thor test examples/demo -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
```

The test wrapper runs the same assertions under Odin's allocation tracker.
It checks that the owned strings, sets, scratch buffers, and deep pure results
are released. No special Odin feature flags are needed.

The walkthrough covers:

| Topic procedure | Examples and interactions |
| --- | --- |
| `imports_and_odin` | Selective/default imports, overloads, mixed source packages, native `when`, source-relative resources |
| `arrays_and_context` | Inferred fixed arrays, empty/nested literals, enum context, private imported types, evaluation order |
| `membership_and_constraints` | Sequence/map/bit-set membership, interval endpoints, exact type lists, value constraints, loop ambiguity |
| `conditional_expressions` | Multiline/lazy ternaries, selector and boolean switches, contextual results, default/enum coverage rules |
| `compile_time_selection` | Both `#switch` forms, declaration selection, `#if`/`#else if`, inactive names and native `when` |
| `immutable_storage` | Runtime initialization, protected subobjects, mutable copies, shallow slice/pointer immutability |
| `formatting_and_strings` | Quoted/raw/nested fstrings, literal braces, quoted Unicode fills, grouping, order, owned strings and borrowed views |
| `sets_and_ownership` | Type/library spellings, lazy allocation, all operators, independent results, anonymous cleanup, shallow keys and aliases |
| `negative_indices` | Reads/writes/addresses, slices, dynamic arrays, UTF-8 bytes, ordinary negative map keys, once-only evaluation |
| `power_and_native_expansion` | Precedence, associativity, float coercion, strict operand types, fixed-width integers, prefix `**` expansion |
| `expression_interactions` | Lazy paths, loop conditions/posts, switch inside fstring, set iteration cleanup, C callbacks |
| `pure_functions_and_constants` | Recursive/generic/imported `fn`, explicit eligible `proc`, forward constants, scratch mutation, exact types and float bits |
| `owned_pure_results` | Recursive ownership, repeated aliases, cloning, runtime/folded materialization, defers, zero-sized slice elements |
| `generic_evaluation` | Type and constant specialization, `#run` inside a runtime generic procedure, computed array sizes |

Commented invalid examples explain current boundaries; they are not executable
rejection tests. Compiler regressions and rejection fixtures remain in `tests/`.

Run twice with `--cache-report` to compare cold and warm compile-time evaluation.
Use `--no-cache` to force native evaluation again. `guide/` contains original
Odin and Thor helpers; `assets/notice.txt` exercises source-relative resources.

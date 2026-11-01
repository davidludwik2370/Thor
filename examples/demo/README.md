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
./thor run examples/demo -debug
./thor test examples/demo -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
```

The test wrapper runs the same assertions under Odin's allocation tracker.
It checks that the owned strings, sets, big numbers, scratch buffers, and deep
pure results are released. The debug run additionally enables Thor's default
allocation tracking and AddressSanitizer. No special Odin feature flags are needed.

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
| `big_numbers_and_construction` | Unquoted long literals, exact decimals, zero allocation, native conversions/aliases, normalization, repeating/text fractions, parsing failures, independent read-only components |
| `big_ownership_and_aggregates` | Direct clones, replacement/self/simultaneous assignment, immutable copies, borrowed/generic/native returns, explicit clones, shallow records versus deep clone, arrays/negative indices, append and map replacement |
| `big_arithmetic_and_control_flow` | Literal coercion including variadic/method parameters, strict typed operands, exact intermediate arithmetic, signed remainders/shifts, bitwise/power, lazy ternaries, selector/boolean value switches, multiple results, skipped assignment, parsing fallback and membership |
| `big_constants_and_evaluation` | Read-only constants, exact decimal arithmetic, both compile-time switches, conditional compilation, automatic/explicit/runtime pure calls, deferred deletion, independently reconstructed nested results |
| `big_formatting_and_libraries` | Builtin abs/clamp/min/max, fixed/slice expansion and empty reductions, shared basic sum/order/sort, generic component properties, nested/pure fstrings, grouping/alignment, tables and explicit native fmt registration |

Commented invalid examples explain current boundaries; they are not executable
rejection tests. Compiler regressions and rejection fixtures remain in `tests/`.

Run twice with `--cache-report` to compare cold and warm compile-time evaluation.
Use `--no-cache` to force native evaluation again. `guide/` contains original
Odin and Thor helpers, including a native numeric-return ownership example;
`assets/notice.txt` exercises source-relative resources. The
[numeric reference](../../docs/arbitrary-precision.md) records the intentional
ownership/type boundaries in full. Smaller focused numeric demos remain in
`examples/big_numbers`, `big_operators`, `big_comptime`, and `big_integration`.

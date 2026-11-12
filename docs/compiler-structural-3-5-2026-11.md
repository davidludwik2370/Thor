# Structural compiler optimization, steps 3–5

Baseline: `482a79c`. This pass covers shared type identities, completed procedure
summaries, and independent semantic/lowering work. The Python reference and Odin
compiler remain unchanged.

## Step 3: shared type identities

Resolver layout/member/path caches now use compact arena-local type IDs.
Permission graph values and convergence snapshots hold canonical immutable type
handles rather than cloning recursive type trees. Owned `Type` values remain at
the compiler's existing public boundaries. IDs do not enter emitted source,
source maps, serialized evaluation values, or persistent cache keys.

Type identity remains nominal. Handles from unrelated arenas are distinct;
metadata invalidation still clears context-dependent answers when bindings,
evaluated values, or contextual typing change. The type arena itself stores
immutable structural descriptions, not those answers.

Initial gates: 58 Rust units and 42 focused semantic/ownership integration
methods passed. Seven alternating warm transpile samples per executable gave
UI 0.844→0.842 s and Othello AI 0.962→0.960 s. These approximately 0.3% differences
are effectively flat, not evidence of a large speedup. The shared representation
is also used as the basis for summary snapshots and lowering workers below.

## Step 4: completed procedure summaries

Dependency retries can retain closed, completed abstract procedure graphs, with
their aliases, effects, and ordered diagnostics. Reuse requires identical existing
syntax, bindings, flags, context witnesses, evaluated values, and numeric-type
activation. Appending files to an existing namespace invalidates it. Helpers,
recursion, unresolved branches, ambient/global runtime state, and projected local
symbols use ordinary analysis. Snapshots have a source-size-dependent budget and
are only harvested when discovery stalls. This is command-local reuse, not a new
persistent project cache.

Seven new integration methods exercise checked and unchecked builds, aliases,
mutation, diagnostics, generic array copies versus slice views, named results,
recursive graphs, global writes, and recursion. The focused 51-method gate and
59 Rust units passed. Seven alternating warm samples versus `482a79c` were flat:
UI 0.867→0.870 s, Othello AI 1.023→1.023 s. These workloads provide controls rather
than evidence of a speedup; the synthetic dependency test verifies that completed
summaries actually reuse work.

## Step 5: independent lowering

Implementation and validation continue after the summary checkpoint.

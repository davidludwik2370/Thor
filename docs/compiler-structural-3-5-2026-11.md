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

## Steps 4–5

Implementation and validation continue after the type-identity checkpoint.
